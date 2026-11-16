"""Capture recovery and health state owned by the VAD thread."""

import logging
import threading
import time
from collections.abc import Callable
from operator import mul

from .capture import AudioCapture
from .config import AudioPipelineConfig
from .metrics import PipelineMetrics
from .session_log import SessionLog

# Preserve the pipeline's log source and messages.
logger = logging.getLogger("audio.pipeline")

# Seconds with no sound from the microphone before the operator is told it is
# silent. A dead input keeps delivering frames, so the stall check never fires
# for it. Incident (Windows, 2026-10): the launcher auto-picked "Microphone
# (Realtek(R) Audio)", which delivered about -80 dBFS RMS for 100 s; nothing
# warned and no references were detected.
SILENT_MIC_S = 30.0
# A frame counts as sound when its RMS is above this level (dBFS, 16-bit full
# scale). Room noise sits at -60 to -45 dBFS and the dead input was at -80, so
# -70 is the midpoint of that gap: 10 dB clear of both, and a quiet room between
# speech does not trip the warning.
SOUND_LEVEL_DBFS = -70.0
_SOUND_MEAN_SQUARE = (32768 * 10 ** (SOUND_LEVEL_DBFS / 20)) ** 2


def frame_is_loud(frame: bytes) -> bool:
    """Whether a 16-bit mono PCM frame's RMS is above SOUND_LEVEL_DBFS.

    Compares mean squares, so there is no square root or logarithm per frame.
    Native byte order, which is little-endian on every platform this runs on.
    """
    samples = memoryview(frame).cast("h")
    energy: int = sum(map(mul, samples, samples))
    return energy > _SOUND_MEAN_SQUARE * len(samples)


class CaptureSupervisor:
    """Restart, watchdog and microphone state, written only by the VAD thread.

    Startup initializes health clocks before launching that thread. The heartbeat
    only reads ``down``, ``last_frame_at`` and ``last_loud_at``. Lifecycle stop
    takes ``lock`` before stopping capture; it never changes supervisor state.
    Callbacks read the pipeline's current lifecycle, capture and session log.
    The supervisor never owns utterance assembly, queues or partials.
    """

    def __init__(
        self,
        config: AudioPipelineConfig,
        metrics: PipelineMetrics,
        get_capture: Callable[[], AudioCapture],
        is_running: Callable[[], bool],
        get_session_log: Callable[[], SessionLog | None],
    ):
        self.config = config
        self.metrics = metrics
        self._get_capture = get_capture
        self._is_running = is_running
        self._get_session_log = get_session_log
        self.lock = threading.Lock()
        self.restart_attempt = 0
        self.restarted_at_s: float | None = None
        self.down = False
        self.last_frame_at: float | None = None
        self.last_loud_at: float | None = None
        self.mic_silent_logged = False
        self._watchdog_last_frame_at = 0.0
        self._first_frame = False

    def initialize_health(self) -> None:
        """Initialize clocks at startup, before the VAD thread exists."""
        self.last_frame_at = time.monotonic()
        self.last_loud_at = time.monotonic()
        self.mic_silent_logged = False

    def begin_watchdog(self) -> None:
        """Start (or reset) first-frame grace on the VAD thread."""
        self._watchdog_last_frame_at = time.monotonic()
        self._first_frame = False

    def note_missing_frame(self, restart: Callable[[str], None]) -> None:
        """Restart an exited or stalled capture, preserving grace even on errors."""
        stall_limit = (
            self.config.capture_stall_restart_s
            if self._first_frame
            else self.config.capture_first_frame_grace_s
        )
        if self._is_running() and (
            self._get_capture().failed
            or time.monotonic() - self._watchdog_last_frame_at >= stall_limit
        ):
            cause = "ffmpeg exited" if self._get_capture().failed else "audio stalled"
            try:
                restart(cause)
            finally:
                self.begin_watchdog()
        else:
            time.sleep(0.01)

    def note_frame(self, frame: bytes) -> None:
        """Record a received frame before the pipeline segments or logs it."""
        self.note_capture_healthy()
        now = time.monotonic()
        self._watchdog_last_frame_at = now
        self._first_frame = True
        self.last_frame_at = now
        self.down = False
        self.note_microphone_level(frame, now)

    def restart(self, cause: str, before_restart: Callable[[], None]) -> None:
        """Restart capture after ffmpeg exits or stalls (VAD thread only).

        Without this, an unplugged microphone or a driver reset ended detection
        for the rest of the service while the process kept running, so neither
        the operator nor a supervisor could tell. The utterance in progress is
        closed and queued (it is real speech up to the failure), then ffmpeg is
        restarted with doubling delays until it starts or the pipeline stops.
        The delays carry over between failures until a restarted stream has
        delivered ``capture_healthy_s`` of audio, so a flapping device backs off.
        """
        self.metrics.capture_failures += 1  # Counts stalled streams as well as exits.
        self.down = True
        before_restart()

        while self._is_running():
            delay = min(
                self.config.capture_restart_base_delay * 2**self.restart_attempt,
                self.config.capture_restart_max_delay,
            )
            self.restart_attempt += 1
            logger.error(
                f"Audio capture failed ({cause}); no references can be detected "
                f"until it restarts. Restarting in {delay:.0f}s "
                f"(attempt {self.restart_attempt})"
            )
            if not self.wait_while_running(delay):
                return

            with self.lock:
                if not self._is_running():
                    return
                self._get_capture().stop_capture()
                try:
                    self._get_capture().start_capture(self.config.input_device)
                except Exception as e:
                    logger.error(f"Audio capture restart failed: {e}")
                    continue

            self.metrics.capture_restarts += 1
            # The silence clock runs from the new stream, not from before the outage.
            self.last_loud_at = time.monotonic()
            self.mic_silent_logged = False
            session_log = self._get_session_log()
            if session_log is not None:
                session_log.event("capture_restarted")
            self.restarted_at_s = self._get_capture().captured_seconds
            logger.warning("Audio capture restarted; listening for Bible references again")
            return

    def note_capture_healthy(self) -> None:
        """Reset the restart backoff once a restarted stream has proven stable."""
        if self.restarted_at_s is None:
            return
        streamed = self._get_capture().captured_seconds - self.restarted_at_s
        if streamed >= self.config.capture_healthy_s:
            self.restart_attempt = 0
            self.restarted_at_s = None

    def note_microphone_level(self, frame: bytes, now: float) -> None:
        """Track whether the microphone is picking up sound (VAD thread only).

        Only the transitions are logged, never every frame: the warning once
        when the silence passes SILENT_MIC_S, and the all-clear when sound returns.
        """
        if frame_is_loud(frame):
            self.last_loud_at = now
            if self.mic_silent_logged:
                self.mic_silent_logged = False
                logger.info("The microphone is picking up sound again")
        elif (
            not self.mic_silent_logged
            and self.last_loud_at is not None
            and now - self.last_loud_at > SILENT_MIC_S
        ):
            self.mic_silent_logged = True
            logger.warning(
                f"The microphone is silent: no sound above {SOUND_LEVEL_DBFS:.0f} dBFS for "
                f"{SILENT_MIC_S:.0f} s. Check that the right microphone is selected "
                f"(--input-device) and not muted."
            )

    def wait_while_running(self, seconds: float) -> bool:
        """Sleep up to ``seconds``, waking early on stop; return whether still running."""
        deadline = time.monotonic() + seconds
        while self._is_running() and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return self._is_running()
