"""Platform selection, compatibility imports and native-operation dispatch."""

import subprocess
import sys
from types import SimpleNamespace

import pytest
from launcher import monitors, network, platform, process, projector, settings
from launcher.platform import fallback, macos, posix, windows


@pytest.mark.parametrize(
    "name, implementation", [("win32", windows), ("linux", fallback), ("darwin", macos)]
)
def test_current_selects_platform(monkeypatch, name, implementation):
    monkeypatch.setattr(sys, "platform", name)
    assert platform.current() is implementation


def test_legacy_value_imports_are_shared():
    assert monitors.Monitor is platform.Monitor
    assert projector.Browser is platform.Browser


@pytest.mark.parametrize("name", ["linux", "darwin"])
def test_posix_platforms_do_not_query_windows(monkeypatch, tmp_path, name):
    def unexpected(*args, **kwargs):
        pytest.fail("fallback queried Windows")

    monkeypatch.setattr(sys, "platform", name)
    monkeypatch.setattr(windows, "enumerate_monitors", unexpected)
    monkeypatch.setattr(windows, "find_profile_processes", unexpected)
    monkeypatch.setattr(macos, "_enumerate_quartz", lambda: [])
    monkeypatch.setattr(macos.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=""))
    assert monitors.enumerate_monitors() == []
    assert projector.find_profile_processes(tmp_path) == []


def test_legacy_helpers_dispatch_to_current_platform(monkeypatch, tmp_path):
    browser = platform.Browser("browser", "edge")
    monitor = platform.Monitor("second", 1920, 0, 1280, 720, False)
    killed = []
    implementation = SimpleNamespace(
        find_browser=lambda env: browser if env == {"test": "value"} else None,
        build_command=lambda *args: list(args),
        find_profile_processes=lambda profile: [12] if profile == tmp_path else [],
        kill_pid_tree=killed.append,
        enumerate_monitors=lambda: [monitor],
    )
    monkeypatch.setattr(platform, "current", lambda: implementation)
    assert projector.find_browser({"test": "value"}) is browser
    assert projector.build_command(browser, "url", monitor, tmp_path) == [
        browser,
        "url",
        monitor,
        tmp_path,
    ]
    assert projector.find_profile_processes(tmp_path) == [12]
    projector.kill_pid_tree(12)
    assert killed == [12]
    assert monitors.enumerate_monitors() == [monitor]


@pytest.mark.parametrize("error", [AttributeError, OSError])
def test_windows_monitor_query_failure_returns_empty(monkeypatch, error):
    def fail():
        raise error("query failed")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(windows, "_enumerate_windows", fail)
    assert monitors.enumerate_monitors() == []


@pytest.mark.parametrize("name", ["win32", "linux", "darwin"])
def test_projector_platform_spawn_preserves_command_and_flags(monkeypatch, tmp_path, name):
    browser = platform.Browser("edge.exe", "edge")
    monitor = platform.Monitor("second", 1920, 0, 1280, 720, False)
    calls = []
    proc = object()

    def popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return proc

    monkeypatch.setattr(sys, "platform", name)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    result = platform.current().open_projector(
        browser, "http://127.0.0.1:8000/display", monitor, tmp_path, popen=popen
    )
    assert result is proc
    assert calls == [
        (
            projector.build_command(browser, "http://127.0.0.1:8000/display", monitor, tmp_path),
            {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                **({"creationflags": 512} if name == "win32" else {"start_new_session": True}),
            },
        )
    ]


def test_windows_pid_tree_uses_existing_taskkill_command(monkeypatch):
    calls = []
    monkeypatch.setattr(windows.subprocess, "run", lambda *args, **kw: calls.append((args, kw)))
    windows.kill_pid_tree(123)
    assert calls == [
        (
            (["taskkill.exe", "/PID", "123", "/T", "/F"],),
            {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "check": False},
        )
    ]


@pytest.mark.parametrize("name", ["win32", "linux", "darwin"])
def test_platform_folder_locations(monkeypatch, tmp_path, name):
    monkeypatch.setattr(sys, "platform", name)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(settings.Path, "home", lambda: tmp_path / "home")
    data = {
        "win32": tmp_path / "roaming" / "BibleLive",
        "linux": tmp_path / "home" / ".biblelive",
        "darwin": tmp_path / "home" / "Library" / "Application Support" / "BibleLive",
    }[name]
    models = tmp_path / "local" / "BibleLive" / "models" if name == "win32" else data / "models"
    assert settings.app_data_dir() == data
    assert settings.default_settings_path() == data / "launcher.json"
    assert settings.models_dir() == models
    assert platform.current().log_dir(tmp_path / "custom") == tmp_path / "custom" / "logs"
    if name == "win32":
        monkeypatch.delenv("APPDATA")
        monkeypatch.delenv("LOCALAPPDATA")
        assert settings.app_data_dir() == tmp_path / "home" / ".biblelive"
        assert settings.models_dir() == tmp_path / "home" / ".biblelive" / "models"


def test_process_wrappers_dispatch_to_current_platform(monkeypatch, tmp_path):
    calls = []
    log_dir = tmp_path / "platform-logs"
    implementation = SimpleNamespace(
        kill_tree=lambda proc, **kw: calls.append((proc, kw)),
        log_dir=lambda data: log_dir if data == tmp_path else None,
    )
    monkeypatch.setattr(platform, "current", lambda: implementation)
    child = object()
    process.kill_tree(child, grace=0.25)
    assert calls == [(child, {"grace": 0.25})]
    plan = process.build_plan(
        settings.LauncherSettings(speech_engine="whisper"),
        app_root=tmp_path,
        data_dir=tmp_path,
        models_dir=tmp_path / "models",
        base_env={},
        probe=lambda: [],
    )
    assert plan.log_dir == log_dir
    assert plan.env["BIBLELIVE_SERVICE_LOG_DIR"] == str(log_dir / "services")
    assert plan.audio_cmd[plan.audio_cmd.index("--session-log-dir") + 1] == str(
        log_dir / "sessions"
    )


@pytest.mark.parametrize("name", ["win32", "linux", "darwin"])
def test_platform_firewall_and_child_process_options(monkeypatch, name):
    monkeypatch.setattr(sys, "platform", name)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert platform.current().firewall_applies() is (name == "win32")
    assert network.firewall_applies(name) is (name == "win32")
    expected = (
        {"creationflags": 512 | 0x08000000} if name == "win32" else {"start_new_session": True}
    )
    assert platform.current().child_process_options() == expected


def test_network_wrappers_dispatch_without_changing_injected_calls(monkeypatch):
    programs = ["python.exe", "pythonw.exe"]
    run = object()
    shell_execute = object()
    implementation = SimpleNamespace(
        firewall_programs=lambda executable: programs if executable == "py" else [],
        firewall_add_script=lambda progs, system32: (
            "script" if progs is programs and system32 == "system" else ""
        ),
        firewall_rule_exists=lambda runner, system32: runner is run and system32 == "system",
        add_firewall_rule=lambda progs, shell, system32: (
            progs is programs and shell is shell_execute and system32 == "system"
        ),
    )
    monkeypatch.setattr(platform, "current", lambda: implementation)
    assert network.firewall_programs("py") is programs
    assert network.firewall_add_script(programs, "system") == "script"
    assert network.firewall_rule_exists(run, "system") is True
    assert network.add_firewall_rule(programs, shell_execute, "system") is True


def test_windows_runas_keeps_shell_execute_arguments(monkeypatch):
    calls = []
    shell32 = SimpleNamespace(ShellExecuteW=lambda *args: calls.append(args) or 42)
    monkeypatch.setattr(windows.ctypes, "windll", SimpleNamespace(shell32=shell32), raising=False)
    assert windows._shell_execute_runas("cmd.exe", "/c script") == 42
    assert calls == [(None, "runas", "cmd.exe", "/c script", None, 0)]


def test_windows_firewall_defaults_to_system32_and_runas(monkeypatch):
    calls = []
    monkeypatch.setattr(windows, "_SYSTEM32", r"C:\Sys")
    monkeypatch.setattr(windows, "_shell_execute_runas", lambda f, p: calls.append((f, p)) or 42)
    assert windows.add_firewall_rule(["py.exe"]) is True
    assert calls[0][0] == r"C:\Sys\cmd.exe"
    assert r'"C:\Sys\netsh.exe" advfirewall' in calls[0][1]


@pytest.mark.parametrize("module", [fallback, macos])
def test_posix_platforms_have_no_firewall_rule(module):
    assert module.firewall_applies() is False
    assert module.firewall_programs("/usr/bin/python3") == []
    assert module.firewall_rule_exists() is False
    assert module.add_firewall_rule(["python3"]) is False


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX-only")
def test_posix_kill_pid_tree_kills_the_group(monkeypatch):
    calls = []
    monkeypatch.setattr(posix.os, "killpg", lambda pid, sig: calls.append(("pg", pid, sig)))
    posix.kill_pid_tree(321)
    assert calls == [("pg", 321, posix.signal.SIGKILL)]


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX-only")
def test_posix_kill_pid_tree_falls_back_to_the_pid(monkeypatch):
    calls = []

    def gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(posix.os, "killpg", gone)
    monkeypatch.setattr(posix.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    posix.kill_pid_tree(321)
    assert calls == [(321, posix.signal.SIGKILL)]


def test_macos_find_browser_prefers_chrome_in_user_applications(monkeypatch, tmp_path):
    monkeypatch.setattr(macos.shutil, "which", lambda _name: None)
    edge = tmp_path / "Applications" / "Microsoft Edge.app" / "Contents" / "MacOS"
    edge.mkdir(parents=True)
    (edge / "Microsoft Edge").write_text("")
    env = {"HOME": str(tmp_path)}
    real_is_file = macos.Path.is_file
    # /Applications is the real one; only the temp home counts in this test.
    monkeypatch.setattr(
        macos.Path, "is_file", lambda p: str(p).startswith(str(tmp_path)) and real_is_file(p)
    )
    assert macos.find_browser(env) == platform.Browser(str(edge / "Microsoft Edge"), "edge")
    chrome = tmp_path / "Applications" / "Google Chrome.app" / "Contents" / "MacOS"
    chrome.mkdir(parents=True)
    (chrome / "Google Chrome").write_text("")
    assert macos.find_browser(env) == platform.Browser(str(chrome / "Google Chrome"), "chrome")


def test_macos_find_browser_none(monkeypatch, tmp_path):
    monkeypatch.setattr(macos.Path, "is_file", lambda p: False)
    monkeypatch.setattr(macos.shutil, "which", lambda _name: None)
    assert macos.find_browser({"HOME": str(tmp_path)}) is None


def test_macos_profile_processes_match_only_the_profile(monkeypatch, tmp_path):
    profile = tmp_path / "display-profile"
    out = "\n".join(
        [
            f"  101 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            f"--user-data-dir={profile} --app=x",
            f"  102 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --type=gpu "
            f"--user-data-dir={profile}",
            "  103 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            f"  {macos.os.getpid()} python launcher {profile}",
            "garbage line",
        ]
    )
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(stdout=out)

    monkeypatch.setattr(macos.subprocess, "run", run)
    assert macos.find_profile_processes(profile) == [101, 102]
    assert calls == [["/bin/ps", "-axww", "-o", "pid=,command="]]


def test_macos_profile_process_query_failure_returns_empty(monkeypatch, tmp_path):
    def fail(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(macos.subprocess, "run", fail)
    assert macos.find_profile_processes(tmp_path) == []


@pytest.mark.parametrize("error", [AttributeError, OSError])
def test_macos_monitor_query_failure_returns_empty(monkeypatch, error):
    def fail():
        raise error("query failed")

    monkeypatch.setattr(macos, "_enumerate_quartz", fail)
    assert macos.enumerate_monitors() == []


@pytest.mark.skipif(sys.platform != "darwin", reason="needs Quartz (macOS)")
def test_macos_real_monitor_query():
    found = macos.enumerate_monitors()
    assert found, "a Mac always has at least one active display"
    assert found[0].primary
    assert (found[0].x, found[0].y) == (0, 0)
