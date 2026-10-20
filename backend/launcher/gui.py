"""Tk window for non-technical operators. Thin: logic lives in ``viewmodel``."""

import base64
import contextlib
import logging
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import replace
from functools import partial
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from . import i18n
from .i18n import t
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
from .process import Launcher, LaunchPlan, build_plan
from .projector import ProjectorBrowser, ProjectorError, operator_url
from .settings import LauncherSettings, app_data_dir, load_settings, save_settings
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
    network_qr,
    network_view,
    plain_options,
    projector_options,
    settings_from_form,
    speech_engine_options,
    state_text,
    ui_language_options,
)

logger = logging.getLogger(__name__)

POLL_MS = 100
STATE_COLORS = {
    "stopped": "#666666",
    "starting": "#b36b00",
    "ready": "#1a7f37",
    "running": "#1a7f37",
    "error": "#c62828",
}


class Choice:
    """Read-only combobox holding (value, label) options."""

    def __init__(self, parent: tk.Misc, options: list[Option], value: str | None, width: int = 34):
        self.var = tk.StringVar()
        self.box = ttk.Combobox(parent, textvariable=self.var, state="readonly", width=width)
        self.options: list[Option] = []
        self.set_options(options, value)

    def set_options(self, options: list[Option], value: str | None) -> None:
        self.options = options
        self.box["values"] = [label for _, label in options]
        self.set(value)

    def get(self) -> str | None:
        label = self.var.get()
        return next((v for v, lbl in self.options if lbl == label), None)

    def set(self, value: str | None) -> None:
        for v, label in self.options:
            if v == value:
                self.var.set(label)
                return
        self.var.set(self.options[0][1] if self.options else "")

    def bind_change(self, callback: Any) -> None:
        self.box.bind("<<ComboboxSelected>>", lambda _e: callback())


def _photo(master: tk.Misc, png: bytes) -> tk.PhotoImage:
    """A Tk image from PNG bytes. Tk 8.6 reads PNG itself, so no imaging library is needed."""
    return tk.PhotoImage(master=master, data=base64.b64encode(png).decode("ascii"))


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        # Network mode needs a token before the server can start; Start saves it.
        self.settings = ensure_network_token(load_settings())
        i18n.set_language(i18n.resolve_ui_language(self.settings.ui_language))
        self.queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.projector = ProjectorBrowser()
        self.monitors: list[Monitor] = []
        self.mic_names: list[str] = []
        self.status = StatusState()
        self.gpu_text: str | None = None
        self.advanced_open = False
        self.running = False
        self.plan: LaunchPlan | None = None
        self.launcher: Launcher | None = None
        self._network: NetworkView | None = None  # addresses for other devices, while running
        # QR codes for those addresses; the App keeps the Tk images alive here, and shows them
        # in a window of their own (the main window has no room for them on a small screen).
        self._qr_photos: dict[str, tk.PhotoImage] = {}
        self.qr_window: tk.Toplevel | None = None
        self.qr_labels: dict[str, ttk.Label] = {}
        # Firewall row under them: "missing", "adding", "not_added" or "present" (None = hidden).
        self._firewall: str | None = None
        self._lock = threading.Lock()
        self._stop_flag = False
        self._log_lines: list[tuple[str, str]] = []
        self.main: ttk.Frame | None = None
        self._inputs: list[Any] = []
        self.form = form_from_settings(self.settings)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_monitors()
        self._build()
        self.refresh_microphones()
        self._probe_gpu()
        self.root.after(POLL_MS, self._poll)

    # ---- construction -------------------------------------------------------------

    def _build(self) -> None:
        if self.main is not None:
            self.main.destroy()
        self._close_qr_window()  # its captions are in the old language
        self.root.title(t("app.title"))
        f = self.form
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        self.main = main
        self._inputs = []

        def row(r: int, key: str, widget: ttk.Widget) -> None:
            ttk.Label(main, text=t(key)).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
            widget.grid(row=r, column=1, sticky="ew", pady=3)

        self.c_lang = Choice(main, language_options(), f.language)
        row(0, "label.speech_language", self.c_lang.box)

        mic_frame = ttk.Frame(main)
        mic_frame.columnconfigure(0, weight=1)
        self.c_mic = Choice(mic_frame, microphone_options(self.mic_names, f.input_device), None)
        self.c_mic.set(f.input_device)
        self.c_mic.box.grid(row=0, column=0, sticky="ew")
        self.b_mic = ttk.Button(mic_frame, text=t("btn.refresh"), command=self.refresh_microphones)
        self.b_mic.grid(row=0, column=1, padx=(6, 0))
        row(1, "label.microphone", mic_frame)

        proj_frame = ttk.Frame(main)
        proj_frame.columnconfigure(0, weight=1)
        self.c_proj = Choice(proj_frame, projector_options(self.monitors), f.projector_monitor)
        self.c_proj.box.grid(row=0, column=0, sticky="ew")
        self.b_proj = ttk.Button(
            proj_frame, text=t("btn.refresh"), command=self.on_refresh_monitors
        )
        self.b_proj.grid(row=0, column=1, padx=(6, 0))
        row(2, "label.projector", proj_frame)

        self.c_auto = Choice(main, auto_mode_options(), f.auto_mode)
        row(3, "label.auto_approve", self.c_auto.box)

        self.c_ui = Choice(main, ui_language_options(), f.ui_language)
        self.c_ui.bind_change(self.on_ui_language)
        row(4, "label.ui_language", self.c_ui.box)

        self.v_open_display = tk.BooleanVar(value=f.open_display)
        self.v_open_operator = tk.BooleanVar(value=f.open_operator)
        k_display = ttk.Checkbutton(
            main, text=t("label.open_display"), variable=self.v_open_display
        )
        k_operator = ttk.Checkbutton(
            main, text=t("label.open_operator"), variable=self.v_open_operator
        )
        k_display.grid(row=5, column=0, columnspan=2, sticky="w")
        k_operator.grid(row=6, column=0, columnspan=2, sticky="w")

        self.v_network = tk.BooleanVar(value=f.allow_network)
        k_network = ttk.Checkbutton(main, text=t("label.allow_network"), variable=self.v_network)
        k_network.grid(row=7, column=0, columnspan=2, sticky="w")

        # Advanced (collapsible)
        self.b_adv = ttk.Button(main, command=self.toggle_advanced, width=28)
        self.b_adv.grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.adv = ttk.Frame(main, padding=(12, 4, 0, 4))
        self.adv.columnconfigure(1, weight=1)
        self.c_model = Choice(self.adv, plain_options(MODEL_OPTIONS), f.model, 22)
        self.c_device = Choice(self.adv, plain_options(DEVICE_OPTIONS), f.device, 22)
        self.c_compute = Choice(self.adv, plain_options(COMPUTE_OPTIONS), f.compute_type, 22)
        self.c_log = Choice(self.adv, plain_options(LOG_LEVEL_OPTIONS), f.log_level, 22)
        self.c_speech = Choice(self.adv, speech_engine_options(), f.speech_engine, 40)
        self.v_port = tk.StringVar(value=f.port)
        self.v_extra = tk.StringVar(value=f.extra_args)
        e_port = ttk.Entry(self.adv, textvariable=self.v_port, width=10)
        e_extra = ttk.Entry(self.adv, textvariable=self.v_extra)
        adv_rows: list[tuple[str, Any]] = [
            ("adv.speech_engine", self.c_speech.box),
            ("adv.model", self.c_model.box),
            ("adv.device", self.c_device.box),
            ("adv.compute_type", self.c_compute.box),
            ("adv.port", e_port),
            ("adv.log_level", self.c_log.box),
            ("adv.extra_args", e_extra),
        ]
        for i, (key, w) in enumerate(adv_rows):
            ttk.Label(self.adv, text=t(key)).grid(row=i, column=0, sticky="w", padx=(0, 10))
            w.grid(row=i, column=1, sticky="w" if w is not e_extra else "ew", pady=2)
        self._show_advanced()

        # Buttons
        buttons = ttk.Frame(main)
        buttons.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        self.b_start = ttk.Button(buttons, text=t("btn.start"), command=self.on_start)
        self.b_stop = ttk.Button(buttons, text=t("btn.stop"), command=self.on_stop)
        self.b_operator = ttk.Button(
            buttons, text=t("btn.open_operator"), command=self.on_open_operator
        )
        self.b_projector = ttk.Button(
            buttons, text=t("btn.show_projector"), command=self.on_show_projector
        )
        self.b_logs = ttk.Button(buttons, text=t("btn.open_logs"), command=self.on_open_logs)
        for i, b in enumerate(
            (self.b_start, self.b_stop, self.b_operator, self.b_projector, self.b_logs)
        ):
            b.grid(row=0, column=i, padx=(0, 6))

        # Status
        status = ttk.Frame(main)
        status.grid(row=11, column=0, columnspan=2, sticky="ew", pady=4)
        self.s_labels: dict[str, ttk.Label] = {}
        for i, key in enumerate(("server", "audio", "gpu")):
            ttk.Label(status, text=t(f"status.{key}") + ":").grid(
                row=i, column=0, sticky="w", padx=(0, 8)
            )
            lbl = ttk.Label(status, text="")
            lbl.grid(row=i, column=1, sticky="w")
            self.s_labels[key] = lbl

        # Other devices on this network: shown while running in network mode.
        self._build_network(main)

        # Log pane
        log_frame = ttk.Frame(main)
        log_frame.grid(row=13, column=0, columnspan=2, sticky="nsew")
        main.rowconfigure(13, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=10, width=80, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.tag_configure("error", foreground="#c62828")
        self.log.tag_configure("warning", foreground="#b36b00")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        for text, tag in self._log_lines:
            self._write_log(text, tag)

        self._inputs = [
            self.c_lang.box,
            self.c_mic.box,
            self.b_mic,
            self.c_proj.box,
            self.b_proj,
            self.c_auto.box,
            k_display,
            k_operator,
            self.c_speech.box,
            self.c_model.box,
            self.c_device.box,
            self.c_compute.box,
            self.c_log.box,
            e_port,
            e_extra,
            k_network,
        ]
        self._refresh_network()
        self._refresh_status()
        self._apply_running_state()

    def _build_network(self, main: ttk.Frame) -> None:
        """Addresses and token for other devices, each with a Copy button (hidden until ready).

        Compact on purpose: with the firewall row it is the tallest state of the window, and
        it must still fit a 1366x768 screen. The token shares the operator page's row, the
        QR button the display page's.
        """
        self.net_frame = ttk.LabelFrame(main, text=t("net.title"), padding=(8, 0, 8, 2))
        self.net_frame.columnconfigure(1, weight=1)
        self.net_frame.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self.net_vars: dict[str, tk.StringVar] = {}
        # (key, grid row, first column, entry width); a field is label, entry, Copy button.
        for key, r, c, width in (
            ("display", 0, 0, 36),
            ("operator", 1, 0, 36),
            ("token", 1, 3, 12),
        ):
            ttk.Label(self.net_frame, text=t(f"net.{key}")).grid(
                row=r, column=c, sticky="w", padx=(0 if c == 0 else 14, 8), pady=1
            )
            var = tk.StringVar()
            self.net_vars[key] = var
            ttk.Entry(self.net_frame, textvariable=var, state="readonly", width=width).grid(
                row=r, column=c + 1, sticky="ew", pady=1
            )
            ttk.Button(self.net_frame, text=t("btn.copy"), command=partial(self._copy, key)).grid(
                row=r, column=c + 2, padx=(6, 0), pady=1
            )
        # Opens the QR window; needs an address, so it is hidden without one (see _refresh_qr).
        self.qr_button = ttk.Button(
            self.net_frame, text=t("net.show_qr"), command=self.on_show_qr_codes
        )
        self.qr_button.grid(row=0, column=3, columnspan=3, sticky="ew", padx=(14, 0), pady=1)
        ttk.Label(self.net_frame, text=t("net.hint"), wraplength=640, justify="left").grid(
            row=2, column=0, columnspan=6, sticky="w"
        )
        self.net_note = ttk.Label(self.net_frame, text="", foreground="#b36b00")
        self.net_note.grid(row=3, column=0, columnspan=6, sticky="w")
        # Only when the rule is missing (see _check_firewall); hidden otherwise.
        self.fw_label = ttk.Label(
            self.net_frame, text="", wraplength=500, justify="left", foreground="#b36b00"
        )
        self.fw_label.grid(row=4, column=0, columnspan=4, sticky="w", pady=(2, 0))
        self.fw_button = ttk.Button(
            self.net_frame, text=t("btn.allow_firewall"), command=self.on_allow_firewall
        )
        self.fw_button.grid(row=4, column=4, columnspan=2, sticky="e", padx=(14, 0), pady=(2, 0))

    def _show_advanced(self) -> None:
        arrow = "v" if self.advanced_open else ">"
        self.b_adv.configure(text=f"{arrow} {t('advanced')}")
        if self.advanced_open:
            self.adv.grid(row=9, column=0, columnspan=2, sticky="ew")
        else:
            self.adv.grid_remove()

    def toggle_advanced(self) -> None:
        self.advanced_open = not self.advanced_open
        self._show_advanced()

    # ---- form <-> settings ----------------------------------------------------------

    def _read_form(self) -> FormValues:
        return FormValues(
            language=self.c_lang.get() or "en",
            input_device=self.c_mic.get(),
            projector_monitor=self.c_proj.get(),
            auto_mode=self.c_auto.get() or "off",
            ui_language=self.c_ui.get() or "auto",
            model=self.c_model.get() or self.settings.model,
            device=self.c_device.get() or "auto",
            compute_type=self.c_compute.get() or "auto",
            port=self.v_port.get(),
            log_level=self.c_log.get() or "INFO",
            extra_args=self.v_extra.get(),
            open_display=self.v_open_display.get(),
            open_operator=self.v_open_operator.get(),
            speech_engine=self.c_speech.get() or "auto",
            allow_network=self.v_network.get(),
        )

    def on_ui_language(self) -> None:
        self.form = self._read_form()
        self.settings = replace(self.settings, ui_language=self.form.ui_language)
        i18n.set_language(i18n.resolve_ui_language(self.form.ui_language))
        with contextlib.suppress(OSError):
            save_settings(self.settings)
        self._build()

    # ---- background refreshes --------------------------------------------------------

    def refresh_microphones(self) -> None:
        saved = self.c_mic.get() if hasattr(self, "c_mic") else self.form.input_device
        settings = replace(self.settings, input_device=saved)

        def work() -> None:
            try:
                names = load_microphones(settings)
            except Exception as exc:  # RuntimeError (no ffmpeg) or anything else: show, don't die
                self.queue.put(("mics", [], str(exc)))
            else:
                self.queue.put(("mics", names, None))

        self._log(t("mic.loading"), "info")
        threading.Thread(target=work, daemon=True).start()

    def refresh_monitors(self) -> None:
        self.monitors = enumerate_monitors()

    def on_refresh_monitors(self) -> None:
        saved = self.c_proj.get()
        self.refresh_monitors()
        self.c_proj.set_options(projector_options(self.monitors), saved)

    def _probe_gpu(self) -> None:
        settings = self.settings

        def work() -> None:
            try:
                plan = build_plan(settings)
            except Exception:
                logger.exception("GPU probe failed")
                return
            self.queue.put(
                ("gpu", describe_speech(plan.speech_engine, plan.device, plan.compute_type))
            )

        threading.Thread(target=work, daemon=True).start()

    # ---- start / stop ---------------------------------------------------------------

    def on_start(self) -> None:
        try:
            settings = ensure_network_token(settings_from_form(self.settings, self._read_form()))
            check_microphone(settings.input_device, self.mic_names)
        except FormError as exc:
            msg = describe_error(exc.code, exc.detail)
            self._log(msg, "error")
            messagebox.showerror(t("app.title"), msg)
            return
        self.settings = settings
        with contextlib.suppress(OSError):
            save_settings(settings)
        self.status = StatusState()
        self.running = True
        self._stop_flag = False
        self._apply_running_state()
        self._log(t("log.logs_in", detail=app_data_dir() / "logs"), "info")
        threading.Thread(target=self._worker, args=(settings,), daemon=True).start()

    def _worker(self, settings: LauncherSettings) -> None:
        try:
            plan = build_plan(settings)
            launcher = Launcher(plan, lambda k, m: self._on_launcher_event(launcher, k, m))
            with self._lock:
                if self._stop_flag:
                    self.queue.put(("finished", 0))
                    return
                self.plan, self.launcher = plan, launcher
            self.queue.put(
                ("gpu", describe_speech(plan.speech_engine, plan.device, plan.compute_type))
            )
            code = launcher.run()
        except Exception as exc:
            logger.exception("Launcher crashed")
            self.queue.put(("event", "error", str(exc), "unknown", str(exc)))
            code = 1
        self.queue.put(("finished", code))

    def _on_launcher_event(self, launcher: Launcher, kind: str, message: str) -> None:
        code = launcher.error_code if kind == "error" else None
        detail = launcher.error_detail if kind == "error" else ""
        self.queue.put(("event", kind, message, code, detail))

    def on_stop(self) -> None:
        self._log(t("log.stopping"), "info")
        self.b_stop.state(["disabled"])
        self._close_qr_window()
        with self._lock:
            self._stop_flag = True
            launcher = self.launcher
        if launcher is not None:
            threading.Thread(target=launcher.stop, daemon=True).start()
        self.projector.close()

    def on_close(self) -> None:
        with self._lock:
            self._stop_flag = True
            launcher = self.launcher
        if launcher is not None:
            launcher.stop()
        self.projector.close()
        self._close_qr_window()
        self.root.destroy()

    # ---- buttons --------------------------------------------------------------------

    def _port(self) -> int:
        return self.plan.port if self.plan else self.settings.port

    def on_open_operator(self) -> None:
        webbrowser.open(self._operator_page())

    def _operator_page(self) -> str:
        """The operator page; in network mode the token rides in the URL fragment."""
        if self.plan and self.plan.network_token:
            return operator_url_with_token(self.plan.port, self.plan.network_token)
        return operator_url(self._port())

    def _copy(self, key: str) -> None:
        value = self.net_vars[key].get()
        if value:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)

    def on_show_projector(self) -> None:
        self.refresh_monitors()
        saved = self.c_proj.get()
        self.c_proj.set_options(projector_options(self.monitors), saved)
        self._open_projector(saved)

    def _open_projector(self, saved: str | None) -> None:
        monitor = choose_projector(self.monitors, saved)
        try:
            result = self.projector.open(self._port(), monitor)
        except ProjectorError as exc:
            key = "projector_missing" if exc.code == "no_monitor" else "projector_failed"
            self._log(describe_error(key, exc.detail), "error")
            return
        if result == "browser_fallback":
            self._log(t("warn.browser_fallback"), "warning")
        elif monitor is not None:
            self._log(t("log.projector_opened", detail=monitor.label), "info")

    def on_open_logs(self) -> None:
        log_dir = app_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(log_dir)
        else:
            webbrowser.open(log_dir.as_uri())

    # ---- events / rendering -----------------------------------------------------------

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _handle(self, item: tuple[Any, ...]) -> None:
        tag = item[0]
        if tag == "event":
            _, kind, message, code, detail = item
            self.status = apply_event(self.status, kind, message, code)
            if (
                kind == "warning"
                and self.plan
                and self.plan.device == "cpu"
                and self.plan.speech_engine == "whisper"
            ):
                self._log(t("warn.no_gpu"), "warning")
            else:
                described = describe_event(kind, message, code, detail)
                if described:
                    self._log(described[1], described[0])
            if kind == "ready":
                self._on_server_ready()
            self._refresh_status()
        elif tag == "finished":
            self.running = False
            self._network = None
            self._qr_photos = {}
            self._firewall = None
            self._refresh_network()
            self.projector.close()
            self._apply_running_state()
            self._refresh_status()
        elif tag == "firewall" and self._network is not None:  # else the server has stopped
            status = item[1]
            if status == "present" and self._firewall == "adding":
                self._log(t("fw.added"), "info")
            elif status == "not_added":
                self._log(t("fw.not_added"), "warning")
            self._firewall = status
            self._refresh_network()
        elif tag == "gpu":
            self.gpu_text = item[1]
            self._refresh_status()
        elif tag == "mics":
            _, names, error = item
            self.mic_names = names
            self.c_mic.set_options(microphone_options(names, self.c_mic.get()), self.c_mic.get())
            if error:
                self._log(error, "warning")

    def _on_server_ready(self) -> None:
        if self.plan and self.plan.network_token:
            self._show_network(self.plan.port, self.plan.network_token)
            self._check_firewall()
        if self.settings.open_operator_on_start:
            webbrowser.open(self._operator_page())
        if self.settings.open_display_on_start:
            self.refresh_monitors()
            self._open_projector(self.settings.projector_monitor)

    def _show_network(self, port: int, token: str) -> None:
        """Show and log the addresses other devices use (the server is ready)."""
        ip = lan_ip()
        view = network_view(port, token, ip)
        self._network = view
        codes = network_qr(port, token, ip)
        self._qr_photos = {}
        if codes is not None:
            self._qr_photos = {
                "display": _photo(self.root, codes.display_png),
                "operator": _photo(self.root, codes.operator_png),
            }
        if view.display_url:
            self._log(
                t("net.log_addresses", display=view.display_url, operator=view.operator_url),
                "info",
            )
        else:
            self._log(t("net.no_address"), "warning")
        self._log(t("net.log_token", token=token), "info")
        self._refresh_network()

    def _refresh_network(self) -> None:
        view = self._network
        if view is None:
            self.net_frame.grid_remove()
            self._close_qr_window()
            return
        self.net_frame.grid()
        self.net_vars["display"].set(view.display_url)
        self.net_vars["operator"].set(view.operator_url)
        self.net_vars["token"].set(view.token)
        if view.display_url:
            self.net_note.grid_remove()  # an empty label would still take a line
        else:
            self.net_note.configure(text=t("net.no_address"))
            self.net_note.grid()
        fw = firewall_view(self._firewall)
        if fw.shown:
            self.fw_label.configure(text=fw.message)
            self.fw_label.grid()
            self.fw_button.grid()
            self.fw_button.state(["!disabled" if fw.button_enabled else "disabled"])
        else:
            self.fw_label.grid_remove()
            self.fw_button.grid_remove()
        self._refresh_qr()

    def _refresh_qr(self) -> None:
        """The QR button needs an address; without one there is nothing to show."""
        if not self._qr_photos:
            self.qr_button.grid_remove()
            self._close_qr_window()
            return
        self.qr_button.grid()
        if self.qr_window is not None:  # open while the addresses changed: show the new codes
            for key, label in self.qr_labels.items():
                label.configure(image=self._qr_photos[key])

    def on_show_qr_codes(self) -> None:
        """The button: open the QR window, or bring the open one to the front."""
        if not self._qr_photos:
            return
        if self.qr_window is not None and self.qr_window.winfo_exists():
            self.qr_window.deiconify()
            self.qr_window.lift()
            self.qr_window.focus_set()
            return
        win = tk.Toplevel(self.root)
        win.title(t("net.qr_title"))
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._close_qr_window)
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        self.qr_labels = {}
        captions = {"display": t("net.display"), "operator": t("net.qr_operator")}
        for col, (key, caption) in enumerate(captions.items()):
            ttk.Label(body, text=caption).grid(row=0, column=col, padx=8)
            label = ttk.Label(body, image=self._qr_photos[key])
            label.grid(row=1, column=col, padx=8, pady=(4, 0), sticky="n")
            self.qr_labels[key] = label
        self.qr_warning = ttk.Label(
            body, text=t("net.qr_warning"), wraplength=400, justify="center", foreground="#c62828"
        )
        self.qr_warning.grid(row=2, column=0, columnspan=2, padx=8, pady=(10, 0))
        # Beside the main window, not on top of its buttons.
        win.geometry(
            f"+{max(self.root.winfo_rootx() + 40, 0)}+{max(self.root.winfo_rooty() + 40, 0)}"
        )
        self.qr_window = win

    def _close_qr_window(self) -> None:
        """Close the QR window if it is open (Stop, the window closing, a new language)."""
        win, self.qr_window = self.qr_window, None
        self.qr_labels = {}
        if win is not None:
            with contextlib.suppress(tk.TclError):
                win.destroy()

    def _check_firewall(self) -> None:
        """Network mode and server ready: look for the BibleLive rule. Never prompts."""
        if not firewall_applies() or not firewall_programs(sys.executable):
            return

        def work() -> None:
            self.queue.put(("firewall", "present" if firewall_rule_exists() else "missing"))

        threading.Thread(target=work, daemon=True).start()

    def on_allow_firewall(self) -> None:
        """The button: one administrator prompt adds the rules, then the row is updated."""
        programs = firewall_programs(sys.executable)
        if not programs:
            return
        self._firewall = "adding"
        self._refresh_network()
        threading.Thread(target=self._add_firewall, args=(programs,), daemon=True).start()

    def _add_firewall(self, programs: list[str]) -> None:
        ok = allow_through_firewall(programs)
        self.queue.put(("firewall", "present" if ok else "not_added"))

    def _refresh_status(self) -> None:
        for key, state in (("server", self.status.server), ("audio", self.status.audio)):
            self.s_labels[key].configure(text=state_text(state), foreground=STATE_COLORS[state])
        gpu = self.s_labels["gpu"]
        gpu.configure(text=self.gpu_text or t("gpu.checking"), foreground="#000000")
        if (
            self.gpu_text
            and self.plan
            and self.plan.device == "cpu"
            and self.plan.speech_engine == "whisper"
        ):
            gpu.configure(foreground=STATE_COLORS["error"])

    def _apply_running_state(self) -> None:
        for w in self._inputs:
            if isinstance(w, ttk.Combobox):
                w.configure(state="disabled" if self.running else "readonly")
            else:
                w.state(["disabled" if self.running else "!disabled"])
        self.c_ui.box.configure(state="readonly")
        self.b_start.state(["disabled" if self.running else "!disabled"])
        for b in (self.b_stop, self.b_operator, self.b_projector):
            b.state(["!disabled" if self.running else "disabled"])

    def _log(self, text: str, level: str) -> None:
        self._log_lines.append((text, level))
        del self._log_lines[:-500]
        self._write_log(text, level)

    def _write_log(self, text: str, level: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", level if level in ("error", "warning") else ())
        self.log.see("end")
        self.log.configure(state="disabled")


def _enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        with contextlib.suppress(AttributeError, OSError):
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined,unused-ignore]


def _set_window_icon(root: tk.Tk) -> None:
    """Use ``<app>/biblelive.ico`` (shipped by the Windows build) if it exists."""
    icon = Path(__file__).resolve().parents[2] / "biblelive.ico"
    if sys.platform == "win32" and icon.is_file():
        with contextlib.suppress(tk.TclError):
            root.iconbitmap(default=str(icon))


def main(*, smoke: bool = False) -> int:
    """Build and run the window. ``smoke`` closes it after a second (construction test)."""
    logging.basicConfig(level=logging.WARNING)
    _enable_dpi_awareness()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Cannot open a window: {exc}", file=sys.stderr)
        return 1
    root.minsize(640, 560)
    _set_window_icon(root)
    app = App(root)
    if smoke:
        root.update()
        root.after(1000, app.on_close)
    root.mainloop()
    return 0
