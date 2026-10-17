"""Tk window for non-technical operators. Thin: logic lives in ``viewmodel``."""

import contextlib
import logging
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from . import i18n
from .i18n import t
from .monitors import Monitor, choose_projector, enumerate_monitors
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
    Option,
    StatusState,
    apply_event,
    auto_mode_options,
    describe_error,
    describe_event,
    describe_speech,
    form_from_settings,
    language_options,
    load_microphones,
    microphone_options,
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


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings = load_settings()
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

        # Advanced (collapsible)
        self.b_adv = ttk.Button(main, command=self.toggle_advanced, width=28)
        self.b_adv.grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
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
        buttons.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(10, 4))
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
        status.grid(row=10, column=0, columnspan=2, sticky="ew", pady=4)
        self.s_labels: dict[str, ttk.Label] = {}
        for i, key in enumerate(("server", "audio", "gpu")):
            ttk.Label(status, text=t(f"status.{key}") + ":").grid(
                row=i, column=0, sticky="w", padx=(0, 8)
            )
            lbl = ttk.Label(status, text="")
            lbl.grid(row=i, column=1, sticky="w")
            self.s_labels[key] = lbl

        # Log pane
        log_frame = ttk.Frame(main)
        log_frame.grid(row=11, column=0, columnspan=2, sticky="nsew")
        main.rowconfigure(11, weight=1)
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
        ]
        self._refresh_status()
        self._apply_running_state()

    def _show_advanced(self) -> None:
        arrow = "v" if self.advanced_open else ">"
        self.b_adv.configure(text=f"{arrow} {t('advanced')}")
        if self.advanced_open:
            self.adv.grid(row=8, column=0, columnspan=2, sticky="ew")
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
            settings = settings_from_form(self.settings, self._read_form())
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
        self.root.destroy()

    # ---- buttons --------------------------------------------------------------------

    def _port(self) -> int:
        return self.plan.port if self.plan else self.settings.port

    def on_open_operator(self) -> None:
        webbrowser.open(operator_url(self._port()))

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
            self.projector.close()
            self._apply_running_state()
            self._refresh_status()
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
        if self.settings.open_operator_on_start:
            webbrowser.open(operator_url(self._port()))
        if self.settings.open_display_on_start:
            self.refresh_monitors()
            self._open_projector(self.settings.projector_monitor)

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
