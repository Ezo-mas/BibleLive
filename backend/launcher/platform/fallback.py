"""Generic launcher behaviour (Linux and other POSIX), without native platform support."""

import shutil
from collections.abc import Mapping
from pathlib import Path

from . import Browser, Monitor
from .chromium import build_command as build_command
from .posix import add_firewall_rule as add_firewall_rule
from .posix import bring_projector_to_front as bring_projector_to_front
from .posix import child_process_options as child_process_options
from .posix import firewall_add_script as firewall_add_script
from .posix import firewall_applies as firewall_applies
from .posix import firewall_programs as firewall_programs
from .posix import firewall_rule_exists as firewall_rule_exists
from .posix import kill_pid_tree as kill_pid_tree
from .posix import kill_tree as kill_tree
from .posix import log_dir as log_dir
from .posix import open_projector as open_projector


def find_browser(env: Mapping[str, str] | None = None) -> Browser | None:
    """Edge first, then Chrome, by the executable names on PATH."""
    del env
    for kind, exe in (("edge", "msedge"), ("chrome", "chrome")):
        found = shutil.which(exe)
        if found:
            return Browser(found, kind)
    return None


def enumerate_monitors() -> list[Monitor]:
    return []


def find_profile_processes(profile_dir: Path) -> list[int]:
    del profile_dir
    return []


def app_data_dir() -> Path:
    return Path.home() / ".biblelive"


def models_dir() -> Path:
    return Path.home() / ".biblelive" / "models"
