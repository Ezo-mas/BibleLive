"""Launcher command/env building, CLI precedence, readiness wait, port check."""

import os
import socket
import subprocess
import sys
import threading
import time

import launcher.process as process
import pytest
from launcher.cli import apply_overrides, build_parser, main
from launcher.process import build_plan, port_in_use, wait_for_ready
from launcher.settings import LauncherSettings, save_settings

GPU = lambda: ["float16", "int8"]  # noqa: E731
REAL_KILLPG = getattr(os, "killpg", None)


@pytest.fixture(autouse=True)
def fake_process_groups(monkeypatch):
    # All Popen children in this module are fakes, including their process groups.
    monkeypatch.setattr(process.os, "killpg", lambda *args: None, raising=False)


def _plan(tmp_path, settings=None, **kw):
    return build_plan(
        settings or LauncherSettings(),
        app_root=tmp_path / "app",
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",  # empty: never the developer's cached models
        base_env={"PATH": "/usr/bin"},
        python="py",
        probe=kw.pop("probe", GPU),
        **kw,
    )


def test_commands_and_env(tmp_path):
    plan = _plan(tmp_path, LauncherSettings(port=9100, language="lt", input_device="Mic"))
    assert plan.server_cmd == ["py", "-m", "core.server"]
    cmd = plan.audio_cmd
    assert cmd[cmd.index("--server") + 1] == "http://127.0.0.1:9100"
    assert cmd[cmd.index("--device") + 1] == "cuda"
    assert cmd[cmd.index("--compute-type") + 1] == "float16"
    assert cmd[cmd.index("--model") + 1] == "large-v3-turbo"
    assert cmd[cmd.index("--input-device") + 1] == "Mic"
    assert plan.env["BIBLELIVE_SERVICE_LOG_DIR"] == str(tmp_path / "data" / "logs" / "services")
    assert plan.env["BIBLELIVE_HOST"] == "127.0.0.1"
    assert plan.env["BIBLELIVE_PORT"] == "9100"
    assert plan.env["BIBLELIVE_SETTINGS_PATH"] == str(tmp_path / "data" / "server_settings.json")
    # The server opens the start language's Bible (fresh install showed KJV).
    assert plan.env["BIBLELIVE_START_LANGUAGE"] == "lt"
    assert plan.env["PYTHONPATH"].split(os.pathsep)[0] == str(tmp_path / "app" / "backend")
    assert plan.ready_url == "http://127.0.0.1:9100/readyz"


def test_huggingface_advice_stays_out_of_the_logs(tmp_path):
    plan = _plan(tmp_path)
    assert plan.env["HF_HUB_DISABLE_SYMLINKS_WARNING"] == "1"
    assert plan.env["HF_HUB_VERBOSITY"] == "error"


def test_ui_language_is_handed_to_the_server(tmp_path, monkeypatch):
    assert _plan(tmp_path, LauncherSettings(ui_language="lt")).env["BIBLELIVE_UI_LANGUAGE"] == "lt"
    assert _plan(tmp_path, LauncherSettings(ui_language="en")).env["BIBLELIVE_UI_LANGUAGE"] == "en"
    # "auto" (the default) is resolved from the Windows UI language, never passed on as "auto".
    monkeypatch.setattr("launcher.i18n.detect_windows_ui_language", lambda: "lt")
    assert _plan(tmp_path).env["BIBLELIVE_UI_LANGUAGE"] == "lt"


def test_cpu_fallback_plan(tmp_path):
    plan = _plan(tmp_path, probe=lambda: [])
    assert plan.device == "cpu" and plan.compute_type == "int8"
    assert plan.warnings


def test_ffmpeg_dir_prepended_only_if_present(tmp_path):
    assert _plan(tmp_path).env["PATH"] == "/usr/bin"
    ffmpeg = tmp_path / "app" / "ffmpeg"
    ffmpeg.mkdir(parents=True)
    assert _plan(tmp_path).env["PATH"].split(os.pathsep)[0] == str(ffmpeg)
    custom = tmp_path / "custom"
    custom.mkdir()
    plan = _plan(tmp_path, LauncherSettings(ffmpeg_dir=str(custom)))
    assert plan.env["PATH"].split(os.pathsep)[0] == str(custom)


def test_cli_flags_override_saved_settings(tmp_path):
    saved = LauncherSettings(language="lt", port=9000, model="small")
    args = build_parser().parse_args(["--port", "9500", "--device", "cpu"])
    merged = apply_overrides(saved, args)
    assert (merged.port, merged.device) == (9500, "cpu")
    assert (merged.language, merged.model) == ("lt", "small")  # untouched


def test_extra_audio_args_after_double_dash():
    args = build_parser().parse_args(["--", "--beam-size", "3"])
    assert apply_overrides(LauncherSettings(), args).extra_audio_args == ["--beam-size", "3"]


def test_dry_run_and_save(tmp_path, capsys):
    path = tmp_path / "l.json"
    save_settings(LauncherSettings(language="lt"), path)
    code = main(["--settings-file", str(path), "--port", "9200", "--dry-run", "--save"])
    out = capsys.readouterr().out
    assert code == 0
    assert "BIBLELIVE_PORT=9200" in out and "http://127.0.0.1:9200" in out
    assert '"lt"' in out
    assert '"port": 9200' in path.read_text(encoding="utf-8")


def test_wait_for_ready_outcomes():
    t = [0.0]
    clock = lambda: t[0]  # noqa: E731

    def sleep(s):
        t[0] += s

    calls = []

    def check(url):
        calls.append(url)
        return len(calls) >= 3

    assert wait_for_ready("u", lambda: True, check=check, sleep=sleep, clock=clock) == "ready"
    assert wait_for_ready("u", lambda: False, check=lambda u: False, sleep=sleep, clock=clock) == (
        "died"
    )
    assert (
        wait_for_ready(
            "u", lambda: True, timeout=2, check=lambda u: False, sleep=sleep, clock=clock
        )
        == "timeout"
    )


def test_port_in_use_detects_listener():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        assert port_in_use(port)
    assert not port_in_use(port)


class _FakeProc:
    def __init__(self, exit_code=None):
        self.exit_code = exit_code
        self.pid = 1

    def poll(self):
        return self.exit_code

    def wait(self, timeout=None):
        return self.exit_code

    def terminate(self):
        self.exit_code = 0

    def kill(self):
        self.exit_code = 0


def test_launcher_reports_early_server_death(tmp_path):
    from launcher.process import Launcher

    plan = _plan(tmp_path, LauncherSettings(port=_free_port()))
    events = []
    launcher = Launcher(
        plan,
        lambda k, m: events.append((k, m)),
        popen=lambda *a, **k: _FakeProc(exit_code=3),
        ready_timeout=2,
        poll_interval=0.01,
    )
    assert launcher.run() == 1
    kinds = [k for k, _ in events]
    assert "error" in kinds and "running" not in kinds and kinds[-1] == "stopped"
    assert (plan.log_dir / "server.log").exists()


def test_launcher_port_in_use_is_clear_error(tmp_path):
    from launcher.process import Launcher

    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        plan = _plan(tmp_path, LauncherSettings(port=srv.getsockname()[1]))
        events = []
        spawned = []
        launcher = Launcher(
            plan, lambda k, m: events.append((k, m)), popen=lambda *a, **k: spawned.append(1)
        )
        assert launcher.run() == 2
    assert not spawned
    assert any(k == "error" and "already in use" in m for k, m in events)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Supervision:
    """Single test thread owns fake time, child lists and event history."""

    def __init__(self, tmp_path, monkeypatch, on_event):
        self.now = 0.0
        self.children = {"server": [], "audio": []}
        self.events = []
        self.killed = []
        self.order = []
        self.ready_calls = []
        self.crashes = []
        monkeypatch.setattr(process, "port_in_use", lambda *_: False)
        monkeypatch.setattr(process, "kill_tree", self.kill)
        monkeypatch.setattr(process, "wait_for_ready", self.ready)
        self.launcher = process.Launcher(
            _plan(tmp_path), self.event, popen=self.spawn, clock=lambda: self.now
        )
        monkeypatch.setattr(self.launcher._stop, "wait", self.wait)
        self.on_event = on_event

    def spawn(self, cmd, **kw):
        name = "server" if cmd == self.launcher.plan.server_cmd else "audio"
        proc = _FakeProc()
        self.order.append(("spawn", proc))
        self.children[name].append((self.now, proc))
        return proc

    def kill(self, proc):
        self.order.append(("kill", proc))
        self.killed.append(proc)
        proc.exit_code = 0

    def ready(self, *args, **kw):
        self.ready_calls.append(self.now)
        return "ready"

    def event(self, kind, message):
        self.events.append((kind, message))
        self.on_event(self, kind)

    def wait(self, delay):
        self.now += delay
        for proc, when, code in self.crashes[:]:
            if self.now >= when:
                proc.exit_code = code
                self.crashes.remove((proc, when, code))
        assert self.now < 1000, "supervision did not finish"
        return self.launcher._stop.is_set()

    def crash_after(self, child, delay, code=7):
        self.crashes.append((self.children[child][-1][1], self.now + delay, code))


@pytest.mark.parametrize("child", ["audio", "server"])
@pytest.mark.parametrize("delay", [5, 30])
def test_child_dying_before_first_healthy_minute_is_fatal(tmp_path, monkeypatch, child, delay):
    def event(run, kind):
        if kind == "running":
            run.crash_after(child, delay)

    run = _Supervision(tmp_path, monkeypatch, event)
    assert run.launcher.run() == 7
    assert run.launcher.error_code == f"{child}_exited"
    assert run.launcher.error_detail.endswith(f"{child}.log")
    assert len(run.children[child]) == 1
    assert not any(kind.endswith("_restarting") for kind, _ in run.events)
    assert len(run.killed) == 2
    assert run.events[-1][0] == "stopped"


def test_server_health_period_starts_when_server_spawns(tmp_path, monkeypatch):
    def event(run, kind):
        if kind == "running":
            run.crash_after("server", 30)
        elif kind == "ready" and len(run.children["server"]) == 2:
            run.launcher.request_stop()

    run = _Supervision(tmp_path, monkeypatch, event)

    def ready(*args, **kwargs):
        if len(run.children["server"]) == 1:
            run.now += 35
        return "ready"

    monkeypatch.setattr(process, "wait_for_ready", ready)
    assert run.launcher.run() == 0
    assert len(run.children["server"]) == 2
    assert ("server_restarting", process.RestartMessage("server", 1, 1)) in run.events


def test_audio_can_qualify_while_restarted_server_waits_for_readiness(tmp_path, monkeypatch):
    def event(run, kind):
        if kind == "running":
            if len(run.children["audio"]) == 1:
                run.crash_after("server", 26)
            else:
                run.launcher.request_stop()

    run = _Supervision(tmp_path, monkeypatch, event)

    def ready(url, is_alive, **kwargs):
        if len(run.children["server"]) == 1:
            run.now = 35
        else:
            run.now = 95  # Audio has now been alive for 60 seconds.
            assert is_alive()
            run.now = 96
            run.children["audio"][-1][1].exit_code = 7
        return "ready"

    monkeypatch.setattr(process, "wait_for_ready", ready)
    assert run.launcher.run() == 0
    assert len(run.children["audio"]) == 2
    assert ("audio_restarting", process.RestartMessage("audio", 1, 1)) in run.events


@pytest.mark.parametrize("child", ["audio", "server"])
def test_running_child_restarts_after_first_delay(tmp_path, monkeypatch, child):
    def event(run, kind):
        if kind == "running" and len(run.children[child]) == 1:
            run.crash_after(child, 61)
        if kind == ("ready" if child == "server" else "running") and len(run.children[child]) == 2:
            run.launcher.request_stop()

    run = _Supervision(tmp_path, monkeypatch, event)
    assert run.launcher.run() == 0
    assert (f"{child}_restarting", process.RestartMessage(child, 1, 1)) in run.events
    assert len(run.children[child]) == 2
    assert run.children[child][1][0] == 62
    old = run.children[child][0][1]
    new = run.children[child][1][1]
    assert run.order.index(("kill", old)) < run.order.index(("spawn", new))
    assert len(run.children["server" if child == "audio" else "audio"]) == 1
    assert len(run.ready_calls) == (2 if child == "server" else 1)


@pytest.mark.parametrize("child", ["audio", "server"])
def test_three_fast_exits_give_up(tmp_path, monkeypatch, child):
    def event(run, kind):
        if kind == "running" or (kind == "ready" and len(run.children[child]) > 1):
            if len(run.children[child]) == 1:
                run.crash_after(child, 61)
            else:
                run.children[child][-1][1].exit_code = 7

    run = _Supervision(tmp_path, monkeypatch, event)
    assert run.launcher.run() == 7
    assert len(run.children[child]) == 3
    assert [m for k, m in run.events if k == f"{child}_restarting"] == [
        process.RestartMessage(child, 1, 1),
        process.RestartMessage(child, 2, 2),
    ]
    assert run.launcher.error_code == f"{child}_restart_gave_up"
    assert run.launcher.error_detail.endswith(f"{child}.log")
    assert run.events[-1][0] == "stopped"


def test_restarted_server_death_before_readiness_counts_toward_limit(tmp_path, monkeypatch):
    def event(run, kind):
        if kind == "running":
            run.crash_after("server", 61, 4)

    run = _Supervision(tmp_path, monkeypatch, event)

    def ready(*args, **kw):
        if len(run.children["server"]) > 1:
            run.children["server"][-1][1].exit_code = 4
            return "died"
        return "ready"

    monkeypatch.setattr(process, "wait_for_ready", ready)
    assert run.launcher.run() == 4
    assert len(run.children["server"]) == 3
    assert run.launcher.error_code == "server_restart_gave_up"


def test_healthy_run_resets_backoff(tmp_path, monkeypatch):
    def event(run, kind):
        if kind == "running":
            if len(run.children["audio"]) == 3:
                run.launcher.request_stop()
            else:
                if len(run.children["audio"]) == 1:
                    run.crash_after("audio", 61)
                else:
                    run.now += 61
                    run.children["audio"][-1][1].exit_code = 7

    run = _Supervision(tmp_path, monkeypatch, event)
    assert run.launcher.run() == 0
    assert [m for k, m in run.events if k == "audio_restarting"] == [
        process.RestartMessage("audio", 1, 1),
        process.RestartMessage("audio", 1, 1),
    ]


def test_backoff_caps_at_thirty_seconds(tmp_path, monkeypatch):
    def event(run, kind):
        if kind == "running":
            count = len(run.children["audio"])
            if count == 8:
                run.launcher.request_stop()
            elif count == 1:
                run.crash_after("audio", 61)
            else:
                # Exits spaced by 31 s avoid the three-exits window without 60 s uptime.
                run.now += 31
                run.children["audio"][-1][1].exit_code = 7

    run = _Supervision(tmp_path, monkeypatch, event)
    assert run.launcher.run() == 0
    assert [m for k, m in run.events if k == "audio_restarting"] == [
        process.RestartMessage("audio", 1, 1),
        process.RestartMessage("audio", 2, 2),
        process.RestartMessage("audio", 3, 4),
        process.RestartMessage("audio", 4, 8),
        process.RestartMessage("audio", 5, 16),
        process.RestartMessage("audio", 6, 30),
        process.RestartMessage("audio", 7, 30),
    ]


def test_stop_during_backoff_is_prompt_and_does_not_spawn(tmp_path, monkeypatch):
    entered_backoff = threading.Event()
    qualified_poll = threading.Event()
    qualified_polls = [0]
    clock_offset = [0]
    children = []
    monkeypatch.setattr(process, "port_in_use", lambda *_: False)
    monkeypatch.setattr(process, "wait_for_ready", lambda *a, **k: "ready")
    monkeypatch.setattr(process, "kill_tree", lambda proc: proc.terminate())

    def spawn(*args, **kwargs):
        proc = _FakeProc()
        children.append(proc)
        if len(children) == 2:

            def poll():
                if clock_offset[0]:
                    qualified_polls[0] += 1
                    if qualified_polls[0] == 2:
                        qualified_poll.set()
                return proc.exit_code

            proc.poll = poll
        return proc

    def event(kind, message):
        if kind == "running":
            clock_offset[0] = 61
        elif kind == "audio_restarting":
            entered_backoff.set()

    launcher = process.Launcher(
        _plan(tmp_path),
        event,
        popen=spawn,
        poll_interval=0.01,
        clock=lambda: time.monotonic() + clock_offset[0],
    )
    result = []
    thread = threading.Thread(target=lambda: result.append(launcher.run()))
    thread.start()
    try:
        assert qualified_poll.wait(2)
        children[-1].exit_code = 7
        assert entered_backoff.wait(2)
        before = time.perf_counter()
        launcher.stop()
        launcher.stop()
        thread.join(2)
        assert not thread.is_alive()
        assert time.perf_counter() - before < 2
        assert result == [0]
        assert len(children) == 2
        assert all(p.poll() is not None for p in children)
    finally:
        launcher.request_stop()
        thread.join(2)


@pytest.mark.parametrize("exited", [False, True])
def test_posix_kills_group_even_after_leader_exits(monkeypatch, exited):
    calls = []
    proc = _FakeProc(exit_code=7 if exited else None)
    monkeypatch.setattr(process.sys, "platform", "linux")
    monkeypatch.setattr(
        process.os,
        "getpgid",
        lambda pid: pytest.fail("must not look up the reaped leader"),
        raising=False,
    )
    monkeypatch.setattr(process.os, "killpg", lambda *args: calls.append(args), raising=False)
    monkeypatch.setattr(process.signal, "SIGKILL", 9, raising=False)
    process.kill_tree(proc)
    assert calls == [(proc.pid, process.signal.SIGTERM), (proc.pid, 9)]


def test_posix_kill_escalates_after_timeout(monkeypatch):
    calls = []
    proc = _FakeProc()
    waits = []

    def wait(timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    proc.wait = wait
    monkeypatch.setattr(process.sys, "platform", "linux")
    monkeypatch.setattr(process.os, "killpg", lambda *args: calls.append(args), raising=False)
    monkeypatch.setattr(process.signal, "SIGKILL", 9, raising=False)
    process.kill_tree(proc, grace=0.25)
    assert calls == [(proc.pid, process.signal.SIGTERM), (proc.pid, 9)]
    assert waits == [0.25, 0.25]


def test_posix_spawn_starts_new_session(tmp_path, monkeypatch):
    seen = {}
    launcher = process.Launcher(
        _plan(tmp_path), popen=lambda *a, **kw: seen.update(kw) or _FakeProc()
    )
    monkeypatch.setattr(process.sys, "platform", "linux")
    launcher._spawn("server", ["fake"])
    launcher.stop()
    assert seen["start_new_session"] is True
    assert "creationflags" not in seen


def test_stop_before_start_never_spawns(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "port_in_use", lambda *_: False)
    launcher = process.Launcher(_plan(tmp_path), popen=lambda *a, **kw: pytest.fail("spawned"))
    launcher.stop()
    assert launcher.run() == 0


@pytest.mark.parametrize("child", ["server", "audio"])
def test_signal_stop_during_spawn_cleans_up_child(tmp_path, monkeypatch, child):
    children = []
    monkeypatch.setattr(process, "port_in_use", lambda *_: False)
    monkeypatch.setattr(process, "wait_for_ready", lambda *a, **k: "ready")
    monkeypatch.setattr(process, "kill_tree", lambda proc: proc.terminate())

    def spawn(cmd, **kwargs):
        proc = _FakeProc()
        children.append(proc)
        if cmd == getattr(launcher.plan, f"{child}_cmd"):
            # A Python signal handler can run on the run thread inside Popen.
            launcher.request_stop()
        return proc

    launcher = process.Launcher(_plan(tmp_path), popen=spawn)
    assert launcher.run() == 0
    assert len(children) == (1 if child == "server" else 2)
    assert all(proc.poll() is not None for proc in children)


def test_cli_opens_page_only_on_initial_server_readiness(tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr("launcher.cli.webbrowser.open", opened.append)
    monkeypatch.setattr("launcher.cli.signal.signal", lambda *a: None)

    class FakeLauncher:
        """The CLI test thread owns this fake's event callback."""

        def __init__(self, plan, on_event):
            self.on_event = on_event

        def run(self):
            self.on_event("ready", "url")
            self.on_event("server_restarting", process.RestartMessage("server", 1, 1))
            self.on_event("ready", "url")
            return 0

    monkeypatch.setattr("launcher.cli.Launcher", FakeLauncher)
    assert main(["--settings-file", str(tmp_path / "settings.json"), "--open", "operator"]) == 0
    assert len(opened) == 1
    assert "[server_restarting] Server exited; restarting in 1 s (attempt 1)" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize("failure_count", [1, 3])
def test_stop_during_child_cleanup_emits_no_restart_or_error(tmp_path, monkeypatch, failure_count):
    def event(run, kind):
        if kind == "running":
            if len(run.children["audio"]) == 1:
                run.crash_after("audio", 61)
            else:
                run.children["audio"][-1][1].exit_code = 7

    run = _Supervision(tmp_path, monkeypatch, event)
    original_kill = run.kill
    kills = []

    def kill(proc):
        kills.append(proc)
        original_kill(proc)
        if len(kills) == failure_count:
            run.launcher.request_stop()

    monkeypatch.setattr(process, "kill_tree", kill)
    assert run.launcher.run() == 0
    assert len([kind for kind, _ in run.events if kind == "audio_restarting"]) == (
        failure_count - 1
    )
    assert not any(kind == "error" for kind, _ in run.events)
    assert run.events[-1][0] == "stopped"


@pytest.mark.parametrize("child", ["audio", "server"])
def test_stop_during_early_child_poll_emits_no_error(tmp_path, monkeypatch, child):
    def event(run, kind):
        if kind == "running":

            def poll():
                run.launcher.request_stop()
                return 7

            run.children[child][-1][1].poll = poll

    run = _Supervision(tmp_path, monkeypatch, event)
    assert run.launcher.run() == 0
    assert not any(kind == "error" for kind, _ in run.events)
    assert run.events[-1][0] == "stopped"


def test_stop_continues_when_audio_cleanup_fails(tmp_path, monkeypatch, caplog):
    launcher = process.Launcher(_plan(tmp_path))
    audio, server = _FakeProc(), _FakeProc()
    launcher._audio, launcher._server = audio, server
    launcher.plan.log_dir.mkdir(parents=True)
    log = (launcher.plan.log_dir / "audio.log").open("wb")
    launcher._logs.append(log)
    killed = []
    events = []
    launcher._on_event = lambda kind, message: events.append(kind)

    def kill(proc):
        killed.append(proc)
        if proc is audio:
            raise OSError("cleanup failed")

    monkeypatch.setattr(process, "kill_tree", kill)
    launcher.stop()
    launcher.stop()
    assert killed == [audio, server]
    assert log.closed
    assert events == ["stopped"]
    assert "cleanup failed" in caplog.text


@pytest.mark.parametrize("error", [ProcessLookupError, PermissionError])
def test_posix_falls_back_to_leader_when_group_signals_fail(monkeypatch, error):
    calls = []
    proc = _FakeProc()
    monkeypatch.setattr(process.sys, "platform", "linux")
    monkeypatch.setattr(process.signal, "SIGKILL", 9, raising=False)
    waits = []

    def killpg(*args):
        calls.append(args)
        raise error()

    def wait(timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired("fake", timeout)
        return proc.exit_code

    proc.wait = wait
    proc.terminate = lambda: calls.append("terminate")
    proc.kill = lambda: calls.append("kill")
    monkeypatch.setattr(process.os, "killpg", killpg, raising=False)
    process.kill_tree(proc)
    assert calls == [(proc.pid, process.signal.SIGTERM), "terminate", (proc.pid, 9), "kill"]


@pytest.mark.parametrize("error", [ProcessLookupError, PermissionError])
def test_posix_suppresses_group_signal_errors(monkeypatch, error):
    calls = []
    monkeypatch.setattr(process.sys, "platform", "linux")
    monkeypatch.setattr(process.signal, "SIGKILL", 9, raising=False)

    def killpg(*args):
        calls.append(args)
        raise error()

    monkeypatch.setattr(process.os, "killpg", killpg, raising=False)
    process.kill_tree(_FakeProc(exit_code=7))
    assert len(calls) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_posix_kills_descendant_after_reaping_leader(monkeypatch):
    import contextlib
    import select
    import signal

    leader_code = (
        "import subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print(child.pid, flush=True)"
    )
    assert REAL_KILLPG is not None
    monkeypatch.setattr(process.os, "killpg", REAL_KILLPG)
    with subprocess.Popen(
        [sys.executable, "-c", leader_code],
        stdout=subprocess.PIPE,
        start_new_session=True,
    ) as leader:
        try:
            assert leader.stdout is not None
            child_pid = int(leader.stdout.readline())
            assert child_pid > 0
            assert leader.wait(timeout=5) == 0
            assert leader.poll() == 0  # The leader has been reaped.
            process.kill_tree(leader, grace=1)
            readable, _, _ = select.select([leader.stdout], [], [], 5)
            assert readable and os.read(leader.stdout.fileno(), 1) == b""
        finally:
            with contextlib.suppress(ProcessLookupError):
                REAL_KILLPG(leader.pid, signal.SIGKILL)


def test_cli_registers_shutdown_signals_and_reports_network_once(tmp_path, monkeypatch, capsys):
    import signal

    plan = _plan(tmp_path)
    plan.network_token = "test-token"
    handlers = {}
    stops = []
    monkeypatch.setattr("launcher.cli.build_plan", lambda *a, **kw: plan)
    monkeypatch.setattr(
        "launcher.cli.signal.signal", lambda sig, handler: handlers.update({sig: handler})
    )
    monkeypatch.setattr("launcher.cli.network_lines", lambda *args: ["token: test-token"])
    monkeypatch.setattr("launcher.cli.firewall_hint", lambda: None)

    class FakeLauncher:
        """The CLI test thread owns the callback and stop list."""

        def __init__(self, plan, on_event):
            self.on_event = on_event

        def request_stop(self):
            stops.append(True)

        def run(self):
            self.on_event("ready", "url")
            self.on_event("ready", "url")
            return 0

    monkeypatch.setattr("launcher.cli.Launcher", FakeLauncher)
    assert main(["--settings-file", str(tmp_path / "settings.json")]) == 0
    assert capsys.readouterr().out.count("[network] token: test-token") == 1
    for name in ("SIGINT", "SIGBREAK", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            handlers[getattr(signal, name)](None, None)
    assert len(stops) == len(handlers)
