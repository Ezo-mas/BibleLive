"""Launcher network mode: operator token, server env, addresses, CLI and window wiring."""

import gc
import json
import threading
from dataclasses import replace
from urllib.parse import parse_qs

import pytest
from launcher import cli, i18n, network
from launcher.cli import (
    apply_overrides,
    build_parser,
    format_plan,
    main,
    network_lines,
    page_url,
)
from launcher.network import (
    TOKEN_ALPHABET,
    TOKEN_LENGTH,
    ensure_network_token,
    lan_ip,
    new_token,
    operator_url_with_token,
)
from launcher.process import build_plan
from launcher.settings import LauncherSettings, load_settings, save_settings
from launcher.viewmodel import (
    NetworkView,
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


