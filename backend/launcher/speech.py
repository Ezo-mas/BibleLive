"""Lithuanian speech model choice: the GGUF models (Kmynas, Canary) or Whisper.

The engines themselves are in ``audio/stt_gguf.py``. This module decides what the launcher
passes to ``audio.cli`` for them, and finds their files in the Hugging Face cache under
``models_dir`` (``HF_HOME``). It never downloads: that is ``commands.download_speech_command``.
"""

import ctypes
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .gpu import SupportedProbe, cuda_supported_compute_types, pick_compute_type


@dataclass(frozen=True)
class GgufModel:
    repo_id: str  # Hugging Face repo that holds the file
    filename: str  # the .gguf file in that repo
    approx_mb: int  # for progress percentages only


# The GGUF speech models: the one place to change a download. A repo may be renamed or moved, in
# which case only these entries change (and the installer / --download-model follow).
GGUF_MODELS: dict[str, GgufModel] = {
    "kmynas": GgufModel("Ezomas/kmynas-parakeet-lt-v4-gguf", "kmynas-v4-Q8_0.gguf", 740),
    "canary": GgufModel("handy-computer/canary-1b-v2-gguf", "canary-1b-v2-Q8_0.gguf", 1140),
}

# Languages the GGUF engines transcribe; as ``GGUF_LANGUAGES`` in audio/stt_gguf.py.
GGUF_LANGUAGES = ("lt",)
MAX_SPEECH_THREADS = 6

_CPU_ENGINE = "kmynas"
_GPU_ENGINE = "kmynas+canary"


@dataclass(frozen=True)
class SpeechChoice:
    """What the audio pipeline will run: ``engine`` after any fallback, with its model files."""

    engine: str  # "whisper" | "kmynas" | "kmynas+canary"
    model_paths: dict[str, str] = field(default_factory=dict)  # GGUF model name -> local file
    warnings: list[str] = field(default_factory=list)


def engine_models(engine: str) -> list[str]:
    """The GGUF models an engine runs, primary first ([] for Whisper)."""
    return [] if engine in ("auto", "whisper") else engine.split("+")


def resolve_speech_engine(
    requested: str, probe: SupportedProbe = cuda_supported_compute_types
) -> str:
    """``auto``: Kmynas + Canary on an NVIDIA GPU, Kmynas alone without one."""
    if requested != "auto":
        return requested
    return _GPU_ENGINE if pick_compute_type(probe()) is not None else _CPU_ENGINE


class _LogicalProcessorInfo(ctypes.Structure):
    """SYSTEM_LOGICAL_PROCESSOR_INFORMATION; only ``relationship`` is read."""

    _fields_ = [
        ("mask", ctypes.c_size_t),
        ("relationship", ctypes.c_uint32),
        ("info", ctypes.c_ubyte * 16),
    ]


_RELATION_PROCESSOR_CORE = 0


def physical_cores() -> int:
    """Physical CPU cores (hyper-threads not counted). Half the logical count if unknown."""
    fallback = max(1, (os.cpu_count() or 2) // 2)
    if sys.platform != "win32":
        return fallback
    entry = ctypes.sizeof(_LogicalProcessorInfo)
    try:
        kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined,unused-ignore]
        size = ctypes.c_uint32(0)
        kernel32.GetLogicalProcessorInformation(None, ctypes.byref(size))  # sets the size
        if size.value < entry:
            return fallback
        buf = (_LogicalProcessorInfo * (size.value // entry))()
        if not kernel32.GetLogicalProcessorInformation(buf, ctypes.byref(size)):
            return fallback
        count = size.value // entry
        cores = sum(1 for item in buf[:count] if item.relationship == _RELATION_PROCESSOR_CORE)
    except (AttributeError, OSError):
        return fallback
    return cores or fallback


def speech_threads() -> int:
    """CPU threads for the speech model: one less than the physical cores (capped at
    ``MAX_SPEECH_THREADS``, at least 1). The spare core runs ffmpeg capture, the server and the
    display browser; Kmynas loses little going from 4 threads to 3."""
    return max(1, min(MAX_SPEECH_THREADS, physical_cores() - 1))


def hub_cache_dir(models_dir: Path) -> Path:
    """The Hugging Face hub cache inside ``models_dir`` (what ``HF_HOME/hub`` resolves to)."""
    return models_dir / "hub"


def cached_gguf_path(name: str, models_dir: Path) -> str | None:
    """Local path of a downloaded GGUF model, or None. Never touches the network."""
    from huggingface_hub import try_to_load_from_cache

    model = GGUF_MODELS[name]
    path = try_to_load_from_cache(
        model.repo_id, model.filename, cache_dir=str(hub_cache_dir(models_dir))
    )
    return path if isinstance(path, str) else None


def resolve_speech(
    requested: str,
    models_dir: Path,
    probe: SupportedProbe = cuda_supported_compute_types,
) -> SpeechChoice:
    """The engine to run, given the setting and the files present.

    A GGUF engine whose files are missing falls back so the service still runs: Kmynas + Canary
    loses Canary and runs Kmynas alone; anything else runs Whisper for Lithuanian until the
    download is done (a warning says so).
    """
    engine = resolve_speech_engine(requested, probe)
    needed = engine_models(engine)
    if not needed:
        return SpeechChoice("whisper")
    paths: dict[str, str] = {}
    for name in needed:
        path = cached_gguf_path(name, models_dir)
        if path is not None:
            paths[name] = path
    missing = [name for name in needed if name not in paths]
    if not missing:
        return SpeechChoice(engine, paths)
    warning = (
        f"Lithuanian speech model {', '.join(missing)} is not downloaded. Run the launcher "
        "with --download-model to download it."
    )
    if engine == _GPU_ENGINE and "kmynas" in paths:
        return SpeechChoice(
            "kmynas", {"kmynas": paths["kmynas"]}, [warning + " Using Kmynas alone."]
        )
    return SpeechChoice("whisper", {}, [warning + " Using Whisper for Lithuanian instead."])
