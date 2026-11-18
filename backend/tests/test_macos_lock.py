"""The Mac app's wheel lock (scripts/macos/requirements-arm64.lock) agrees with constraints.txt.

The lock adds a SHA256 to each pinned version for the app's platform; it must never pin a
version the constraints (what CI tests) do not. Regenerate it with
scripts/macos/lock_requirements.py after changing the constraints.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCK = REPO / "scripts" / "macos" / "requirements-arm64.lock"
CONSTRAINTS = REPO / "backend" / "constraints.txt"

_PIN = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;\\]+)")


def _name(raw: str) -> str:
    return re.sub(r"[-_.]+", "-", raw).lower()


def _lock_entries() -> dict[str, tuple[str, list[str]]]:
    text = LOCK.read_text(encoding="utf-8").replace("\\\n", " ")
    entries: dict[str, tuple[str, list[str]]] = {}
    for line in text.splitlines():
        match = _PIN.match(line)
        if match:
            hashes = re.findall(r"--hash=sha256:([0-9a-f]+)", line)
            entries[_name(match.group(1))] = (match.group(2), hashes)
    return entries


def _constraints() -> dict[str, set[str]]:
    pins: dict[str, set[str]] = {}
    for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line.strip())
        if match:
            pins.setdefault(_name(match.group(1)), set()).add(match.group(2))
    return pins


def test_every_locked_wheel_has_one_sha256():
    entries = _lock_entries()
    assert len(entries) > 30  # the backend and its audio + gguf extras
    for name, (_version, hashes) in entries.items():
        assert len(hashes) == 1 and len(hashes[0]) == 64, name


def test_the_lock_pins_the_constraints_versions():
    constraints = _constraints()
    for name, (version, _hashes) in _lock_entries().items():
        assert name in constraints, f"{name} is locked but not in constraints.txt"
        assert version in constraints[name], (
            f"{name}=={version} in the lock, {sorted(constraints[name])} in constraints.txt: "
            "run scripts/macos/lock_requirements.py"
        )


def test_the_lock_has_the_speech_stack_and_the_mac_vad_wheel():
    entries = _lock_entries()
    for name in ("faster-whisper", "ctranslate2", "transcribe-cpp", "webrtcvad-wheels", "numpy"):
        assert name in entries, name
    assert "webrtcvad" not in entries  # source-only on PyPI: needs a compiler on the Mac
