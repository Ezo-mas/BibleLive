"""Platform selection, compatibility imports and native-operation dispatch."""

import subprocess
import sys
from types import SimpleNamespace

import pytest
from launcher import monitors, platform, projector
from launcher.platform import fallback, windows


@pytest.mark.parametrize(
    "name, implementation", [("win32", windows), ("linux", fallback), ("darwin", fallback)]
)
def test_current_selects_platform(monkeypatch, name, implementation):
    monkeypatch.setattr(sys, "platform", name)
    assert platform.current() is implementation


def test_legacy_value_imports_are_shared():
    assert monitors.Monitor is platform.Monitor
    assert projector.Browser is platform.Browser


@pytest.mark.parametrize("name", ["linux", "darwin"])
def test_fallback_does_not_query_native_monitors_or_processes(monkeypatch, tmp_path, name):
    def unexpected(*args, **kwargs):
        pytest.fail("fallback queried Windows")

    monkeypatch.setattr(sys, "platform", name)
    monkeypatch.setattr(windows, "enumerate_monitors", unexpected)
    monkeypatch.setattr(windows, "find_profile_processes", unexpected)
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
