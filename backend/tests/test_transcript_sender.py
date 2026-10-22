"""Tests for audio.TranscriptSender: best-effort posting of transcripts for follow-along.

The processor side (only confident transcripts are submitted) is tested with the
other pipeline tests in test_audio_language_switch.py, which need the audio extras.
"""

import logging
import threading
import time
from types import SimpleNamespace

import pytest
import requests

from audio.config import AudioPipelineConfig
from audio.metrics import PipelineMetrics
from audio.transcript_sender import TranscriptSender

TEXT = "šeimininkas reikalauja sąskaitų atsiskaitymo"


def _reply(status: int, body=None) -> SimpleNamespace:
    """A server answer. ``body`` is its JSON (default: a success reply, follow-along off)."""
    payload = {"status": "success", "follow_along_active": False} if body is None else body
    return SimpleNamespace(status_code=status, text="", json=lambda: payload)


@pytest.fixture
def posts(monkeypatch):
    """Record each POST to the transcript endpoint.

    ``answers`` is consumed one entry per call: a status code is returned as the
    response, a ready-made reply is returned as it is, an exception is raised.
    Once it is empty every call gets a 200.
    """
    calls = []
    answers = []

    def fake_post(url, json, timeout, headers=None):
        calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        if answers:
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return _reply(answer) if isinstance(answer, int) else answer
        return _reply(200)

    monkeypatch.setattr("audio.transcript_sender.requests.post", fake_post)
    return SimpleNamespace(calls=calls, answers=answers)


def _wait_for(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition(), "the worker did not get to it in time"


def _sender(**config) -> TranscriptSender:
    cfg = AudioPipelineConfig(**config)
    return TranscriptSender("http://server", cfg, PipelineMetrics())


def test_posts_each_transcript_with_its_language(posts):
    sender = _sender()
    sender.start()
    try:
        sender.submit(TEXT, "lt")
        _wait_for(lambda: len(posts.calls) == 1)
    finally:
        sender.stop()

    [call] = posts.calls
    assert call["url"] == "http://server/api/transcript"
    assert call["json"] == {"text": TEXT, "language": "lt"}
    assert call["timeout"] == 2.0
    assert call["headers"] is None  # no token configured, so no Authorization header


def test_sends_the_bearer_token_like_the_reference_sender(posts):
    sender = _sender(api_token="pipeline-token")
    sender.start()
    try:
        sender.submit(TEXT, "lt")
        _wait_for(lambda: len(posts.calls) == 1)
    finally:
        sender.stop()

    assert posts.calls[0]["headers"] == {"Authorization": "Bearer pipeline-token"}


def test_one_attempt_per_transcript_and_failures_are_only_counted(posts):
    """No retry, and nothing that could read as a delivery problem for references."""
    posts.answers.extend([requests.exceptions.ConnectionError("refused"), 500, 200])
    metrics = PipelineMetrics()
    sender = TranscriptSender("http://server", AudioPipelineConfig(), metrics)
    sender.start()
    try:
        for text in ("one", "two", "three"):
            sender.submit(text, "lt")
        _wait_for(lambda: len(posts.calls) == 3)
    finally:
        sender.stop()

    assert [call["json"]["text"] for call in posts.calls] == ["one", "two", "three"]
    assert metrics.transcripts_failed == 2
    assert metrics.api_retries == 0
    assert metrics.references_failed == 0
    assert metrics.references_sent == 0


def test_a_failed_request_is_logged_at_debug_only(posts, caplog):
    posts.answers.append(requests.exceptions.Timeout("slow"))
    sender = _sender()
    sender.start()
    try:
        with caplog.at_level(logging.DEBUG, logger="audio.transcript_sender"):
            sender.submit(TEXT, "lt")
            _wait_for(lambda: len(posts.calls) == 1)
    finally:
        sender.stop()

    levels = [r.levelno for r in caplog.records if r.name == "audio.transcript_sender"]
    assert levels == [logging.DEBUG]
    assert TEXT not in caplog.text  # the transcript itself is never logged


def test_queue_keeps_the_newest_transcripts_when_full(posts):
    """The worker is not running yet, so the queue fills and the oldest is dropped."""
    metrics = PipelineMetrics()
    sender = TranscriptSender(
        "http://server", AudioPipelineConfig(transcript_queue_maxsize=2), metrics
    )
    sender.submit("one", "lt")
    sender.submit("two", "lt")
    sender.submit("three", "lt")
    assert metrics.transcripts_dropped == 1

    sender.start()
    try:
        _wait_for(lambda: len(posts.calls) == 2)
    finally:
        sender.stop()

    assert [call["json"]["text"] for call in posts.calls] == ["two", "three"]


def test_an_older_server_without_the_endpoint_is_told_once_and_not_posted_to_again(posts, caplog):
    posts.answers.append(404)
    sender = _sender()
    with caplog.at_level(logging.INFO, logger="audio.transcript_sender"):
        sender.start()
        try:
            sender.submit(TEXT, "lt")
            _wait_for(lambda: len(posts.calls) == 1)
        finally:
            sender.stop()  # joins the worker, so the 404 has been handled

        sender.submit(TEXT, "lt")
        sender.submit(TEXT, "lt")

    assert len(posts.calls) == 1
    assert sender._queue.empty()
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "no follow-along endpoint" in infos[0].getMessage()


def test_stop_is_safe_before_start_and_twice(posts):
    sender = _sender()
    sender.stop()
    sender.start()
    sender.stop()
    sender.stop()
    assert posts.calls == []


def test_items_queued_behind_a_404_are_skipped_not_posted(posts, caplog):
    posts.answers.append(404)
    metrics = PipelineMetrics()
    sender = TranscriptSender("http://server", AudioPipelineConfig(), metrics)
    for text in ("one", "two", "three"):
        sender.submit(text, "lt")

    with caplog.at_level(logging.INFO, logger="audio.transcript_sender"):
        sender.start()
        try:
            _wait_for(sender._queue.empty)  # the worker has taken all three
        finally:
            sender.stop()

    assert [call["json"]["text"] for call in posts.calls] == ["one"]
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "no follow-along endpoint" in infos[0].getMessage()
    # A 404 says the server is older, not that a transcript failed to arrive.
    assert metrics.transcripts_failed == 0


def test_stop_empties_the_queue_without_posting(posts):
    sender = _sender()
    sender.submit("one", "lt")
    sender.submit("two", "lt")

    sender.stop()

    assert sender._queue.empty()
    assert posts.calls == []


def test_stop_empties_the_queue_behind_a_post_still_in_flight(monkeypatch):
    in_flight = threading.Event()
    release = threading.Event()
    texts = []

    def slow_post(url, json, timeout, headers=None):
        texts.append(json["text"])
        in_flight.set()
        release.wait(5)
        return _reply(200)

    monkeypatch.setattr("audio.transcript_sender.requests.post", slow_post)
    sender = _sender()
    sender.start()
    worker = sender._thread
    try:
        sender.submit("one", "lt")
        assert in_flight.wait(2)
        sender.submit("two", "lt")
        sender.submit("three", "lt")

        sender.stop(timeout=0.1)  # the join gives up while "one" is still in flight

        assert sender._queue.empty()
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert texts == ["one"]  # "two" and "three" were drained, never posted


# --- armed: the server's follow_along_active answer -----------------------------


def _armed_reply(active: bool = True) -> SimpleNamespace:
    return _reply(200, {"status": "success", "follow_along_active": active})


def _not_json():
    raise ValueError("no JSON here")


def test_a_success_reply_with_the_flag_true_arms_the_sender(posts):
    posts.answers.append(_armed_reply(True))
    sender = _sender()

    sender._post(TEXT, "lt")

    assert sender.armed is True


def test_a_success_reply_with_the_flag_false_disarms_it(posts):
    posts.answers.extend([_armed_reply(True), _armed_reply(False)])
    sender = _sender()

    sender._post(TEXT, "lt")
    sender._post(TEXT, "lt")

    assert sender.armed is False


@pytest.mark.parametrize(
    "reply",
    [
        _reply(200, {"status": "success"}),  # no field: an older server that answers 200
        _reply(200, {"status": "success", "follow_along_active": "true"}),  # not a JSON boolean
        _reply(200, ["follow_along_active"]),  # not an object
        SimpleNamespace(status_code=200, text="<html>", json=_not_json),  # not JSON at all
    ],
    ids=["missing-field", "string-flag", "not-an-object", "not-json"],
)
def test_a_reply_that_does_not_say_armed_leaves_it_disarmed(posts, reply):
    posts.answers.append(reply)
    sender = _sender()

    sender._post(TEXT, "lt")

    assert sender.armed is False


@pytest.mark.parametrize(
    "failure",
    [requests.exceptions.ConnectionError("refused"), 404, 500],
    ids=["request-failed", "not-found", "server-error"],
)
def test_a_failed_post_disarms_the_sender(posts, failure):
    posts.answers.extend([_armed_reply(True), failure])
    sender = _sender()

    sender._post(TEXT, "lt")
    assert sender.armed is True
    sender._post(TEXT, "lt")

    assert sender.armed is False


def test_stop_disarms_the_sender(posts):
    posts.answers.append(_armed_reply(True))
    sender = _sender()
    sender._post(TEXT, "lt")
    assert sender.armed is True

    sender.stop()

    assert sender.armed is False


def test_the_worker_thread_arms_the_sender_from_its_reply(posts):
    posts.answers.append(_armed_reply(True))
    sender = _sender()
    sender.start()
    try:
        sender.submit(TEXT, "lt")
        _wait_for(lambda: sender.armed)
    finally:
        sender.stop()
