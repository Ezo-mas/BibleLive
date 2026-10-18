"""Launcher window support: settings validation, stop safety, i18n, monitors, projector, view-model."""

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from launcher import i18n
from launcher.cli import apply_overrides, build_parser
from launcher.monitors import Monitor, choose_projector, enumerate_monitors
from launcher.process import Launcher, build_plan
from launcher.projector import (
    Browser,
    ProjectorBrowser,
    ProjectorError,
    build_command,
    display_url,
    find_browser,
    find_profile_processes,
)
from launcher.settings import (
    AUTO_MODES,
    LANGUAGES,
    MODELS,
    LauncherSettings,
    load_settings,
)
from launcher.viewmodel import (
    FormError,
    StatusState,
    apply_event,
    check_microphone,
    describe_error,
    describe_event,
    describe_gpu,
    form_from_settings,
    microphone_options,
    projector_options,
    settings_from_form,
)

PRIMARY = Monitor("\\\\.\\DISPLAY1", 0, 0, 1920, 1080, True)
SECOND = Monitor("\\\\.\\DISPLAY2", 1920, 0, 1280, 720, False)
THIRD = Monitor("\\\\.\\DISPLAY3", 3200, 0, 1280, 720, False)


# --- Part A ---------------------------------------------------------------------------


def test_choices_match_audio_cli():
    pytest.importorskip("audio.cli")
    from audio.cli import build_parser as audio_parser

    actions = {a.dest: a for a in audio_parser()._actions}
    assert tuple(actions["model"].choices) == MODELS
    assert tuple(actions["language"].choices) == LANGUAGES
    # launcher CLI uses the same shared tuples
    launcher_actions = {a.dest: a for a in build_parser()._actions}
    assert tuple(launcher_actions["model"].choices) == MODELS
    assert tuple(launcher_actions["language"].choices) == LANGUAGES


def test_invalid_enum_settings_fall_back(tmp_path, caplog):
    path = tmp_path / "l.json"
    path.write_text(
        json.dumps(
            {
                "language": "xx",
                "device": "tpu",
                "compute_type": "bf16",
                "model": "huge",
                "auto_mode": "yolo",
                "log_level": "LOUD",
                "ui_language": "fr",
                "port": 9000,
            }
        ),
        encoding="utf-8",
    )
    assert load_settings(path) == LauncherSettings(port=9000)
    assert "invalid value" in caplog.text


def test_valid_enum_settings_kept(tmp_path):
    path = tmp_path / "l.json"
    path.write_text(json.dumps({"language": "lt", "auto_mode": "high"}), encoding="utf-8")
    s = load_settings(path)
    assert (s.language, s.auto_mode) == ("lt", "high")
    assert set(AUTO_MODES) >= {"off", "high"}


class _FakeProc:
    def __init__(self, code=None):
        self.code = code
        self.pid = 1

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code

    def terminate(self):
        self.code = 0

    def kill(self):
        self.code = 0


def _launcher(tmp_path, popen, events, **kw):
    plan = build_plan(
        LauncherSettings(port=1),
        app_root=tmp_path / "app",
        data_dir=tmp_path / "data",
        base_env={},
        python="py",
        probe=lambda: ["float16"],
    )
    return Launcher(plan, lambda k, m: events.append((k, m)), popen=popen, **kw)


def test_stop_is_idempotent_and_emits_once(tmp_path):
    events: list = []
    launcher = _launcher(tmp_path, lambda *a, **k: _FakeProc(), events)
    threads = [threading.Thread(target=launcher.stop) for _ in range(5)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    launcher.stop()
    assert [k for k, _ in events].count("stopped") == 1


def test_child_exit_names_its_log(tmp_path, monkeypatch):
    events: list = []
    procs = iter([_FakeProc(), _FakeProc(code=7)])  # server alive, audio dies
    monkeypatch.setattr("launcher.process.port_in_use", lambda *_a, **_k: False)
    monkeypatch.setattr("launcher.process.wait_for_ready", lambda *a, **k: "ready")
    launcher = _launcher(tmp_path, lambda *a, **k: next(procs), events, poll_interval=0.01)
    assert launcher.run() == 7
    errors = [m for k, m in events if k == "error"]
    assert errors and "audio.log" in errors[0]
    assert launcher.error_code == "audio_exited"
    assert launcher.error_detail.endswith("audio.log")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows creation flags")
def test_spawn_uses_windows_creation_flags(tmp_path):
    seen: dict = {}

    def popen(cmd, **kw):
        seen.update(kw)
        return _FakeProc()

    launcher = _launcher(tmp_path, popen, [])
    launcher._spawn("server", ["x"])
    launcher.stop()
    flags = seen["creationflags"]
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert flags & subprocess.CREATE_NO_WINDOW


# --- i18n -----------------------------------------------------------------------------


def test_i18n_tables_have_same_keys():
    assert set(i18n.EN) == set(i18n.LT)


def test_i18n_placeholders_match():
    import string

    fmt = string.Formatter()
    for key, en in i18n.EN.items():
        names = {f for _, f, _, _ in fmt.parse(en) if f}
        assert names == {f for _, f, _, _ in fmt.parse(i18n.LT[key]) if f}, key


def test_t_language_switch_and_fallback():
    try:
        i18n.set_language("lt")
        assert i18n.t("btn.start") == "Paleisti"
        assert "ą" in i18n.t("label.ui_language")
        i18n.set_language("en")
        assert i18n.t("btn.start") == "Start"
        assert i18n.t("no.such.key") == "no.such.key"
        assert i18n.t("err.port_in_use", detail=8000).startswith("Port 8000")
    finally:
        i18n.set_language("en")


def test_resolve_ui_language(monkeypatch):
    assert i18n.resolve_ui_language("lt") == "lt"
    assert i18n.resolve_ui_language("en") == "en"
    monkeypatch.setattr(i18n, "detect_windows_ui_language", lambda: "lt")
    assert i18n.resolve_ui_language("auto") == "lt"


def test_detect_language_does_not_crash():
    assert i18n.detect_windows_ui_language() in ("en", "lt")


# --- monitors -------------------------------------------------------------------------


def test_choose_projector_first_non_primary():
    assert choose_projector([PRIMARY, SECOND, THIRD]) == SECOND
    assert choose_projector([SECOND, PRIMARY]) == SECOND


def test_choose_projector_prefers_saved_when_connected():
    assert choose_projector([PRIMARY, SECOND, THIRD], THIRD.device) == THIRD
    assert choose_projector([PRIMARY, SECOND], THIRD.device) == SECOND  # saved unplugged


def test_choose_projector_none_without_second_screen():
    assert choose_projector([PRIMARY]) is None
    assert choose_projector([]) is None


def test_enumerate_monitors_returns_list():
    result = enumerate_monitors()
    assert isinstance(result, list)
    if sys.platform == "win32" and result:
        assert sum(m.primary for m in result) == 1


# --- projector ------------------------------------------------------------------------


def test_build_command_edge_fullscreen_app_window():
    cmd = build_command(
        Browser("C:/msedge.exe", "edge"), display_url(8000), SECOND, Path("C:/prof")
    )
    assert cmd[0] == "C:/msedge.exe"
    assert "--app=http://127.0.0.1:8000/display" in cmd
    # Full screen but not kiosk: F11 / Alt+F4 still work on the projector window.
    assert "--start-fullscreen" in cmd and "--kiosk" not in cmd
    # No sign-in, sync or extensions in the private profile (no extension pop-ups).
    assert {"--disable-sync", "--disable-extensions"} <= set(cmd)
    assert "--disable-features=msImplicitSignin" in cmd
    assert "--no-first-run" in cmd
    assert "--window-position=1920,0" in cmd
    assert any(a.startswith("--user-data-dir=") and "prof" in a for a in cmd)


def test_build_command_chrome_same_flags():
    cmd = build_command(Browser("chrome.exe", "chrome"), "http://x", SECOND, Path("p"))
    assert "--start-fullscreen" in cmd and not any("kiosk" in a for a in cmd)


def test_find_browser_prefers_edge(tmp_path, monkeypatch):
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    for p in (edge, chrome):
        p.parent.mkdir(parents=True)
        p.write_text("")
    monkeypatch.setattr("launcher.projector.shutil.which", lambda _e: None)
    assert find_browser({"ProgramFiles": str(tmp_path)}) == Browser(str(edge), "edge")
    edge.unlink()
    assert find_browser({"ProgramFiles": str(tmp_path)}) == Browser(str(chrome), "chrome")
    chrome.unlink()
    assert find_browser({"ProgramFiles": str(tmp_path)}) is None


def test_projector_requires_monitor_and_never_falls_back():
    opened: list = []
    pb = ProjectorBrowser(find=lambda: Browser("e", "edge"), open_default=opened.append)
    with pytest.raises(ProjectorError) as err:
        pb.open(8000, None)
    assert err.value.code == "no_monitor" and not opened


def test_projector_open_and_close(tmp_path):
    calls: list = []
    proc = _FakeProc()
    pb = ProjectorBrowser(
        find=lambda: Browser("e.exe", "edge"),
        popen=lambda cmd, **kw: calls.append(cmd) or proc,
        profile_dir=tmp_path / "prof",
        find_stale=lambda _profile: [],
    )
    assert pb.open(8123, SECOND) is None
    assert "--app=http://127.0.0.1:8123/display" in calls[0]
    assert (tmp_path / "prof").is_dir()
    pb.close()
    pb.close()  # idempotent


def test_projector_default_browser_fallback_warns():
    opened: list = []
    pb = ProjectorBrowser(find=lambda: None, open_default=opened.append)
    assert pb.open(8000, SECOND) == "browser_fallback"
    assert opened == ["http://127.0.0.1:8000/display"]


# --- view-model -----------------------------------------------------------------------


def test_form_round_trip():
    s = LauncherSettings(
        language="lt",
        input_device="Mic (USB)",
        projector_monitor=SECOND.device,
        port=9100,
        extra_audio_args=["--beam-size", "3"],
        open_display_on_start=False,
    )
    assert settings_from_form(LauncherSettings(), form_from_settings(s)) == s


def test_form_validation():
    form = form_from_settings(LauncherSettings())
    for bad in ("abc", "0", "70000", ""):
        form.port = bad
        with pytest.raises(FormError) as err:
            settings_from_form(LauncherSettings(), form)
        assert err.value.code == "invalid_port"
    form.port = "8000"
    form.extra_args = '"unterminated'
    with pytest.raises(FormError) as err:
        settings_from_form(LauncherSettings(), form)
    assert err.value.code == "invalid_args"


def test_automatic_microphone_needs_a_choice_when_several_are_connected():
    # Live test 2026-10-17: "automatic" took the first input, a silent built-in mic.
    with pytest.raises(FormError) as err:
        check_microphone(None, ["Microphone (Realtek)", "Microphone (Blackmagic Design)"])
    assert err.value.code == "choose_microphone"
    assert describe_error("choose_microphone", "") != describe_error("nope", "")
    check_microphone("Microphone (Blackmagic Design)", ["a", "b"])  # chosen: fine
    check_microphone(None, ["Microphone (USB)"])  # only one: automatic is safe
    check_microphone(None, [])  # list not loaded yet: the pipeline decides


def test_form_keeps_unshown_fields():
    base = LauncherSettings(ffmpeg_dir="C:/ff")
    out = settings_from_form(base, form_from_settings(base))
    assert out.ffmpeg_dir == "C:/ff"


def test_option_lists():
    assert [v for v, _ in projector_options([PRIMARY, SECOND])] == [None, SECOND.device]
    opts = microphone_options(["A", "B"], "Gone")
    assert [v for v, _ in opts] == [None, "Gone", "A", "B"]


def test_status_transitions():
    s = StatusState()
    s = apply_event(s, "status", "Starting server on 127.0.0.1:8000...", None)
    assert s == StatusState("starting", "stopped")
    s = apply_event(s, "ready", "http://127.0.0.1:8000", None)
    s = apply_event(s, "status", "Starting audio pipeline...", None)
    s = apply_event(s, "running", "x", None)
    assert s == StatusState("ready", "running")
    s = apply_event(s, "error", "boom", "audio_exited")
    assert s.audio == "error" and s.server == "ready"
    s = apply_event(s, "stopped", "", None)
    assert s == StatusState("stopped", "error")  # error stays visible after stop


def test_server_error_marks_server():
    s = apply_event(StatusState(), "error", "x", "port_in_use")
    assert s == StatusState("error", "stopped")


def test_error_descriptions_translated():
    try:
        i18n.set_language("lt")
        text = describe_error("port_in_use", "8000")
        assert "8000" in text and "Prievadas" in text
        assert "audio.log" in describe_error("audio_exited", "C:/x/audio.log")
        assert "Projektorius" in describe_error("projector_missing", "")
        i18n.set_language("en")
        assert "Projector not connected" in describe_error("projector_missing", "")
        assert "boom" in describe_error("mystery", "", "boom")
        assert describe_event("stopped", "", None, "") == ("info", "BibleLive stopped.")
        assert describe_event("error", "m", "server_failed", "log")[0] == "error"
    finally:
        i18n.set_language("en")


def test_gpu_description():
    assert "float16" in describe_gpu("cuda", "float16")
    assert "emergency" in describe_gpu("cpu", "int8")


def test_apply_overrides_still_works():
    args = build_parser().parse_args(["--model", "small"])
    assert apply_overrides(LauncherSettings(), args).model == "small"


def test_projector_kills_stale_profile_browsers_before_launch(tmp_path):
    killed: list[int] = []
    order: list[str] = []
    pb = ProjectorBrowser(
        find=lambda: Browser("e.exe", "edge"),
        popen=lambda cmd, **kw: order.append("popen") or _FakeProc(),
        profile_dir=tmp_path / "prof",
        find_stale=lambda profile: order.append(f"find:{profile.name}") or [11, 22],
        kill_pid=lambda pid: killed.append(pid) or order.append("kill"),
    )
    assert pb.open(8000, SECOND) is None
    assert killed == [11, 22]
    assert order == ["find:prof", "kill", "kill", "popen"]


def test_find_profile_processes_is_noop_off_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    assert find_profile_processes(tmp_path) == []


def test_find_profile_processes_parses_pids_and_skips_self(monkeypatch, tmp_path):
    import os

    import launcher.projector as projector

    monkeypatch.setattr(sys, "platform", "win32")
    seen: dict = {}

    def fake_run(cmd, **kw):
        seen["env"] = kw["env"]
        out = f"123 {os.getpid()} not-a-pid 456"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(projector.subprocess, "run", fake_run)
    monkeypatch.setattr(projector.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert find_profile_processes(tmp_path) == [123, 456]
    assert seen["env"]["BL_PROFILE"] == str(tmp_path)


def test_gui_smoke_constructs_when_display_available(tmp_path, monkeypatch):
    for var in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setattr("launcher.gui.load_microphones", lambda settings: [])
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.destroy()
    from launcher.gui import main

    assert main(smoke=True) == 0


def test_projector_close_kills_the_browser_it_handed_off_to(tmp_path):
    # Edge's first process exits after handing the window to another one (not its
    # child): closing must still find that one through the private profile.
    killed: list[int] = []
    running: list[int] = []
    pb = ProjectorBrowser(
        find=lambda: Browser("e.exe", "edge"),
        popen=lambda cmd, **kw: _FakeProc(code=0),
        profile_dir=tmp_path / "prof",
        find_stale=lambda _profile: list(running),
        kill_pid=killed.append,
    )
    assert pb.open(8000, SECOND) is None
    running.append(33)  # the window now lives in another browser process
    pb.close()
    assert killed == [33]
    pb.close()  # closing again does nothing more
    assert killed == [33]
