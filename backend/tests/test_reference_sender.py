"""Tests for audio.ReferenceSender (dedup + retry/backoff)."""

import threading
import time
from types import SimpleNamespace

import pytest
import requests

from audio.config import AudioPipelineConfig
from audio.metrics import PipelineMetrics
from audio.sender import ReferenceSender


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def make_ref(book="John", chapter=3, verse_start=16, verse_end=None, confidence="high"):
    return SimpleNamespace(
        book=book,
        chapter=chapter,
        verse_start=verse_start,
        verse_end=verse_end,
        confidence=confidence,
        inferred=False,
    )


@pytest.fixture
def sender(monkeypatch):
    cfg = AudioPipelineConfig(api_max_retries=2, api_retry_base_delay=0.0, api_retry_max_delay=0.0)
    return ReferenceSender("http://server", cfg, PipelineMetrics())


def test_send_success(monkeypatch, sender):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse(201)

    monkeypatch.setattr("audio.sender.requests.post", fake_post)
    ref = make_ref()
    sender.send(ref)

    assert len(calls) == 1
    url, payload, _ = calls[0]
    assert url == "http://server/api/reference"
    assert payload == {
        "book": "John",
        "chapter": 3,
        "verse_start": 16,
        "verse_end": None,
        "confidence": "high",
        "inferred": False,
        "text": "",
    }
    assert sender.metrics.references_sent == 1
    assert sender.metrics.api_retries == 0


def test_duplicate_suppressed(monkeypatch, sender):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(url)
        return FakeResponse(201)

    monkeypatch.setattr("audio.sender.requests.post", fake_post)
    sender.send(make_ref())
    sender.send(make_ref())

    assert len(calls) == 1
    assert sender.metrics.references_deduplicated == 1


def test_server_error_retries_then_succeeds(monkeypatch, sender):
    responses = [FakeResponse(503), FakeResponse(500), FakeResponse(200)]

    def fake_post(url, json, timeout):
        return responses.pop(0)

    monkeypatch.setattr("audio.sender.requests.post", fake_post)
    monkeypatch.setattr("audio.sender.time.sleep", lambda _s: None)

    sender.send(make_ref())

    assert not responses
    assert sender.metrics.api_retries == 2
    assert sender.metrics.references_sent == 1
    assert sender.metrics.references_failed == 0


def test_client_error_not_retried(monkeypatch, sender):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(url)
        return FakeResponse(422, "bad request")

    monkeypatch.setattr("audio.sender.requests.post", fake_post)

    sender.send(make_ref())

    assert len(calls) == 1
    assert sender.metrics.references_failed == 1
    assert sender.metrics.api_retries == 0


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.Timeout(),
        requests.exceptions.ConnectionError("refused"),
    ],
)
def test_timeout_and_connection_error_retried(monkeypatch, sender, exc):
    attempts = []

    def fake_post(url, json, timeout):
        attempts.append(url)
        if len(attempts) < 3:
            raise exc
        return FakeResponse(201)

    monkeypatch.setattr("audio.sender.requests.post", fake_post)
    monkeypatch.setattr("audio.sender.time.sleep", lambda _s: None)

    sender.send(make_ref())

    assert len(attempts) == 3
    assert sender.metrics.references_sent == 1
    assert sender.metrics.api_retries == 2


def test_retries_exhausted_marks_failure(monkeypatch, sender):
    def fake_post(url, json, timeout):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr("audio.sender.requests.post", fake_post)
    monkeypatch.setattr("audio.sender.time.sleep", lambda _s: None)

    sender.send(make_ref())

    # api_max_retries=2 -> 3 attempts total, 2 backoffs
    assert sender.metrics.api_retries == 2
    assert sender.metrics.references_failed == 1
    assert sender.metrics.references_sent == 0


def test_retry_delay_capped(monkeypatch):
    cfg = AudioPipelineConfig(api_retry_base_delay=1.0, api_retry_max_delay=2.0)
    s = ReferenceSender("http://server", cfg, PipelineMetrics())
    monkeypatch.setattr("audio.sender.random.uniform", lambda a, b: b)
    assert s._retry_delay(0) == pytest.approx(1.1)
    assert s._retry_delay(1) == pytest.approx(2.0)  # capped at max delay
    assert s._retry_delay(5) == 2.0  # capped


def test_send_includes_bearer_token_when_configured(monkeypatch):
    """A server with auth enabled needs the operator token on every reference."""
    cfg = AudioPipelineConfig(api_token="pipeline-token")
    sender = ReferenceSender("http://server", cfg, PipelineMetrics())
    seen = []

    def fake_post(url, json, timeout, headers=None):
        seen.append(headers)
        return FakeResponse(201)

    monkeypatch.setattr("audio.sender.requests.post", fake_post)
    sender.send(make_ref())

    assert seen == [{"Authorization": "Bearer pipeline-token"}]


def test_send_does_not_retry_unauthorized(monkeypatch, sender):
    """A 401 will not fix itself, so it is not retried and is counted as failed."""
    calls = []

    def fake_post(url, json, timeout):
        calls.append(url)
        return FakeResponse(401, "Missing authorization header")

    monkeypatch.setattr("audio.sender.requests.post", fake_post)
    sender.send(make_ref())

    assert len(calls) == 1
    assert sender.metrics.references_failed == 1
    assert sender.metrics.api_retries == 0


def test_delivery_problem_set_when_retries_run_out_and_cleared_by_a_success(monkeypatch, sender):
    """The heartbeat reports this, so it must describe the latest send only."""
    responses = iter([FakeResponse(503)] * 3 + [FakeResponse(201)])
    monkeypatch.setattr("audio.sender.requests.post", lambda *a, **k: next(responses))

    sender.send(make_ref())
    assert sender.delivery_problem == "the server kept failing or did not answer"

    sender.send(make_ref(chapter=4))
    assert sender.delivery_problem is None


def test_delivery_problem_set_on_unauthorized(monkeypatch, sender):
    monkeypatch.setattr("audio.sender.requests.post", lambda *a, **k: FakeResponse(401))

    sender.send(make_ref())

    assert sender.delivery_problem == "the server rejected the pipeline's token"


def test_rejected_reference_is_not_a_delivery_problem(monkeypatch, sender):
    """A 404 for a verse that does not exist is the server doing its job."""
    monkeypatch.setattr("audio.sender.requests.post", lambda *a, **k: FakeResponse(404, "No such"))

    sender.send(make_ref())

    assert sender.metrics.references_failed == 1
    assert sender.delivery_problem is None


# ---------------------------------------------------------------------------
# Background delivery: submit() never blocks the transcription thread
# ---------------------------------------------------------------------------
def _background_sender(**config) -> ReferenceSender:
    settings = {"api_retry_base_delay": 0.0, "api_retry_max_delay": 0.0, **config}
    return ReferenceSender("http://server", AudioPipelineConfig(**settings), PipelineMetrics())


def _wait_for(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for the sender thread"
        time.sleep(0.01)


def test_submit_returns_at_once_while_the_server_is_slow(monkeypatch):
    """The reason for the thread: a hung POST must not hold transcription."""
    release = threading.Event()
    posted = []

    def slow_post(url, json, timeout, **kwargs):
        posted.append(json["book"])
        release.wait(5)
        return FakeResponse(201)

    monkeypatch.setattr("audio.sender.requests.post", slow_post)
    sender = _background_sender()
    sender.start()
    try:
        started = time.monotonic()
        sender.submit(make_ref())
        assert time.monotonic() - started < 0.1

        _wait_for(lambda: posted == ["John"])
        release.set()
        _wait_for(lambda: sender.metrics.references_sent == 1)
    finally:
        release.set()
        sender.stop()


def test_queued_references_are_sent_in_order(monkeypatch):
    posted = []
    monkeypatch.setattr(
        "audio.sender.requests.post",
        lambda url, json, timeout, **kw: posted.append(json["chapter"]) or FakeResponse(201),
    )
    sender = _background_sender()
    for chapter in (1, 2, 3):
        sender.submit(make_ref(chapter=chapter))

    sender.start()
    try:
        _wait_for(lambda: len(posted) == 3)
    finally:
        sender.stop()
    assert posted == [1, 2, 3]


def test_submit_deduplicates_before_queueing():
    sender = _background_sender()

    sender.submit(make_ref())
    sender.submit(make_ref())

    assert sender._queue.qsize() == 1
    assert sender.metrics.references_deduplicated == 1


def test_full_queue_drops_the_oldest_reference():
    sender = _background_sender(send_queue_maxsize=2)

    for chapter in (1, 2, 3):
        sender.submit(make_ref(chapter=chapter))

    waiting = [sender._queue.get_nowait()[1].chapter for _ in range(2)]
    assert waiting == [2, 3]
    assert sender.metrics.references_dropped == 1
    assert sender.delivery_problem == "it was discarded because the send queue is full"


def test_reference_too_old_to_matter_is_dropped_not_sent(monkeypatch):
    """With auto-approve on, a late reference would go straight onto the screen."""
    posted = []
    monkeypatch.setattr(
        "audio.sender.requests.post", lambda *a, **k: posted.append(1) or FakeResponse(201)
    )
    sender = _background_sender(send_max_age_s=0.05)
    sender.submit(make_ref())
    time.sleep(0.1)

    sender.start()
    try:
        _wait_for(lambda: sender.metrics.references_dropped == 1)
    finally:
        sender.stop()
    assert posted == []
    assert sender.delivery_problem.startswith("it was discarded because it could not be sent")


def test_retries_stop_once_the_reference_is_stale(monkeypatch):
    posted = []

    def failing_post(*args, **kwargs):
        posted.append(1)
        time.sleep(0.06)
        return FakeResponse(503)

    monkeypatch.setattr("audio.sender.requests.post", failing_post)
    sender = _background_sender(send_max_age_s=0.1, api_max_retries=10)
    sender.submit(make_ref())
    sender.start()
    try:
        _wait_for(lambda: sender.metrics.references_dropped == 1)
    finally:
        sender.stop()
    assert 1 <= len(posted) < 11


def test_stop_cuts_a_retry_backoff_short(monkeypatch):
    posted = threading.Event()
    monkeypatch.setattr(
        "audio.sender.requests.post", lambda *a, **k: posted.set() or FakeResponse(503)
    )
    sender = _background_sender(api_retry_base_delay=30.0, api_retry_max_delay=30.0)
    sender.submit(make_ref())
    sender.start()
    assert posted.wait(3)

    started = time.monotonic()
    sender.stop()

    assert time.monotonic() - started < 1.0
    assert sender.metrics.references_failed == 1


def test_stop_delivers_what_is_already_queued(monkeypatch):
    """The pipeline flushes held references on shutdown, right before stop()."""
    posted = []
    monkeypatch.setattr(
        "audio.sender.requests.post",
        lambda url, json, timeout, **kw: posted.append(json["chapter"]) or FakeResponse(201),
    )
    sender = _background_sender()
    sender.submit(make_ref(chapter=7))
    sender.submit(make_ref(chapter=8))

    # Stop straight after starting: the worker sees the stop request before it
    # has taken anything off the queue, and must still empty it.
    sender.start()
    sender.stop()

    assert posted == [7, 8]
    assert sender._queue.qsize() == 0


def test_send_still_delivers_on_the_calling_thread(monkeypatch):
    """The benchmark replays through send(); it must stay synchronous."""
    monkeypatch.setattr("audio.sender.requests.post", lambda *a, **k: FakeResponse(201))
    sender = _background_sender()

    sender.send(make_ref())

    assert sender.metrics.references_sent == 1
    assert sender._thread is None
