"""Temporary monitor labels for the running webview launcher."""

import ctypes
import logging
import sys
import time
from html import escape
from typing import Any

from .i18n import get_language, t
from .monitors import Monitor

logger = logging.getLogger(__name__)


def label_window_rect(monitor: Monitor, scale: float = 1.0) -> tuple[int, int, int, int]:
    """Center a 420x260 label in the monitor's coordinate space."""
    width = min(round(420 * scale), monitor.width)
    height = min(round(260 * scale), monitor.height)
    return (
        monitor.x + (monitor.width - width) // 2,
        monitor.y + (monitor.height - height) // 2,
        width,
        height,
    )


def _label_html(monitor: Monitor) -> str:
    primary = f'<div class="primary">{escape(t("screen.main"))}</div>' if monitor.primary else ""
    return f"""<!doctype html>
<html lang="{get_language()}">
<meta charset="utf-8">
<style>
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
       background: #221e1a; color: #fbf2e3; font: 22px system-ui, sans-serif; text-align: center; }}
strong {{ display: block; font-size: 52px; overflow-wrap: anywhere; }}
.details {{ color: #d0c1ae; }}
.primary {{ color: #f7bc62; margin-top: 12px; }}
</style>
<main><strong>{escape(monitor.name)}</strong>
<div class="details">({monitor.resolution})</div>{primary}</main>
</html>"""


def _place_windows_label(window: Any, monitor: Monitor) -> None:
    if sys.platform != "win32":
        return
    from ctypes import wintypes

    native = window.native

    def place() -> None:
        # The launcher sets per-monitor DPI awareness before starting WinForms, so
        # GetMonitorInfoW gives physical pixels. pywebview 6 scales x/y/size using
        # the HWND's initial DPI before moving it, which can be the wrong monitor's.
        # Put the hidden HWND on the target in physical pixels, then size it at
        # that monitor's DPI. Avoid webview.show(), which explicitly activates it.
        user = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = native.Handle.ToInt64()
        user.GetDpiForWindow.argtypes = [wintypes.HWND]
        user.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            *([ctypes.c_int] * 4),
            wintypes.UINT,
        ]
        flags = 0x0004 | 0x0010  # SWP_NOZORDER | SWP_NOACTIVATE
        if not user.SetWindowPos(hwnd, None, *label_window_rect(monitor), flags):
            raise ctypes.WinError(ctypes.get_last_error())
        scale = (user.GetDpiForWindow(hwnd) or 96) / 96
        if not user.SetWindowPos(hwnd, None, *label_window_rect(monitor, scale), flags):
            raise ctypes.WinError(ctypes.get_last_error())
        native.Show()  # focus=False sets WS_EX_NOACTIVATE.

    native.Invoke(window.gui.Func[window.gui.Type](place))


def identify_screens(webview: Any, monitors: list[Monitor], primary_screen: Any) -> None:
    """Create labels on a bridge worker and keep the request active through cleanup."""
    windows = []
    try:
        for monitor in monitors:
            x, y, width, height = label_window_rect(monitor)
            on_windows = sys.platform == "win32"
            window = webview.create_window(
                monitor.label,
                html=_label_html(monitor),
                x=x,
                y=y,
                width=width,
                height=height,
                min_size=(1, 1),
                frameless=True,
                on_top=True,
                focus=False,
                hidden=on_windows,
                background_color="#221e1a",
                # CGDisplayBounds uses a top-left origin at the primary screen.
                # Cocoa flips y relative to the supplied screen, so use the primary
                # frame even if the launcher is active on a different screen.
                screen=primary_screen if sys.platform == "darwin" else None,
            )
            if window is not None:
                windows.append(window)
                if on_windows:
                    _place_windows_label(window, monitor)
        time.sleep(3)
    finally:
        for window in windows:
            try:
                window.destroy()
            except Exception:
                logger.exception("Could not close monitor label")
