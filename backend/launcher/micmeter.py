"""Idle microphone level meter backed by a short-lived ffmpeg capture."""

import contextlib
import math
import shutil
import struct
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable

from audio.capture import ffmpeg_input_args

from . import platform
from .i18n import t
from .platform.macos import microphone_access

BLOCK_SAMPLES = 800
BLOCK_BYTES = BLOCK_SAMPLES * 2


def block_level(block: bytes) -> float:
    peak = max(abs(sample) for (sample,) in struct.iter_unpack("<h", block))
    if peak == 0:
        return 0.0
    dbfs = 20 * math.log10(peak / 32767)
    return max(0.0, min(1.0, (dbfs + 60) / 60))


def meter_input_args(args: list[str]) -> list[str]:
    """Ask DirectShow for 50 ms buffers: by default it hands over ~500 ms of audio at once,
    so the meter moved in bursts and one word looked like two sounds. The pipeline keeps
    its own input arguments."""
    if args[:2] == ["-f", "dshow"]:
        return [*args[:2], "-audio_buffer_size", "50", *args[2:]]
    return args


class MicMeter:
    def __init__(
        self,
        *,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        access: Callable[..., str] = microphone_access,
    ) -> None:
        self._popen = popen
        self._access = access
        self._lifecycle = threading.Lock()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._levels: deque[float] = deque(maxlen=100)
        self._state = "off"
        self._error: str | None = None

    def start(
        self,
        device: str | None,
        *,
        env: dict[str, str],
        can_start: Callable[[], bool],
        restart: bool = False,
    ) -> None:
        with self._lifecycle:
            if restart and can_start():
                self._stop()
            if (self._thread is None or not self._thread.is_alive()) and can_start():
                self._start(device, env, can_start)

    def stop(self) -> None:
        with self._lifecycle:
            self._stop()

    def _start(
        self, device: str | None, env: dict[str, str], can_start: Callable[[], bool]
    ) -> None:
        self._cancel.clear()
        with self._lock:
            self._state = "starting"
            self._error = None
            self._levels.clear()
        self._thread = threading.Thread(
            target=self._read, args=(device, env, can_start), daemon=True
        )
        self._thread.start()

    def _stop(self) -> None:
        with self._lock:
            self._cancel.set()
            if self._process is not None:
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        with self._lock:
            self._state = "off"
            self._error = None
            self._levels.clear()

    def drain(self) -> list[float]:
        return self.snapshot()[2]

    def snapshot(self) -> tuple[str, str | None, list[float]]:
        """Read status and drain levels atomically."""
        with self._lock:
            levels = list(self._levels) if self._state == "on" else []
            self._levels.clear()
            return self._state, t(self._error) if self._error else None, levels

    def _read(self, device: str | None, env: dict[str, str], can_start: Callable[[], bool]) -> None:
        error: str | None = "meter.device_failed"
        try:
            if sys.platform == "darwin":
                status = self._access(ask=False)
                if status == "not_determined" and not self._cancel.is_set():
                    status = self._access(cancelled=self._cancel.is_set)
                if status in ("denied", "restricted"):
                    error = "warn.mic_denied"
                    return
            # Windows does not use the supplied child environment to locate the executable.
            ffmpeg = shutil.which("ffmpeg", path=env.get("PATH")) or "ffmpeg"
            args = meter_input_args(ffmpeg_input_args(device, ffmpeg=ffmpeg))
            if self._cancel.is_set() or not can_start():
                error = None  # superseded (a session is starting or another mic was chosen)
                return
            proc = self._popen(
                [ffmpeg, *args, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                **platform.current().child_process_options(),
            )
            with self._lock:
                self._process = proc
            assert proc.stdout is not None
            with proc.stdout:
                while not self._cancel.is_set():
                    block = proc.stdout.read(BLOCK_BYTES)
                    if len(block) != BLOCK_BYTES:
                        break
                    with self._lock:
                        self._state = "on"
                        self._levels.append(block_level(block))
        except FileNotFoundError:
            error = "meter.ffmpeg_missing"
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc.__context__, FileNotFoundError):
                error = "meter.ffmpeg_missing"
        finally:
            if self._process is not None:
                # EOF can also come from a failed pipe, so ensure the child is gone.
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
                self._process.wait()
            with self._lock:
                self._process = None
                if not self._cancel.is_set():
                    self._state = "error" if error else "off"
                    self._error = error
