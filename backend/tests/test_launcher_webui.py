"""Headless checks for the launcher webview bridge."""

import io
import json
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import pytest
from launcher import cli, diagnostics, i18n, micmeter, screen_labels, speech, webui
from launcher.logtail import LogBuffer, LogTail
from launcher.platform import Monitor, PortOwner
from launcher.process import LaunchPlan
from launcher.screen_labels import label_window_rect
from launcher.settings import LauncherSettings, load_settings, save_settings
from launcher.webui import LauncherApi, LauncherController

from audio.levels import BLOCK_SAMPLES, block_level


def wait_for(check):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if value := check():
            return value
        time.sleep(0.01)
    pytest.fail("Timed out waiting for launcher worker")


@pytest.mark.parametrize("fail", [False, True])
def test_collect_diagnostics(tmp_path, monkeypatch, fail):
    entered = threading.Event()
    finish = threading.Event()
    reveal = Mock()
    monkeypatch.setattr(i18n, "_current", i18n.get_language())
    monkeypatch.setattr(webui, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        webui, "system_info", lambda settings, monitors, engine: {"monitors": monitors}
    )

    def build(data_dir, log_dir, settings, out_path, *, extra_info, now, plan_token):
        entered.set()
        assert finish.wait(3)
        assert data_dir == tmp_path
        assert out_path.parent == log_dir / "diagnostics"
        if fail:
            raise OSError("disk full")
        return out_path

    controller = LauncherController(
        load=lambda: LauncherSettings(ui_language="en"),
        bundle_builder=build,
        reveal_file=reveal,
    )
    api = LauncherApi(controller)
    assert api.poll()["state"]["diagnostics"]["status"] == "idle"
    assert api.collect_diagnostics() == {"ok": True}
    try:
        assert entered.wait(3)
        assert api.poll()["state"]["diagnostics"]["status"] == "running"
        assert api.collect_diagnostics()["ok"] is False
    finally:
        finish.set()
    expected = "error" if fail else "done"

    def completed():
        poll = api.poll()
        return poll if poll["state"]["diagnostics"]["status"] == expected else None

    result = wait_for(completed)
    status = result["state"]["diagnostics"]
    if fail:
        reveal.assert_not_called()
        assert status["file"] is None
        assert status["text"] == "Could not create the diagnostics bundle."
    else:
        reveal.assert_called_once()
        path = reveal.call_args.args[0]
        assert status["file"] == path.name
        assert path.name.startswith("BibleLive-diagnostics-")
        assert any(line["level"] == "INFO" and str(path) in line["text"] for line in result["log"])
    controller.close()


def test_diagnostics_uses_live_plan_and_finishes_after_reveal_error(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(webui, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(diagnostics, "query_gpus", lambda run: [])
    reveal = Mock(side_effect=RuntimeError("file manager unavailable"))
    controller = LauncherController(
        load=lambda: LauncherSettings(language="en", speech_engine="whisper"), reveal_file=reveal
    )
    controller._plan = LaunchPlan(
        [],
        [],
        {"BIBLELIVE_OPERATOR_TOKEN": "plan-only-secret"},
        tmp_path,
        tmp_path,
        8000,
        "cpu",
        "int8",
        speech_engine="kmynas",
    )
    (tmp_path / "server.log").write_text("plan-only-secret", encoding="utf-8")
    assert controller.collect_diagnostics()["ok"]
    wait_for(lambda: controller.poll()["state"]["diagnostics"]["status"] == "done")
    assert "Revealing diagnostics failed" in caplog.text
    with ZipFile(reveal.call_args.args[0]) as archive:
        assert archive.read("server.log") == b"[REDACTED]"
        speech = json.loads(archive.read("diagnostics.json"))["speech"]
        assert speech["resolved"] == "kmynas"
        assert speech["settings_resolved"] == "whisper"


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
        launch_entered=threading.Event(),
        allow_ready=threading.Event(),
        stopped=threading.Event(),
        finish=threading.Event(),
        meter_exit=threading.Event(),
        launches=[],
        processes=[],
        qr_calls=[],
        qr_answer=lambda visible: visible,
        speech_engine="whisper",
        speech_fallback=False,
        self_test_popen=Mock(),
    )
    session.meter_exit.set()
    session.allow_ready.set()

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
            speech_engine=session.speech_engine,
            speech_fallback=session.speech_fallback,
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
            session.launch_entered.set()
            assert session.allow_ready.wait(3)
            if session.finish.is_set():
                return 0
            for warning in self.plan.warnings:
                self.emit("warning", warning)
            self.emit("ready", self.plan.base_url)
            self.emit("running", "")
            session.running.set()
            assert session.finish.wait(3)
            self.emit("stopped", "")
            return 0

        def stop(self):
            session.stopped.set()
            session.finish.set()
            session.allow_ready.set()

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
        post_audience_qr=lambda port, token, visible: (
            session.qr_calls.append((port, token, visible)) or session.qr_answer(visible)
        ),
        self_test_popen=session.self_test_popen,
    )
    try:
        yield LauncherApi(controller), settings_path, trace, session
    finally:
        session.meter_exit.set()
        session.finish.set()
        session.allow_ready.set()
        controller.close()
        i18n.set_language(original_language)


def test_speech_check_uses_unsaved_engine_and_matches_auto_on_cpu(bridge, monkeypatch, tmp_path):
    api, _, _, _ = bridge
    monkeypatch.setattr(webui, "models_dir", lambda: tmp_path)
    monkeypatch.setattr(webui, "cuda_supported_compute_types", lambda: ["float16"])
    kmynas = str(tmp_path / speech.GGUF_MODELS["kmynas"].revision / "kmynas.gguf")
    monkeypatch.setattr(
        speech, "cached_gguf_path", lambda name, _: kmynas if name == "kmynas" else None
    )
    assert api.check_speech("unknown", "cpu") == {
        "wanted": "kmynas+canary",
        "runs": "kmynas",
        "models": [{"name": "kmynas", "state": "ok"}, {"name": "canary", "state": "missing"}],
    }
    assert api.check_speech("whisper", "cpu") == {
        "wanted": "whisper",
        "runs": "whisper",
        "models": [],
    }


def test_speech_fallback_warns_even_when_kmynas_runs(bridge):
    api, _, _, session = bridge
    session.speech_engine = "kmynas"
    session.speech_fallback = True
    api.init()
    state = wait_for(lambda: (state := api.poll()["state"])["speech"] and state)
    assert state["speech_warning"] is True
    assert state["speech_fallback"] is True


class SelfTestProcess:
    def __init__(self, output, exit_code):
        self.stdout = io.BytesIO(output)
        self.exit_code = exit_code
        self.exited = threading.Event()
        self.killed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stdout.close()

    def poll(self):
        return self.exit_code if self.exited.is_set() else None

    def wait(self):
        assert self.exited.wait(3)
        return self.exit_code

    def kill(self):
        self.killed = True
        self.exit_code = 1
        self.exited.set()


@pytest.mark.parametrize(
    ("marker", "exit_code", "status", "level"),
    [
        ("SELFTEST OK: Kmynas", 0, "ok", "INFO"),
        ("SELFTEST FAIL: model not downloaded", 1, "fail", "ERROR"),
    ],
)
def test_self_test_streams_and_refuses_start_until_child_exits(
    bridge, monkeypatch, tmp_path, marker, exit_code, status, level
):
    api, _, _, session = bridge
    monkeypatch.setattr(webui, "models_dir", lambda: tmp_path)
    child = SelfTestProcess(f"Loading model\n{marker}\n".encode(), exit_code)
    session.self_test_popen.return_value = child
    form = {**api.init()["form"], "speech_engine": "kmynas", "device": "cpu", "language": "lt"}
    assert api.run_self_test(form) == {"ok": True}
    seen = []

    def read_log():
        seen.extend(api.poll()["log"])
        return any(line["text"] == marker for line in seen)

    wait_for(read_log)
    assert api.poll()["state"]["self_test"] == {"status": "running", "text": None}
    assert api.start(form)["error"]["code"] == "self_test_busy"
    assert api.run_self_test(form)["error"]["code"] == "self_test_busy"
    assert [
        (line["text"], line["level"]) for line in seen if line["text"] in (marker, "Loading model")
    ] == [
        ("Loading model", "INFO"),
        (marker, level),
    ]
    args, kwargs = session.self_test_popen.call_args
    assert args[0] == [
        sys.executable,
        "-m",
        "launcher",
        "--gpu-self-test",
        "--speech-engine",
        "kmynas",
        "--device",
        "cpu",
        "--compute-type",
        form["compute_type"],
        "--model",
        form["model"],
        "--language",
        "lt",
    ]
    assert kwargs["env"]["HF_HOME"] == str(tmp_path)
    assert kwargs["stderr"] == subprocess.STDOUT
    for key, value in webui.platform.current().child_process_options().items():
        assert kwargs[key] == value
    child.exited.set()
    wait_for(lambda: api.poll()["state"]["self_test"] == {"status": status, "text": marker})


def test_self_test_is_refused_while_live_and_with_invalid_speech_settings(bridge):
    api, _, _, session = bridge
    form = api.init()["form"]
    assert api.run_self_test({**form, "model": "--save"})["error"]["code"] == "invalid_speech"
    assert api.start(form) == {"ok": True}
    assert session.running.wait(3)
    assert api.run_self_test(form)["error"]["code"] == "session_busy"
    session.self_test_popen.assert_not_called()


@pytest.mark.parametrize("timeout", [True, False])
def test_self_test_is_killed_on_timeout_or_close(bridge, monkeypatch, timeout):
    api, _, _, session = bridge
    child = SelfTestProcess(b"", 0)
    session.self_test_popen.return_value = child
    if timeout:
        monkeypatch.setattr(webui, "_SELF_TEST_TIMEOUT_S", 0.02)
    assert api.run_self_test(api.init()["form"]) == {"ok": True}
    wait_for(lambda: session.self_test_popen.called)
    if timeout:
        wait_for(lambda: api.poll()["state"]["self_test"]["status"] == "fail")
        assert api.poll()["state"]["self_test"]["text"] == "The speech self-test timed out."
    else:
        api._controller.close()
    assert child.killed


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


def test_start_during_microphone_switch_waits_for_meter_exit(bridge):
    api, _, trace, session = bridge
    form = api.init()["form"]
    wait_for(lambda: session.processes)
    session.meter_exit.clear()
    switch = threading.Thread(target=api.set_microphone, args=("Mic B",))
    switch.start()
    try:
        assert session.processes[0].exit.wait(3)
        assert api.poll()["state"]["session"] == "idle"
        assert api.start({**form, "input_device": "Mic B"}) == {"ok": True}
        assert not session.launch_entered.is_set()
    finally:
        session.meter_exit.set()
        switch.join(3)
    assert not switch.is_alive()
    assert session.running.wait(3)
    assert trace == ["meter started", "meter exited", "launcher running"]


def test_start_during_meter_device_lookup_prevents_spawn(bridge, monkeypatch):
    api, _, trace, session = bridge
    lookup = threading.Event()
    release = threading.Event()
    input_args = micmeter.ffmpeg_input_args

    def blocked_lookup(*args, **kwargs):
        lookup.set()
        assert release.wait(3)
        return input_args(*args, **kwargs)

    monkeypatch.setattr(micmeter, "ffmpeg_input_args", blocked_lookup)
    form = api.init()["form"]
    try:
        assert lookup.wait(3)
        assert api.start(form) == {"ok": True}
        assert not session.launch_entered.is_set()
    finally:
        release.set()
    assert session.running.wait(3)
    assert trace == ["launcher running"]


def test_meter_device_lookup_timeout_is_reported(bridge, monkeypatch):
    api, _, _, session = bridge
    uncaught = []

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 10)

    monkeypatch.setattr(threading, "excepthook", uncaught.append)
    monkeypatch.setattr(micmeter, "ffmpeg_input_args", timeout)
    api.init()

    def meter_error():
        snapshot = api.poll()
        if snapshot["meter"] == "error":
            return snapshot["meter_error"]
        return None

    assert wait_for(meter_error) == i18n.t("meter.device_failed")
    assert not session.processes
    assert not uncaught


@pytest.mark.parametrize("phase", ["starting", "live"])
def test_close_stops_launcher_and_joins_worker_without_restarting_meter(bridge, phase):
    api, _, trace, session = bridge
    controller = api._controller
    if phase == "starting":
        session.allow_ready.clear()
    form = api.init()["form"]
    wait_for(lambda: session.processes)
    assert api.start(form) == {"ok": True}
    ready = session.launch_entered if phase == "starting" else session.running
    assert ready.wait(3)
    assert api.poll()["state"]["session"] == phase
    worker = controller._worker
    assert worker is not None and worker.is_alive()

    controller.request_close()
    assert not session.stopped.is_set()
    assert worker.is_alive()
    controller.close()

    assert session.stopped.is_set()
    assert not worker.is_alive()
    assert api.poll()["state"]["session"] == "idle"
    assert trace == ["meter started", "meter exited", "launcher running"]


def test_stop_while_building_plan_returns_to_idle_and_resumes_meter(bridge, monkeypatch):
    api, _, trace, session = bridge
    controller = api._controller
    building = threading.Event()
    release = threading.Event()
    build = controller._build_plan

    def blocked_build(settings):
        building.set()
        assert release.wait(3)
        return build(settings)

    form = api.init()["form"]
    wait_for(lambda: session.processes)
    monkeypatch.setattr(controller, "_build_plan", blocked_build)
    assert api.start(form) == {"ok": True}
    try:
        assert building.wait(3)
        assert not session.launches
        assert api.stop() is None
        assert api.poll()["state"]["session"] == "stopping"
    finally:
        release.set()
    wait_for(lambda: len(session.processes) == 2)
    assert api.poll()["state"]["session"] == "idle"
    assert trace == ["meter started", "meter exited", "meter started"]


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
    assert api.poll()["state"]["error"]["port"] is None  # not a busy-port error
    session.launches[0].error_code, session.launches[0].error_detail = "port_in_use", "8000"
    session.launches[0].emit("error", "Port 8000 is already in use.")
    assert api.poll()["state"]["error"]["port"] == 8000
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


def test_operator_qr_code_in_network_details(bridge):
    api, _, _, session = bridge
    form = api.init()["form"]
    assert api.start({**form, "allow_network": True}) == {"ok": True}
    assert session.running.wait(3)
    network = api.poll()["state"]["network"]
    assert network["operator_qr"].startswith("data:image/png;base64,")
    assert (network["audience_qr"], network["audience_qr_busy"]) == (False, False)


def test_audience_qr_is_shown_and_hidden_through_the_server(bridge):
    api, settings_path, _, session = bridge
    form = api.init()["form"]
    api.set_audience_qr(True)  # no session yet: nothing to ask
    assert session.qr_calls == []
    assert api.start({**form, "allow_network": True}) == {"ok": True}
    assert session.running.wait(3)
    token = load_settings(settings_path).network_token

    api.set_audience_qr(True)
    assert wait_for(lambda: api.poll()["state"]["network"]["audience_qr"])
    assert session.qr_calls == [(8000, token, True)]
    api.set_audience_qr(False)
    wait_for(lambda: len(session.qr_calls) == 2)
    assert wait_for(lambda: not api.poll()["state"]["network"]["audience_qr_busy"])
    assert api.poll()["state"]["network"]["audience_qr"] is False
    assert session.qr_calls[1] == (8000, token, False)


def test_audience_qr_failure_and_no_address_are_logged(bridge):
    api, _, _, session = bridge
    form = api.init()["form"]
    assert api.start({**form, "allow_network": True}) == {"ok": True}
    assert session.running.wait(3)
    api.poll()

    def unreachable(_visible):
        raise OSError("connection refused")

    warnings: list[str] = []

    def settled() -> bool:
        poll = api.poll()
        warnings.extend(line["text"] for line in poll["log"] if line["level"] == "WARNING")
        return not poll["state"]["network"]["audience_qr_busy"]

    session.qr_answer = unreachable
    api.set_audience_qr(True)
    wait_for(settled)
    assert any("Could not reach the server" in text for text in warnings)
    session.qr_answer = lambda _visible: False  # the server found no address
    api.set_audience_qr(True)
    wait_for(lambda: len(session.qr_calls) == 2 and settled())
    assert any("no network address" in text for text in warnings)
    assert api.poll()["state"]["network"]["audience_qr"] is False


def test_stopping_hides_the_audience_qr_state(bridge):
    api, _, _, session = bridge
    form = api.init()["form"]
    assert api.start({**form, "allow_network": True}) == {"ok": True}
    assert session.running.wait(3)
    api.set_audience_qr(True)
    assert wait_for(lambda: api.poll()["state"]["network"]["audience_qr"])
    api.stop()
    wait_for(lambda: api.poll()["state"]["session"] == "idle")
    assert api.poll()["state"]["network"] is None


def test_api_exposes_only_contract_methods(bridge):
    api, _, _, _ = bridge
    assert {name for name in dir(api) if not name.startswith("_")} == {
        "init",
        "poll",
        "start",
        "check_speech",
        "run_self_test",
        "check_port",
        "stop",
        "refresh_devices",
        "set_microphone",
        "open_operator",
        "show_projector",
        "identify_screens",
        "open_logs",
        "collect_diagnostics",
        "allow_firewall",
        "set_audience_qr",
        "set_ui_language",
        "set_view",
        "fit_window",
        "copy_text",
    }


def test_identify_screens_uses_listed_monitors_and_ignores_repeat_and_close():
    monitors = [Monitor("Built-in", 0, 0, 1920, 1080, True)]
    identify = Mock()
    controller = LauncherController(load=LauncherSettings, identify=identify)
    controller.identify_screens()
    identify.assert_not_called()
    controller._monitors = monitors
    identify.side_effect = lambda _: controller.identify_screens()
    controller.identify_screens()
    controller.close()
    controller.identify_screens()
    identify.assert_called_once_with(monitors)


@pytest.mark.parametrize(
    ("monitor", "scale", "expected"),
    [
        (Monitor("DISPLAY1", 0, 0, 1920, 1080, True), 1.5, (645, 345, 630, 390)),
        (Monitor("DISPLAY2", -1600, -900, 1600, 900, False), 1, (-1010, -580, 420, 260)),
        (Monitor("DISPLAY3", 1920, 0, 1920, 1080, False), 1, (2670, 410, 420, 260)),
    ],
)
def test_label_window_rect(monitor, scale, expected):
    assert label_window_rect(monitor, scale) == expected


@pytest.mark.parametrize("fail_second", [False, True])
def test_identify_screens_closes_every_created_window(monkeypatch, fail_second):
    monitors = [
        Monitor("Built-in", 0, 0, 1920, 1080, True),
        Monitor("Display 2", -1600, -900, 1600, 900, False),
    ]
    events = []

    def create(title, **options):
        events.append(("create", title))
        assert options["frameless"] and options["on_top"] and not options["focus"]
        if title == monitors[0].label:
            assert i18n.t("screen.main") in options["html"]
        else:
            assert (options["x"], options["y"]) == (-1010, -580)
            if fail_second:
                raise RuntimeError("screen disconnected")
        return SimpleNamespace(destroy=lambda: events.append(("destroy", title)))

    monkeypatch.setattr(screen_labels, "_place_windows_label", lambda *_: None)
    monkeypatch.setattr(
        screen_labels.time, "sleep", lambda seconds: events.append(("wait", seconds))
    )
    webview = SimpleNamespace(create_window=create)
    if fail_second:
        with pytest.raises(RuntimeError, match="screen disconnected"):
            screen_labels.identify_screens(webview, monitors, None)
    else:
        screen_labels.identify_screens(webview, monitors, None)
    expected = [("create", monitor.label) for monitor in monitors]
    if not fail_second:
        expected.append(("wait", 3))
    expected.extend(
        ("destroy", monitor.label) for monitor in (monitors[:1] if fail_second else monitors)
    )
    assert events == expected


def test_check_port_validates_and_only_checks_when_idle():
    busy = Mock(side_effect=[False, True, True])
    owner = Mock(side_effect=[PortOwner(1234, "obs64.exe"), None])
    health = Mock(side_effect=[False, True])
    api = LauncherApi(
        LauncherController(
            load=LauncherSettings, port_owner=owner, port_in_use=busy, health_probe=health
        )
    )
    free = {"free": True, "program": None, "pid": None, "bibleLive": False}
    assert api.check_port("65536") == {"error": "invalid_port"}
    assert api.check_port(" 8000 ") == free
    owner.assert_not_called()
    health.assert_not_called()
    assert api.check_port("8000") == {**free, "free": False, "program": "obs64.exe", "pid": 1234}
    assert api.check_port("8000") == {**free, "free": False, "bibleLive": True}
    api._controller._session = "live"
    assert api.check_port("8000") == free
    assert [call.args for call in busy.call_args_list] == [(8000,), (8000,), (8000,)]
    assert owner.call_count == 2
    assert health.call_count == 2


def test_fit_window_keeps_width_clamps_height_and_skips_expanded_window(bridge, monkeypatch):
    api, _, _, _ = bridge
    assert api.fit_window(580, 760) is None
    monkeypatch.setattr(sys, "platform", "win32")
    native = SimpleNamespace(is_fullscreen=False, WindowState="Normal")
    sizes = []
    window = SimpleNamespace(
        width=1120,
        height=800,
        maximized=False,
        fullscreen=False,
        native=native,
        resize=lambda width, height: sizes.append((width, height)),
    )
    api._controller.attach_window(
        lambda content, view: webui._fit_window(window, content, view, 900)
    )
    assert api.fit_window(580, 760) is None
    api.fit_window(400, 760)
    api.fit_window(1000, 760)
    api.fit_window(760, 760)
    api.fit_window(float("nan"), 760)
    native.WindowState = "Maximized"
    api.fit_window(580, 760)
    native.WindowState = "Normal"
    native.is_fullscreen = True
    api.fit_window(580, 760)
    assert sizes == [(1120, 620), (1120, 480), (1120, 900)]


@pytest.mark.parametrize(("color", "expected"), [("#faf8f4", 0xF4F8FA), ("#1c1b19", 0x191B1C)])
def test_caption_colorref(color, expected):
    assert webui._colorref(color) == expected


def test_solid_title_bar_is_noop_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert webui._solid_title_bar(None) is None


def test_solid_title_bar_reapplies_caption_after_theme_change(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    calls = []

    def set_attribute(hwnd, attribute, value):
        calls.append((hwnd, attribute, value))
        return 0

    monkeypatch.setitem(
        sys.modules,
        "webview.platforms.winforms",
        SimpleNamespace(DwmSetWindowAttribute=set_attribute),
    )
    form = SimpleNamespace(Handle=SimpleNamespace(ToInt32=lambda: 42), dark=True)
    form.is_dark_theme = lambda: form.dark
    form.update_title_bar_theme = lambda: calls.append("original")
    webui._solid_title_bar(SimpleNamespace(native=form))
    form.dark = False
    form.update_title_bar_theme()
    assert calls == [(42, 38, 1), (42, 35, 0x191B1C), "original", (42, 38, 1), (42, 35, 0xF4F8FA)]


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("http://127.0.0.1:5173/launcher.html", True),
        ("https://LOCALHOST/launcher.html?demo", True),
        ("http://[::1]:5173/launcher.html", True),
        ("https://example.com/launcher.html", False),
        ("http://localhost.example.com/launcher.html", False),
        ("http://localhost@example.com/launcher.html", False),
        ("http://example.com\\@localhost/launcher.html", False),
        ("http://[::1]example.com/launcher.html", False),
        ("file://localhost/launcher.html", False),
        ("//localhost/launcher.html", False),
    ],
)
def test_webview_main_checks_dev_url_and_registers_closing_handler(
    bridge, monkeypatch, tmp_path, caplog, url, allowed
):
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
        return SimpleNamespace(
            events=SimpleNamespace(closing=closing, before_show=Closing()),
            destroy=lambda: observed.update(destroyed=True),
        )

    def start(**options):
        observed.update(options)
        closing.handler()
        assert api.start(api.init()["form"])["ok"] is False

    fake_webview = SimpleNamespace(
        create_window=create_window,
        start=start,
        screens=[SimpleNamespace(x=0, y=0, height=768)],
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    page = tmp_path / "static" / "launcher" / "launcher.html"
    page.parent.mkdir(parents=True)
    page.touch()
    monkeypatch.setattr(webui, "_default_app_root", lambda: tmp_path)
    monkeypatch.setattr(webui, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setenv("BIBLELIVE_LAUNCHER_URL", url)
    monkeypatch.setattr(webui, "LauncherController", lambda *, identify: controller)
    monkeypatch.setitem(
        sys.modules, "launcher.gui", SimpleNamespace(_enable_dpi_awareness=lambda: None)
    )
    assert webui.main() == 0
    assert observed["title"] == i18n.t("app.title")
    # macOS loads the page from its file, so no local server triggers the firewall prompt.
    local_page = page.as_uri() if webui._SERVE_FROM_FILE else str(page)
    assert observed["url"] == (url if allowed else local_page)
    assert ("Ignoring BIBLELIVE_LAUNCHER_URL" in caplog.text) is not allowed
    assert (observed["width"], observed["height"], observed["min_size"]) == (1120, 600, (760, 480))
    assert observed["http_server"] is not webui._SERVE_FROM_FILE
    # Its own profile under the launcher profiles folder, not a private-mode temp one.
    assert observed["private_mode"] is False
    assert Path(observed["storage_path"]).parent == webui.webview_profiles_root()
    assert observed["gui"] == ("edgechromium" if sys.platform == "win32" else None)
    assert isinstance(observed["js_api"], LauncherApi)
    assert api.start(api.init()["form"])["ok"] is False


def test_cli_falls_back_to_tk_when_page_missing(tmp_path, monkeypatch, caplog):
    # A real smoke watchdog exits the entire process, even after main() returns.
    watchdog = Mock()
    monkeypatch.setattr(webui, "faulthandler", SimpleNamespace(dump_traceback_later=watchdog))
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
        watchdog.assert_called_once_with(webui._SMOKE_TIMEOUT_S, exit=True)
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

    def is_live():
        snapshot = api.poll()
        lines.extend(snapshot["log"])
        return snapshot["state"]["session"] == "live"

    wait_for(is_live)
    assert [line["text"] for line in lines].count(no_gpu) == 1


def test_meter_start_superseded_during_lookup_is_off_not_an_error(monkeypatch):
    # A session starting (or another mic chosen) while the device lookup runs is not a failure.
    allowed = iter([True, False])  # start() checks once, the reader once after the lookup
    monkeypatch.setattr(micmeter, "ffmpeg_input_args", lambda device, **_: ["-i", "x"])
    spawned = []
    meter = micmeter.MicMeter(popen=lambda *a, **k: spawned.append(a), access=lambda **_: "")
    meter.start("Mic A", env={"PATH": ""}, can_start=lambda: next(allowed, False))
    wait_for(lambda: meter._thread is not None and not meter._thread.is_alive())
    assert spawned == []
    assert meter.snapshot()[:2] == ("off", None)
    meter.stop()


def test_webview_profiles_live_in_local_app_data(monkeypatch, tmp_path):
    monkeypatch.setattr(webui, "models_dir", lambda: tmp_path / "BibleLive" / "models")
    assert webui.webview_profiles_root() == tmp_path / "BibleLive" / "webview"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to delete an open file")
def test_each_launch_gets_a_profile_and_unused_ones_are_removed(tmp_path):
    first, first_lock = webui.claim_webview_profile(tmp_path)
    second, second_lock = webui.claim_webview_profile(tmp_path)
    assert first != second
    assert first.is_dir()  # still in use by the first launch: kept
    first_lock.close()
    third, third_lock = webui.claim_webview_profile(tmp_path)
    try:
        assert not first.exists()  # its launch ended: removed
        assert second.is_dir() and third.is_dir()
    finally:
        second_lock.close()
        third_lock.close()


def test_meter_asks_dshow_for_small_buffers_and_leaves_other_inputs_alone():
    dshow = ["-f", "dshow", "-i", "audio=Mic A"]
    assert micmeter.meter_input_args(dshow) == [
        "-f",
        "dshow",
        "-audio_buffer_size",
        "50",
        "-i",
        "audio=Mic A",
    ]
    assert micmeter.meter_input_args(["-f", "avfoundation", "-i", ":default"]) == [
        "-f",
        "avfoundation",
        "-i",
        ":default",
    ]


def test_window_icon_prefers_the_built_app_then_the_checkout(tmp_path):
    assert webui.window_icon(tmp_path) is None
    source = tmp_path / "scripts" / "windows" / "installer" / "biblelive.ico"
    source.parent.mkdir(parents=True)
    source.touch()
    assert webui.window_icon(tmp_path) == source
    built = tmp_path / "biblelive.ico"
    built.touch()
    assert webui.window_icon(tmp_path) == built


def test_pywebview_page_call_threads_do_not_block_exit():
    util = SimpleNamespace(Thread=threading.Thread)
    webui._daemon_bridge_threads(SimpleNamespace(util=util))
    assert util.Thread(target=lambda: None).daemon
    assert not util.Thread(target=lambda: None, daemon=False).daemon


@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
def test_closing_the_window_lets_main_clean_up(monkeypatch, platform_name):
    # macOS Cmd-Q would otherwise end the process before webview.start() returns, leaving the
    # server and the pipeline running: there the close is cancelled and the window closed.
    monkeypatch.setattr(webui.sys, "platform", platform_name)
    calls = []
    window = SimpleNamespace(destroy=lambda: calls.append("destroy"))
    controller = SimpleNamespace(request_close=lambda: calls.append("request_close"))
    result = webui._on_closing(window, controller)
    if platform_name == "darwin":
        assert result is False
        assert calls == ["request_close", "destroy"]
    else:
        assert result is None
        assert calls == ["request_close"]


def test_audience_qr_request_goes_to_this_computer_with_the_token(monkeypatch):
    from launcher import webui

    seen = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    def urlopen(request, timeout):
        seen.update(
            url=request.full_url,
            method=request.get_method(),
            auth=request.get_header("Authorization"),
            body=json.loads(request.data),
            timeout=timeout,
        )
        return Response(b'{"visible": true, "url": "http://192.0.2.1:8100/display"}')

    class Opener:
        def __init__(self, handlers):
            proxies = [h for h in handlers if isinstance(h, webui.urllib.request.ProxyHandler)]
            assert proxies and proxies[0].proxies == {}  # never through a proxy
            self.open = urlopen

    monkeypatch.setattr(webui.urllib.request, "build_opener", lambda *h: Opener(h))
    assert webui._post_audience_qr(8100, "tok", True) is True
    assert seen == {
        "url": "http://127.0.0.1:8100/api/display/qr",
        "method": "POST",
        "auth": "Bearer tok",
        "body": {"visible": True},
        "timeout": webui._AUDIENCE_QR_TIMEOUT_S,
    }
