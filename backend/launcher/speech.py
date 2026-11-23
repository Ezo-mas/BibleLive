"""Speech model choice and pins: the GGUF models (Kmynas, Canary) and Whisper.

The engines themselves are in ``audio/stt_gguf.py`` and ``audio/stt.py``. This module decides
what the launcher passes to ``audio.cli`` for them, and finds their files in the Hugging Face
cache under ``models_dir`` (``HF_HOME``). It never downloads: that is
``commands.download_speech_command``, which checks every file against the SHA256 pinned here.
"""

import ctypes
import hashlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .gpu import SupportedProbe, cuda_supported_compute_types, pick_compute_type


@dataclass(frozen=True)
class GgufModel:
    repo_id: str  # Hugging Face repo that holds the file
    filename: str  # the .gguf file in that repo
    approx_mb: int  # for progress percentages only
    revision: str  # the pinned commit of the repo
    sha256: str  # of ``filename`` at ``revision``


@dataclass(frozen=True)
class WhisperModel:
    repo_id: str  # the id faster-whisper uses (the Hub cache folder is named after it)
    revision: str  # the pinned commit of the repo
    files: dict[str, str]  # file -> SHA256: what faster-whisper downloads from the repo


# The speech models the launcher downloads, each pinned to a commit and its files to a SHA256:
# the one place to change a download (and the installer / --download-model follow). Re-pin with
# scripts/pin-models.py. A new hash is a new model: benchmark it first (AGENTS.md).
GGUF_MODELS: dict[str, GgufModel] = {
    "kmynas": GgufModel(
        "Ezomas/kmynas-parakeet-lt-v4-gguf",
        "kmynas-v4-Q8_0.gguf",
        740,
        "b4c73adf13eae8ae563b30395fd2870e7c565a1a",
        "4c364ab27b37e2dff21c6c702fc99b0a99ecc420a28313a852014c93c95df726",
    ),
    "canary": GgufModel(
        "handy-computer/canary-1b-v2-gguf",
        "canary-1b-v2-Q8_0.gguf",
        1140,
        "692af661b140183b60e44f55ad03c59e1e77d078",
        "224f83d1bc487b3303b495a7d6874912fdece93de19d1a04b550829c30a5d289",
    ),
}

# The Whisper models the installer offers. Any other --model name is downloaded and loaded by
# name, unpinned, as faster-whisper does it. large-v3-turbo's repo now redirects to
# dropbox-dash/faster-whisper-large-v3-turbo; the old id keeps existing downloads valid.
WHISPER_MODELS: dict[str, WhisperModel] = {
    "small": WhisperModel(
        "Systran/faster-whisper-small",
        "536b0662742c02347bc0e980a01041f333bce120",
        {
            "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
            "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
            "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
            "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
        },
    ),
    "medium": WhisperModel(
        "Systran/faster-whisper-medium",
        "08e178d48790749d25932bbc082711ddcfdfbc4f",
        {
            "config.json": "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807",
            "model.bin": "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
            "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
            "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
        },
    ),
    "large-v3": WhisperModel(
        "Systran/faster-whisper-large-v3",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        {
            "config.json": "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
            "model.bin": "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
            "preprocessor_config.json": (
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"
            ),
            "tokenizer.json": "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
            "vocabulary.json": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
        },
    ),
    "large-v3-turbo": WhisperModel(
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        {
            "config.json": "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
            "model.bin": "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
            "preprocessor_config.json": (
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"
            ),
            "tokenizer.json": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
            "vocabulary.json": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
        },
    ),
}


def file_sha256(path: str | Path) -> str:
    """SHA256 of a file, read in 1 MiB blocks (a model file can be 3 GB)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


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
    fallback: bool = False


@dataclass(frozen=True)
class SpeechModelStatus:
    name: str
    state: Literal["ok", "old", "missing"]


@dataclass(frozen=True)
class SpeechCheck:
    wanted: str
    runs: str
    models: list[SpeechModelStatus]


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


def _cached_file(repo_id: str, filename: str, models_dir: Path, revision: str | None) -> str | None:
    from huggingface_hub import try_to_load_from_cache

    path = try_to_load_from_cache(
        repo_id, filename, cache_dir=str(hub_cache_dir(models_dir)), revision=revision
    )
    return path if isinstance(path, str) else None


def cached_gguf_path(name: str, models_dir: Path, *, pinned_only: bool = False) -> str | None:
    """Local path of a downloaded GGUF model, or None. Never touches the network.

    The pinned commit first. Unless ``pinned_only``, a file downloaded before the pin (the
    repo's ``main`` at that time) is used too, so an older install keeps running; see
    :func:`is_pinned_path`.
    """
    model = GGUF_MODELS[name]
    path = _cached_file(model.repo_id, model.filename, models_dir, model.revision)
    if path is None and not pinned_only:
        path = _cached_file(model.repo_id, model.filename, models_dir, None)
    return path


def is_pinned_path(name: str, path: str) -> bool:
    """True if ``path`` (from :func:`cached_gguf_path`) is the pinned commit's file."""
    return Path(path).parent.name == GGUF_MODELS[name].revision


def cached_whisper_dir(model: str, models_dir: Path) -> str | None:
    """The folder of a pinned Whisper model downloaded at its pinned commit, or None.

    None for a model without a pin, or one downloaded by name before the pin: the pipeline
    then loads it by name, as before. Never touches the network.
    """
    pin = WHISPER_MODELS.get(model)
    if pin is None:
        return None
    folder: Path | None = None
    for filename in pin.files:
        path = _cached_file(pin.repo_id, filename, models_dir, pin.revision)
        if path is None:
            return None
        folder = Path(path).parent
    return str(folder) if folder is not None else None


def whisper_model_arg(model: str, models_dir: Path) -> str:
    """What ``audio.cli --model`` gets: the pinned download's folder, else the model name.

    A folder loads exactly the pinned files and needs no network to start (faster-whisper
    resolves a name against the Hub, or its cached ``main``, every time it loads).
    """
    return cached_whisper_dir(model, models_dir) or model


def _check_speech_files(
    requested: str, models_dir: Path, probe: SupportedProbe
) -> tuple[SpeechCheck, dict[str, str]]:
    wanted = resolve_speech_engine(requested, probe)
    paths: dict[str, str] = {}
    models = []
    for name in engine_models(wanted):
        path = cached_gguf_path(name, models_dir)
        if path is None:
            models.append(SpeechModelStatus(name, "missing"))
        else:
            paths[name] = path
            models.append(SpeechModelStatus(name, "ok" if is_pinned_path(name, path) else "old"))
    runs = wanted
    if len(paths) != len(models):
        runs = "kmynas" if "kmynas" in paths else "whisper"
    return SpeechCheck(wanted, runs, models), paths


def speech_check(
    requested: str, models_dir: Path, probe: SupportedProbe = cuda_supported_compute_types
) -> SpeechCheck:
    """Report cached model status and the engine Start would use, without downloading."""
    report, _paths = _check_speech_files(requested, models_dir, probe)
    return report


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
    report, paths = _check_speech_files(requested, models_dir, probe)
    missing = [model.name for model in report.models if model.state == "missing"]
    unpinned = [model.name for model in report.models if model.state == "old"]
    pin_warnings = (
        [
            f"Lithuanian speech model {', '.join(unpinned)} is an older download, not the "
            "checked version. Run the launcher with --download-model to update it."
        ]
        if unpinned
        else []
    )
    if not missing:
        return SpeechChoice(report.runs, paths, pin_warnings)
    warning = (
        f"Lithuanian speech model {', '.join(missing)} is not downloaded. Run the launcher "
        "with --download-model to download it."
    )
    if report.runs == "kmynas":
        return SpeechChoice(
            report.runs,
            {"kmynas": paths["kmynas"]},
            [warning + " Using Kmynas alone.", *pin_warnings],
            fallback=True,
        )
    return SpeechChoice(
        report.runs, {}, [warning + " Using Whisper for Lithuanian instead."], fallback=True
    )
