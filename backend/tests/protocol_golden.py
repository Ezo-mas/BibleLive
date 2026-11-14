"""Shared helpers for the checked-in WebSocket protocol examples."""

import os
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "protocol"


def golden(group, name, wire):
    path = FIXTURES / group / f"{name}.json"
    if os.environ.get("BIBLELIVE_UPDATE_PROTOCOL_FIXTURES") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wire + "\n", encoding="utf-8")
    assert path.read_text(encoding="utf-8") == wire + "\n"
