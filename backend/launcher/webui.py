"""pywebview launcher window and its thread-safe bridge."""

import base64
import contextlib
import ctypes
import faulthandler
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import i18n, platform
from .commands import apply_model_env
from .gpu import cuda_supported_compute_types
from .i18n import t
from .logtail import LogBuffer, LogTail
from .micmeter import MicMeter
from .monitors import Monitor, choose_projector, enumerate_monitors
from .network import (
    allow_through_firewall,
    ensure_network_token,
    firewall_applies,
    firewall_programs,
    firewall_rule_exists,
    lan_ip,
    operator_url_with_token,
    qr_png,
)
from .platform.macos import microphone_access
from .process import (
    Launcher,
    LaunchPlan,
    _default_app_root,
    bible_live_on_port,
    build_plan,
    port_in_use,
    python_child_env,
)
from .projector import ProjectorBrowser, ProjectorError, operator_url
from .screen_labels import identify_screens
from .settings import (
    COMPUTE_TYPES,
    DEVICES,
    LANGUAGES,
    LAUNCHER_VIEWS,
    MODELS,
    SPEECH_ENGINES,
    UI_LANGUAGES,
    LauncherSettings,
    app_data_dir,
    load_settings,
    models_dir,
    save_settings,
)
from .speech import speech_check
from .viewmodel import (
    LOG_LEVEL_OPTIONS,
    FormError,
    FormValues,
    NetworkView,
    Option,
    StatusState,
    apply_event,
    auto_mode_options,
    check_microphone,
    compute_options,
    describe_error,
    describe_event,
    describe_speech,
    device_options,
    firewall_view,
    form_from_settings,
    language_options,
    load_microphones,
    microphone_options,
    model_options,
    network_view,
    plain_options,
    projector_options,
    settings_from_form,
    speech_engine_options,
    ui_language_options,
    validate_port,
)

logger = logging.getLogger(__name__)
FALLBACK = 2
_MIN_WINDOW_SIZE = (760, 480)
_AUDIENCE_QR_TIMEOUT_S = 5.0
_SELF_TEST_TIMEOUT_S = 180.0
_ERROR_FIELDS = {
    "invalid_port": "port",
    "invalid_args": "extra_args",
    "choose_microphone": "input_device",
}


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)
    else:
        webbrowser.open(path.as_uri())


def _copy_text(text: str) -> None:
    if sys.platform == "win32":
        cmd, data = ["clip.exe"], text.encode("utf-16")
    elif sys.platform == "darwin":
        cmd, data = ["pbcopy"], text.encode()
    elif os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        cmd, data = ["wl-copy"], text.encode()
    else:
        cmd, data = ["xclip", "-selection", "clipboard"], text.encode()
    flags = platform.current().child_process_options().get("creationflags", 0)
    subprocess.run(cmd, input=data, check=True, creationflags=flags)


def _post_audience_qr(port: int, token: str, visible: bool) -> bool:
    """Ask this computer's server to show or hide the audience QR code; returns whether it shows.

    Only ever sent to 127.0.0.1, so the token in the header never leaves this computer.
    """
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/display/qr",
        data=json.dumps({"visible": visible}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    # No proxy: a system or HTTP_PROXY proxy would otherwise get this request, token included.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=_AUDIENCE_QR_TIMEOUT_S) as response:
        return json.load(response).get("visible") is True


def _png_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _options(options: list[Option]) -> list[dict[str, str | None]]:
    return [{"value": value, "label": label} for value, label in options]


class LauncherController:
    """Own the idle meter and the launcher session independently of the webview."""

    def __init__(
        self,
        *,
        load: Callable[[], LauncherSettings] = load_settings,
        save: Callable[[LauncherSettings], object] = save_settings,
        plan_builder: Callable[[LauncherSettings], LaunchPlan] = build_plan,
        launcher_factory: Callable[[LaunchPlan, Callable[[str, str], None]], Launcher] = Launcher,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        mic_lister: Callable[[LauncherSettings], list[str]] = load_microphones,
        monitor_lister: Callable[[], list[Monitor]] = enumerate_monitors,
        open_browser: Callable[[str], object] = webbrowser.open,
        open_folder: Callable[[Path], None] = _open_folder,
        get_lan_ip: Callable[[], str | None] = lan_ip,
        firewall_supported: Callable[[], bool] = firewall_applies,
        get_firewall_programs: Callable[[str], list[str]] = firewall_programs,
        firewall_exists: Callable[[], bool] = firewall_rule_exists,
        add_firewall: Callable[[list[str]], bool] = allow_through_firewall,
        clock: Callable[[], float] = time.time,
        access: Callable[..., str] = microphone_access,
        projector: ProjectorBrowser | None = None,
        copy: Callable[[str], None] = _copy_text,
        post_audience_qr: Callable[[int, str, bool], bool] = _post_audience_qr,
        self_test_popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        identify: Callable[[list[Monitor]], None] | None = None,
        port_owner: Callable[[int], platform.PortOwner | None] = platform.port_owner,
        port_in_use: Callable[[int], bool] = port_in_use,
        health_probe: Callable[[int], bool] = bible_live_on_port,
    ) -> None:
        self._lock = threading.RLock()
        self._save = save
        self._build_plan = plan_builder
        self._launcher_factory = launcher_factory
        self._mic_lister = mic_lister
        self._monitor_lister = monitor_lister
        self._open_browser = open_browser
        self._open_folder = open_folder
        self._lan_ip = get_lan_ip
        self._firewall_supported = firewall_supported
        self._firewall_programs = get_firewall_programs
        self._firewall_exists = firewall_exists
        self._add_firewall = add_firewall
        self._clock = clock
        self._access = access
        self._copy = copy
        self._post_audience_qr = post_audience_qr
        self._self_test_popen = self_test_popen
        self._identify = identify
        self._identifying = False
        self._port_owner = port_owner
        self._port_in_use = port_in_use
        self._health_probe = health_probe
        self._fit_window: Callable[[float, float], None] | None = None
        self._projector = (
            projector if projector is not None else ProjectorBrowser(open_default=open_browser)
        )
        self._meter = MicMeter(popen=popen, access=access)
        self._log = LogBuffer(clock)

        self._settings = ensure_network_token(load())
        i18n.set_language(i18n.resolve_ui_language(self._settings.ui_language))
        self._selected_mic = self._settings.input_device
        self._monitors: list[Monitor] = []
        self._mic_names: list[str] = []
        self._refreshing = False
        self._options_changed = False
        self._initialized = False
        self._closed = False
        self._session = "idle"
        self._status = StatusState()
        self._started_at: int | None = None
        self._error: str | None = None
        self._error_port: int | None = None  # set when the error is a busy port
        self._no_gpu_logged = False
        self._network: NetworkView | None = None
        # The operator page's QR code (token included) for Advanced > Network, as a data URI.
        self._operator_qr: str | None = None
        # The audience QR code on the displays: what the server last said, and a request
        # in flight (the button waits for it rather than guessing).
        self._audience_qr = False
        self._audience_qr_busy = False
        self._firewall: str | None = None
        self._probe_plan: LaunchPlan | None = None
        self._plan: LaunchPlan | None = None
        self._launcher: Launcher | None = None
        self._worker: threading.Thread | None = None
        self._self_test_status = "idle"
        self._self_test_text: str | None = None
        self._self_test_process: subprocess.Popen[bytes] | None = None
        self._self_test_worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._tail: LogTail | None = None

    def _device_options(self) -> dict[str, Any]:
        return {
            "microphone": _options(microphone_options(self._mic_names, self._selected_mic)),
            "projector": _options(projector_options(self._monitors)),
        }

    def _options(self) -> dict[str, Any]:
        return {
            **self._device_options(),
            "language": _options(language_options()),
            "speech_engine": _options(speech_engine_options()),
            "model": _options(model_options()),
            "device": _options(device_options()),
            "compute_type": _options(compute_options()),
            "auto_mode": _options(auto_mode_options()),
            "ui_language": _options(ui_language_options()),
            "log_level": _options(plain_options(LOG_LEVEL_OPTIONS)),
        }

    def _state(self) -> dict[str, Any]:
        plan = self._plan or self._probe_plan
        network = None
        if self._network is not None:
            network = asdict(self._network)
            row = firewall_view(self._firewall)
            network["firewall"] = (
                {"message": row.message, "button_enabled": row.button_enabled}
                if row.shown
                else None
            )
            network["operator_qr"] = self._operator_qr
            network["audience_qr"] = self._audience_qr
            network["audience_qr_busy"] = self._audience_qr_busy
        return {
            "session": self._session,
            "server": self._status.server,
            "audio": self._status.audio,
            "speech": describe_speech(plan.speech_engine, plan.device, plan.compute_type)
            if plan
            else None,
            "speech_warning": bool(
                plan
                and (
                    plan.speech_fallback
                    or (plan.speech_engine == "whisper" and plan.device == "cpu")
                )
            ),
            "speech_fallback": bool(plan and plan.speech_fallback),
            "self_test": {"status": self._self_test_status, "text": self._self_test_text},
            "started_at": self._started_at,
            "has_monitors": bool(self._monitors),
            "network": network,
            "error": {"text": self._error, "port": self._error_port}
            if self._error is not None
            else None,
        }

    def init(self) -> dict[str, Any]:
        with self._lock:
            if not self._initialized and not self._closed:
                self._initialized = True
                self.refresh_devices()
                threading.Thread(
                    target=self._probe_speech, args=(self._settings,), daemon=True
                ).start()
            return {
                "platform": sys.platform,
                "language": i18n.get_language(),
                "view": self._settings.launcher_view,
                "form": asdict(
                    form_from_settings(replace(self._settings, input_device=self._selected_mic))
                ),
                "options": self._options(),
                "state": self._state(),
            }

    def poll(self) -> dict[str, Any]:
        with self._lock:
            if self._tail is not None:
                self._tail.read()
            options = self._device_options() if self._options_changed else None
            self._options_changed = False
            meter, error, levels = self._meter.snapshot()
            if self._session != "idle":
                meter, error, levels = "off", None, []
            return {
                "state": self._state(),
                "levels": levels,
                "meter": meter,
                "meter_error": error,
                "log": self._log.drain(),
                "options": options,
            }

    def refresh_devices(self) -> None:
        with self._lock:
            if self._closed or self._refreshing:
                return
            self._refreshing = True
            self._log.append("launcher", "INFO", t("mic.loading"))
            settings = replace(self._settings, input_device=self._selected_mic)
            threading.Thread(target=self._refresh_devices, args=(settings,), daemon=True).start()

    def _refresh_devices(self, settings: LauncherSettings) -> None:
        error = None
        try:
            names = self._mic_lister(settings)
        except Exception as exc:
            names, error = [], str(exc)
        try:
            monitors = self._monitor_lister()
        except Exception:
            logger.exception("Monitor enumeration failed")
            monitors = []
        with self._lock:
            self._refreshing = False
            if not self._closed:
                self._mic_names = names
                self._monitors = monitors
                self._options_changed = True
                if error:
                    self._log.append("launcher", "WARNING", error)
        self._start_meter()

    def _probe_speech(self, settings: LauncherSettings) -> None:
        try:
            plan = self._build_plan(settings)
        except Exception:
            logger.exception("Speech probe failed")
            return
        with self._lock:
            if not self._closed:
                self._probe_plan = plan
        self._start_meter()

    def _start_meter(self, *, restart: bool = False) -> None:
        with self._lock:
            plan = self._plan or self._probe_plan
            device = self._selected_mic
        if plan is None:
            return

        def can_start() -> bool:
            with self._lock:
                return (
                    self._initialized
                    and not self._closed
                    and self._session == "idle"
                    and device == self._selected_mic
                    and plan is (self._plan or self._probe_plan)
                )

        # Meter operations can join a reader that is still checking the device.
        self._meter.start(device, env=plan.env, can_start=can_start, restart=restart)

    def set_microphone(self, device: str | None) -> None:
        with self._lock:
            if self._closed or self._session != "idle":
                return
            self._selected_mic = device
        self._start_meter(restart=True)

    def _persist(self, settings: LauncherSettings) -> None:
        self._settings = settings
        with contextlib.suppress(OSError):
            self._save(settings)

    def set_view(self, view: str) -> None:
        with self._lock:
            if view in LAUNCHER_VIEWS and not self._closed:
                self._persist(replace(self._settings, launcher_view=view))

    @property
    def view(self) -> str:
        return self._settings.launcher_view

    def attach_window(self, fit: Callable[[float, float], None]) -> None:
        self._fit_window = fit

    def fit_window(self, content_height: float, viewport_height: float) -> None:
        if (
            self._fit_window is not None
            and not self._closed
            and math.isfinite(content_height)
            and math.isfinite(viewport_height)
            and content_height > 0
            and viewport_height > 0
        ):
            self._fit_window(content_height, viewport_height)

    def set_ui_language(self, code: str) -> dict[str, Any]:
        with self._lock:
            if code in UI_LANGUAGES and not self._closed:
                self._persist(replace(self._settings, ui_language=code))
                i18n.set_language(i18n.resolve_ui_language(code))
            return {"language": i18n.get_language(), "options": self._options()}

    def _start_error(self, code: str, text: str) -> dict[str, Any]:
        self._log.append("launcher", "ERROR", text)
        return {
            "ok": False,
            "error": {"code": code, "text": text, "field": _ERROR_FIELDS.get(code)},
        }

    def check_port(self, port: str) -> dict[str, Any]:
        free = {"free": True, "program": None, "pid": None, "bibleLive": False}
        with self._lock:
            if self._closed or self._session != "idle":
                return free
        try:
            if not isinstance(port, str):
                raise FormError("invalid_port")
            number = validate_port(port)
        except FormError as exc:
            return {"error": exc.code}
        if not self._port_in_use(number):
            return free
        owner = self._port_owner(number)
        bible_live = self._health_probe(number)
        with self._lock:
            if self._closed or self._session != "idle":
                return free
        return {
            "free": False,
            "program": owner.program if owner else None,
            "pid": owner.pid if owner else None,
            "bibleLive": bible_live,
        }

    def start(self, form: FormValues) -> dict[str, Any]:
        with self._lock:
            if self._closed or self._session != "idle":
                return self._start_error("session_busy", t("err.session_busy"))
            if self._self_test_status == "running":
                return self._start_error("self_test_busy", t("err.self_test_busy"))
            self._error = None
            self._error_port = None
            self._no_gpu_logged = False
            try:
                settings = settings_from_form(self._settings, form, include_session_audio=True)
                check_microphone(settings.input_device, self._mic_names)
            except FormError as exc:
                return self._start_error(exc.code, describe_error(exc.code, exc.detail))
            settings = ensure_network_token(settings)
            self._persist(settings)
            i18n.set_language(i18n.resolve_ui_language(settings.ui_language))
            self._selected_mic = settings.input_device
            self._session = "starting"
            self._status = StatusState(server="starting")
            self._started_at = None
            self._cancel.clear()
            self._worker = threading.Thread(target=self._run, args=(settings,), daemon=True)
            self._worker.start()
            return {"ok": True}

    def check_speech(self, engine: str, device: str) -> dict[str, Any]:  # noqa: ARG002
        # build_plan looks at hardware for Auto even with an explicitly selected CPU device.
        requested = engine if engine in SPEECH_ENGINES else "auto"
        return asdict(speech_check(requested, models_dir(), cuda_supported_compute_types))

    def run_self_test(self, form: FormValues) -> dict[str, Any]:
        with self._lock:
            if self._closed or self._session != "idle":
                return self._start_error("session_busy", t("err.session_busy"))
            if self._self_test_status == "running":
                return self._start_error("self_test_busy", t("err.self_test_busy"))
            try:
                settings = settings_from_form(self._settings, form)
            except FormError as exc:
                return self._start_error(exc.code, describe_error(exc.code, exc.detail))
            if (
                settings.speech_engine not in SPEECH_ENGINES
                or settings.device not in DEVICES
                or settings.compute_type not in COMPUTE_TYPES
                or settings.model not in MODELS
                or settings.language not in LANGUAGES
            ):
                return self._start_error("invalid_speech", t("err.invalid_speech"))

            self._self_test_status, self._self_test_text = "running", None
            self._self_test_worker = threading.Thread(
                target=self._run_self_test, args=(settings,), daemon=True
            )
            self._self_test_worker.start()
            return {"ok": True}

    def _run_self_test(self, settings: LauncherSettings) -> None:
        app_root = _default_app_root()
        env = python_child_env(app_root, dict(os.environ))
        apply_model_env(models_dir(), env)
        env.setdefault("HF_HUB_VERBOSITY", "error")
        cmd = [
            sys.executable,
            "-m",
            "launcher",
            "--gpu-self-test",
            "--speech-engine",
            settings.speech_engine,
            "--device",
            settings.device,
            "--compute-type",
            settings.compute_type,
            "--model",
            settings.model,
            "--language",
            settings.language,
        ]
        timed_out = threading.Event()
        result = None
        failure = None
        status = "fail"
        try:
            with self._lock:
                if self._closed:
                    return
                process = self._self_test_popen(
                    cmd,
                    cwd=str(app_root),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    **platform.current().child_process_options(),
                )
                self._self_test_process = process

            def timeout() -> None:
                if process.poll() is None:
                    timed_out.set()
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()

            timer = threading.Timer(_SELF_TEST_TIMEOUT_S, timeout)
            timer.daemon = True
            timer.start()
            try:
                with process:
                    assert process.stdout is not None
                    for raw in process.stdout:
                        line = raw.decode("utf-8", errors="replace").rstrip()
                        if line.startswith(("SELFTEST OK:", "SELFTEST FAIL:")):
                            result = line
                        level = "ERROR" if line.startswith("SELFTEST FAIL:") else "INFO"
                        with self._lock:
                            self._log.append("launcher", level, line)
                    exit_code = process.wait()
            finally:
                timer.cancel()
                timer.join()

            if timed_out.is_set():
                failure = t("err.self_test_timeout")
            elif exit_code == 0 and result and result.startswith("SELFTEST OK:"):
                status = "ok"
            elif not result or not result.startswith("SELFTEST FAIL:"):
                failure = t("err.self_test_exit", detail=exit_code)
        except Exception as exc:  # OSError from Popen, or anything else: say why, never hang
            logger.exception("Speech self-test failed to run")
            failure = t("err.self_test_exit", detail=exc)
        finally:
            with self._lock:
                if failure is not None:
                    self._log.append("launcher", "ERROR", failure)
                self._self_test_process = None
                self._self_test_status, self._self_test_text = status, failure or result

    def _run(self, settings: LauncherSettings) -> None:
        try:
            self._meter.stop()
            if self._cancel.is_set():
                return
            if sys.platform == "darwin":
                self._ask_microphone()
            if self._cancel.is_set():
                return
            plan = self._build_plan(settings)
            with self._lock:
                if self._cancel.is_set():
                    return
                launcher = self._launcher_factory(
                    plan, lambda k, m: self._launcher_event(launcher, k, m)
                )
                self._plan, self._launcher = plan, launcher
                self._tail = LogTail(plan.log_dir, self._log)
                self._log.append("launcher", "INFO", t("log.logs_in", detail=plan.log_dir))
            launcher.run()
        except Exception as exc:
            logger.exception("Launcher crashed")
            self._on_event("error", str(exc), unexpected=True)
        finally:
            with self._lock:
                self._launcher = None
                self._status = apply_event(self._status, "stopped", "", None)
                self._clear_network()
                self._close_session_resources()
                self._session = "idle"
                self._started_at = None
            self._start_meter()

    def _ask_microphone(self) -> None:
        status = self._access(ask=False)
        if status == "not_determined" and not self._cancel.is_set():
            with self._lock:
                self._log.append("launcher", "INFO", t("log.mic_asking"))
            status = self._access(cancelled=self._cancel.is_set)
        with self._lock:
            if self._cancel.is_set():
                return
            if status == "not_determined":
                self._log.append("launcher", "INFO", t("log.mic_no_answer"))
            elif status in ("denied", "restricted"):
                self._log.append("launcher", "WARNING", t(f"warn.mic_{status}"))

    def _launcher_event(self, launcher: Launcher, kind: str, message: str) -> None:
        with self._lock:
            if launcher is self._launcher:
                self._on_event(kind, message)

    def _on_event(self, kind: str, message: str, *, unexpected: bool = False) -> None:
        with self._lock:
            launcher, plan = self._launcher, self._plan
            code = launcher.error_code if kind == "error" and launcher and not unexpected else None
            detail = launcher.error_detail if code and launcher else ""
            self._status = apply_event(self._status, kind, message, code)
            if (
                kind == "warning"
                and plan
                and plan.device == "cpu"
                and plan.speech_engine == "whisper"
            ):
                # The plan can carry several device warnings; they all mean this one.
                if not self._no_gpu_logged:
                    self._no_gpu_logged = True
                    self._log.append("launcher", "WARNING", t("warn.no_gpu"))
            elif described := describe_event(kind, message, code, detail):
                level, text = described
                self._log.append("launcher", level.upper(), text)
                if kind == "error":
                    self._error = text
                    self._error_port = (
                        int(detail) if code == "port_in_use" and detail.isdigit() else None
                    )
            if kind == "ready" and self._session == "starting" and plan is not None:
                self._session = "live"
                self._started_at = int(self._clock() * 1000)
                self._on_server_ready(plan)

    def _on_server_ready(self, plan: LaunchPlan) -> None:
        if plan.network_token:
            ip = self._lan_ip()
            self._network = view = network_view(plan.port, plan.network_token, ip)
            if view.display_url:
                self._operator_qr = _png_data_uri(
                    qr_png(operator_url_with_token(plan.port, plan.network_token, ip or ""))
                )
                self._log.append(
                    "launcher",
                    "INFO",
                    t("net.log_addresses", display=view.display_url, operator=view.operator_url),
                )
            else:
                self._log.append("launcher", "WARNING", t("net.no_address"))
            if self._firewall_supported() and self._firewall_programs(sys.executable):
                threading.Thread(target=self._check_firewall, args=(view,), daemon=True).start()
        if self._settings.open_operator_on_start:
            self.open_operator()
        if self._settings.open_display_on_start:
            self.show_projector()

    def _check_firewall(self, view: NetworkView) -> None:
        exists = self._firewall_exists()
        with self._lock:
            if self._network is view and self._session == "live":
                self._firewall = "present" if exists else "missing"

    def allow_firewall(self) -> None:
        with self._lock:
            view = self._network
            if (
                view is None
                or self._session != "live"
                or not firewall_view(self._firewall).button_enabled
            ):
                return
            programs = self._firewall_programs(sys.executable)
            if programs:
                self._firewall = "adding"
                threading.Thread(
                    target=self._allow_firewall, args=(view, programs), daemon=True
                ).start()

    def _allow_firewall(self, view: NetworkView, programs: list[str]) -> None:
        ok = self._add_firewall(programs)
        with self._lock:
            if self._network is view and self._session == "live":
                self._firewall = "present" if ok else "not_added"
                self._log.append(
                    "launcher", "INFO" if ok else "WARNING", t("fw.added" if ok else "fw.not_added")
                )

    def _clear_network(self) -> None:
        self._network = None
        self._operator_qr = None
        self._audience_qr = False
        self._audience_qr_busy = False
        self._firewall = None

    def set_audience_qr(self, visible: bool) -> None:
        """Show or hide the audience QR code on the displays (the server draws it)."""
        with self._lock:
            view, plan = self._network, self._plan
            if (
                view is None
                or not view.display_url
                or plan is None
                or not plan.network_token
                or self._session != "live"
                or self._audience_qr_busy
            ):
                return
            self._audience_qr_busy = True
            threading.Thread(
                target=self._send_audience_qr,
                args=(view, plan.port, plan.network_token, visible),
                daemon=True,
            ).start()

    def _send_audience_qr(self, view: NetworkView, port: int, token: str, visible: bool) -> None:
        shown: bool | None
        try:
            shown = self._post_audience_qr(port, token, visible)
        except (OSError, ValueError) as exc:  # URLError and HTTPError are OSErrors
            logger.warning("Audience QR request failed: %s", exc)
            shown = None
        with self._lock:
            if self._network is not view or self._session != "live":
                return  # the session ended meanwhile; its server is gone
            self._audience_qr_busy = False
            if shown is None:
                self._log.append("launcher", "WARNING", t("net.audience_qr_failed"))
                return
            self._audience_qr = shown
            if visible and not shown:
                self._log.append("launcher", "WARNING", t("net.audience_qr_no_address"))

    def _port(self) -> int:
        return self._plan.port if self._plan else self._settings.port

    def open_operator(self) -> None:
        with self._lock:
            if self._closed or self._session != "live":
                return
            token = self._plan.network_token if self._plan else None
            url = (
                operator_url_with_token(self._port(), token)
                if token
                else operator_url(self._port())
            )
            self._open_browser(url)

    def identify_screens(self) -> None:
        with self._lock:
            if self._closed or self._identifying or not self._monitors or self._identify is None:
                return
            self._identifying = True
            monitors = self._monitors.copy()
        try:
            self._identify(monitors)
        finally:
            with self._lock:
                self._identifying = False

    def show_projector(self) -> None:
        with self._lock:
            if self._closed or self._session != "live":
                return
            self._monitors = self._monitor_lister()
            monitor = choose_projector(self._monitors, self._settings.projector_monitor)
            try:
                result = self._projector.open(self._port(), monitor)
            except ProjectorError as exc:
                key = "projector_missing" if exc.code == "no_monitor" else "projector_failed"
                self._log.append("launcher", "ERROR", describe_error(key, exc.detail))
            else:
                if result == "browser_fallback":
                    self._log.append("launcher", "WARNING", t("warn.browser_fallback"))
                elif result == "already_open":
                    self._log.append("launcher", "INFO", t("log.projector_already_open"))
                elif monitor is not None:
                    self._log.append(
                        "launcher", "INFO", t("log.projector_opened", detail=monitor.label)
                    )

    def open_logs(self) -> None:
        with self._lock:
            directory = (
                self._plan.log_dir if self._plan else platform.current().log_dir(app_data_dir())
            )
        self._open_folder(directory)

    def copy_text(self, text: str) -> None:
        try:
            self._copy(text)
        except (OSError, subprocess.SubprocessError):
            logger.warning("Could not copy text to the clipboard", exc_info=True)

    def stop(self) -> None:
        with self._lock:
            if self._session in ("idle", "stopping"):
                return
            self._log.append("launcher", "INFO", t("log.stopping"))
            self._session = "stopping"
            self._cancel.set()
            self._clear_network()
            launcher = self._launcher
            self._projector.close()
        if launcher is not None:
            launcher.stop()

    def _close_session_resources(self) -> None:
        self._projector.close()
        if self._tail is not None:
            self._tail.stop()
            self._tail = None

    def request_close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            process = self._self_test_process
            if process is not None and process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()

    def close(self) -> None:
        self.request_close()
        with self._lock:
            worker, launcher, self_test = self._worker, self._launcher, self._self_test_worker
        if launcher is not None:
            launcher.stop()
        if worker is not None:
            worker.join()
        if self_test is not None:
            self_test.join()
        self._meter.stop()
        with self._lock:
            self._close_session_resources()


class LauncherApi:
    """Only contract methods are public, since pywebview inspects this object."""

    def __init__(self, controller: LauncherController) -> None:
        self._controller = controller

    def init(self) -> dict[str, Any]:
        return self._controller.init()

    def poll(self) -> dict[str, Any]:
        return self._controller.poll()

    def start(self, form: dict[str, Any]) -> dict[str, Any]:
        return self._controller.start(FormValues(**form))

    def check_speech(self, engine: str, device: str) -> dict[str, Any]:
        return self._controller.check_speech(engine, device)

    def run_self_test(self, form: dict[str, Any]) -> dict[str, Any]:
        return self._controller.run_self_test(FormValues(**form))

    def check_port(self, port: str) -> dict[str, Any]:
        return self._controller.check_port(port)

    def stop(self) -> None:
        self._controller.stop()

    def refresh_devices(self) -> None:
        self._controller.refresh_devices()

    def set_microphone(self, device: str | None) -> None:
        self._controller.set_microphone(device)

    def open_operator(self) -> None:
        self._controller.open_operator()

    def show_projector(self) -> None:
        self._controller.show_projector()

    def identify_screens(self) -> None:
        self._controller.identify_screens()

    def open_logs(self) -> None:
        self._controller.open_logs()

    def allow_firewall(self) -> None:
        self._controller.allow_firewall()

    def set_audience_qr(self, visible: bool) -> None:
        self._controller.set_audience_qr(visible is True)

    def set_ui_language(self, code: str) -> dict[str, Any]:
        return self._controller.set_ui_language(code)

    def set_view(self, view: str) -> None:
        self._controller.set_view(view)

    def fit_window(self, content_height: float, viewport_height: float) -> None:
        self._controller.fit_window(content_height, viewport_height)

    def copy_text(self, text: str) -> None:
        self._controller.copy_text(text)


def _is_loopback_url(url: str) -> bool:
    if re.search(r"[\x00-\x20\x7f\\]", url):
        return False
    try:
        parsed = urlsplit(url)
        _ = parsed.port  # Validate the port if one was supplied.
        return (
            parsed.scheme in {"http", "https"}
            and re.fullmatch(
                r"(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]+)?",
                parsed.netloc,
                re.IGNORECASE | re.ASCII,
            )
            is not None
        )
    except ValueError:
        return False


def _fit_window(window: Any, content: float, viewport: float, max_height: int | None) -> None:
    native = window.native
    if native is None or window.maximized or window.fullscreen:
        return
    if sys.platform == "win32":
        expanded = native.is_fullscreen or str(native.WindowState) == "Maximized"
    elif sys.platform == "darwin":
        expanded = native.isZoomed() or native.styleMask() & (
            1 << 14
        )  # NSWindowStyleMaskFullScreen
    elif hasattr(native, "isMaximized"):  # Qt
        expanded = native.isMaximized() or native.isFullScreen()
    else:  # GTK
        state = native.get_window().get_state()
        expanded = state & (state.MAXIMIZED | state.FULLSCREEN)
    if expanded:
        return
    current_height = window.height
    height = round(current_height + (content - viewport))
    if max_height is not None:
        height = min(height, max_height)
    height = max(height, _MIN_WINDOW_SIZE[1])
    if height != current_height:
        window.resize(window.width, height)


def _colorref(color: str) -> int:
    red, green, blue = bytes.fromhex(color.removeprefix("#"))
    return red | (green << 8) | (blue << 16)


def _solid_title_bar(window: Any) -> None:
    if sys.platform != "win32":
        return
    try:
        from webview.platforms.winforms import DwmSetWindowAttribute

        form = window.native
        update_theme = form.update_title_bar_theme

        def apply_caption() -> None:
            try:
                hwnd = form.Handle.ToInt32()
                # Match the header's --surface in launcher.css.
                color = _colorref("#1c1b19" if form.is_dark_theme() else "#faf8f4")
                backdrop = DwmSetWindowAttribute(hwnd, 38, 1)  # DWMSBT_NONE
                caption = DwmSetWindowAttribute(hwnd, 35, color)  # DWMWA_CAPTION_COLOR
                if backdrop < 0 or caption < 0:
                    logger.debug("Launcher caption attributes rejected: %s, %s", backdrop, caption)
            except Exception:
                logger.debug("Could not set launcher caption attributes", exc_info=True)

        def update_solid_theme() -> None:
            with contextlib.suppress(Exception):
                update_theme()
            apply_caption()

        form.update_title_bar_theme = update_solid_theme
        apply_caption()
    except Exception:
        logger.debug("Could not install launcher caption theme", exc_info=True)


def window_icon(app_root: Path | None = None) -> Path | None:
    """The BibleLive icon: beside the app in a build, in the installer sources in a checkout.

    Without it the window and its taskbar button show python.exe's icon.
    """
    root = app_root or _default_app_root()
    for icon in (
        root / "biblelive.ico",
        root / "scripts" / "windows" / "installer" / "biblelive.ico",
    ):
        if icon.is_file():
            return icon
    return None


def _own_taskbar_button() -> None:
    """Windows groups a window under its process's executable (python.exe) on the taskbar,
    with Python's icon, unless the process names its own app."""
    if sys.platform == "win32":
        with contextlib.suppress(AttributeError, OSError):
            shell32 = ctypes.windll.shell32  # type: ignore[attr-defined,unused-ignore]
            shell32.SetCurrentProcessExplicitAppUserModelID("BibleLive.Launcher")


def webview_profiles_root() -> Path:
    """Where the launcher window's browser profiles live: local (not roaming) app data."""
    return models_dir().parent / "webview"


_PROFILE_LOCK = "in-use.lock"


def claim_webview_profile(root: Path | None = None) -> tuple[Path, Any]:
    """A fresh browser profile for this launch, after removing the ones no launch is using.

    One profile per launch: a shared folder stays locked by WebView2 while another launcher, or
    a browser process orphaned by a crash, still has it open, and WebView2 then cannot start.
    pywebview's private mode instead makes a temp profile it often fails to delete on exit.
    Each launch keeps a lock file open in its profile; the next start removes every profile
    whose lock file can be deleted (Windows refuses to delete an open file), so the folders
    clean themselves up. Returns the profile and the open lock file (keep it open until exit).
    """
    root = root or webview_profiles_root()
    root.mkdir(parents=True, exist_ok=True)
    for old in root.iterdir():
        if not old.is_dir():
            continue
        try:
            (old / _PROFILE_LOCK).unlink(missing_ok=True)
        except OSError:
            continue  # another launcher has it open
        shutil.rmtree(
            old, ignore_errors=True
        )  # files an orphaned browser holds stay till next time
    profile = root / f"p{os.getpid()}-{time.time_ns()}"
    profile.mkdir()
    lock = open(profile / _PROFILE_LOCK, "w")  # noqa: SIM115 (held for the window's lifetime)
    return profile, lock


def _daemon_bridge_threads(webview: Any) -> None:
    """Run pywebview's page-call threads as daemons, so a closed window cannot block exit.

    pywebview answers each page call to the launcher API on a new thread, which hands the result
    back with ``evaluate_js``. On macOS that waits for the Cocoa run loop; when the window closes
    while a call is in flight, the loop is gone, the wait never ends, and Python's exit waits
    on the (non-daemon) thread forever. Seen as a hung smoke test on CI (2026-11-21). The
    result is for a page that no longer exists, so nothing is lost by not waiting.
    """
    util = getattr(webview, "util", None)
    if util is None:
        return

    class _DaemonThread(threading.Thread):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("daemon", True)
            super().__init__(*args, **kwargs)

    setattr(util, "Thread", _DaemonThread)  # noqa: B010 (a module attribute, by name)


def _on_closing(window: Any, controller: LauncherController) -> bool | None:
    """The window is closing: from its close button, or (macOS) Cmd-Q / Dock > Quit.

    On macOS, Quit runs Cocoa's terminate:, which ends the process before webview.start()
    returns, so main()'s cleanup would never stop the server, the pipeline and their ffmpeg
    (each in its own session; they kept running after Cmd-Q on the M1, 2026-11-21). So the
    close is cancelled there and the window closed instead: closing the last window ends
    webview.start() the way the close button does, and the cleanup runs. Returning False
    cancels; NSWindow.close does not ask again.
    """
    controller.request_close()
    if sys.platform == "darwin":
        window.destroy()
        return False
    return None


_SMOKE_TIMEOUT_S = 60
_SERVE_FROM_FILE = sys.platform == "darwin"


def main(*, smoke: bool = False) -> int:
    logging.basicConfig(level=logging.WARNING)
    if smoke:
        # A launcher that hangs (starting up, never loading its page, or shutting down) would
        # hang a build's smoke test: print every thread's stack and exit instead. Left armed
        # until the process exits.
        faulthandler.dump_traceback_later(_SMOKE_TIMEOUT_S, exit=True)
    url = os.environ.get("BIBLELIVE_LAUNCHER_URL")
    if url and not _is_loopback_url(url):
        logger.warning("Ignoring BIBLELIVE_LAUNCHER_URL: expected a loopback HTTP(S) URL")
        url = None
    if not url:
        page = _default_app_root() / "static" / "launcher" / "launcher.html"
        if not page.is_file():
            logger.warning("Launcher page missing at %s", page)
            return FALLBACK
        # macOS: a file URL, so pywebview starts no local web server. The Mac application
        # firewall asks to "accept incoming connections" for any listening socket of an app
        # without a Developer ID signature, even one on 127.0.0.1. WebView2 (Windows) does not
        # run module scripts from file URLs, so there the page comes from pywebview's server.
        url = page.as_uri() if _SERVE_FROM_FILE else str(page)

    controller = None
    lock = None
    try:
        import webview  # type: ignore[import-untyped,unused-ignore]

        _daemon_bridge_threads(webview)
        profile, lock = claim_webview_profile()

        if sys.platform == "win32":
            from .gui import _enable_dpi_awareness

            _enable_dpi_awareness()
        _own_taskbar_button()
        screens = webview.screens
        # The primary desktop screen contains the coordinate origin.
        primary = next(
            (s for s in screens if s.x == 0 and s.y == 0), screens[0] if screens else None
        )
        max_height = primary.height - 60 if primary is not None else None
        controller = LauncherController(
            identify=lambda monitors: identify_screens(webview, monitors, primary)
        )
        height = 600 if controller.view == "simple" else 800
        if max_height is not None:
            height = max(_MIN_WINDOW_SIZE[1], min(height, max_height))
        window = webview.create_window(
            t("app.title"),
            url,
            js_api=LauncherApi(controller),
            width=1120,
            height=height,
            min_size=_MIN_WINDOW_SIZE,
        )
        if window is None:
            return FALLBACK
        controller.attach_window(
            lambda content, viewport: _fit_window(window, content, viewport, max_height)
        )
        window.events.before_show += _solid_title_bar
        # Let the native window close before the finally block waits for cleanup.
        window.events.closing += lambda: _on_closing(window, controller)
        if smoke:

            def close_after_load() -> None:
                time.sleep(1)
                window.destroy()

            window.events.loaded += close_after_load
        webview.start(
            http_server=not _SERVE_FROM_FILE,
            # This launch's own profile (see claim_webview_profile), not private mode's temp one.
            private_mode=False,
            storage_path=str(profile),
            icon=str(icon) if (icon := window_icon()) else None,
            gui="edgechromium" if sys.platform == "win32" else None,
        )
    except (Exception, SystemExit):
        logger.exception("Could not open the webview launcher")
        return FALLBACK
    finally:
        if controller is not None:
            controller.close()
        if lock is not None:
            lock.close()  # the next start removes this profile
    return 0
