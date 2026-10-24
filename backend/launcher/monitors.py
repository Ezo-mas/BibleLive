"""Monitor enumeration (Windows, ctypes) and projector selection."""

import sys
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Monitor:
    device: str  # e.g. "\\\\.\\DISPLAY2"
    x: int
    y: int
    width: int
    height: int
    primary: bool

    @property
    def label(self) -> str:
        name = self.device.lstrip("\\.") or self.device
        return f"{name} ({self.width}x{self.height}){' *' if self.primary else ''}"


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
    if sys.platform != "win32":
        return []
    try:
        return _enumerate_windows()
    except (AttributeError, OSError):
        return []


def _enumerate_windows() -> list[Monitor]:
    # sys.platform (not os.name) so mypy skips this body on Linux, where
    # typeshed has no ctypes.WINFUNCTYPE.
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [  # noqa: RUF012 (ctypes convention)
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HANDLE,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )
    user32 = ctypes.windll.user32  # type: ignore[attr-defined,unused-ignore]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFOEXW)]
    found: list[Monitor] = []

    def _cb(hmon: int, _hdc: int, _rect: object, _lparam: int) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append(
                Monitor(
                    device=info.szDevice,
                    x=r.left,
                    y=r.top,
                    width=r.right - r.left,
                    height=r.bottom - r.top,
                    primary=bool(info.dwFlags & 1),  # MONITORINFOF_PRIMARY
                )
            )
        return True

    user32.EnumDisplayMonitors(None, None, callback_type(_cb), 0)
    return sorted(found, key=lambda m: (not m.primary, m.device))
