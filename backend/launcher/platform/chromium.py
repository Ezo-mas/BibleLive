"""The projector command line for Chromium browsers (Edge, Chrome); the same on every OS."""

from pathlib import Path

from . import Browser, Monitor


def build_command(browser: Browser, url: str, monitor: Monitor, profile_dir: Path) -> list[str]:
    """Pure: the command line placing a full-screen app window on ``monitor``.

    A full-screen app window, not kiosk mode: it looks the same on the projector (no
    browser bars), but F11 leaves full screen and Alt+F4 closes it, which kiosk mode
    blocks, leaving the operator no way to move or close the window.
    """
    return [
        browser.path,
        f"--app={url}",
        "--start-fullscreen",
        "--no-first-run",
        "--no-default-browser-check",
        # The private profile must stay empty: Edge otherwise signs it into the Windows
        # account on first launch and syncs that account's extensions into it, which
        # opens an extra window of extension-install pages next to the display.
        "--disable-sync",
        "--disable-extensions",
        "--disable-features=msImplicitSignin",
        f"--window-position={monitor.x},{monitor.y}",
        f"--user-data-dir={profile_dir}",
    ]
