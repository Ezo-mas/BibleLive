"""Tests for audio.ReferenceSender (dedup + retry/backoff)."""

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
