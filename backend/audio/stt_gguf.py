"""Lithuanian speech-to-text with GGUF speech models through transcribe.cpp.

Engines (``--stt``):

- ``whisper`` (default): faster-whisper for every language (``audio/stt.py``).
- ``kmynas``: Kmynas v4, a Lithuanian fine-tune of NVIDIA Parakeet. Runs on a
  plain CPU faster than real time (8th-gen i5 / Zen 1 class).
- ``canary``: NVIDIA Canary-1B-v2.
- ``kmynas+canary``: both on every utterance; a reference only one model finds
  is reported as low confidence (``audio/ensemble.py``). For NVIDIA GPUs.

The GGUF models handle Lithuanian only. English utterances go to Whisper,
which is loaded the first time one arrives (or at start when the pipeline
starts in English), so a Lithuanian-only service never loads it.

Needs the optional ``transcribe-cpp`` package (``pip install -e "./backend[gguf]"``).
Its default native library runs on the CPU or, through Vulkan, on a GPU; for
CUDA point ``TRANSCRIBE_LIBRARY`` at a CUDA build of transcribe.cpp
(``-DTRANSCRIBE_CUDA=ON``). ``--device cpu`` keeps the models on the CPU.
Model files: GGUF conversions of the NeMo checkpoints (Q8_0 measured as
accurate as F32).
"""

import contextlib
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Literal, Protocol

import numpy as np

from .stt import (
    SpeechToText,
    Transcription,
    _validate_language,
    collapse_repetition,
    fix_common_transcription_errors,
)

logger = logging.getLogger(__name__)

STT_ENGINES = ("whisper", "kmynas", "canary", "kmynas+canary")
# Languages the GGUF engines transcribe; anything else goes to Whisper.
GGUF_LANGUAGES = ("lt",)
SAMPLE_RATE = 16000


def _register_cuda_dll_dirs() -> None:
    """Let a CUDA build of transcribe.cpp find its DLLs (Windows).

    Python does not search PATH for a DLL's dependencies, so the library's own
    folder and the CUDA toolkit's ``bin`` (``bin/x64`` in CUDA 13) are added
    explicitly. No-op elsewhere or when nothing is configured.
    """
    if sys.platform != "win32":
        return
    dirs = []
    library = os.environ.get("TRANSCRIBE_LIBRARY")
    if library:
        dirs.append(os.path.dirname(os.path.abspath(library)))
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        dirs += [os.path.join(cuda_path, "bin", "x64"), os.path.join(cuda_path, "bin")]
    for d in dirs:
        if os.path.isdir(d):
            with contextlib.suppress(OSError):
                os.add_dll_directory(d)


def engine_models(engine: str) -> list[str]:
    """The GGUF models an ``--stt`` engine runs, primary first ([] for Whisper)."""
    if engine not in STT_ENGINES:
        raise ValueError(f"Unknown STT engine '{engine}'. Supported: {', '.join(STT_ENGINES)}")
    return [] if engine == "whisper" else engine.split("+")


class SpeechModel(Protocol):
    """What :class:`MultiModelSpeechToText` needs from one model."""

    name: str

    def transcribe(self, audio: np.ndarray, language: str) -> Transcription: ...


class GgufSpeechToText:
    """One GGUF speech model (Kmynas or Canary) loaded through transcribe.cpp.

    Not thread-safe: the pipeline's transcription thread is its only caller.
    """

    def __init__(self, name: str, model_path: str, device: str = "cuda", cpu_threads: int = 0):
        _register_cuda_dll_dirs()
        try:
            import transcribe_cpp
        except ImportError as e:
            raise RuntimeError(
                f'--stt {name} needs the transcribe-cpp package (pip install -e "./backend[gguf]")'
            ) from e

        self.name = name
        self._errors = transcribe_cpp.errors
        # "auto" takes the best device the native library can use (CUDA in a
        # CUDA build, else Vulkan, else CPU); TRANSCRIBE_BACKEND overrides it.
        backend: Literal["cpu", "auto"] = "cpu" if device == "cpu" else "auto"
        logger.info(f"Loading {name} model '{model_path}' ({backend})...")
        self._model = transcribe_cpp.Model(model_path, backend=backend)
        self._session = self._model.session(n_threads=cpu_threads)
        logger.info(f"{name} model loaded on {self._model.backend}")

    def transcribe(self, audio: np.ndarray, language: str) -> Transcription:
        """Transcribe one utterance (float32 samples at 16 kHz)."""
        start_time = time.time()
        low_confidence_reason: str | None = None
        try:
            # A float32 buffer: the binding copies it without a per-sample loop.
            text = self._session.run(memoryview(audio), language=language).text
        except self._errors.OutputRepetition as e:
            # The decoder stopped a loop and kept one copy; like a trimmed
            # Whisper loop, what is left came out of a degenerate decode.
            partial = e.partial_result
            text = partial.text if partial is not None else ""
            low_confidence_reason = f"{self.name}: output repetition"
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"{self.name} transcription error: {e}")
            return Transcription(None, duration_ms, failed=True, source=self.name)

        text = fix_common_transcription_errors(text.strip())
        text, loop = collapse_repetition(text)
        if loop:
            logger.warning(f"Discarded {self.name} repetition loop ({loop})")
            low_confidence_reason = low_confidence_reason or f"repetition loop: {loop}"
        duration_ms = (time.time() - start_time) * 1000
        if not text:
            return Transcription(None, duration_ms, source=self.name)
        return Transcription(text, duration_ms, low_confidence_reason, source=self.name)


class MultiModelSpeechToText:
    """GGUF models for Lithuanian, Whisper for every other language.

    Same interface as :class:`audio.stt.SpeechToText` (``language``,
    ``set_language``, ``transcribe_utterance``). With two models the result is
    the first model's transcript with the second one's as ``alternate``.

    Whisper loads in the background when a switch asks for it: loading (and a
    first download) takes about a minute on a CPU, and on the transcription
    thread it held up every queued utterance, Lithuanian ones included. Until it
    is ready, utterances in its languages are skipped as unheard; a failed load
    reports them as failed (the operator sees the warning) until the next switch
    tries again. Starting in such a language still loads it before returning.
    """

    def __init__(
        self,
        models: list[SpeechModel],
        whisper_factory: Callable[[], SpeechToText],
        language: str = "lt",
    ):
        if not models:
            raise ValueError("MultiModelSpeechToText needs at least one model")
        _validate_language(language)
        self.models = models
        self.sample_rate = SAMPLE_RATE
        self._language = language
        self._whisper_factory = whisper_factory
        self._whisper: SpeechToText | None = None
        self._whisper_lock = threading.Lock()
        self._whisper_loader: threading.Thread | None = None
        self._whisper_failed = False
        if language not in GGUF_LANGUAGES:
            # Starting in such a language: load now, and a failure stops startup.
            logger.info("Loading Whisper for non-Lithuanian speech...")
            self._whisper = whisper_factory()

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, language: str) -> None:
        """Switch the transcription language; Whisper starts loading if it needs to."""
        _validate_language(language)
        self._language = language
        logger.info("Speech-to-text language set to '%s'", language)
        if language not in GGUF_LANGUAGES:
            self._start_whisper_load(retry=True)

    def _start_whisper_load(self, retry: bool = False) -> None:
        """Load Whisper on its own thread, unless it is loaded or loading.

        After a failed load only an explicit switch (``retry``) tries again, so a
        missing download is not retried on every utterance.
        """
        with self._whisper_lock:
            if self._whisper is not None:
                return
            if self._whisper_loader is not None and self._whisper_loader.is_alive():
                return
            if self._whisper_failed and not retry:
                return
            self._whisper_failed = False
            self._whisper_loader = threading.Thread(
                target=self._load_whisper, name="whisper-load", daemon=True
            )
            self._whisper_loader.start()

    def _load_whisper(self) -> None:
        logger.info("Loading Whisper for non-Lithuanian speech in the background...")
        try:
            whisper = self._whisper_factory()
        except Exception:
            logger.exception("Whisper failed to load; non-Lithuanian speech is not transcribed")
            with self._whisper_lock:
                self._whisper_failed = True
            return
        with self._whisper_lock:
            self._whisper = whisper
        logger.info("Whisper is ready")

    def wait_for_whisper(self, timeout: float | None = None) -> bool:
        """Block until a started Whisper load ends; True when Whisper is ready."""
        loader = self._whisper_loader
        if loader is not None:
            loader.join(timeout)
        return self._whisper is not None

    def transcribe_utterance(self, pcm: bytes, language: str | None = None) -> Transcription:
        """Transcribe one utterance of 16 kHz mono int16 PCM in ``language``."""
        language = self._language if language is None else language
        _validate_language(language)
        if language not in GGUF_LANGUAGES:
            whisper = self._whisper
            if whisper is not None:
                return whisper.transcribe_utterance(pcm, language)
            self._start_whisper_load()
            if self._whisper_failed:
                return Transcription(None, 0.0, failed=True)
            logger.info("Whisper is still loading: skipping this '%s' utterance", language)
            return Transcription(None, 0.0)

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio) < self.sample_rate * 0.5:  # as Whisper: skip under 0.5 s
            return Transcription(None, 0.0, source=self.models[0].name)

        results = [model.transcribe(audio, language) for model in self.models]
        primary = results[0]
        if len(results) == 1:
            return primary
        alternate = results[1]
        return replace(
            primary,
            duration_ms=primary.duration_ms + alternate.duration_ms,
            # Either model failing means detection is degraded: report it.
            failed=primary.failed or alternate.failed,
            alternate=alternate,
        )


def create_gguf_speech_to_text(
    engine: str,
    model_paths: dict[str, str | None],
    device: str,
    cpu_threads: int,
    language: str,
    whisper_factory: Callable[[], SpeechToText],
) -> MultiModelSpeechToText:
    """Load the GGUF models ``engine`` needs (``model_paths`` keyed by model name)."""
    names = engine_models(engine)
    # Check every path before loading anything: a load takes seconds.
    for name in names:
        if not model_paths.get(name):
            raise ValueError(f"--stt {engine} needs --{name}-model (a .gguf file)")
    models: list[SpeechModel] = [
        GgufSpeechToText(name, str(model_paths[name]), device=device, cpu_threads=cpu_threads)
        for name in names
    ]
    return MultiModelSpeechToText(models, whisper_factory, language)
