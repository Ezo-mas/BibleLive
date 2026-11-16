"""Monitor enumeration and projector selection."""

from collections.abc import Sequence

from . import platform
from .platform import Monitor as Monitor


def choose_projector(monitors: Sequence[Monitor], saved: str | None = None) -> Monitor | None:
    """The saved monitor if still connected, else the first non-primary one, else None."""
    if saved:
        for m in monitors:
            if m.device == saved:
                return m
    for m in monitors:
        if not m.primary:
            return m
    return None


def enumerate_monitors() -> list[Monitor]:
    """All attached monitors (primary first); empty off Windows or if the query fails."""
    return platform.current().enumerate_monitors()
