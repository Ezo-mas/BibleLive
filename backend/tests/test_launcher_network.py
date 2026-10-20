"""Launcher network mode: operator token, server env, addresses, CLI and window wiring."""

import gc
import json
import subprocess
import sys
import threading
from dataclasses import replace
from urllib.parse import parse_qs

import pytest
from launcher import cli, i18n, network
from launcher.cli import (
    apply_overrides,
    build_parser,
    firewall_hint,
    format_plan,
    main,
    network_lines,
    page_url,
)
from launcher.network import (
    FIREWALL_RULE_NAME,
    TOKEN_ALPHABET,
    TOKEN_LENGTH,
    add_firewall_rule,
    allow_through_firewall,
    ensure_network_token,
    firewall_add_script,
    firewall_applies,
    firewall_programs,
    firewall_rule_exists,
    lan_ip,
    new_token,
    operator_url_with_token,
    wait_until,
)
from launcher.process import build_plan
from launcher.settings import LauncherSettings, load_settings, save_settings
from launcher.viewmodel import (
    NetworkView,
    firewall_view,
    form_from_settings,
    network_view,
    settings_from_form,
)

from core.config import Config


def _plan(tmp_path, settings, base_env=None):
    return build_plan(
        settings,
        app_root=tmp_path / "app",
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",  # empty: never the developer's cached models
        base_env=base_env if base_env is not None else {"PATH": "/usr/bin"},
        python="py",
        probe=lambda: ["float16"],
    )


# --- settings and token ---------------------------------------------------------------


def test_network_mode_is_off_by_default():
    s = LauncherSettings()
    assert s.allow_network is False and s.network_token is None


def test_network_settings_round_trip_and_bad_values_fall_back(tmp_path):
    path = tmp_path / "launcher.json"
    s = LauncherSettings(allow_network=True, network_token="abcd2345wxyz")
    save_settings(s, path)
    assert load_settings(path) == s
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["allow_network"] is True and data["network_token"] == "abcd2345wxyz"
    path.write_text(json.dumps({"allow_network": "yes", "network_token": 7}), encoding="utf-8")
    assert load_settings(path) == LauncherSettings()


def test_token_uses_the_unambiguous_alphabet():
    assert len(TOKEN_ALPHABET) == len(set(TOKEN_ALPHABET))
    for ch in "il01o":
        assert ch not in TOKEN_ALPHABET
    token = new_token()
    assert len(token) == TOKEN_LENGTH == 12
    assert set(token) <= set(TOKEN_ALPHABET)


def test_new_tokens_are_random():
    assert len({new_token() for _ in range(20)}) == 20


def test_token_is_made_once_when_network_mode_is_on_and_kept():
    off = LauncherSettings()
    assert ensure_network_token(off) == off  # off: no token is made
    on = ensure_network_token(LauncherSettings(allow_network=True))
    assert on.network_token and len(on.network_token) == TOKEN_LENGTH
    assert ensure_network_token(on) == on  # reused, not regenerated
    emptied = ensure_network_token(LauncherSettings(allow_network=True, network_token=""))
    assert emptied.network_token


# --- what the server process gets -----------------------------------------------------


def test_network_off_keeps_loopback_and_no_auth(tmp_path):
    base = {"PATH": "/usr/bin", "BIBLELIVE_HOST": "0.0.0.0"}
    plan = _plan(tmp_path, LauncherSettings(), base_env=base)
    assert plan.env["BIBLELIVE_HOST"] == "127.0.0.1"
    assert "BIBLELIVE_REQUIRE_AUTH" not in plan.env
    assert "BIBLELIVE_OPERATOR_TOKEN" not in plan.env
    assert plan.network_token is None


def test_network_on_listens_on_all_interfaces_with_the_token(tmp_path):
    settings = LauncherSettings(port=9100, allow_network=True, network_token="tok123abc")
    plan = _plan(tmp_path, settings)
    assert plan.env["BIBLELIVE_HOST"] == "0.0.0.0"
    assert plan.env["BIBLELIVE_REQUIRE_AUTH"] == "true"
    assert plan.env["BIBLELIVE_OPERATOR_TOKEN"] == "tok123abc"
    assert plan.network_token == "tok123abc"
    # The launcher's own readiness check and the audio pipeline still use loopback.
    assert plan.ready_url == "http://127.0.0.1:9100/readyz"
    cmd = plan.audio_cmd
    assert cmd[cmd.index("--server") + 1] == "http://127.0.0.1:9100"


def test_network_on_overrides_inherited_auth_settings(tmp_path):
    base = {
        "PATH": "/usr/bin",
        "BIBLELIVE_OPERATOR_TOKEN": "old",
        "BIBLELIVE_REQUIRE_AUTH": "false",
    }
    plan = _plan(tmp_path, LauncherSettings(allow_network=True, network_token="new"), base)
    assert plan.env["BIBLELIVE_OPERATOR_TOKEN"] == "new"
    assert plan.env["BIBLELIVE_REQUIRE_AUTH"] == "true"


def test_network_on_without_a_token_is_refused(tmp_path):
    with pytest.raises(ValueError, match="token"):
        _plan(tmp_path, LauncherSettings(allow_network=True))


def test_server_config_reads_the_names_the_launcher_sets(tmp_path, monkeypatch):
    plan = _plan(tmp_path, LauncherSettings(allow_network=True, network_token="tok123abc"))
    for name in ("BIBLELIVE_HOST", "BIBLELIVE_REQUIRE_AUTH", "BIBLELIVE_OPERATOR_TOKEN"):
        monkeypatch.setenv(name, plan.env[name])
    cfg = Config()
    assert cfg.HOST == "0.0.0.0"
    assert cfg.REQUIRE_AUTH is True
    assert cfg.OPERATOR_TOKEN == "tok123abc"


# --- addresses ------------------------------------------------------------------------


def test_lan_ip_prefers_the_outgoing_interface(monkeypatch):
    monkeypatch.setattr(network, "_route_ip", lambda: "192.168.1.23")
    monkeypatch.setattr(network, "_host_ips", lambda: ["10.9.9.9"])
    assert lan_ip() == "192.168.1.23"


def test_lan_ip_falls_back_to_host_addresses(monkeypatch):
    monkeypatch.setattr(network, "_route_ip", lambda: None)
    monkeypatch.setattr(network, "_host_ips", lambda: ["127.0.0.1", "169.254.10.2", "192.168.0.7"])
    assert lan_ip() == "192.168.0.7"
    monkeypatch.setattr(network, "_route_ip", lambda: "169.254.1.1")  # link-local is no use
    assert lan_ip() == "192.168.0.7"


def test_lan_ip_is_none_without_a_usable_address(monkeypatch):
    monkeypatch.setattr(network, "_route_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(network, "_host_ips", lambda: ["127.0.0.1"])
    assert lan_ip() is None


def test_lan_ip_real_lookup_never_crashes():
    ip = lan_ip()
    assert ip is None or ip.count(".") == 3


def test_operator_url_percent_encodes_the_token():
    assert operator_url_with_token(8000, "abc23") == "http://127.0.0.1:8000/operator#token=abc23"
    url = operator_url_with_token(8000, "a+b c/#")
    assert url == "http://127.0.0.1:8000/operator#token=a%2Bb%20c%2F%23"
    # The token lives only in the fragment, and decodes the way URLSearchParams decodes it
    # (a raw "+" would become a space, so it must arrive encoded).
    assert url.split("#", 1)[0] == "http://127.0.0.1:8000/operator"
    assert parse_qs(url.split("#", 1)[1]) == {"token": ["a+b c/#"]}


def test_network_view_urls_and_no_address():
    view = network_view(8000, "tok", "192.168.1.23")
    assert view == NetworkView(
        "http://192.168.1.23:8000/display", "http://192.168.1.23:8000/operator", "tok"
    )
    assert network_view(8000, "tok", None) == NetworkView("", "", "tok")


def test_page_url_puts_the_token_only_on_the_operator_page(tmp_path):
    on = _plan(tmp_path, LauncherSettings(port=8100, allow_network=True, network_token="tok"))
    assert page_url(on, "operator") == "http://127.0.0.1:8100/operator#token=tok"
    assert page_url(on, "display") == "http://127.0.0.1:8100/display"
    off = _plan(tmp_path, LauncherSettings(port=8100))
    assert page_url(off, "operator") == "http://127.0.0.1:8100/operator"


def test_network_lines_show_addresses_and_token(monkeypatch):
    monkeypatch.setattr(cli, "lan_ip", lambda: "192.168.1.23")
    lines = network_lines(8000, "tok")
    assert any("http://192.168.1.23:8000/display" in line for line in lines)
    assert any("http://192.168.1.23:8000/operator" in line for line in lines)
    assert any(line.endswith("tok") for line in lines)
    monkeypatch.setattr(cli, "lan_ip", lambda: None)
    lines = network_lines(8000, "tok")
    assert len(lines) == 1 and "No network address" in lines[0] and "tok" in lines[0]


# --- form and CLI ---------------------------------------------------------------------


def test_form_keeps_network_mode_and_token():
    # The window lays the form over the saved settings, so the token survives a Start.
    s = LauncherSettings(allow_network=True, network_token="tok")
    assert settings_from_form(s, form_from_settings(s)) == s
    off = settings_from_form(s, replace(form_from_settings(s), allow_network=False))
    assert off.allow_network is False and off.network_token == "tok"


def test_network_flag_overrides_the_saved_setting():
    args = build_parser().parse_args(["--network", "on"])
    assert apply_overrides(LauncherSettings(), args).allow_network is True
    args = build_parser().parse_args(["--network", "off"])
    assert apply_overrides(LauncherSettings(allow_network=True), args).allow_network is False
    args = build_parser().parse_args([])
    assert apply_overrides(LauncherSettings(allow_network=True), args).allow_network is True
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--network", "maybe"])


def test_format_plan_never_prints_the_token(tmp_path):
    plan = _plan(tmp_path, LauncherSettings(allow_network=True, network_token="secrettok1"))
    text = format_plan(plan)
    assert "BIBLELIVE_HOST=0.0.0.0" in text
    assert "BIBLELIVE_OPERATOR_TOKEN=(hidden)" in text
    assert "secrettok1" not in text


def test_dry_run_with_network_keeps_the_token_but_not_the_mode(tmp_path, capsys):
    path = tmp_path / "l.json"
    save_settings(LauncherSettings(language="lt"), path)
    assert main(["--settings-file", str(path), "--network", "on", "--dry-run"]) == 0
    first = load_settings(path)
    assert first.allow_network is False  # --network is for this run; only --save keeps it
    assert first.network_token
    out = capsys.readouterr().out
    assert first.network_token not in out
    assert "BIBLELIVE_OPERATOR_TOKEN=(hidden)" in out
    # A later start reuses the same token.
    assert main(["--settings-file", str(path), "--network", "on", "--dry-run"]) == 0
    assert load_settings(path).network_token == first.network_token


# --- window ---------------------------------------------------------------------------


def _close_window(root, threads_before):
    """Remove a test's window from the shared root once the threads it started have finished.

    The window's background probes hold the App. If one is still running when the test
    ends, the App can be finalized on that worker thread, and tkinter's Variable.__del__
    then calls Tcl from the wrong thread (tkinter reports "main thread is not in main
    loop"). So wait for those threads, then remove the widgets. Nothing here changes the
    window's own threading.
    """
    for thread in threading.enumerate():
        if thread not in threads_before and thread is not threading.current_thread():
            thread.join(timeout=10)
    for child in root.winfo_children():
        child.destroy()


@pytest.fixture(autouse=True)
def _collect_on_the_main_thread():
    """Free a closed window's App here, after the test has let go of it, never later on a worker."""
    yield
    gc.collect()


def test_window_shows_addresses_token_and_operator_fragment(tk_root, tmp_path, monkeypatch):
    for var in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setattr("launcher.gui.load_microphones", lambda settings: [])
    monkeypatch.setattr("launcher.gui.lan_ip", lambda: "192.168.1.23")
    save_settings(
        LauncherSettings(allow_network=True, network_token="tok123abc"),
        tmp_path / "BibleLive" / "launcher.json",
    )
    from launcher.gui import App

    root = tk_root
    threads_before = set(threading.enumerate())
    try:
        app = App(root)
        assert app.settings.network_token == "tok123abc"
        assert app.net_frame.winfo_manager() == ""  # hidden until the server is ready
        app._show_network(8000, "tok123abc")
        assert app.net_vars["display"].get() == "http://192.168.1.23:8000/display"
        assert app.net_vars["operator"].get() == "http://192.168.1.23:8000/operator"
        assert app.net_vars["token"].get() == "tok123abc"
        assert app.net_frame.winfo_manager() == "grid"
        assert any("tok123abc" in text for text, _ in app._log_lines)
        app._copy("token")
        assert root.clipboard_get() == "tok123abc"
        app.plan = _plan(tmp_path, app.settings)
        assert app._operator_page() == "http://127.0.0.1:8000/operator#token=tok123abc"
        app._network = None
        app._refresh_network()
        assert app.net_frame.winfo_manager() == ""
    finally:
        i18n.set_language("en")
        _close_window(root, threads_before)


# --- Windows Firewall -----------------------------------------------------------------
# Nothing here touches the real firewall: netsh and ShellExecute are always passed in as fakes.

SYSTEM32 = r"C:\Windows\System32"
FW_PY = r"C:\BibleLive\python\python.exe"
FW_PYW = r"C:\BibleLive\python\pythonw.exe"
NETSH = r'"C:\Windows\System32\netsh.exe" advfirewall firewall add rule name=BibleLive '
RULE_ARGS = "dir=in action=allow profile=private enable=yes"


def _completed(code: int, out: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr=b"")


def test_firewall_rule_name_matches_the_installer():
    assert FIREWALL_RULE_NAME == "BibleLive"


def test_firewall_add_script_adds_one_private_inbound_rule_per_program():
    script = firewall_add_script([FW_PY, FW_PYW], system32=SYSTEM32)
    assert script == f'{NETSH}{RULE_ARGS} program="{FW_PY}" & {NETSH}{RULE_ARGS} program="{FW_PYW}"'
    assert "profile=private" in script
    assert "profile=public" not in script and "profile=any" not in script


def test_firewall_programs_are_the_interpreters_beside_sys_executable(tmp_path):
    (tmp_path / "python.exe").touch()
    (tmp_path / "pythonw.exe").touch()
    exe = str(tmp_path / "python.exe")
    assert firewall_programs(exe) == [str(tmp_path / "python.exe"), str(tmp_path / "pythonw.exe")]
    (tmp_path / "pythonw.exe").unlink()  # whichever exist
    assert firewall_programs(exe) == [str(tmp_path / "python.exe")]
    assert firewall_programs(str(tmp_path / "elsewhere" / "python.exe")) == []


def test_firewall_rule_exists_shows_the_rule_unelevated():
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _completed(0, b"Rule Name:  BibleLive\r\nEnabled:  Yes\r\n")

    assert firewall_rule_exists(fake_run, system32=SYSTEM32) is True
    args, kwargs = calls[0]
    assert args == [
        r"C:\Windows\System32\netsh.exe",
        "advfirewall",
        "firewall",
        "show",
        "rule",
        "name=BibleLive",
    ]
    assert kwargs["capture_output"] is True and kwargs["check"] is False
    if sys.platform == "win32":
        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_firewall_rule_exists_reads_the_exit_code_and_the_name():
    # netsh exits 1 and says "No rules match" when the rule is not there.
    none = b"No rules match the specified criteria.\r\n"
    assert firewall_rule_exists(lambda *a, **k: _completed(1, none)) is False
    # Exit 0 without the rule's name is not this rule.
    assert firewall_rule_exists(lambda *a, **k: _completed(0, b"")) is False

    def cannot_run(*args, **kwargs):
        raise FileNotFoundError("netsh.exe")

    assert firewall_rule_exists(cannot_run) is False


def test_add_firewall_rule_is_one_elevated_cmd_and_reports_the_prompt():
    calls = []

    def accepted(file, params):
        calls.append((file, params))
        return 42  # ShellExecute: above 32 means the elevated command started

    assert add_firewall_rule([FW_PY, FW_PYW], accepted, system32=SYSTEM32) is True
    assert len(calls) == 1  # one prompt for both rules
    file, params = calls[0]
    assert file == r"C:\Windows\System32\cmd.exe"
    assert params == f'/c "{firewall_add_script([FW_PY, FW_PYW], SYSTEM32)}"'
    # 5 is SE_ERR_ACCESSDENIED (prompt declined); 32 and below are errors too.
    assert add_firewall_rule([FW_PY], lambda f, p: 5, system32=SYSTEM32) is False
    assert add_firewall_rule([FW_PY], lambda f, p: 32, system32=SYSTEM32) is False


def test_allow_through_firewall_looks_for_the_rule_after_the_prompt():
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return _completed(0, b"Rule Name: BibleLive")

    assert allow_through_firewall([FW_PY], shell_execute=lambda f, p: 42, run=run) is True
    assert len(seen) == 1
    # A declined prompt is not waited for: one look, and no rule.
    declined = allow_through_firewall(
        [FW_PY], shell_execute=lambda f, p: 5, run=lambda *a, **k: _completed(1)
    )
    assert declined is False


def test_wait_until_polls_until_true_or_the_deadline():
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    answers = iter([False, False, True])
    got = wait_until(
        lambda: next(answers), timeout=10, interval=2, sleep=sleep, clock=lambda: now[0]
    )
    assert got is True and sleeps == [2, 2]
    now[0] = 0.0
    assert (
        wait_until(lambda: False, timeout=5, interval=2, sleep=sleep, clock=lambda: now[0]) is False
    )
    assert now[0] >= 5


def test_firewall_row_is_for_windows_only():
    assert firewall_applies("win32") is True
    assert firewall_applies("linux") is False
    assert firewall_applies("darwin") is False


def test_firewall_row_states():
    i18n.set_language("en")
    assert firewall_view(None).shown is False
    assert firewall_view("present").shown is False
    missing = firewall_view("missing")
    assert missing.shown and missing.button_enabled
    assert "Windows Firewall" in missing.message
    adding = firewall_view("adding")
    assert adding.shown and not adding.button_enabled  # one prompt at a time
    again = firewall_view("not_added")
    assert again.shown and again.button_enabled
    assert "not added" in again.message


def test_console_hint_only_on_windows_without_the_rule(monkeypatch):
    monkeypatch.setattr(cli, "firewall_applies", lambda: True)
    monkeypatch.setattr(cli, "firewall_rule_exists", lambda: False)
    assert "--gui" in (firewall_hint() or "")
    monkeypatch.setattr(cli, "firewall_rule_exists", lambda: True)
    assert firewall_hint() is None
    monkeypatch.setattr(cli, "firewall_applies", lambda: False)
    monkeypatch.setattr(cli, "firewall_rule_exists", lambda: False)
    assert firewall_hint() is None


@pytest.fixture
def firewall_window(tk_root, tmp_path, monkeypatch):
    """A launcher window in network mode; microphones, addresses and firewall are fakes."""
    for var in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setattr("launcher.gui.load_microphones", lambda settings: [])
    monkeypatch.setattr("launcher.gui.lan_ip", lambda: "192.168.1.23")
    monkeypatch.setattr("launcher.gui.firewall_applies", lambda: True)
    monkeypatch.setattr("launcher.gui.firewall_programs", lambda executable: [FW_PY])
    monkeypatch.setattr("launcher.gui.firewall_rule_exists", lambda: False)
    save_settings(
        LauncherSettings(
            allow_network=True,
            network_token="tok123abc",
            open_display_on_start=False,
            open_operator_on_start=False,
        ),
        tmp_path / "BibleLive" / "launcher.json",
    )
    from launcher.gui import App

    root = tk_root
    threads_before = set(threading.enumerate())
    try:
        yield App(root)
    finally:
        i18n.set_language("en")
        _close_window(root, threads_before)


def _handle_until(app, tag):
    """Handle queued events, as the window's poll does, up to the one with ``tag``."""
    while True:
        item = app.queue.get(timeout=5)  # worker threads post here
        app._handle(item)
        if item[0] == tag:
            return item


def test_window_firewall_row_checks_then_adds_the_rule(firewall_window, monkeypatch):
    app = firewall_window
    results = iter([False, True])  # first prompt declined, then accepted
    added = []

    def fake_allow(programs):
        added.append(list(programs))
        return next(results)

    monkeypatch.setattr("launcher.gui.allow_through_firewall", fake_allow)
    app._show_network(8000, "tok123abc")
    app._check_firewall()
    assert _handle_until(app, "firewall") == ("firewall", "missing")
    assert app.fw_label.winfo_manager() == "grid"
    assert app.fw_button.instate(["!disabled"])
    app.on_allow_firewall()
    assert app.fw_button.instate(["disabled"])  # waiting for the prompt
    _handle_until(app, "firewall")  # declined
    assert app.fw_label.winfo_manager() == "grid"
    assert "not added" in app.fw_label.cget("text")
    assert app.fw_button.instate(["!disabled"])
    app.on_allow_firewall()
    _handle_until(app, "firewall")  # accepted: the rule is there now
    assert added == [[FW_PY], [FW_PY]]
    assert app.fw_label.winfo_manager() == ""
    assert app.net_frame.winfo_manager() == "grid"


def test_window_checks_the_firewall_only_when_network_mode_is_on(firewall_window, tmp_path):
    app = firewall_window
    checked = []
    app._check_firewall = lambda: checked.append(True)  # type: ignore[method-assign]
    app.plan = _plan(tmp_path, LauncherSettings())  # network off
    app._on_server_ready()
    assert checked == []
    app.plan = _plan(tmp_path, app.settings)  # network on
    app._on_server_ready()
    assert checked == [True]


def test_late_firewall_result_after_stop_is_ignored(firewall_window):
    app = firewall_window
    app._show_network(8000, "tok123abc")
    app._handle(("finished", 0))  # the server stopped
    app._handle(("firewall", "missing"))  # a check that was still running
    assert app._firewall is None
    assert app.fw_label.winfo_manager() == ""


def test_window_fits_a_1366x768_screen_with_the_network_and_firewall_rows(firewall_window):
    """The tallest state: network mode, server ready, firewall row shown.

    A 1366x768 laptop leaves about 697 px of client height; the log pane must keep its lines.
    """
    app = firewall_window
    app.root.update_idletasks()
    off = app.root.winfo_reqheight()
    app._show_network(8000, "tok123abc")
    app.root.update_idletasks()
    on = app.root.winfo_reqheight()
    app._firewall = "missing"
    app._refresh_network()
    app.root.update_idletasks()
    tallest = app.root.winfo_reqheight()
    assert app.fw_label.winfo_manager() == "grid"
    assert on > off  # the panel is there
    assert tallest <= 690, f"requests {tallest} px of height (off {off}, on {on})"
    assert int(app.log.cget("height")) >= 4  # the log keeps at least four lines
