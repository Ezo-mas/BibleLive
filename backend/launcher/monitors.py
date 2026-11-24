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


def usable_geometry(
    rect: Sequence[int],
    monitors: Sequence[Monitor],
    min_size: tuple[int, int],
    min_visible: float = 0.5,
) -> tuple[int, int, int, int] | None:
    """The saved window rectangle if it is mostly on one current monitor, else None.

    ``rect`` is [x, y, width, height] in screen pixels, like the monitors. A rectangle that
    is too small, or less than ``min_visible`` of its area on any single monitor (a monitor
    that was unplugged, a changed layout), is not restored: the caller keeps its default
    centred placement. A rectangle bigger than the monitor holding most of it is trimmed to fit.
    """
    if len(rect) != 4:
        return None
    x, y, width, height = rect
    if width < min_size[0] or height < min_size[1]:
        return None
    best: Monitor | None = None
    best_overlap = 0
    for m in monitors:
        overlap_w = min(x + width, m.x + m.width) - max(x, m.x)
        overlap_h = min(y + height, m.y + m.height) - max(y, m.y)
        overlap = max(overlap_w, 0) * max(overlap_h, 0)
        if overlap > best_overlap:
            best, best_overlap = m, overlap
    if best is None or best_overlap < min_visible * width * height:
        return None
    width = min(width, best.width)
    height = min(height, best.height)
    x = max(best.x, min(x, best.x + best.width - width))
    y = max(best.y, min(y, best.y + best.height - height))
    return x, y, width, height
