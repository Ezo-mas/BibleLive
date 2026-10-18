"""Launcher command/env building, CLI precedence, readiness wait, port check."""

import os
import socket

from launcher.cli import apply_overrides, build_parser, main
from launcher.process import build_plan, port_in_use, wait_for_ready
from launcher.settings import LauncherSettings, save_settings

GPU = lambda: ["float16", "int8"]  # noqa: E731


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
