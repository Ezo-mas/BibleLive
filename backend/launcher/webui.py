"""pywebview launcher window and its thread-safe bridge."""

import contextlib
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import i18n, platform
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
)
from .platform.macos import microphone_access
from .process import Launcher, LaunchPlan, _default_app_root, build_plan
from .projector import ProjectorBrowser, ProjectorError, operator_url
from .settings import (
    LAUNCHER_VIEWS,
    UI_LANGUAGES,
    LauncherSettings,
    app_data_dir,
    load_settings,
    save_settings,
)
from .viewmodel import (
    COMPUTE_OPTIONS,
    DEVICE_OPTIONS,
    LOG_LEVEL_OPTIONS,
    MODEL_OPTIONS,
    FormError,
    FormValues,
    NetworkView,
    Option,
    StatusState,
    apply_event,
    auto_mode_options,
    check_microphone,
    describe_error,
    describe_event,
    describe_speech,
    firewall_view,
    form_from_settings,
    language_options,
    load_microphones,
    microphone_options,
    network_view,
    plain_options,
    projector_options,
    settings_from_form,
    speech_engine_options,
    ui_language_options,
)

logger = logging.getLogger(__name__)
FALLBACK = 2
_MIN_WINDOW_SIZE = (760, 480)
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
        self._no_gpu_logged = False
        self._network: NetworkView | None = None
        self._firewall: str | None = None
        self._probe_plan: LaunchPlan | None = None
        self._plan: LaunchPlan | None = None
        self._launcher: Launcher | None = None
        self._worker: threading.Thread | None = None
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
            "model": _options(plain_options(MODEL_OPTIONS)),
            "device": _options(plain_options(DEVICE_OPTIONS)),
            "compute_type": _options(plain_options(COMPUTE_OPTIONS)),
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
        return {
            "session": self._session,
            "server": self._status.server,
            "audio": self._status.audio,
            "speech": describe_speech(plan.speech_engine, plan.device, plan.compute_type)
            if plan
            else None,
            "speech_warning": bool(
                plan and plan.speech_engine == "whisper" and plan.device == "cpu"
            ),
            "started_at": self._started_at,
            "network": network,
            "error": {"text": self._error} if self._error is not None else None,
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

    def start(self, form: FormValues) -> dict[str, Any]:
        with self._lock:
            if self._closed or self._session != "idle":
                return self._start_error("session_busy", t("err.session_busy"))
            self._error = None
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
                self._network = None
                self._firewall = None
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
            if kind == "ready" and self._session == "starting" and plan is not None:
                self._session = "live"
                self._started_at = int(self._clock() * 1000)
                self._on_server_ready(plan)

    def _on_server_ready(self, plan: LaunchPlan) -> None:
        if plan.network_token:
            self._network = view = network_view(plan.port, plan.network_token, self._lan_ip())
            if view.display_url:
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
            self._network = None
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

    def close(self) -> None:
        self.request_close()
        with self._lock:
            worker, launcher = self._worker, self._launcher
        if launcher is not None:
            launcher.stop()
        if worker is not None:
            worker.join()
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

    def open_logs(self) -> None:
        self._controller.open_logs()

    def allow_firewall(self) -> None:
        self._controller.allow_firewall()

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


def main(*, smoke: bool = False) -> int:
    logging.basicConfig(level=logging.WARNING)
    url = os.environ.get("BIBLELIVE_LAUNCHER_URL")
    if url and not _is_loopback_url(url):
        logger.warning("Ignoring BIBLELIVE_LAUNCHER_URL: expected a loopback HTTP(S) URL")
        url = None
    if not url:
        page = _default_app_root() / "static" / "launcher" / "launcher.html"
        if not page.is_file():
            logger.warning("Launcher page missing at %s", page)
            return FALLBACK
        url = str(page)

    controller = None
    try:
        import webview  # type: ignore[import-untyped,unused-ignore]

        if sys.platform == "win32":
            from .gui import _enable_dpi_awareness

            _enable_dpi_awareness()
        screens = webview.screens
        # The primary desktop screen contains the coordinate origin.
        primary = next(
            (s for s in screens if s.x == 0 and s.y == 0), screens[0] if screens else None
        )
        max_height = primary.height - 60 if primary is not None else None
        controller = LauncherController()
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
        window.events.closing += controller.request_close
        if smoke:

            def close_after_load() -> None:
                time.sleep(1)
                window.destroy()

            window.events.loaded += close_after_load
        webview.start(
            http_server=True,
            private_mode=True,
            gui="edgechromium" if sys.platform == "win32" else None,
        )
    except (Exception, SystemExit):
        logger.exception("Could not open the webview launcher")
        return FALLBACK
    finally:
        if controller is not None:
            controller.close()
    return 0
