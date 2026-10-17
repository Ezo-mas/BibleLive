"""Persistent launcher settings (JSON in %APPDATA%\\BibleLive by default)."""

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8000
DEFAULT_MODEL = "large-v3-turbo"

# Allowed values for enum-like settings (shared with cli.py and the GUI).
LANGUAGES = ("en", "lt")
MODELS = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v2",
    "large-v3",
    "large-v3-turbo",
    "distil-large-v3",
)
DEVICES = ("auto", "cuda", "cpu")
COMPUTE_TYPES = ("auto", "float16", "float32", "int8", "int8_float16")
AUTO_MODES = ("off", "high", "medium_and_up", "all")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
UI_LANGUAGES = ("auto", "en", "lt")
# Lithuanian speech model (--stt): "auto" = by graphics card, see launcher/speech.py.
SPEECH_ENGINES = ("auto", "whisper", "kmynas", "kmynas+canary")

_ALLOWED: dict[str, tuple[str, ...]] = {
    "language": LANGUAGES,
    "model": MODELS,
    "device": DEVICES,
    "compute_type": COMPUTE_TYPES,
    "auto_mode": AUTO_MODES,
    "log_level": LOG_LEVELS,
    "ui_language": UI_LANGUAGES,
    "speech_engine": SPEECH_ENGINES,
}


def app_data_dir() -> Path:
    """Per-user data directory: ``%APPDATA%\\BibleLive`` (``~/.biblelive`` elsewhere)."""
    appdata = os.environ.get("APPDATA")
    if sys.platform == "win32" and appdata:
        return Path(appdata) / "BibleLive"
    return Path.home() / ".biblelive"


def models_dir() -> Path:
    """Where speech models are cached (``HF_HOME``): ``%LOCALAPPDATA%\\BibleLive\\models``.

    Machine-local rather than roaming: the models are ~1.6 GB. ``~/.biblelive/models`` elsewhere.
    """
    local = os.environ.get("LOCALAPPDATA")
    if sys.platform == "win32" and local:
        return Path(local) / "BibleLive" / "models"
    return Path.home() / ".biblelive" / "models"


def default_settings_path() -> Path:
    return app_data_dir() / "launcher.json"


@dataclass
class LauncherSettings:
    language: str = "en"
    input_device: str | None = None
    model: str = DEFAULT_MODEL
    device: str = "auto"  # "auto" | "cuda" | "cpu"
    compute_type: str = "auto"  # "auto" | float16 | float32 | int8 | int8_float16
    port: int = DEFAULT_PORT
    auto_mode: str = "off"
    log_level: str = "INFO"
    ffmpeg_dir: str | None = None  # None = <app root>/ffmpeg
    extra_audio_args: list[str] = field(default_factory=list)
    ui_language: str = "auto"  # "auto" (Windows UI language) | "en" | "lt"
    projector_monitor: str | None = None  # monitor device name; None = first non-primary
    open_display_on_start: bool = True
    open_operator_on_start: bool = True
    speech_engine: str = (
        "auto"  # Lithuanian speech model; "auto" | whisper | kmynas | kmynas+canary
    )
    # Record each service (session log + WAV) under %APPDATA%\BibleLive\logs\sessions.
    session_audio: bool = True


def _coerce(value: Any, default: Any) -> Any:
    """Return ``value`` if it has the type of ``default`` (str or None when default is None)."""
    if default is None:
        return value if value is None or isinstance(value, str) else default
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if isinstance(default, list):
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        return list(value) if ok else list(default)
    return value if isinstance(value, type(default)) else default


def load_settings(path: Path | None = None) -> LauncherSettings:
    """Load settings; a missing, unreadable or corrupt file yields defaults."""
    path = path or default_settings_path()
    defaults = LauncherSettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return defaults
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable launcher settings %s: %s", path, exc)
        return defaults
    if not isinstance(raw, dict):
        logger.warning("Ignoring launcher settings %s: not a JSON object", path)
        return defaults
    values: dict[str, Any] = {}
    for f in fields(LauncherSettings):
        if f.name not in raw:
            continue
        default = getattr(defaults, f.name)
        value = _coerce(raw[f.name], default)
        allowed = _ALLOWED.get(f.name)
        if allowed is not None and value not in allowed:
            value = default
        if value == default and raw[f.name] != default:
            logger.warning("Launcher setting %r has an invalid value; using default", f.name)
        values[f.name] = value
    return LauncherSettings(**values)


def save_settings(settings: LauncherSettings, path: Path | None = None) -> Path:
    """Write settings atomically and return the path used."""
    path = path or default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
