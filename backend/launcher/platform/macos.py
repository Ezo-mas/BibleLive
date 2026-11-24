"""macOS (Apple Silicon) launcher operations."""

import logging
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

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
from .posix import port_owner as port_owner

logger = logging.getLogger("launcher.projector")

# (kind, app bundle, executable inside Contents/MacOS); Chrome first: unlike Windows, no
# Chromium browser comes with the system, and Chrome is the one most Macs have.
_BROWSERS = (
    ("chrome", "Google Chrome.app", "Google Chrome"),
    ("edge", "Microsoft Edge.app", "Microsoft Edge"),
)


def find_browser(env: Mapping[str, str] | None = None) -> Browser | None:
    """Chrome, then Edge: /Applications, then ~/Applications, then PATH."""
    env = os.environ if env is None else env
    home = env.get("HOME") or str(Path.home())
    roots = (Path("/Applications"), Path(home) / "Applications")
    for kind, bundle, exe in _BROWSERS:
        for root in roots:
            p = root / bundle / "Contents" / "MacOS" / exe
            if p.is_file():
                return Browser(str(p), kind)
    for kind, name in (("chrome", "google-chrome"), ("edge", "microsoft-edge")):
        found = shutil.which(name)
        if found:
            return Browser(found, kind)
    return None


def find_profile_processes(profile_dir: Path) -> list[int]:
    """PIDs of processes whose command line mentions ``profile_dir``.

    A display browser left over from a crashed run keeps the private profile locked, so a
    new launch would hand off to it and exit. Only the profile path is matched, so the
    user's own browser is never touched.
    """
    try:
        out = subprocess.run(
            ["/bin/ps", "-axww", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not look for stale display browsers: %s", exc)
        return []
    needle = str(profile_dir)
    own = os.getpid()
    pids = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != own and needle in command:
            pids.append(int(pid))
    return pids


def enumerate_monitors() -> list[Monitor]:
    """All active displays (main display first); empty if the query fails."""
    try:
        return _enumerate_quartz()
    except (AttributeError, OSError):
        return []


def _enumerate_quartz() -> list[Monitor]:
    # Quartz Display Services through ctypes: no PyObjC in the bundle. Coordinates are
    # global points with the main display's top-left at 0,0, which is what Chromium's
    # --window-position expects.
    if sys.platform != "darwin":
        return []
    import ctypes
    import ctypes.util

    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]  # noqa: RUF012

    class CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]  # noqa: RUF012

    class CGRect(ctypes.Structure):
        _fields_ = [("origin", CGPoint), ("size", CGSize)]  # noqa: RUF012

    path = ctypes.util.find_library("CoreGraphics") or (
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )
    cg = ctypes.CDLL(path)
    cg.CGGetActiveDisplayList.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    cg.CGGetActiveDisplayList.restype = ctypes.c_int32
    cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    cg.CGDisplayBounds.restype = CGRect
    for fn in ("CGMainDisplayID",):
        getattr(cg, fn).restype = ctypes.c_uint32
    for fn in ("CGDisplayIsBuiltin", "CGDisplayUnitNumber", "CGDisplayMirrorsDisplay"):
        getattr(cg, fn).argtypes = [ctypes.c_uint32]
        getattr(cg, fn).restype = ctypes.c_uint32

    ids = (ctypes.c_uint32 * 16)()
    count = ctypes.c_uint32(0)
    if cg.CGGetActiveDisplayList(16, ids, ctypes.byref(count)) != 0:
        return []
    return _monitors(cg, list(ids[: count.value]))


def _monitors(cg: Any, ids: list[int]) -> list[Monitor]:
    """One Monitor per display, leaving out mirror copies.

    A projector set to mirror the built-in screen is still an active display, at the same
    0,0 bounds; listing it would let the display page open full screen over the operator's
    own screen. Windows reports a duplicated screen as one monitor, so both platforms then
    have no projector to offer (``no_monitor``).
    """
    main = cg.CGMainDisplayID()
    found: list[Monitor] = []
    for did in ids:
        if cg.CGDisplayMirrorsDisplay(did):  # kCGNullDirectDisplay (0) unless a mirror copy
            continue
        r = cg.CGDisplayBounds(did)
        name = (
            "Built-in" if cg.CGDisplayIsBuiltin(did) else f"Display {cg.CGDisplayUnitNumber(did)}"
        )
        found.append(
            Monitor(
                device=name,
                x=round(r.origin.x),
                y=round(r.origin.y),
                width=round(r.size.width),
                height=round(r.size.height),
                primary=did == main,
            )
        )
    return sorted(found, key=lambda m: (not m.primary, m.device))


def _support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "BibleLive"


def app_data_dir() -> Path:
    """Per-user data directory: ``~/Library/Application Support/BibleLive``."""
    return _support_dir()


def models_dir() -> Path:
    """Speech model cache (``HF_HOME``, ~1.6 GB): beside the data, not in Caches.

    ~/Library/Caches may be emptied by the system, which would mean a re-download before
    a service.
    """
    return _support_dir() / "models"


# AVAuthorizationStatus values.
_MIC_STATUS = {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}
# Completion blocks handed to AVFoundation stay alive for the process: macOS may answer after
# microphone_access() has given up waiting, and a freed block would crash the launcher then.
_KEEP_ALIVE: list[object] = []


def microphone_access(
    *, ask: bool = True, timeout: float = 120.0, cancelled: Callable[[], bool] | None = None
) -> str:
    """This app's microphone permission: authorized, denied, restricted or not_determined.

    With ``ask`` and no decision yet, shows the system prompt ("BibleLive" would like to
    access the microphone) and waits up to ``timeout`` s for the answer; not_determined
    means no answer in time (or ``cancelled()`` turned true while waiting). The launcher asks at Start, before the browser opens, so the
    prompt is not hidden behind the operator page. "unknown" off macOS or if the query fails
    (the pipeline's ffmpeg then triggers the prompt itself, as before).
    """
    if sys.platform != "darwin":
        return "unknown"
    try:
        return _microphone_access(ask, timeout, cancelled or (lambda: False))
    except Exception as exc:  # never fatal: the pipeline's ffmpeg still asks
        logger.warning("Could not check the microphone permission: %s", exc)
        return "unknown"


def _microphone_access(ask: bool, timeout: float, cancelled: Callable[[], bool]) -> str:
    # AVCaptureDevice through ctypes and the Objective-C runtime (no PyObjC in the bundle).
    import ctypes

    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    avf = ctypes.CDLL("/System/Library/Frameworks/AVFoundation.framework/AVFoundation")
    libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    device = objc.objc_getClass(b"AVCaptureDevice")
    audio = ctypes.c_void_p.in_dll(avf, "AVMediaTypeAudio").value
    if not (send and device and audio):
        raise OSError("AVFoundation is not available")

    status_of = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        send
    )
    status = _MIC_STATUS.get(
        status_of(device, objc.sel_registerName(b"authorizationStatusForMediaType:"), audio),
        "unknown",
    )
    if status != "not_determined" or not ask:
        return status

    # requestAccessForMediaType:completionHandler: takes a block; build a global one by hand
    # (the Blocks ABI: isa, flags, reserved, invoke, descriptor).
    invoke_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_bool)

    class Descriptor(ctypes.Structure):
        _fields_ = [  # noqa: RUF012
            ("reserved", ctypes.c_ulong),
            ("size", ctypes.c_ulong),
            ("signature", ctypes.c_char_p),
        ]

    class Block(ctypes.Structure):
        _fields_ = [  # noqa: RUF012
            ("isa", ctypes.c_void_p),
            ("flags", ctypes.c_int),
            ("reserved", ctypes.c_int),
            ("invoke", invoke_type),
            ("descriptor", ctypes.POINTER(Descriptor)),
        ]

    answered = threading.Event()
    granted: list[bool] = []

    def on_answer(_block: object, ok: bool) -> None:
        granted.append(bool(ok))
        answered.set()

    invoke = invoke_type(on_answer)
    descriptor = Descriptor(0, ctypes.sizeof(Block), b"v12@?0B8")
    block_is_global, block_has_signature = 1 << 28, 1 << 30
    block = Block(
        ctypes.addressof(ctypes.c_void_p.in_dll(libsystem, "_NSConcreteGlobalBlock")),
        block_is_global | block_has_signature,
        0,
        invoke,
        ctypes.pointer(descriptor),
    )
    _KEEP_ALIVE.append((invoke, descriptor, block))
    request = ctypes.CFUNCTYPE(
        None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    )(send)
    request(
        device,
        objc.sel_registerName(b"requestAccessForMediaType:completionHandler:"),
        audio,
        ctypes.byref(block),
    )
    waited = 0.0
    while not answered.wait(0.25):
        waited += 0.25
        if waited >= timeout or cancelled():
            return "not_determined"
    return "authorized" if granted[0] else "denied"
