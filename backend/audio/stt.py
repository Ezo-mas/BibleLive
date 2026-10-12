"""Speech-to-text transcription using faster-whisper."""

import contextlib
import logging
import os
import re
import sys
import time
from dataclasses import dataclass


def _register_nvidia_dll_dirs() -> None:
    """Register pip-installed NVIDIA DLL dirs before ctranslate2 loads (Windows).

    The cu12 wheels (nvidia-cublas-cu12, nvidia-cudnn-cu12, ...) drop their
    DLLs under <site-packages>/nvidia/*/bin, which is not on the DLL search
    path by default — without this, WhisperModel(device="cuda") fails with
    "cublas64_12.dll is not found". Locations resolve from sys.path so this
    survives venv recreates with no manual PATH edits. No-op off Windows or
    when the wheels are absent.
    """
    # sys.platform (not os.name) so mypy skips this body on Linux, where
    # typeshed has no os.add_dll_directory.
    if sys.platform != "win32":
        return
    bin_dirs: list[str] = []
    for base in sys.path:
        if not base or not os.path.isdir(base):
            continue
        nvidia_dir = os.path.join(base, "nvidia")
        if not os.path.isdir(nvidia_dir):
            continue
        try:
            packages = os.listdir(nvidia_dir)
        except OSError:
            continue
        for pkg in packages:
            bin_dir = os.path.join(nvidia_dir, pkg, "bin")
            if os.path.isdir(bin_dir):
                bin_dirs.append(bin_dir)
                with contextlib.suppress(OSError, AttributeError):
                    os.add_dll_directory(bin_dir)
    if bin_dirs:
        # ctranslate2 resolves cublas via PATH rather than the
        # AddDllDirectory list, so export the folders to the process PATH as
        # well (read dynamically by the loader at call time). Prepend so the
        # venv-pinned copies win over any stale system ones.
        path_entries = os.environ.get("PATH", "").split(os.pathsep)
        missing = [d for d in bin_dirs if d not in path_entries]
        if missing:
            os.environ["PATH"] = os.pathsep.join([*missing, *path_entries])


_register_nvidia_dll_dirs()

import numpy as np  # noqa: E402 (deferred: DLL dirs must register before ctranslate2 loads)
from faster_whisper import WhisperModel  # noqa: E402 (deferred: see above)

from core.books import CANONICAL_BOOKS  # noqa: E402 (deferred: see above)

logger = logging.getLogger(__name__)

# Per-language Whisper initial prompts: bias the decoder toward Bible
# vocabulary and "chapter/verse" number patterns for each supported language.
WHISPER_INITIAL_PROMPTS: dict[str, str] = {
    "en": (
        "The sermon references Bible books such as Genesis, Exodus, Leviticus, "
        "Numbers, Deuteronomy, Joshua, Judges, Ruth, Samuel, Kings, Chronicles, "
        "Ezra, Nehemiah, Esther, Job, Psalms, Proverbs, Ecclesiastes, Isaiah, "
        "Jeremiah, Ezekiel, Daniel, Matthew, Mark, Luke, John, Acts, Romans, "
        "Corinthians, Galatians, Ephesians, Philippians, Colossians, Thessalonians, "
        "Timothy, Titus, Philemon, Hebrews, James, Peter, Jude, and Revelation. "
        "For example: turn to John chapter three verse sixteen, or Romans 8:28."
    ),
    "lt": (
        "Pamokslas mini Rašto knygas: Pradžios, Išėjimo, Kunigų, Skaičių, "
        "Pakartoto Įstatymo, Jozuės, Teisėjų, Rūtos, Samuelio, Karalių, "
        "Metraščių, Ezros, Nehemijo, Esteros, Jobo, Psalmynas, Psalmės, "
        "Patarlių, Mokytojo, Izaijo, Jeremijo, Ezechielio, Danielės, Mato, "
        "Morkaus, Luko, Jono evangelija, Apaštalų darbai, Romiečiams, "
        "Korintiečiams, Galatams, Efeziečiams, Filipiečiams, Kolosiečiams, "
        "Tesalonikiečiams, Timotėjui, Titui, Filemonui, Hebrėjams, Jokūbo, "
        "Petro, Judo, Apreiškimas Jonui. Pavyzdžiui: Jono evangelija "
        "trečias skyrius, šešiolikta eilutė."
    ),
}

# Per-language Whisper hotwords (empty = off). In faster-whisper these are not a
# decode-time boost: they are text placed in the same "previous text" prompt
# slot as ``initial_prompt``, just ahead of it, and re-applied on every 30 s
# seek. Left empty on purpose: a bare Lithuanian number/"skyrius/eilutė" word
# list measured WORSE on the small model (2026-10-12, 07-05 trim40 + 09-13,
# 25 gold refs: 5 TP / 5 FP without it, 3 TP / 12 FP with it), mostly extra
# false positives. Re-measure with benchmarks/run_benchmark.py before enabling.
WHISPER_HOTWORDS: dict[str, str] = {"en": "", "lt": ""}

SUPPORTED_LANGUAGES = tuple(WHISPER_INITIAL_PROMPTS.keys())


@dataclass(frozen=True)
class _LanguageState:
    """Whisper language and its decoder prompt, swapped as a single unit.

    The two must always agree: an English ``language`` paired with a Lithuanian
    ``initial_prompt`` (or vice versa) biases the decoder toward the wrong
    vocabulary. Keeping them in one immutable object means a reader always sees
    a consistent pair and ``set_language`` swaps it in a single assignment.
    """

    language: str
    initial_prompt: str
    hotwords: str


def _validate_language(language: str) -> None:
    """Raise :class:`ValueError` unless ``language`` is supported."""
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Unsupported language '{language}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}"
        )


def _language_state(language: str) -> _LanguageState:
    """Build the immutable ``(language, prompt)`` pair for ``language``."""
    _validate_language(language)
    return _LanguageState(language, WHISPER_INITIAL_PROMPTS[language], WHISPER_HOTWORDS[language])


# Regex corrections for merged "book chapter verse" numbers.
#
# The book alternation is generated from the shared canon (core/books.py). A
# hand-maintained copy here is how "timothy" came to be misspelled "timon" and
# silently dropped from the corrections. Multi-word and numbered names allow
# optional whitespace between words because Whisper often writes them merged
# ("1samuel", "songofsolomon").
_BOOK_NAMES_PATTERN = "|".join(r"\s?".join(book.name.lower().split()) for book in CANONICAL_BOOKS)

_REFERENCE_CORRECTIONS: dict[str, str] = {
    # "mark112" -> "mark 1 12"
    rf"\b({_BOOK_NAMES_PATTERN})\s*(\d+)\s*(\d+)\b": r"\1 \2:\3",
    # "mark 1:12 5" -> "mark 1:12-5"
    rf"\b({_BOOK_NAMES_PATTERN})\s*(\d+):(\d+)\s*(\d+)\b": r"\1 \2:\3-\4",
}

# Whisper hallucination guard. On low-energy or tonal input the decoder can
# latch onto a phrase from the initial prompt and loop it for the rest of the
# buffer ("Romans 8, or Romans 8, or Romans 8, ..." for tens of seconds). A
# sermon transcript never repeats a phrase this many times back to back, so
# anything at or above these thresholds is treated as a decoding artifact.
MIN_REPEAT_BLOCK_WORDS = 2  # shortest phrase that can be the repeated block
MIN_REPEAT_COUNT = 3  # repetitions of that block before we call it a loop
MIN_REPEAT_WORDS = 8  # total repeated words before we call it a loop


class SpeechToText:
    """Handles speech-to-text transcription using faster-whisper"""

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cuda",
        language: str = "en",
        compute_type: str | None = None,
    ):
        # Language + prompt live in one immutable value (see _LanguageState);
        # set_language() swaps the pair atomically at runtime.
        self._state = _language_state(language)

        # Default compute type: int8 (works on all GPUs incl. Pascal GTX 10xx
        # and CPU). Pass compute_type explicitly (e.g. "float16") on newer GPUs
        # (Turing RTX 20xx+ / Ampere+) for best quality.
        if compute_type is None:
            compute_type = "int8"
        elif device == "cuda":
            import ctranslate2

            supported = ctranslate2.get_supported_compute_types("cuda")
            if compute_type not in supported:
                logger.warning(
                    f"compute_type '{compute_type}' not supported on this GPU "
                    f"(supported: {sorted(supported)}), falling back to 'int8'"
                )
                compute_type = "int8"

        logger.info(
            f"Loading Whisper model '{model_size}' on {device} "
            f"(compute_type={compute_type}, language={language})..."
        )
        try:
            self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
            logger.info("Whisper model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise

        # Audio buffer for accumulating speech
        self.audio_buffer: list[float] = []
        self.sample_rate = 16000

    @property
    def language(self) -> str:
        """The language the next transcription will use."""
        return self._state.language

    @property
    def initial_prompt(self) -> str:
        """The Whisper initial prompt matching :attr:`language`."""
        return self._state.initial_prompt

    @property
    def hotwords(self) -> str:
        """The Whisper hotwords matching :attr:`language` (may be empty)."""
        return self._state.hotwords

    def set_language(self, language: str) -> None:
        """Switch the transcription language and its initial prompt.

        Safe to call from another thread: the language/prompt pair is replaced
        atomically, and :meth:`transcribe_buffer` reads it once before calling
        Whisper, so an in-flight transcription keeps the pair it started with.
        The Whisper model itself is language-agnostic and is not reloaded.

        Raises:
            ValueError: if ``language`` is not in ``SUPPORTED_LANGUAGES``.
        """
        self._state = _language_state(language)
        logger.info("Speech-to-text language set to '%s'", language)

    def add_audio_frame(self, frame: bytes):
        """Add audio frame to buffer for transcription"""
        # Convert bytes to numpy array
        audio_np = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        self.audio_buffer.extend(audio_np)

    def transcribe_buffer(self, language: str | None = None) -> tuple[str | None, float]:
        """
        Transcribe accumulated audio buffer and clear it.

        Args:
            language: the language this utterance was captured in. ``None``
                uses the current global language. Passing the utterance's own
                language keeps a segment queued before a runtime switch paired
                with the language (and initial prompt) it was captured under.

        Returns:
            Tuple of (transcribed_text, duration_ms)

        Raises:
            ValueError: if an explicit ``language`` is not supported.
        """
        if not self.audio_buffer:
            return None, 0.0

        # Resolve the language/prompt pair once, before consuming audio:
        # set_language() may swap the global pair from another thread, and the
        # two must never be mixed. No lock is held during transcription.
        state = self._state if language is None else _language_state(language)

        # Convert buffer to numpy array
        audio_np = np.array(self.audio_buffer, dtype=np.float32)

        # Clear buffer for next utterance
        self.audio_buffer = []

        # Skip if audio is too short (less than 0.5 seconds)
        if len(audio_np) < self.sample_rate * 0.5:
            return None, 0.0

        start_time = time.time()

        try:
            # Transcribe with Whisper
            segments, info = self.model.transcribe(
                audio_np,
                language=state.language,
                initial_prompt=state.initial_prompt,
                # Only sent when set: older faster-whisper releases (the pin
                # allows >=0.9.0) reject the argument, and the broad except
                # below would then turn every utterance into "no transcript".
                **({"hotwords": state.hotwords} if state.hotwords else {}),
                beam_size=5,
                best_of=5,
                temperature=0.0,
                compression_ratio_threshold=2.4,
                log_prob_threshold=-1.0,
                no_speech_threshold=0.6,
                condition_on_previous_text=False,
                # Decoder-side loop guards (post-processing in
                # _collapse_repetition is the backstop if these are ever hit).
                repetition_penalty=1.05,
                no_repeat_ngram_size=6,
            )

            # Combine all segments
            text = " ".join([segment.text.strip() for segment in segments]).strip()

            # Apply post-processing fixes for common transcription errors
            text = self._fix_common_transcription_errors(text)

            # Drop degenerate decoder loops before anything downstream sees them
            text, loop = self._collapse_repetition(text)

            duration_ms = (time.time() - start_time) * 1000

            if loop:
                logger.warning(f"Discarded Whisper repetition loop ({loop})")

            return text if text else None, duration_ms

        except RuntimeError as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Whisper runtime error: {e}")
            return None, duration_ms
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Unexpected transcription error: {e}", exc_info=True)
            return None, duration_ms

    def _collapse_repetition(self, text: str) -> tuple[str, str | None]:
        """Collapse a degenerate Whisper repetition loop.

        Whisper sometimes latches onto a phrase (often one seeded by the initial
        prompt) and repeats it for the rest of the buffer. That single artifact
        turns into dozens of "found reference" lines downstream, so the repeated
        block is reduced to one occurrence here.

        Returns the cleaned text and a description of the loop that was removed,
        or ``None`` when the text is not looping.
        """
        words = text.split()
        if len(words) < MIN_REPEAT_COUNT * MIN_REPEAT_BLOCK_WORDS:
            return text, None

        # Try each block size, and each start position, looking for a block that
        # repeats back to back often enough to be a decoding artifact.
        best: tuple[int, int, int] | None = None  # (start, block_len, count)
        max_block = max(MIN_REPEAT_BLOCK_WORDS, len(words) // MIN_REPEAT_COUNT)

        for block_len in range(MIN_REPEAT_BLOCK_WORDS, max_block + 1):
            for start in range(0, len(words) - block_len * MIN_REPEAT_COUNT + 1):
                block = words[start : start + block_len]
                count = 1
                while words[start + block_len * count : start + block_len * (count + 1)] == block:
                    count += 1
                if count < MIN_REPEAT_COUNT or count * block_len < MIN_REPEAT_WORDS:
                    continue
                # Prefer the earliest, longest match.
                if best is None or start < best[0]:
                    best = (start, block_len, count)

        if best is None:
            return text, None

        start, block_len, count = best
        collapsed = words[: start + block_len]  # keep one occurrence of the block
        block_text = " ".join(words[start : start + block_len])
        return " ".join(collapsed), f"'{block_text}' repeated {count} times, trimmed to 1"

    def _fix_common_transcription_errors(self, text: str) -> str:
        """Fix common transcription errors in Bible references"""
        # More conservative approach: only rewrite clearly merged
        # "book<digits><digits>" / "book<c:v> <v>" patterns, and only when the
        # surrounding words look like a Bible reference.
        for pattern, replacement in _REFERENCE_CORRECTIONS.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        return text
