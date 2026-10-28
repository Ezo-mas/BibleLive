#!/usr/bin/env python3
"""Spike: can the candidate macOS Python open a real, visible Tk file dialog?

Pre-pin check from the macOS installer plan (nothing is pinned until this
passes on macOS arm64). A timeout alone would prove nothing, so the check
distinguishes a visible, waiting dialog from a blocked or headless session:

1. ``import tkinter`` works and reports Tk/Tcl versions;
2. a real ``Tk`` root maps and reports whether it is viewable;
3. the dialog child writes a marker recording whether **its** window is
   viewable before opening the modal file dialog;
4. PASS requires the marker to say ``viewable=True``, the child to still be
   waiting when the timeout fires, and therefore the dialog to be on screen
   awaiting a click.

Exit code 0 = pass, 1 = fail, with the reason on stdout.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import tkinter
from pathlib import Path
from tkinter import filedialog

DIALOG_PROBE = """
import pathlib
import tkinter
from tkinter import filedialog

root = tkinter.Tk()
root.title("BibleLive dialog spike")
root.geometry("320x120+40+40")
root.update()
pathlib.Path("@MARKER@").write_text(
    f"viewable={bool(root.winfo_viewable())}"
    f" ismapped={bool(root.winfo_ismapped())}"
    f" screen={root.winfo_screenwidth()}x{root.winfo_screenheight()}",
    encoding="utf-8",
)
filedialog.askopenfilename(parent=root, title="BibleLive spike")
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Tk root + file dialog spike")
    parser.add_argument(
        "--seconds", type=float, default=15.0, help="how long the dialog may wait (default: 15)"
    )
    args = parser.parse_args()

    print(f"tkinter imported: TkVersion={tkinter.TkVersion} TclVersion={tkinter.TclVersion}")
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:
        print(f"TK SPIKE: FAIL - cannot create a Tk root ({exc})")
        return 1
    root.title("BibleLive Tk spike")
    root.geometry("320x120+40+40")
    root.update()
    print(
        f"harness root: viewable={bool(root.winfo_viewable())}"
        f" ismapped={bool(root.winfo_ismapped())}"
        f" screen={root.winfo_screenwidth()}x{root.winfo_screenheight()}"
    )
    dialog = filedialog.Open(root, title="spike")
    print(f"filedialog.Open constructs: {type(dialog).__name__}")
    root.update()
    root.destroy()

    with tempfile.TemporaryDirectory(prefix="biblelive-tk-spike-") as tmp:
        marker = Path(tmp) / "dialog-window.txt"
        probe = [sys.executable, "-c", DIALOG_PROBE.replace("@MARKER@", str(marker))]
        try:
            result = subprocess.run(probe, timeout=args.seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            result = None
            timed_out = True
        window = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
        print(f"dialog window: {window or '(no marker - the child never opened a window)'}")
        if not timed_out:
            assert result is not None
            print(
                f"file dialog: child exited on its own (rc={result.returncode}) - no visible wait"
            )
            print("TK SPIKE: FAIL")
            return 1
        if "viewable=True" not in window:
            print("file dialog: still waiting, but its window was not viewable (headless session?)")
            print("TK SPIKE: FAIL")
            return 1
        print(f"file dialog: viewable window waiting for input after {args.seconds:g}s")
        print("TK SPIKE: PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
