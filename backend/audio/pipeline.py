"""Main audio processing pipeline for BibleLive."""

import logging
import os
import threading
import time
from operator import mul
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any

from core.bible_lookup import BibleLookup
from core.verse_parser import Reference, StreamingParser
from core.verse_text import VerseTextIndex

from .capture import AudioCapture
from .config import AudioPipelineConfig
from .control import LanguageControlServer
from .detection import (
    build_streaming_parser,
    flush_held,
    parser_sources,
    switch_language,
    unique_references,
    whisper_start_language,
)
from .ensemble import merge_agreement
from .heartbeat import HeartbeatSender
from .metrics import PipelineMetrics
from .partials import PartialMailbox, PartialScheduler, PartialSnapshot, PartialTranscript
from .segmenter import FRAME_DURATION_MS, Segmenter, SegmentEvent
from .sender import ReferenceSender
from .session_log import SessionLog
from .stt import SUPPORTED_LANGUAGES, SpeechToText, Transcription
from .stt_gguf import (
    MultiModelSpeechToText,
    create_gguf_speech_to_text,
    engine_models,
)
from .transcript_sender import TranscriptSender
from .vad import create_vad

logger = logging.getLogger(__name__)

# Seconds without a single audio frame before the operator is told capture has
# stalled. Frames arrive every 30 ms, so this is far beyond any normal hiccup.
AUDIO_STALL_S = 5.0

# 16 kHz, 16-bit mono PCM: the bytes of one second of audio (the VAD frames are this format).
PCM_BYTES_PER_SECOND = 2 * 16000
# Seconds the transcription thread waits for a final utterance before it checks for a
# partial transcript to run. Short, so a snapshot posted while it waits is picked up promptly.
PARTIAL_POLL_S = 0.1

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


def _health_issue(code: str, text: str, **params: str | int | float) -> dict[str, Any]:
    """One health-report problem: a stable ``code``, its ``params``, and the English ``text``."""
    return {"code": code, "params": params, "text": text}


class BibleLiveAudioProcessor:
    """Main audio processing pipeline for BibleLive with reliability improvements"""

    def __init__(
        self,
        bible_db_path: str = "bible_data/biblelive.sqlite",
        server_url: str = "http://localhost:8000",
        config: AudioPipelineConfig | None = None,
        verse_text_db_path: str | None = None,
    ):
        # Configuration
        self.config = config or AudioPipelineConfig()
        self.bible_db_path = bible_db_path
        # Lithuanian Bible text: a chapter-only reference gets the verse the
        # speaker reads aloud. Optional; without it the chapter is reported.
        self.verse_text_db_path = verse_text_db_path
        self.server_url = server_url

        # Initialize components with bounded queues
        self.audio_capture = AudioCapture(queue_maxsize=self.config.audio_queue_maxsize)
        self.vad = create_vad(
            self.config.vad,
            aggressiveness=self.config.vad_aggressiveness,
            silero_threshold=self.config.silero_threshold,
            silero_neg_threshold=self.config.silero_neg_threshold,
        )
        # Whisper for every language, or GGUF models for Lithuanian (Whisper
        # then loads only once a non-Lithuanian utterance needs it).
        self._stt_models = engine_models(self.config.stt_engine)
        self.stt: SpeechToText | MultiModelSpeechToText
        if self._stt_models:
            self.stt = create_gguf_speech_to_text(
                self.config.stt_engine,
                {"kmynas": self.config.kmynas_model, "canary": self.config.canary_model},
                device=self.config.whisper_device,
                cpu_threads=self.config.cpu_threads,
                language=self.config.language,
                whisper_factory=self._create_whisper,
            )
        else:
            self.stt = self._create_whisper()

        # VAD queue for speech segments (bounded). Each item is
        # ``(language, frames)``: the language the utterance was captured in, so
        # a switch does not retroactively re-tag segments already queued.
        self.vad_queue: Queue[tuple[str, list[bytes]]] = Queue(
            maxsize=self.config.vad_queue_maxsize
        )

        # State tracking
        self.is_running = False
        self.frame_duration_ms = FRAME_DURATION_MS
        self._frame_duration_s = self.frame_duration_ms / 1000
        # Utterance assembly, shared with the benchmark (owned by the VAD thread).
        self.segmenter = Segmenter.from_config(self.vad.is_speech, self.config)

        # Metrics
        self.metrics = PipelineMetrics()
        self.last_metrics_log = time.time()

        # Reference delivery (dedup + retry-with-backoff)
        self.sender = ReferenceSender(server_url, self.config, self.metrics)
        # Confident transcripts for follow-along hints (best effort, never blocks).
        self.transcript_sender = TranscriptSender(server_url, self.config, self.metrics)
        # Partial transcripts of the utterance still being spoken, for follow-along only
        # (see partials.py). The VAD thread owns the scheduler and the utterance number;
        # the mailbox hands snapshots to the transcription thread, which owns the accumulator.
        # The scheduler and the accumulator are built only with partials on, so a bad
        # partial setting cannot stop a service that does not use them.
        partials_on = self.config.partial_transcripts
        self._partial_schedule: PartialScheduler | None = (
            PartialScheduler.from_config(self.config) if partials_on else None
        )
        self._partial_mailbox = PartialMailbox()
        self._partial_transcript: PartialTranscript | None = (
            PartialTranscript(self.config.partial_mode) if partials_on else None
        )
        self._utterance_seq = 0  # VAD thread: bumped at each utterance start

        # Thread-local storage for BibleLookup + per-language parsers (owned by
        # the transcription thread).
        self._local = threading.local()

        # Runtime language switching. The control thread records a request in
        # ``_pending_language``; the VAD thread applies it to its own capture
        # state; the transcription thread picks the parser from each utterance's
        # tag. ``_capture_language`` is only touched by the VAD thread.
        self._capture_language: str = self.config.language
        self._pending_language: str | None = None
        self._pending_language_lock = threading.Lock()
        self.control_server: LanguageControlServer | None = None

        # Capture restart after an unrequested ffmpeg exit (VAD thread). The lock
        # keeps stop() and a restart from interleaving, which could otherwise
        # start a new ffmpeg after stop() has already shut capture down.
        self._capture_lock = threading.Lock()
        self._capture_restart_attempt = 0
        self._capture_restarted_at_s: float | None = None

        # Health the operator should see (reported by the heartbeat thread).
        # Each field has one writer: capture state the VAD thread, the rest the
        # transcription thread; the heartbeat thread only reads them.
        self._capture_down = False
        # time.monotonic() of the last audio frame; a stalled ffmpeg delivers
        # nothing without exiting, so silence from capture is reported too.
        self._last_frame_at: float | None = None
        # time.monotonic() of the last frame that was sound, and whether the
        # silent-microphone warning is up (VAD thread, see _note_microphone_level).
        self._last_loud_at: float | None = None
        self._mic_silent_logged = False
        self._consecutive_transcription_errors = 0
        self._parser_issues: list[dict[str, Any]] = []
        self.heartbeat: HeartbeatSender | None = (
            HeartbeatSender(server_url, self.config, self.health_report)
            if self.config.heartbeat_interval > 0
            else None
        )

        # Live session log, opened by start() when configured.
        self.session_log: SessionLog | None = None

        # Worker threads
        self.vad_thread: threading.Thread | None = None
        self.transcription_thread: threading.Thread | None = None

    def _create_whisper(self) -> SpeechToText:
        return SpeechToText(
            model_size=self.config.whisper_model,
            device=self.config.whisper_device,
            language=whisper_start_language(self._stt_models, self.config.language),
            compute_type=self.config.whisper_compute_type,
            beam_size=self.config.beam_size,
            cpu_threads=self.config.cpu_threads,
        )

    def start(self):
        """Start the audio processing pipeline.

        Raises:
            RuntimeError: if the pipeline is already running. The guard is
                outside the rollback ``try`` so a rejected second start cannot
                unwind the live run (stop capture, join its threads, or leak
                its bound control socket).
        """
        if self.is_running:
            raise RuntimeError("Audio processor is already running")

        logger.info("Starting BibleLive Audio Pipeline...")

        try:
            # Bind the control server before anything else so a busy port fails
            # startup clearly instead of after capture is already running.
            if self.config.control_enabled:
                self.control_server = LanguageControlServer(
                    get_language=lambda: self.current_language,
                    set_language=self.set_language,
                    host=self.config.control_host,
                    port=self.config.control_port,
                    token=self.config.control_token,
                )
                self.control_server.start()

            self.is_running = True
            self._last_frame_at = time.monotonic()
            self._last_loud_at = time.monotonic()
            self._mic_silent_logged = False

            if self.config.session_log_dir:
                self.session_log = self._open_session_log(self.config.session_log_dir)

            # Start audio capture
            self.audio_capture.start_capture(self.config.input_device)

            # Start reference delivery before anything can detect a reference
            self.sender.start()
            self.transcript_sender.start()

            # Start VAD processing thread
            vad_thread = threading.Thread(target=self._vad_loop, daemon=True, name="vad-processor")
            self.vad_thread = vad_thread
            vad_thread.start()

            # Start transcription thread
            transcription_thread = threading.Thread(
                target=self._transcription_loop, daemon=True, name="transcription-processor"
            )
            self.transcription_thread = transcription_thread
            transcription_thread.start()

            # Report health to the server so the operator can see a failure
            if self.heartbeat is not None:
                self.heartbeat.start()
        except BaseException:
            # A partially started pipeline must not leave the control port bound,
            # capture running, or is_running stuck True for direct callers.
            self._rollback_start()
            raise

        logger.info("Audio pipeline started. Listening for Bible references...")

    def _rollback_start(self) -> None:
        """Unwind a failed :meth:`start`, preserving the original exception."""
        self.is_running = False
        if self.heartbeat is not None:
            self.heartbeat.stop()
        try:
            self.audio_capture.stop_capture()
        except Exception as e:
            logger.warning(f"Error stopping audio capture during startup rollback: {e}")
        if self.control_server is not None:
            try:
                self.control_server.stop()
            except Exception as e:
                logger.warning(f"Error stopping control server during startup rollback: {e}")
            self.control_server = None
        for thread in (self.vad_thread, self.transcription_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
        self.sender.stop()
        self.transcript_sender.stop()
        self._close_session_log()

    def _open_session_log(self, directory: str) -> SessionLog | None:
        """Open the session log; a failure only costs the log, never detection."""
        config = self.config
        header = {
            "stt_engine": config.stt_engine,
            "models": {
                name: os.path.basename(getattr(config, f"{name}_model") or "")
                for name in getattr(self, "_stt_models", [])
            },
            "whisper_model": config.whisper_model,
            "device": config.whisper_device,
            "language": config.language,
            "vad": config.vad,
        }
        try:
            return SessionLog(directory, config.session_audio, header, self._frame_duration_s)
        except Exception as e:
            logger.error(f"Could not open the session log in {directory}: {e}")
            return None

    def _close_session_log(self) -> None:
        session_log = getattr(self, "session_log", None)
        if session_log is not None:
            session_log.close()
            self.session_log = None

    def stop(self):
        """Stop the audio processing pipeline"""
        logger.info("Stopping audio pipeline...")
        self.is_running = False

        # Stop reporting first: the server then shows the pipeline as offline,
        # which is what it is about to be.
        if self.heartbeat is not None:
            self.heartbeat.stop()

        # Stop audio capture (after any restart in progress has finished, so it
        # cannot start ffmpeg again behind our back)
        with self._capture_lock:
            self.audio_capture.stop_capture()

        # Stop the runtime language-control server
        if self.control_server is not None:
            self.control_server.stop()
            self.control_server = None

        # Wait for worker threads
        if self.vad_thread and self.vad_thread.is_alive():
            self.vad_thread.join(timeout=2.0)
        if self.transcription_thread and self.transcription_thread.is_alive():
            self.transcription_thread.join(timeout=2.0)

        # Then delivery, once nothing can submit any more
        self.sender.stop()
        self.transcript_sender.stop()

        # The transcription thread closes its own thread-local BibleLookup on
        # exit (see _transcription_loop): stop() runs on the main thread whose
        # threading.local() namespace never holds it, so closing here would be
        # dead code.

        # Last: the transcription thread logs held references it flushes
        self._close_session_log()

        # Log final metrics
        self.metrics.log_summary()
        logger.info("Audio pipeline stopped")

    @property
    def current_language(self) -> str:
        """The language the pipeline is currently transcribing in."""
        return self.stt.language

    def set_language(self, language: str) -> None:
        """Switch the spoken language at runtime (safe from the control thread).

        - the STT language/initial prompt are swapped atomically, so the next
          transcription uses the new language immediately;
        - the switch is posted to the VAD thread, which discards any partial
          utterance captured in the old language so it is not transcribed as
          the new one;
        - utterances already queued keep the tag they were captured with and
          are parsed with the matching parser.

        Raises:
            ValueError: if ``language`` is not in ``SUPPORTED_LANGUAGES``.
        """
        self.stt.set_language(language)
        with self._pending_language_lock:
            self._pending_language = language
        logger.info("Language switch requested: %s", language)

    def health_report(self) -> dict:
        """What the heartbeat tells the server: the language and any problems.

        Each problem is one sentence for the operator, naming what has stopped
        working, and comes with a stable code and its parameters so the console
        can word it in its own language. An empty list means the pipeline is
        detecting normally (a quiet room is not a problem).
        """
        issues: list[dict[str, Any]] = []
        # An uncaught exception ends a worker thread silently; this thread
        # would otherwise go on reporting a pipeline that detects nothing.
        for thread, role in (
            (self.vad_thread, "listening"),
            (self.transcription_thread, "transcription"),
        ):
            if self.is_running and thread is not None and not thread.is_alive():
                issues.append(
                    _health_issue(
                        "thread_stopped",
                        f"The pipeline's {role} thread has stopped; restart the pipeline",
                        role=role,
                    )
                )
        if self._capture_down:
            issues.append(
                _health_issue(
                    "capture_stopped",
                    "Audio capture stopped; the pipeline is trying to restart it",
                )
            )
        elif self.is_running and self._last_frame_at is not None:
            # Capture streams continuously (silence included), so a gap means
            # ffmpeg is stuck, not that the room is quiet.
            now = time.monotonic()
            silent_s = now - self._last_frame_at
            if silent_s > AUDIO_STALL_S:
                seconds = round(silent_s)
                issues.append(
                    _health_issue(
                        "audio_stalled",
                        f"No audio has arrived from the microphone for {seconds} s",
                        seconds=seconds,
                    )
                )
            elif self._last_loud_at is not None and now - self._last_loud_at > SILENT_MIC_S:
                # Frames arrive but none has any sound in it: a dead or muted input.
                seconds = round(now - self._last_loud_at)
                issues.append(
                    _health_issue(
                        "mic_silent",
                        f"The microphone is silent: no sound for {seconds} s. "
                        "Check that the right microphone is selected and not muted.",
                        seconds=seconds,
                    )
                )
        errors = self._consecutive_transcription_errors
        if errors:
            issues.append(
                _health_issue(
                    "stt_failed",
                    f"Speech recognition failed on the last {errors} "
                    f"utterance{'s' if errors != 1 else ''}",
                    count=errors,
                )
            )
        delivery_problem = self.sender.delivery_problem
        if delivery_problem:
            issues.append(
                _health_issue(
                    "delivery_failed",
                    f"The last detected reference was not delivered: {delivery_problem}",
                    detail=delivery_problem,
                )
            )
        issues.extend(self._parser_issues)
        return {
            "language": self.current_language,
            "interval_s": self.config.heartbeat_interval,
            "problems": [issue["text"] for issue in issues],
            "issues": issues,
        }

    def _consume_pending_language_switch(self) -> None:
        """Apply a pending language switch (VAD thread only).

        The VAD thread owns the segmenter, so only it may discard a partial
        utterance; the control thread merely records the request. Queued
        utterances are left alone.
        """
        with self._pending_language_lock:
            pending = self._pending_language
            self._pending_language = None

        if pending is None or pending == self._capture_language:
            return

        dropped = self.segmenter.reset()
        self._partial_mailbox.discard(self._utterance_seq)
        session_log = getattr(self, "session_log", None)
        if session_log is not None:
            session_log.event("language", language=pending, dropped_frames=dropped)
        if dropped:
            logger.info(
                "Language switched to '%s': discarding partial utterance (%d frames)",
                pending,
                dropped,
            )
        self._capture_language = pending

    def _vad_loop(self):
        """VAD processing loop - detects speech segments"""
        frame_duration_ms = self.frame_duration_ms

        while self.is_running:
            # Apply any runtime language switch before capturing more audio.
            self._consume_pending_language_switch()

            # Get audio frame
            frame = self.audio_capture.get_audio_frame(frame_duration_ms)
            if frame is None:
                if self.audio_capture.failed and self.is_running:
                    self._restart_capture()
                else:
                    time.sleep(0.01)
                continue

            self._note_capture_healthy()
            now = time.monotonic()
            self._last_frame_at = now
            self._note_microphone_level(frame, now)
            self.metrics.audio_chunks_captured += 1
            session_log = getattr(self, "session_log", None)
            if session_log is not None:
                session_log.write_frame(frame)

            event = self.segmenter.push(frame)
            if event is not None:
                if event.kind == "start":
                    self._utterance_seq += 1
                    session_log = getattr(self, "session_log", None)
                    if session_log is not None:
                        session_log.speech_started(event.onset_frames)
                    logger.info("Voice activity detected, capturing utterance...")
                else:
                    # The final covers the utterance from here on: drop its partial.
                    self._partial_mailbox.discard(self._utterance_seq)
                    self._queue_speech_segment(event)
            if self.config.partial_transcripts:
                self._offer_partial(event)

            # Update queue depth metrics
            self.metrics.audio_queue_depth = self.audio_capture.audio_queue.qsize()
            self.metrics.vad_queue_depth = self.vad_queue.qsize()

            # Periodic metrics logging
            if time.time() - self.last_metrics_log > self.config.metrics_log_interval:
                self.metrics.log_summary()
                self.last_metrics_log = time.time()

    def _offer_partial(self, event: SegmentEvent | None) -> None:
        """Hand the transcription thread a snapshot of the open utterance when one is due.

        Called for every frame, armed or not, so the scheduler sees each utterance start
        and its timing stays right when follow-along arms mid-utterance. A snapshot is
        handed over only while the server says follow-along is armed (VAD thread only).
        """
        if self._partial_schedule is None:  # partials off: the VAD loop does not call this
            return
        due = self._partial_schedule.due(self.segmenter, event)
        if due and self.transcript_sender.armed:
            snapshot = PartialSnapshot(
                self._utterance_seq, self._capture_language, tuple(self.segmenter.speech_frames)
            )
            self._partial_mailbox.put(snapshot)

    def _restart_capture(self) -> None:
        """Restart capture after ffmpeg exited on its own (VAD thread only).

        Without this, an unplugged microphone or a driver reset ended detection
        for the rest of the service while the process kept running, so neither
        the operator nor a supervisor could tell. The utterance in progress is
        closed and queued (it is real speech up to the failure), then ffmpeg is
        restarted with doubling delays until it starts or the pipeline stops.
        The delays carry over between failures until a restarted stream has
        delivered ``capture_healthy_s`` of audio, so a flapping device backs off.
        """
        self.metrics.capture_failures += 1
        self._capture_down = True
        event = self.segmenter.flush()
        self._partial_mailbox.discard(self._utterance_seq)
        if event is not None:
            self._queue_speech_segment(event)

        while self.is_running:
            delay = min(
                self.config.capture_restart_base_delay * 2**self._capture_restart_attempt,
                self.config.capture_restart_max_delay,
            )
            self._capture_restart_attempt += 1
            logger.error(
                f"Audio capture stopped unexpectedly; no references can be detected "
                f"until it restarts. Restarting in {delay:.0f}s "
                f"(attempt {self._capture_restart_attempt})"
            )
            if not self._wait_while_running(delay):
                return

            with self._capture_lock:
                if not self.is_running:
                    return
                self.audio_capture.stop_capture()
                try:
                    self.audio_capture.start_capture(self.config.input_device)
                except Exception as e:
                    logger.error(f"Audio capture restart failed: {e}")
                    continue

            self.metrics.capture_restarts += 1
            self._capture_down = False
            # The silence clock runs from the new stream, not from before the outage.
            self._last_loud_at = time.monotonic()
            self._mic_silent_logged = False
            session_log = getattr(self, "session_log", None)
            if session_log is not None:
                session_log.event("capture_restarted")
            self._capture_restarted_at_s = self.audio_capture.captured_seconds
            logger.warning("Audio capture restarted; listening for Bible references again")
            return

    def _note_capture_healthy(self) -> None:
        """Reset the restart backoff once a restarted stream has proven stable."""
        if self._capture_restarted_at_s is None:
            return
        streamed = self.audio_capture.captured_seconds - self._capture_restarted_at_s
        if streamed >= self.config.capture_healthy_s:
            self._capture_restart_attempt = 0
            self._capture_restarted_at_s = None

    def _note_microphone_level(self, frame: bytes, now: float) -> None:
        """Track whether the microphone is picking up sound (VAD thread only).

        Only the transitions are logged, never every frame: the warning once
        when the silence passes SILENT_MIC_S, and the all-clear when sound returns.
        """
        if frame_is_loud(frame):
            self._last_loud_at = now
            if self._mic_silent_logged:
                self._mic_silent_logged = False
                logger.info("The microphone is picking up sound again")
        elif (
            not self._mic_silent_logged
            and self._last_loud_at is not None
            and now - self._last_loud_at > SILENT_MIC_S
        ):
            self._mic_silent_logged = True
            logger.warning(
                f"The microphone is silent: no sound above {SOUND_LEVEL_DBFS:.0f} dBFS for "
                f"{SILENT_MIC_S:.0f} s. Check that the right microphone is selected "
                f"(--input-device) and not muted."
            )

    def _wait_while_running(self, seconds: float) -> bool:
        """Sleep up to ``seconds``, waking early on stop; return whether still running."""
        deadline = time.monotonic() + seconds
        while self.is_running and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return self.is_running

    def _queue_speech_segment(self, event: SegmentEvent) -> None:
        """Queue a closed utterance tagged with the capture language."""
        frames = event.frames
        utterance_seconds = self._frame_duration_s * len(frames)
        if event.kind == "end":
            # Before the put: once queued, the transcription thread may log
            # the utterance at once, and must find its position.
            session_log = getattr(self, "session_log", None)
            if session_log is not None:
                session_log.utterance_queued(frames, event.trailing_silent_frames)
            try:
                self.vad_queue.put((self._capture_language, frames), timeout=0.1)
                self.metrics.vad_segments_detected += 1
                logger.info(
                    f"Utterance ended ({event.reason}): {utterance_seconds:.2f}s of audio "
                    f"({len(frames)} frames, {self._capture_language}) "
                    f"queued for transcription"
                )
            except Full:
                if session_log is not None:
                    session_log.utterance_dropped(frames)
                logger.warning("VAD queue full, dropping speech segment (slow transcription)")
        else:
            logger.debug(
                f"Ignoring {utterance_seconds:.2f}s blip "
                f"(fewer than {self.segmenter.min_speech_frames} voiced frames)"
            )

    def _transcription_loop(self):
        """Transcription processing loop - transcribes speech segments"""
        # Initialize thread-local Bible lookup and one parser per language
        self._init_thread_local_parsers()

        try:
            while self.is_running:
                # Finals first (each carries the language it was captured in): a queued
                # utterance waits behind at most the one partial already running.
                try:
                    language, speech_frames = self.vad_queue.get_nowait()
                except Empty:
                    snapshot = self._partial_mailbox.take()
                    if snapshot is not None:
                        try:
                            self._process_partial(snapshot)
                        except Exception as e:
                            # Follow-along only: a failed partial is dropped. It must not end
                            # this thread, which transcribes the finals too.
                            self.metrics.partials_dropped += 1
                            logger.warning(f"Partial transcript dropped after an error: {e}")
                            logger.debug("Partial transcript error", exc_info=True)
                        continue
                    try:
                        language, speech_frames = self.vad_queue.get(timeout=PARTIAL_POLL_S)
                    except Empty:
                        continue

                # Process the utterance
                self._process_utterance(language, speech_frames)
        finally:
            # Flush held chapter-only references so stop() does not drop them,
            # then close the thread-local lookup here, the only thread that can
            # see it. Bounded gap, accepted: if a Whisper call is still in
            # flight past stop()'s 2 s join, the sender is already stopped when
            # this flush lands and the reference is lost. Rare and bounded by
            # the join timeout; shutdown must not hang on a stuck transcription.
            try:
                parsers = getattr(self._local, "streaming_parsers", None) or {}
                for held_language in list(parsers):
                    flushed = self._flush_held(held_language)
                    session_log = getattr(self, "session_log", None)
                    if session_log is not None:
                        session_log.references("shutdown_flush", flushed)
                    for ref in flushed:
                        logger.info("Flushing held reference on shutdown: %s", ref)
                        try:
                            self.sender.submit(ref)
                        except Exception as e:
                            logger.warning(f"Error submitting flushed reference: {e}")
            finally:
                lookup = getattr(self._local, "bible_lookup", None)
                if lookup is not None:
                    try:
                        lookup.close()
                    except Exception as e:
                        logger.warning(f"Error closing Bible lookup: {e}")
                    self._local.bible_lookup = None

    def _init_thread_local_parsers(self):
        """Initialize the thread-local Bible lookup and one parser per language.

        Parsers live in the transcription thread's thread-local storage: the
        control thread cannot rebuild them (its ``threading.local()`` namespace
        is a different one) and must not, so it only ever sets a flag.
        """
        if not hasattr(self._local, "bible_lookup"):
            try:
                self._local.bible_lookup = BibleLookup(self.bible_db_path, readonly=True)
                logger.debug(f"Thread {threading.current_thread().name}: Initialized Bible lookup")
            except Exception as e:
                logger.error(
                    f"Thread {threading.current_thread().name}: Failed to initialize Bible lookup: {e}"
                )
                self._local.bible_lookup = None

        lookup = self._local.bible_lookup
        validator = lookup.get_validator_hook() if lookup and lookup.conn else None
        issues: list[dict[str, Any]] = []
        if validator is None:
            # Parsing still works, but nothing checks that a detected verse
            # exists before it reaches the operator.
            issues.append(
                _health_issue(
                    "bible_db_unavailable",
                    "The pipeline could not open the Bible database, so detections "
                    "are not checked against it",
                )
            )

        verse_text = self._load_verse_text()

        # English and Lithuanian parsing stay separate implementations; the
        # language selects which tables a parser is built with. GGUF models
        # transcribe Lithuanian only, so other languages' parsers read Whisper.
        models = getattr(self, "_stt_models", [])
        parsers: dict[str, StreamingParser] = {}
        # Two-model mode: the second model's transcripts get their own parsers,
        # so its context never mixes with the first model's.
        alternate_parsers: dict[str, StreamingParser] = {}
        for language in SUPPORTED_LANGUAGES:
            try:
                sources = parser_sources(models, language)
                parsers[language] = self._make_parser(language, validator, verse_text, sources[0])
                if len(sources) > 1:
                    alternate_parsers[language] = self._make_parser(
                        language, validator, verse_text, sources[1]
                    )
            except Exception as e:
                logger.error(f"Failed to initialize '{language}' parser: {e}")
                issues.append(
                    _health_issue(
                        "parser_failed",
                        f"The '{language}' reference parser failed to start, so no "
                        f"references are detected in that language",
                        language=language,
                    )
                )

        self._parser_issues = issues
        self._local.streaming_parsers = parsers
        self._local.alternate_parsers = alternate_parsers
        self._local.last_parser_language = None
        logger.debug(
            f"Thread {threading.current_thread().name}: Initialized parsers for "
            f"{', '.join(parsers) or 'no languages'}"
        )

    @staticmethod
    def _make_parser(
        language: str,
        validator,
        verse_text: VerseTextIndex | None,
        source: str,
    ) -> StreamingParser:
        """One streaming parser for ``source``'s transcripts in ``language``."""
        return build_streaming_parser(language, source, validator, verse_text)

    def _flush_held(self, language: str) -> list[Reference]:
        """Release ``language``'s held references (both models' in two-model mode)."""
        parsers = getattr(self._local, "streaming_parsers", None) or {}
        alternates = getattr(self._local, "alternate_parsers", None) or {}
        return flush_held(parsers, alternates, language)

    def _alternate_parser(self, language: str) -> StreamingParser | None:
        """The second model's parser for ``language`` (two-model mode only)."""
        return (getattr(self._local, "alternate_parsers", None) or {}).get(language)

    def _load_verse_text(self) -> VerseTextIndex | None:
        """The Lithuanian verse-text index, or None when it is not available."""
        path = self.verse_text_db_path
        if not path:
            return None
        if not Path(path).is_file():
            logger.info(
                "Verse text database %s not found: chapter-only references are "
                "reported without picking the verse read aloud",
                path,
            )
            return None
        try:
            return VerseTextIndex.from_sqlite(path)
        except Exception as e:
            logger.warning(f"Failed to load verse text from {path}: {e}")
            return None

    def _select_parser(self, language: str) -> StreamingParser | None:
        """Select the parser for ``language``, resetting context on a switch."""
        parsers: dict[str, StreamingParser] = getattr(self._local, "streaming_parsers", None) or {}
        parser = parsers.get(language)
        if parser is None:
            logger.warning("No streaming parser available for language '%s'", language)
            return None

        last = getattr(self._local, "last_parser_language", None)
        if language != last:
            alternates = getattr(self._local, "alternate_parsers", None) or {}
            for ref in switch_language(parsers, alternates, last, language):
                logger.info("Flushing held reference on language switch: %s", ref)
                self.sender.submit(ref)
            self._local.last_parser_language = language
            logger.info("Selected '%s' parser (context reset)", language)

        return parser

    def _process_partial(self, snapshot: PartialSnapshot) -> None:
        """Transcribe the utterance still being spoken, for follow-along only (worker thread).

        One pass of the primary speech model. Nothing here reaches the parser, the
        reference sender, the session log or the transcription failure counters. A
        partial that failed or is not confident is dropped, and counted as such.
        """
        # Disarmed since the snapshot was taken: partials are only sent while armed.
        accumulator = self._partial_transcript
        if accumulator is None or not self.transcript_sender.armed:
            return
        audio = accumulator.audio_for(snapshot)
        if audio is None:
            return
        result = self.stt.transcribe_partial(audio, snapshot.language)
        if result is None:  # this engine takes no partials for this language (Whisper)
            return
        self.metrics.partials_transcribed += 1
        self.metrics.partial_audio_seconds += len(audio) / PCM_BYTES_PER_SECOND
        self.metrics.partial_transcription_ms += result.duration_ms
        usable = not result.failed and result.low_confidence_reason is None
        if not usable:
            self.metrics.partials_dropped += 1
        # Every audio chunk is passed on, usable or not, so the accumulator stays in step.
        text = accumulator.add(snapshot, result.text if usable else None)
        if text:
            self.transcript_sender.submit(text, snapshot.language)

    def _process_utterance(self, language: str, speech_frames: list[bytes]):
        """Process a completed utterance captured in ``language``"""
        result, references = self._detect_references(language, speech_frames)
        session_log = getattr(self, "session_log", None)
        if session_log is not None:
            session_log.utterance(speech_frames, language, result, references)

    def _detect_references(
        self, language: str, speech_frames: list[bytes]
    ) -> tuple[Transcription | None, list[Reference]]:
        """Transcribe, parse and send one utterance; returns what was heard and sent."""
        audio_seconds = self._frame_duration_s * len(speech_frames)
        logger.info(
            f"Processing utterance ({len(speech_frames)} frames, {audio_seconds:.2f}s audio, "
            f"language={language})..."
        )

        # Transcribe with the language this utterance was captured in, so a
        # runtime switch cannot re-language a segment that was already queued.
        self.metrics.transcription_attempts += 1
        result = self.stt.transcribe_utterance(b"".join(speech_frames), language)
        transcript, duration_ms = result.text, result.duration_ms
        # Two-model mode: the second model's transcript of the same audio.
        alternate = result.alternate
        if result.failed:
            self._consecutive_transcription_errors += 1
        else:
            self._consecutive_transcription_errors = 0

        if transcript is None and (alternate is None or alternate.text is None):
            # An utterance too short to transcribe, or one with no words in it, is
            # normal; only a speech model error counts as a failure.
            if result.failed:
                self.metrics.transcription_failures += 1
            else:
                self.metrics.transcriptions_empty += 1
            logger.debug("No transcription produced")
            return result, []

        self.metrics.transcription_successes += 1
        self.metrics.record_transcription_duration(duration_ms)
        if alternate is None:
            logger.info(f"Transcription ({duration_ms:.0f}ms): '{transcript}'")
        else:
            logger.info(
                f"Transcription ({duration_ms:.0f}ms): {result.source}: '{transcript}' | "
                f"{alternate.source}: '{alternate.text}'"
            )

        # A confident transcript also goes to the server, which may hint the verse the
        # speaker reads next. A shaky one never does: its words may not be what was said.
        confident = [
            r.text
            for r in (result, alternate)
            if r is not None and r.text and r.low_confidence_reason is None
        ]
        if confident:
            self.transcript_sender.submit(confident[0], language)

        # Process with the parser for the utterance's capture language
        parser = self._select_parser(language)
        if parser is None:
            logger.warning("Streaming parser not initialized, cannot detect references")
            return result, []

        # Wallclock-stamped so a book/chapter context older than the
        # TTL is dropped instead of attaching to stray numbers.
        now = time.time()
        references = self._parse_transcription(parser, result, now)
        if alternate is not None:
            alternate_parser = self._alternate_parser(language)
            alternate_refs = (
                self._parse_transcription(alternate_parser, alternate, now)
                if alternate_parser is not None
                else None
            )
            if references is None and alternate_refs is None:
                return result, []
            # Only references both models found keep their confidence; a
            # parser error on one model still leaves the other's (as low).
            references = merge_agreement(references or [], alternate_refs or [])
        elif references is None:
            return result, []

        if not references:
            logger.debug("No Bible references detected in transcription")
            return result, []

        self.metrics.references_detected += len(references)
        logger.info(f"Found {len(references)} Bible reference(s):")
        for ref in references:
            logger.info(
                f"  - {ref} [{ref.confidence}]" + (" (from context)" if ref.inferred else "")
            )

            # Queue for the sender thread (deduplicated); never blocks on the server
            self.sender.submit(ref)
        return result, references

    def _parse_transcription(
        self, parser: StreamingParser, result: Transcription, now: float
    ) -> list[Reference] | None:
        """One model's references in its transcript, each once; None on a parser error.

        References from a shaky decode are marked "low".
        """
        if result.text is None:
            return []
        try:
            references = parser.feed(result.text, now)
        except Exception as e:
            logger.error(f"Error parsing references: {e}", exc_info=True)
            return None

        # Collapsed here so logs and metrics describe what was actually heard.
        unique, duplicates = unique_references(references, result.low_confidence_reason)
        if duplicates:
            self.metrics.references_deduplicated += duplicates
            logger.warning(f"Collapsed {duplicates} duplicate reference(s) within this utterance")
        if result.low_confidence_reason and unique:
            self.metrics.references_low_confidence += len(unique)
            logger.warning(
                f"Low-confidence transcription ({result.low_confidence_reason}): "
                f"marking {len(unique)} reference(s) as low confidence"
            )
        return unique
