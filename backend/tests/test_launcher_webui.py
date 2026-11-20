"""Headless checks for the launcher webview bridge."""

import json
import struct
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from launcher import cli, i18n, webui
from launcher.logtail import LogBuffer, LogTail
from launcher.micmeter import BLOCK_SAMPLES, block_level
from launcher.process import LaunchPlan
from launcher.settings import LauncherSettings, load_settings, save_settings
from launcher.webui import LauncherApi, LauncherController


def wait_for(check):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if value := check():
            return value
        time.sleep(0.01)
    pytest.fail("Timed out waiting for launcher worker")


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    # The fake devices use Windows inputs explicitly, avoiding default-device discovery.
    settings_path = tmp_path / "launcher.json"
    save_settings(
        LauncherSettings(
            ui_language="en",
            input_device="Mic A",
            open_display_on_start=False,
            open_operator_on_start=False,
        ),
        settings_path,
    )
    original_language = i18n.get_language()
    trace = []
    session = SimpleNamespace(
        running=threading.Event(),
        finish=threading.Event(),
        meter_exit=threading.Event(),
        launches=[],
        processes=[],
    )
    session.meter_exit.set()

    class MeterProcess:
        def __init__(self):
            self.exit = threading.Event()
            self.stdout = self

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, size):
            self.exit.wait()
            return b""

        def kill(self):
            self.exit.set()

        def wait(self):
            assert self.exit.is_set()
            assert session.meter_exit.wait(3)
            trace.append("meter exited")
            return 0

    def popen(cmd, **kwargs):
        assert cmd[-7:] == ["-ac", "1", "-ar", "16000", "-f", "s16le", "-"]
        assert kwargs["env"] == {"PATH": ""}
        trace.append("meter started")
        proc = MeterProcess()
        session.processes.append(proc)
        return proc

    def build_plan(settings):
        return LaunchPlan(
            [],
            [],
            {"PATH": ""},
            tmp_path,
            tmp_path,
            settings.port,
            "cpu",
            "int8",
            network_token=settings.network_token if settings.allow_network else None,
            # Two device warnings, as build_plan gives on a PC without CUDA.
            warnings=["CUDA not found", "Using int8"],
        )

    class Launch:
        error_code = "audio_exited"
        error_detail = "audio.log"

        def __init__(self, plan, emit):
            self.plan = plan
            self.emit = emit
            session.launches.append(self)

        def run(self):
            trace.append("launcher running")
            for warning in self.plan.warnings:
                self.emit("warning", warning)
            self.emit("ready", self.plan.base_url)
            self.emit("running", "")
            session.running.set()
            assert session.finish.wait(3)
            self.emit("stopped", "")
            return 0

        def stop(self):
            session.finish.set()

    controller = LauncherController(
        load=lambda: load_settings(settings_path),
        save=lambda settings: save_settings(settings, settings_path),
        plan_builder=build_plan,
        launcher_factory=Launch,
        popen=popen,
        mic_lister=lambda _: ["Mic A", "Mic B"],
        monitor_lister=list,
        get_lan_ip=lambda: "192.0.2.1",
        firewall_supported=lambda: True,
        get_firewall_programs=lambda _: ["python"],
        firewall_exists=lambda: False,
        access=lambda **_: "authorized",
        clock=lambda: 1234.5,
    )
    try:
        yield LauncherApi(controller), settings_path, trace, session
    finally:
        session.meter_exit.set()
        session.finish.set()
        controller.close()
        i18n.set_language(original_language)


def test_contract_validation_and_device_refresh(bridge):
    api, _, _, _ = bridge
    initial = api.init()
    form = initial["form"]
    assert json.loads(json.dumps(initial)) == initial
    assert set(form) == {
        "language",
        "input_device",
        "projector_monitor",
        "auto_mode",
        "ui_language",
        "model",
        "device",
        "compute_type",
        "port",
        "log_level",
        "extra_args",
        "open_display",
        "open_operator",
        "speech_engine",
        "allow_network",
        "session_audio",
    }
    result = api.start({**form, "port": "bad"})
    assert result["ok"] is False
    assert result["error"]["field"] == "port"
    options = wait_for(lambda: api.poll()["options"])
    assert [option["value"] for option in options["microphone"]] == [None, "Mic A", "Mic B"]
    assert api.poll()["options"] is None
    api.refresh_devices()
    assert wait_for(lambda: api.poll()["options"]) == options
    assert api.poll()["options"] is None
    result = api.start({**form, "input_device": None})
    assert result["ok"] is False
    assert result["error"]["field"] == "input_device"
    assert api.start({**form, "extra_args": '"'})["error"]["field"] == "extra_args"


def test_meter_waits_for_ffmpeg_exit_then_resumes(bridge):
    api, settings_path, trace, session = bridge
    form = api.init()["form"]
    wait_for(lambda: session.processes)
    session.meter_exit.clear()
    assert api.start({**form, "session_audio": False}) == {"ok": True}
    assert session.processes[0].exit.wait(3)
    assert not session.launches
    session.meter_exit.set()
    assert session.running.wait(3)
    session.finish.set()
    wait_for(lambda: len(session.processes) == 2)
    assert trace == ["meter started", "meter exited", "launcher running", "meter started"]
    assert not load_settings(settings_path).session_audio
    assert api.init()["form"]["session_audio"] is False


def test_view_and_ui_language(bridge):
    api, path, _, _ = bridge
    english = api.init()["options"]
    assert api.set_view("advanced") is None
    assert load_settings(path).launcher_view == api.init()["view"] == "advanced"
    lithuanian = api.set_ui_language("lt")
    assert lithuanian["language"] == "lt"
    assert lithuanian["options"]["microphone"][0]["label"] != english["microphone"][0]["label"]
    assert load_settings(path).ui_language == "lt"


def test_network_and_error_state(bridge):
    api, _, _, session = bridge
    form = api.init()["form"]
    assert api.start({**form, "allow_network": True}) == {"ok": True}
    assert session.running.wait(3)
    state = api.poll()["state"]
    assert (state["session"], state["server"], state["audio"]) == ("live", "ready", "running")
    assert state["started_at"] == 1234500
    assert state["network"]["display_url"] == "http://192.0.2.1:8000/display"
    assert state["network"]["operator_url"] == "http://192.0.2.1:8000/operator"
    assert state["network"]["token"]
    assert wait_for(lambda: api.poll()["state"]["network"]["firewall"])["button_enabled"]
    session.launches[0].emit("error", "The audio exited")
    assert api.poll()["state"]["error"]["text"]
    session.finish.set()
    wait_for(lambda: api.poll()["state"]["session"] == "idle")
    assert api.poll()["state"]["error"]["text"]
    session.finish.clear()
    session.running.clear()
    assert api.start(form) == {"ok": True}
    assert api.poll()["state"]["error"] is None


@pytest.mark.parametrize(("peak", "level"), [(0, 0), (-32768, 1), (32767, 1), (1036, 0.5)])
def test_pcm_block_level(peak, level):
    block = struct.pack("<h", peak) + bytes(2 * (BLOCK_SAMPLES - 1))
    assert block_level(block) == pytest.approx(level, abs=0.0001)


def test_tail_reads_only_new_lines_and_detects_truncation(tmp_path):
    server = tmp_path / "server.log"
    server.write_bytes(b"INFO: old content\n")
    buffer = LogBuffer()
    tail = LogTail(tmp_path, buffer)
    with server.open("ab") as stream:
        stream.write(b"\n=== 2026-11-20 17:45:00 python -m uvicorn\n\x1b[32mINFO\x1b[0m:  hel")
    tail.read()
    assert buffer.drain() == []
    with server.open("ab") as stream:
        stream.write(b"lo\r\nCRITICAL:  goodbye\n")
    (tmp_path / "audio.log").write_bytes(
        b"2026-11-20 17:50:15,522 - WARNING - audio - failed \xff\nother\n"
    )
    tail.read()
    lines = buffer.drain()
    assert [(line["source"], line["level"], line["text"]) for line in lines] == [
        ("server", "INFO", "INFO:  hello"),
        ("server", "ERROR", "CRITICAL:  goodbye"),
        ("audio", "WARNING", "2026-11-20 17:50:15,522 - WARNING - audio - failed \ufffd"),
        ("audio", "INFO", "other"),
    ]
    assert lines[2]["time"] == "17:50:15"
    server.write_bytes(b"DEBUG: reset\n")
    tail.read()
    assert [line["level"] for line in buffer.drain()] == ["DEBUG"]
    server.rename(tmp_path / "server.old")
    server.write_bytes(b"INFO: replacement is longer than before\n")
    tail.read()
    assert [line["text"] for line in buffer.drain()] == ["INFO: replacement is longer than before"]
    tail.read()
    assert buffer.drain() == []


def test_tail_caps_entries(tmp_path):
    buffer = LogBuffer(limit=2)
    tail = LogTail(tmp_path, buffer)
    (tmp_path / "server.log").write_bytes(b"first\nsecond\nthird\n")
    tail.read()
    assert [line["text"] for line in buffer.drain()] == [
        "second",
        "third",
        i18n.t("log.lines_skipped", count=1),
    ]


def test_api_exposes_only_contract_methods(bridge):
    api, _, _, _ = bridge
    assert {name for name in dir(api) if not name.startswith("_")} == {
        "init",
        "poll",
        "start",
        "stop",
        "refresh_devices",
        "set_microphone",
        "open_operator",
        "show_projector",
        "open_logs",
        "allow_firewall",
        "set_ui_language",
        "set_view",
        "copy_text",
    }


def test_webview_main_uses_dev_url_and_closing_handler(bridge, monkeypatch):
    api, _, _, _ = bridge
    controller = api._controller
    observed = {}

    class Closing:
        def __iadd__(self, handler):
            self.handler = handler
            return self

    closing = Closing()

    def create_window(title, url, **options):
        observed.update(title=title, url=url, **options)
        return SimpleNamespace(events=SimpleNamespace(closing=closing))

    def start(**options):
        observed.update(options)
        closing.handler()

    fake_webview = SimpleNamespace(
        create_window=create_window,
        start=start,
        screens=[SimpleNamespace(x=0, y=0, height=768)],
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setenv("BIBLELIVE_LAUNCHER_URL", "http://127.0.0.1:5173/launcher.html")
    monkeypatch.setattr(webui, "LauncherController", lambda: controller)
    monkeypatch.setitem(
        sys.modules, "launcher.gui", SimpleNamespace(_enable_dpi_awareness=lambda: None)
    )
    assert webui.main() == 0
    assert observed["title"] == i18n.t("app.title")
    assert observed["url"] == "http://127.0.0.1:5173/launcher.html"
    assert (observed["width"], observed["height"], observed["min_size"]) == (1120, 708, (760, 600))
    assert observed["http_server"] is observed["private_mode"] is True
    assert observed["gui"] == ("edgechromium" if sys.platform == "win32" else None)
    assert isinstance(observed["js_api"], LauncherApi)
    assert api.start(api.init()["form"])["ok"] is False


def test_cli_falls_back_to_tk_when_page_missing(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("BIBLELIVE_LAUNCHER_URL", raising=False)
    monkeypatch.delenv("BIBLELIVE_LAUNCHER_UI", raising=False)
    monkeypatch.setattr(webui, "_default_app_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: LauncherSettings(ui_language="en"))
    calls = []
    fake_gui = SimpleNamespace(main=lambda *, smoke: calls.append(smoke) or 0)
    monkeypatch.setitem(sys.modules, "launcher.gui", fake_gui)
    original_language = i18n.get_language()
    try:
        assert cli.main(["--smoke"]) == 0
        assert calls == [True]
        assert "Launcher page missing" in caplog.text
        assert "Opening the Tk launcher" in caplog.text
    finally:
        i18n.set_language(original_language)


def test_whisper_on_the_processor_is_reported_once_per_session(bridge):
    api, _, _, session = bridge
    form = api.init()["form"]
    assert api.start(form)["ok"] is True
    assert session.running.wait(3)
    no_gpu = i18n.t("warn.no_gpu")
    lines = []
    wait_for(lambda: lines.extend(api.poll()["log"]) or api.poll()["state"]["session"] == "live")
    lines += api.poll()["log"]
    assert [line["text"] for line in lines].count(no_gpu) == 1
