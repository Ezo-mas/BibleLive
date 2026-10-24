"""End-to-end live path: real ffmpeg, VAD, segmenter, parser, HTTP, server, WebSocket.

The other audio tests replace one stage at a time. These run the whole chain a
Sunday service uses, with exactly one stand-in, the speech-to-text model:

    ffmpeg (fixture WAV) -> AudioCapture -> real WebRTC VAD -> Segmenter
      -> FakeSpeechToText -> StreamingParser (DB validator) -> ReferenceSender
      -> HTTP -> uvicorn + FastAPI -> real WebSocket clients

The fake STT is deterministic and needs no model download or GPU; everything
around it is production code on real threads, real subprocess pipes, a real
socket and real WebSockets.

Audio seam (no production change): ``subprocess.Popen`` as seen by
``audio.capture`` is wrapped. The wrapper receives the production argv,
verifies its layout (so the capture format contract stays pinned), swaps only
the four platform input items for a WAV file read at real time, and starts the
real process. Each launch can get its own input, so a capture restart reads a
different file than the stream that died.

Contract assertions use ``assert``; preconditions and harness failures use
``pytest.fail`` / ``RuntimeError`` / ``TimeoutError``, so a broken harness
reads differently from broken production behaviour.
"""

import importlib
import json
import os
import shutil
import subprocess
import threading
import time
import wave
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty

import pytest


def _require(module: str):
    """Import an optional dependency: fail in CI where it must exist, skip locally.

    Same policy as ``require_ffmpeg``: a silent skip in CI would leave the live
    path untested while the suite stays green. ``websockets>=11`` (sync client,
    ``recv(timeout=)``) arrives via ``uvicorn[standard]``.
    """
    try:
        return importlib.import_module(module)
    except ImportError as error:
        if os.environ.get("CI"):
            pytest.fail(f"{module} is required in CI but could not be imported: {error}")
        pytest.skip(
            f"{module} not installed ({error}); the live-path tests need it",
            allow_module_level=True,
        )


np = _require("numpy")
_require("webrtcvad")
uvicorn = _require("uvicorn")
websockets_client = _require("websockets.sync.client")

from audio.config import AudioPipelineConfig  # noqa: E402
from audio.pipeline import BibleLiveAudioProcessor  # noqa: E402
from audio.segmenter import MIN_SPEECH_FRAMES  # noqa: E402
from audio.stt import Transcription  # noqa: E402
from core.bible_lookup import BibleLookup  # noqa: E402
from core.config import config  # noqa: E402
from core.server.app import create_app  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.audio]

SAMPLE_RATE = 16000
VAD_FRAME_BYTES = SAMPLE_RATE * 30 // 1000 * 2  # 30 ms of 16-bit mono = 960 bytes
# The capture format contract: the tail of the production ffmpeg argv.
FFMPEG_OUTPUT_ARGS = ["-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
WS_TIMEOUT_S = 15.0


# --------------------------------------------------------------------------- #
# Requirements
# --------------------------------------------------------------------------- #


@pytest.fixture
def require_ffmpeg():
    """The live tests need a real ffmpeg: fail in CI, skip locally (via ``ffmpeg_seam``).

    A silent skip in CI would turn the whole live path into untested code while
    the suite stays green, so CI (which sets ``CI``) must have ffmpeg installed.
    """
    if shutil.which("ffmpeg") is not None:
        return
    if os.environ.get("CI"):
        pytest.fail("ffmpeg is required in CI (install it in the workflow); not on PATH")
    pytest.skip("ffmpeg not on PATH; the live-path tests need a real ffmpeg")


# --------------------------------------------------------------------------- #
# Fixture audio
# --------------------------------------------------------------------------- #


def _speech(seconds: float):
    """Voiced, speech-like signal the real WebRTC VAD classifies as speech.

    Harmonics (1..20, 1/k roll-off) of a ~150 Hz fundamental with 5 Hz vibrato,
    4 Hz amplitude modulation, peak 0.25 of full scale (about -12 dBFS).
    Deterministic: a closed formula, no randomness.
    """
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    f0 = 150 + 6 * np.sin(2 * np.pi * 5 * t)
    phase = 2 * np.pi * np.cumsum(f0) / SAMPLE_RATE
    wave_ = sum(np.sin(k * phase) / k for k in range(1, 21))
    wave_ = wave_ * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
    wave_ = wave_ / np.abs(wave_).max() * 0.25
    return (wave_ * 32767).astype("<i2")


def make_wav(path: Path, segments: list[tuple[str, float]]) -> Path:
    """Write a 16 kHz mono s16le WAV from ``("silence" | "speech", seconds)`` pieces."""
    pieces = []
    for kind, seconds in segments:
        if kind == "speech":
            pieces.append(_speech(seconds))
        elif kind == "silence":
            pieces.append(np.zeros(int(seconds * SAMPLE_RATE), dtype="<i2"))
        else:
            raise ValueError(f"unknown segment kind {kind!r}")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(np.concatenate(pieces).tobytes())
    return path


# --------------------------------------------------------------------------- #
# ffmpeg seam
# --------------------------------------------------------------------------- #


class FfmpegSeam:
    """Records the ffmpeg processes ``AudioCapture`` starts and what it asked for.

    ``inputs[n]`` is what the n-th launch reads; launches past the end reuse the
    last entry. ``audio_path`` sets a single input for every launch.
    """

    def __init__(self) -> None:
        self.inputs: list[Path] = []
        self.processes: list[subprocess.Popen] = []
        self.argvs: list[list[str]] = []

    @property
    def audio_path(self) -> Path | None:
        return self.inputs[0] if self.inputs else None

    @audio_path.setter
    def audio_path(self, path: Path) -> None:
        self.inputs = [path]

    @property
    def process(self) -> subprocess.Popen:
        if len(self.processes) != 1:
            pytest.fail(f"expected exactly one ffmpeg process, saw {len(self.processes)}")
        return self.processes[0]


@pytest.fixture
def ffmpeg_seam(monkeypatch, require_ffmpeg):
    """Make ``AudioCapture`` read a WAV file instead of a microphone.

    Production argv is ``ffmpeg <4 platform input items> -ac 1 -ar 16000 -f s16le -``.
    The wrapper refuses any other layout (RuntimeError: a changed capture format
    is a harness-level failure), replaces only the input items, and starts the
    real process.
    """
    seam = FfmpegSeam()
    real_popen = subprocess.Popen

    def popen(argv, *args, **kwargs):
        if (
            not isinstance(argv, list)
            or argv[0] != "ffmpeg"
            or len(argv) != 1 + 4 + len(FFMPEG_OUTPUT_ARGS)
            or argv[-len(FFMPEG_OUTPUT_ARGS) :] != FFMPEG_OUTPUT_ARGS
        ):
            raise RuntimeError(f"capture ffmpeg argv layout changed: {argv!r}")
        if not seam.inputs:
            raise RuntimeError("test forgot to set ffmpeg_seam.audio_path / inputs")
        source = seam.inputs[min(len(seam.processes), len(seam.inputs) - 1)]
        replaced = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",  # play at real time, like a live input
            "-i",
            str(source),
            *FFMPEG_OUTPUT_ARGS,
        ]
        seam.argvs.append(replaced)
        process = real_popen(replaced, *args, **kwargs)
        seam.processes.append(process)
        return process

    monkeypatch.setattr("audio.capture.subprocess.Popen", popen)
    try:
        yield seam
    finally:
        # Production's stop_capture() terminates ffmpeg but leaves its pipe
        # objects open; close them here so the suite's strict warning filter
        # does not trip over a ResourceWarning when they are garbage collected.
        for process in seam.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()


# --------------------------------------------------------------------------- #
# Fake speech-to-text
# --------------------------------------------------------------------------- #


@dataclass
class SttRecorder:
    """What the fake STT was given: one PCM blob per transcribed utterance."""

    scripted: list[Transcription]
    utterances: list[bytes] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)


@pytest.fixture
def install_fake_stt(monkeypatch):
    """Return ``install(transcripts) -> SttRecorder`` that swaps in the fake STT."""

    def install(scripted: list[Transcription]) -> SttRecorder:
        recorder = SttRecorder(scripted=list(scripted))

        class FakeSpeechToText:
            """Same surface the pipeline uses; scripted results, records its input."""

            def __init__(self, language: str = "en", **_kwargs) -> None:
                self._language = language

            @property
            def language(self) -> str:
                return self._language

            def set_language(self, language: str) -> None:
                self._language = language

            def transcribe_utterance(
                self, pcm: bytes, language: str | None = None
            ) -> Transcription:
                with recorder._lock:
                    recorder.utterances.append(bytes(pcm))
                    recorder.languages.append(language or self._language)
                    index = len(recorder.utterances) - 1
                if index < len(recorder.scripted):
                    return recorder.scripted[index]
                return Transcription(text=None, duration_ms=0.0)

        monkeypatch.setattr("audio.pipeline.SpeechToText", FakeSpeechToText)
        return recorder

    return install


# --------------------------------------------------------------------------- #
# VAD input recorder
# --------------------------------------------------------------------------- #


@dataclass
class VadInput:
    """Every frame the VAD/segmenter was fed, and every utterance it closed.

    STT receives one joined PCM blob per utterance, so exact 30 ms framing can
    only be checked where frames enter the segmenter (architecture invariant 3).
    ``closed`` holds ``(reason, frames)`` for every utterance handed on, whether
    closed by the audio (silence, pause, cap) or by ``flush()`` at a capture
    failure.
    """

    frame_sizes: list[int] = field(default_factory=list)
    started: int = 0
    closed: list[tuple[str, list[bytes]]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def frame_count(self) -> int:
        with self._lock:
            return len(self.frame_sizes)


def record_vad_input(processor: BibleLiveAudioProcessor) -> VadInput:
    """Wrap the processor's segmenter so the test sees exactly what the VAD saw."""
    seen = VadInput()
    segmenter = processor.segmenter
    real_push, real_flush = segmenter.push, segmenter.flush

    def note(event):
        if event is None:
            return
        with seen._lock:
            if event.kind == "start":
                seen.started += 1
            else:
                seen.closed.append((event.reason or "", list(event.frames)))

    def push(frame: bytes):
        with seen._lock:
            seen.frame_sizes.append(len(frame))
        event = real_push(frame)
        note(event)
        return event

    def flush():
        event = real_flush()
        note(event)
        return event

    segmenter.push = push  # type: ignore[method-assign]
    segmenter.flush = flush  # type: ignore[method-assign]
    return seen


# --------------------------------------------------------------------------- #
# Real server
# --------------------------------------------------------------------------- #


@dataclass
class RunningServer:
    port: int
    app_state: object


@pytest.fixture
def live_server(kjv_db, tmp_path, monkeypatch):
    """The real FastAPI app on a real socket (uvicorn in a background thread)."""
    if config.REQUIRE_AUTH:
        pytest.fail("these tests run unauthenticated; config.REQUIRE_AUTH must be False")
    monkeypatch.setattr(config, "SETTINGS_PATH", str(tmp_path / "server_settings.json"))
    monkeypatch.setattr(config, "AUTO_MODE", "off")
    # A proxy variable must not redirect the sender's (or any client's) loopback calls.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    app = create_app(bible_lookup=BibleLookup(str(kjv_db), readonly=True))
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", ws_ping_interval=None)
    )
    thread = threading.Thread(target=server.run, daemon=True, name="test-uvicorn")
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive():
            pytest.fail("uvicorn thread exited before the server started")
        if time.monotonic() > deadline:
            server.should_exit = True
            pytest.fail("uvicorn did not start within 10 s")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]

    try:
        yield RunningServer(port=port, app_state=app.state.app_state)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            pytest.fail("uvicorn thread did not stop within 10 s")


def wait_for_message(ws, predicate, what: str, timeout: float = WS_TIMEOUT_S) -> dict:
    """Receive until ``predicate(message)``; ``TimeoutError`` (not an assertion) if it never comes."""
    deadline = time.monotonic() + timeout
    seen: list[str] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"no {what} within {timeout}s; saw message types {seen}")
        try:
            raw = ws.recv(timeout=remaining)
        except TimeoutError:
            raise TimeoutError(f"no {what} within {timeout}s; saw message types {seen}") from None
        message = json.loads(raw)
        seen.append(message["type"])
        if predicate(message):
            return message


def expect_message_type(ws, expected: str) -> dict:
    """Receive one message that must be of ``expected`` type (harness precondition)."""
    message = json.loads(ws.recv(timeout=WS_TIMEOUT_S))
    if message["type"] != expected:
        pytest.fail(f"expected a {expected!r} message, got {message['type']!r}")
    return message


DISPLAY_QUIET_S = 0.5


def assert_display_silent(display, when: str) -> None:
    """The display socket must have no message waiting, now or within ``DISPLAY_QUIET_S``.

    Everything the server sends the display goes out in the same request that
    triggers it, so by the time the test has read the operator's reply to that
    request, any display message is already in flight; half a second is ample.
    """
    try:
        message = json.loads(display.recv(timeout=DISPLAY_QUIET_S))
    except TimeoutError:
        return
    raise AssertionError(f"display received {message['type']!r} {when}: {message}")


def connect_clients(port: int, stack):
    """Open display + operator sockets and consume their initial state."""
    display = stack.enter_context(websockets_client.connect(f"ws://127.0.0.1:{port}/ws/display"))
    expect_message_type(display, "clear")
    expect_message_type(display, "display_settings")
    operator = stack.enter_context(websockets_client.connect(f"ws://127.0.0.1:{port}/ws/operator"))
    snapshot = expect_message_type(operator, "state_snapshot")
    return operator, display, snapshot


def make_live_processor(kjv_db: Path, port: int, **overrides) -> BibleLiveAudioProcessor:
    return BibleLiveAudioProcessor(
        bible_db_path=str(kjv_db),
        server_url=f"http://127.0.0.1:{port}",
        config=AudioPipelineConfig(
            control_enabled=False,
            input_device="fixture",  # non-None: no dshow device listing on Windows
            whisper_device="cpu",  # ignored by the fake STT
            **overrides,
        ),
    )


def pending_john_3_16(message: dict) -> dict | None:
    if message["type"] != "pending_update":
        return None
    for ref in message["data"]["pending"]:
        if (ref["book"], ref["chapter"], ref["verse_start"]) == ("John", 3, 16):
            return ref
    return None


def kjv_text(kjv_db: Path, book: str, chapter: int, verse: int) -> str:
    lookup = BibleLookup(str(kjv_db), readonly=True)
    try:
        verses = lookup.get_verse_range(book, chapter, verse)
    finally:
        lookup.close()
    if not verses:
        pytest.fail(f"{book} {chapter}:{verse} missing from the test database")
    return " ".join(text for _, text in verses)


def pipeline_threads(processor: BibleLiveAudioProcessor) -> dict[str, threading.Thread | None]:
    """Every thread the running pipeline owns, by role.

    Call while the pipeline runs: ``stop()`` sets the sender's and heartbeat's
    handles to ``None``, and a capture restart replaces the capture threads.
    """
    capture = processor.audio_capture
    return {
        "vad": processor.vad_thread,
        "transcription": processor.transcription_thread,
        "capture": capture.capture_thread,
        "ffmpeg-stderr": capture.stderr_thread,
        "reference-sender": processor.sender._thread,
        "pipeline-heartbeat": processor.heartbeat._thread if processor.heartbeat else None,
    }


def assert_threads_ended(threads: dict[str, threading.Thread | None]) -> None:
    """Every pipeline thread captured while running has ended after ``stop()``."""
    for name, thread in threads.items():
        if thread is None:
            pytest.fail(f"{name} thread was never started")
        thread.join(timeout=2.0)
        assert not thread.is_alive(), f"{name} thread still alive after stop()"


def byte_accounting(processor: BibleLiveAudioProcessor) -> dict[str, int]:
    """Where every byte ffmpeg delivered ended up. Call only once VAD/capture threads ended."""
    capture = processor.audio_capture
    queued = 0
    while True:
        try:
            queued += len(capture.audio_queue.get_nowait())
        except Empty:
            break
    return {
        "captured": capture.total_bytes_captured,
        "framed": processor.metrics.audio_chunks_captured * VAD_FRAME_BYTES,
        "still_queued": queued,
        "carry_over": len(capture._frame_buffer),
        "dropped_chunks": capture.dropped_chunks,
    }


def assert_every_byte_accounted_for(processor: BibleLiveAudioProcessor) -> dict[str, int]:
    """Every captured byte became a 960-byte VAD frame or is still waiting to.

    Bytes ffmpeg delivers in arbitrary-sized chunks are re-cut into exact VAD
    frames. The old bug discarded the excess past each frame boundary, so
    ``captured`` exceeded everything below. After the VAD and capture threads
    have ended, each captured byte is in exactly one place: in a frame the VAD
    consumed (``framed``), in a chunk still in the capture queue, or in the
    sub-frame carry-over buffer. So the books balance exactly (deterministic, no
    tolerance) and the carry-over is strictly smaller than one frame.
    """
    books = byte_accounting(processor)
    assert books["dropped_chunks"] == 0, f"queue overflow lost audio: {books}"
    assert books["framed"] <= books["captured"], books
    assert books["carry_over"] < VAD_FRAME_BYTES, books
    assert books["captured"] == books["framed"] + books["still_queued"] + books["carry_over"], (
        f"captured bytes went missing between ffmpeg and the VAD frames: {books}"
    )
    return books


# --------------------------------------------------------------------------- #
# Tests 1a / 1b
# --------------------------------------------------------------------------- #


def _run_detect_then_approve(
    *,
    kjv_db: Path,
    tmp_path: Path,
    live_server: RunningServer,
    ffmpeg_seam: FfmpegSeam,
    install_fake_stt,
    transcription: Transcription,
    auto_mode: str | None,
    expected_confidence: str,
):
    """Shared flow: audio -> pending on the operator -> approve -> display shows it."""
    ffmpeg_seam.audio_path = make_wav(
        tmp_path / "service.wav",
        # Long trailing silence stands in for "the microphone is still open":
        # ffmpeg keeps running until stop_capture terminates it.
        [("silence", 1.0), ("speech", 2.0), ("silence", 30.0)],
    )
    stt = install_fake_stt([transcription])
    processor = make_live_processor(kjv_db, live_server.port)
    vad_input = record_vad_input(processor)

    with ExitStack() as stack:
        operator, display, snapshot = connect_clients(live_server.port, stack)
        if snapshot["data"]["auto_mode"] != "off":
            pytest.fail(f"server started in auto mode {snapshot['data']['auto_mode']!r}")
        if auto_mode is not None:
            operator.send(json.dumps({"action": "set_auto_mode", "auto_mode": auto_mode}))
            wait_for_message(operator, lambda m: m["type"] == "auto_mode_confirmed", "auto_mode")

        try:
            processor.start()
            threads = pipeline_threads(processor)

            message = wait_for_message(
                operator,
                lambda m: pending_john_3_16(m) is not None,
                "pending_update with John 3:16",
            )
            ref = pending_john_3_16(message)
            assert ref is not None
            assert ref["verse_end"] is None
            assert ref["confidence"] == expected_confidence

            # Audio reached the VAD in exact 30 ms frames, and STT received
            # exactly the one utterance the segmenter closed, byte for byte.
            assert set(vad_input.frame_sizes) == {VAD_FRAME_BYTES}
            assert len(vad_input.closed) == 1, vad_input.closed
            _reason, utterance_frames = vad_input.closed[0]
            assert len(utterance_frames) >= MIN_SPEECH_FRAMES
            assert stt.utterances == [b"".join(utterance_frames)]
            assert stt.languages == ["en"]

            # Approval gate: the reference is pending, so the display must have
            # received nothing. A server that displayed it on arrival would
            # have sent it right after the pending_update just read (or
            # auto-approved it), so it would be waiting on the socket here.
            assert_display_silent(display, "before the operator approved")

            # Only now does the operator approve: exactly one reference, with
            # the database text, and nothing after it.
            operator.send(json.dumps({"action": "approve", "reference_id": ref["id"]}))
            confirmed = wait_for_message(
                operator, lambda m: m["type"] in ("approval_confirmed", "error"), "approval"
            )
            assert confirmed["type"] == "approval_confirmed", confirmed
            assert confirmed["data"]["reference"]["id"] == ref["id"]
            shown = json.loads(display.recv(timeout=WS_TIMEOUT_S))
            assert shown["type"] == "reference"
            assert (shown["data"]["book"], shown["data"]["chapter"]) == ("John", 3)
            assert shown["data"]["verse_start"] == 16
            assert shown["data"]["text"] == kjv_text(kjv_db, "John", 3, 16)
            assert_display_silent(display, "after the single approved reference")
        finally:
            processor.stop()

        process = ffmpeg_seam.process
        assert process.returncode is not None, "ffmpeg was left running after stop()"
        assert_threads_ended(threads)
        assert not processor.is_running
        assert processor.metrics.capture_failures == 0
        books = assert_every_byte_accounted_for(processor)
        assert books["framed"] == vad_input.frame_count * VAD_FRAME_BYTES, books


@pytest.mark.slow
def test_detected_reference_reaches_display_only_via_operator_approval(
    kjv_db, tmp_path, live_server, ffmpeg_seam, install_fake_stt
):
    """Spoken audio becomes a pending reference; the display shows it only after approval.

    Covers the whole chain with auto-approve off: the VAD frame invariant (every
    frame fed to the VAD is exactly 960 bytes and STT gets the closed utterance
    unchanged), the operator approval gate (the display receives nothing until
    approval, then the database text), and clean shutdown (ffmpeg and every
    pipeline thread, including the sender and heartbeat, end after ``stop()``).
    """
    _run_detect_then_approve(
        kjv_db=kjv_db,
        tmp_path=tmp_path,
        live_server=live_server,
        ffmpeg_seam=ffmpeg_seam,
        install_fake_stt=install_fake_stt,
        transcription=Transcription(text="turn with me to John 3:16", duration_ms=1.0),
        auto_mode=None,
        expected_confidence="high",
    )


@pytest.mark.slow
def test_low_confidence_detection_is_not_auto_displayed_under_auto_high(
    kjv_db, tmp_path, live_server, ffmpeg_seam, install_fake_stt
):
    """A shaky decode is queued as ``low`` and stays off the display under auto="high".

    Whisper marks suspect transcripts (silence hallucinations, loops); the
    pipeline downgrades their references to ``low`` precisely so auto-approve
    cannot put them on the projector. The display stays silent while the
    reference is pending (an auto-approval would put it there at arrival), and
    then receives exactly one reference once the operator approves it by hand.
    """
    _run_detect_then_approve(
        kjv_db=kjv_db,
        tmp_path=tmp_path,
        live_server=live_server,
        ffmpeg_seam=ffmpeg_seam,
        install_fake_stt=install_fake_stt,
        transcription=Transcription(
            text="turn with me to John 3:16",
            duration_ms=1.0,
            low_confidence_reason="no_speech_prob 0.9 > 0.6",
        ),
        auto_mode="high",
        expected_confidence="low",
    )


# --------------------------------------------------------------------------- #
# Test 7
# --------------------------------------------------------------------------- #

# Restart backoff for test 7: short, so the test stays fast. It plays no part
# in draining the dead stream. Once ffmpeg exits, get_audio_frame() keeps
# returning frames from the audio already queued, and returns None only when
# that queue is empty and the stream has failed. Only then does the VAD loop
# call _restart_capture(), which flushes the open utterance and starts the new
# process after this delay. So the queue is drained by construction, before the
# restart begins.
RESTART_DELAY_S = 0.5


@pytest.mark.slow
def test_unexpected_ffmpeg_exit_flushes_then_restarts(
    kjv_db, tmp_path, live_server, ffmpeg_seam, install_fake_stt
):
    """If ffmpeg dies mid-utterance, the open utterance is flushed and capture restarts.

    Contract (``BibleLiveAudioProcessor._restart_capture``): the utterance still
    open when the stream ended is flushed and transcribed, and its reference
    reaches the operator; the failure is counted once; a new ffmpeg is started
    after the backoff and its frames are processed, with the pipeline still
    running and reporting healthy; ``stop()`` then ends every thread and every
    ffmpeg process, old and new.

    The first input is a short file that ends 0.6 s after the speech (less than
    the 1.5 s end-of-speech wait), so only the flush can close that utterance;
    the check that it closed with reason ``"flush"`` rules out the restarted
    stream's silence closing it later instead.

    What this test does not prove is draining queued audio: real-time ffmpeg
    lets the VAD keep up, so the queue is already empty when the stream dies.
    That a failed stream's queued audio is still framed before ``None`` is
    returned is covered by ``test_capture_recovery.py::
    test_unrequested_exit_marks_capture_failed_and_frames_stop_blocking``.
    """
    segments = [("silence", 1.0), ("speech", 2.0), ("silence", 0.6)]
    expected_bytes = sum(int(seconds * SAMPLE_RATE) for _, seconds in segments) * 2
    if expected_bytes % VAD_FRAME_BYTES:
        pytest.fail("fixture must be whole frames, so no partial frame is discarded on restart")
    ffmpeg_seam.inputs = [
        make_wav(tmp_path / "ends_mid_utterance.wav", segments),
        make_wav(tmp_path / "microphone_back.wav", [("silence", 30.0)]),
    ]
    stt = install_fake_stt([Transcription(text="John 3:16", duration_ms=1.0)])
    processor = make_live_processor(
        kjv_db,
        live_server.port,
        capture_restart_base_delay=RESTART_DELAY_S,
        capture_healthy_s=1.0,
    )
    vad_input = record_vad_input(processor)

    with ExitStack() as stack:
        operator, _display, _snapshot = connect_clients(live_server.port, stack)
        try:
            processor.start()
            first_stream = pipeline_threads(processor)

            # Precondition: the first ffmpeg played the file and exited on its own.
            deadline = time.monotonic() + 20
            while ffmpeg_seam.processes[0].poll() is None:
                if time.monotonic() > deadline:
                    pytest.fail("ffmpeg did not finish playing the 3.6 s fixture within 20 s")
                time.sleep(0.02)
            if ffmpeg_seam.processes[0].returncode != 0:
                pytest.fail(f"ffmpeg exited with {ffmpeg_seam.processes[0].returncode}")
            if vad_input.started < 1:
                # Give the VAD a moment to consume the tail of the stream.
                time.sleep(0.5)
            if vad_input.started < 1:
                pytest.fail("the real VAD never reported speech for the fixture audio")

            # Contract 1: the flushed utterance's reference reaches the operator.
            try:
                message = wait_for_message(
                    operator,
                    lambda m: pending_john_3_16(m) is not None,
                    "pending_update with John 3:16",
                    timeout=10.0,
                )
            except TimeoutError as error:
                raise AssertionError(f"flushed reference never reached the operator: {error}")
            assert pending_john_3_16(message)["confidence"] == "high"  # type: ignore[index]

            # ...and it got there through the drain: the only utterance closed
            # before the restart was flushed, and STT received exactly it.
            assert [reason for reason, _ in vad_input.closed] == ["flush"], vad_input.closed
            flushed = vad_input.closed[0][1]
            assert len(flushed) >= MIN_SPEECH_FRAMES
            assert stt.utterances == [b"".join(flushed)]

            # Contract 2: ffmpeg is started again after the backoff.
            deadline = time.monotonic() + RESTART_DELAY_S + 10
            while len(ffmpeg_seam.processes) < 2:
                if time.monotonic() > deadline:
                    raise AssertionError("capture was not restarted after ffmpeg exited")
                time.sleep(0.02)
            restarted = ffmpeg_seam.processes[1]
            assert ffmpeg_seam.argvs[1][ffmpeg_seam.argvs[1].index("-i") + 1].endswith(
                "microphone_back.wav"
            )

            # Contract 3: the restarted stream's frames are processed: at least
            # 2 s more of 30 ms frames reach the VAD after the restart.
            frames_at_restart = vad_input.frame_count
            deadline = time.monotonic() + 10
            while vad_input.frame_count < frames_at_restart + 67:
                if time.monotonic() > deadline:
                    raise AssertionError(
                        f"restarted capture delivered no audio "
                        f"({vad_input.frame_count - frames_at_restart} frames in 10 s)"
                    )
                time.sleep(0.05)
            assert restarted.poll() is None, "restarted ffmpeg exited"
            assert processor.is_running
            assert not processor.audio_capture.failed
            assert processor.metrics.capture_failures == 1
            assert processor.metrics.capture_restarts == 1
            assert set(vad_input.frame_sizes) == {VAD_FRAME_BYTES}
            # The operator would see a healthy pipeline again.
            assert processor.health_report()["problems"] == []
            second_stream = pipeline_threads(processor)
        finally:
            processor.stop()

        # Contract 4: stop() ends every thread of both streams and every ffmpeg.
        assert_threads_ended(first_stream)
        assert_threads_ended(second_stream)
        for process in ffmpeg_seam.processes:
            assert process.returncode is not None, "an ffmpeg process was left running"
        assert len(ffmpeg_seam.processes) == 2
        assert not processor.is_running
        # Nothing was lost across the restart: the dead stream ended on a frame
        # boundary, so every captured byte (both streams) is framed or waiting.
        books = assert_every_byte_accounted_for(processor)
        assert books["framed"] == vad_input.frame_count * VAD_FRAME_BYTES, books


# --------------------------------------------------------------------------- #
# VAD frame carry-over (architecture invariant 3), no ffmpeg needed
# --------------------------------------------------------------------------- #


def test_audio_frames_are_exact_and_lose_no_bytes_across_misaligned_chunks():
    """``get_audio_frame`` re-cuts arbitrary-sized chunks into exact frames, losing nothing.

    ffmpeg's pipe reads can return any size (320 bytes on Linux, but not
    guaranteed), so the live test above only catches a dropped carry-over when
    reads happen to be misaligned. This pins it deterministically: chunk sizes
    that straddle the 960-byte frame boundary in every way, a known byte
    pattern, and a byte-for-byte comparison. The old bug discarded the excess
    past each frame, so the concatenated frames diverged from the input.
    """
    from audio.capture import AudioCapture

    capture = AudioCapture()
    capture.is_running = True
    sizes = [320, 128, 700, 1, 959, 961, 1920, 33, 960, 5000, 17, 959, 2, 1]
    data = bytes(i % 251 for i in range(sum(sizes)))
    offset = 0
    for size in sizes:
        capture.audio_queue.put(data[offset : offset + size])
        offset += size

    frame_count = len(data) // VAD_FRAME_BYTES
    frames: list[bytes | None] = []

    def read_frames() -> None:
        frames.extend(capture.get_audio_frame(30) for _ in range(frame_count))

    # A regression that loses bytes would starve get_audio_frame and block it
    # forever; bound the wait so that is a failure, not a hung suite.
    reader = threading.Thread(target=read_frames, daemon=True, name="frame-reader")
    reader.start()
    reader.join(timeout=10)
    if reader.is_alive():
        capture.is_running = False  # release the blocked reader
        pytest.fail("get_audio_frame starved: bytes were lost between queued chunks")

    assert all(frame is not None and len(frame) == VAD_FRAME_BYTES for frame in frames)
    assert b"".join(frames) == data[: frame_count * VAD_FRAME_BYTES]  # type: ignore[arg-type]
    # The remainder is the sub-frame carry-over plus chunks never needed yet.
    leftover = bytes(capture._frame_buffer)
    while not capture.audio_queue.empty():
        leftover += capture.audio_queue.get_nowait()
    assert leftover == data[frame_count * VAD_FRAME_BYTES :]
    assert len(capture._frame_buffer) < VAD_FRAME_BYTES
