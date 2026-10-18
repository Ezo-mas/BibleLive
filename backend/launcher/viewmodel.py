"""Testable GUI logic: settings <-> form values, option lists, status mapping.

Nothing here imports tkinter, so it runs (and is unit-tested) headless.
"""

import os
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from .i18n import t
from .monitors import Monitor
from .process import MSG_STARTING_AUDIO, MSG_STARTING_SERVER_PREFIX, build_plan
from .settings import (
    AUTO_MODES,
    COMPUTE_TYPES,
    DEVICES,
    LANGUAGES,
    LOG_LEVELS,
    MODELS,
    SPEECH_ENGINES,
    LauncherSettings,
)

LANGUAGE_NAMES = {"lt": "Lietuvių", "en": "English"}


class FormError(ValueError):
    """Invalid form input. ``code`` maps to an ``err.<code>`` translation."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass
class FormValues:
    language: str
    input_device: str | None
    projector_monitor: str | None
    auto_mode: str
    ui_language: str
    model: str
    device: str
    compute_type: str
    port: str
    log_level: str
    extra_args: str
    open_display: bool
    open_operator: bool
    speech_engine: str = "auto"


def form_from_settings(s: LauncherSettings) -> FormValues:
    return FormValues(
        language=s.language,
        input_device=s.input_device,
        projector_monitor=s.projector_monitor,
        auto_mode=s.auto_mode,
        ui_language=s.ui_language,
        model=s.model,
        device=s.device,
        compute_type=s.compute_type,
        port=str(s.port),
        log_level=s.log_level,
        extra_args=shlex.join(s.extra_audio_args),
        open_display=s.open_display_on_start,
        open_operator=s.open_operator_on_start,
        speech_engine=s.speech_engine,
    )


def settings_from_form(base: LauncherSettings, form: FormValues) -> LauncherSettings:
    """Settings with the form laid over ``base`` (keeps fields the form doesn't show)."""
    try:
        port = int(form.port.strip())
    except ValueError:
        raise FormError("invalid_port") from None
    if not 1 <= port <= 65535:
        raise FormError("invalid_port")
    try:
        extra = shlex.split(form.extra_args) if form.extra_args.strip() else []
    except ValueError as exc:
        raise FormError("invalid_args", str(exc)) from exc
    return replace(
        base,
        language=form.language,
        input_device=form.input_device or None,
        projector_monitor=form.projector_monitor or None,
        auto_mode=form.auto_mode,
        ui_language=form.ui_language,
        model=form.model,
        device=form.device,
        compute_type=form.compute_type,
        port=port,
        log_level=form.log_level,
        extra_audio_args=extra,
        open_display_on_start=form.open_display,
        open_operator_on_start=form.open_operator,
        speech_engine=form.speech_engine,
    )


def check_microphone(input_device: str | None, mic_names: Sequence[str]) -> None:
    """Refuse "automatic" when several microphones are connected.

    Automatic can only take the first DirectShow input (ffmpeg cannot see the
    Windows default), which on a live test was a silent built-in microphone.
    With one microphone, or the list not loaded yet, automatic stays allowed.
    """
    if input_device is None and len(mic_names) > 1:
        raise FormError("choose_microphone")


Option = tuple[str | None, str]  # (stored value, label shown)


def language_options() -> list[Option]:
    return [(code, LANGUAGE_NAMES[code]) for code in LANGUAGES]


def ui_language_options() -> list[Option]:
    return [("auto", t("ui.auto")), ("lt", LANGUAGE_NAMES["lt"]), ("en", LANGUAGE_NAMES["en"])]


def auto_mode_options() -> list[Option]:
    return [(m, t(f"auto.{m}")) for m in AUTO_MODES]


_SPEECH_LABEL_KEYS = {
    "auto": "speech.auto",
    "whisper": "speech.whisper",
    "kmynas": "speech.kmynas",
    "kmynas+canary": "speech.kmynas_canary",
}


def speech_engine_options() -> list[Option]:
    return [(v, t(_SPEECH_LABEL_KEYS[v])) for v in SPEECH_ENGINES]


def plain_options(values: Sequence[str]) -> list[Option]:
    return [(v, v) for v in values]


MODEL_OPTIONS = MODELS
DEVICE_OPTIONS = DEVICES
COMPUTE_OPTIONS = COMPUTE_TYPES
LOG_LEVEL_OPTIONS = LOG_LEVELS


def microphone_options(names: Sequence[str], saved: str | None) -> list[Option]:
    """Automatic first; a saved microphone that is currently unplugged stays selectable."""
    opts: list[Option] = [(None, t("mic.automatic"))]
    known = list(names)
    if saved and saved not in known:
        known.insert(0, saved)
    opts += [(n, n) for n in known]
    return opts


def projector_options(monitors: Sequence[Monitor]) -> list[Option]:
    """Automatic plus every non-primary monitor (the operator's own screen is excluded)."""
    opts: list[Option] = [(None, t("projector.automatic"))]
    opts += [(m.device, m.label) for m in monitors if not m.primary]
    return opts


def load_microphones(
    settings: LauncherSettings, lister: Callable[[], list[str]] | None = None
) -> list[str]:
    """Input device names, with a bundled ffmpeg made visible on PATH first.

    May block for a second or two: call from a worker thread. Raises RuntimeError
    if ffmpeg is missing (from ``audio.capture``).
    """
    if lister is None:
        os.environ["PATH"] = build_plan(settings).env["PATH"]
        from audio.capture import list_input_devices

        lister = list_input_devices
    return lister()


# --- status mapping -------------------------------------------------------------------


@dataclass(frozen=True)
class StatusState:
    server: str = "stopped"  # stopped | starting | ready | error
    audio: str = "stopped"  # stopped | starting | running | error


def apply_event(state: StatusState, kind: str, message: str, error_code: str | None) -> StatusState:
    if kind == "status":
        if message.startswith(MSG_STARTING_SERVER_PREFIX):
            return StatusState(server="starting", audio="stopped")
        if message == MSG_STARTING_AUDIO:
            return replace(state, audio="starting")
    elif kind == "ready":
        return replace(state, server="ready")
    elif kind == "running":
        return replace(state, audio="running")
    elif kind == "error":
        if error_code and error_code.startswith("audio"):
            return replace(state, audio="error")
        return StatusState(server="error", audio="stopped")
    elif kind == "stopped":
        return StatusState(
            server="error" if state.server == "error" else "stopped",
            audio="error" if state.audio == "error" else "stopped",
        )
    return state


def state_text(state: str) -> str:
    return t(f"state.{state}")


def describe_gpu(device: str, compute_type: str) -> str:
    key = "gpu.cuda" if device == "cuda" else "gpu.cpu"
    return t(key, device=device, compute=compute_type)


def describe_speech(engine: str, device: str, compute_type: str) -> str:
    """The "speech recognition" status line: Whisper keeps the GPU wording."""
    if engine == "whisper":
        return describe_gpu(device, compute_type)
    where = "speech.where_cpu" if device == "cpu" else "speech.where_gpu"
    name = "speech.short.kmynas_canary" if engine == "kmynas+canary" else "speech.short.kmynas"
    return t("speech.status", engine=t(name), where=t(where))


def describe_event(
    kind: str, message: str, error_code: str | None, error_detail: str
) -> tuple[str, str] | None:
    """Translate a Launcher event into (level, text) for the log pane (None = skip)."""
    if kind == "status":
        if message.startswith(MSG_STARTING_SERVER_PREFIX):
            return "info", t("log.starting_server")
        if message == MSG_STARTING_AUDIO:
            return "info", t("log.starting_audio")
        return "info", message
    if kind == "ready":
        return "info", t("log.server_ready", detail=message)
    if kind == "running":
        return "info", t("log.audio_running")
    if kind == "stopped":
        return "info", t("log.stopped")
    if kind == "error":
        return "error", describe_error(error_code, error_detail, message)
    if kind == "warning":
        return "warning", message
    return None


def describe_error(code: str | None, detail: str, fallback: str = "") -> str:
    """Friendly translated text for a launcher/projector/form error code."""
    if code in _KNOWN_ERRORS:
        return t(f"err.{code}", detail=detail)
    return t("err.unknown", detail=fallback or detail)


_KNOWN_ERRORS = {
    "port_in_use",
    "server_failed",
    "server_exited",
    "audio_exited",
    "server_timeout",
    "server_spawn",
    "audio_spawn",
    "projector_missing",
    "projector_failed",
    "invalid_port",
    "invalid_args",
    "choose_microphone",
}
