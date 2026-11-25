import asyncio
import logging
import struct
from unittest.mock import MagicMock, Mock

import httpx
import pytest
import requests
import uvicorn

from audio import level_sender
from audio.config import AudioPipelineConfig
from audio.level_sender import LevelSender
from audio.levels import BLOCK_SAMPLES, block_level
from core.config import config
from core.server.models import PipelineLevels

LEVELS_URL = "/api/pipeline/levels"


def test_level_sender_batch_limit_matches_api():
    schema = PipelineLevels.model_json_schema()
    assert schema["properties"]["levels"]["maxItems"] == level_sender.MAX_BATCH


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_access_log_omits_only_successful_meter_posts(app, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_AUTH", True)
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "meter-token")
    logger = logging.getLogger("uvicorn.access")
    # A handler of its own on the logger sees each record once, however earlier tests
    # left uvicorn's logging (caplog via propagation saw records twice after
    # uvicorn's dictConfig had turned propagation off).
    access = _Records()
    monkeypatch.setattr(logger, "level", logging.INFO)
    logger.addHandler(access)
    heartbeat = "/api/pipeline/heartbeat"

    async def scenario():
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, http="h11", log_config=None)
        )
        task = asyncio.create_task(server.serve())
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                        pytest.fail("uvicorn did not start")
                    await asyncio.sleep(0.01)
            port = server.servers[0].sockets[0].getsockname()[1]
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", trust_env=False
            ) as client:
                headers = {"Authorization": "Bearer meter-token"}
                batch = {"levels": [0.5]}
                assert (
                    await client.post(LEVELS_URL, json=batch, headers=headers)
                ).status_code == 204
                assert (
                    await client.post(f"{LEVELS_URL}?a=1", json=batch, headers=headers)
                ).status_code == 204
                assert (await client.post(LEVELS_URL, json=batch)).status_code == 401
                assert (
                    await client.post(LEVELS_URL, json={"levels": []}, headers=headers)
                ).status_code == 422
                assert (await client.get(LEVELS_URL)).status_code == 405
                report = {"language": "lt", "interval_s": 5}
                assert (
                    await client.post(heartbeat, json=report, headers=headers)
                ).status_code == 200
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)

    try:
        asyncio.run(scenario())
    finally:
        logger.removeHandler(access)

    assert [record.args[1:] for record in access.records] == [
        ("POST", LEVELS_URL, "1.1", 401),
        ("POST", LEVELS_URL, "1.1", 422),
        ("GET", LEVELS_URL, "1.1", 405),
        ("POST", heartbeat, "1.1", 200),
    ]


@pytest.mark.parametrize("levels", [[], [0.5] * 21, [-0.01], [1.01], [True], ["0.5"], [None]])
def test_rejects_invalid_level_batches(client, levels):
    assert client.post(LEVELS_URL, json={"levels": levels}).status_code == 422


def test_levels_require_operator_token_when_auth_enabled(client, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_AUTH", True)
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "meter-token")
    batch = {"levels": [0.0, 1.0]}

    assert client.post(LEVELS_URL, json=batch).status_code == 401
    assert (
        client.post(
            LEVELS_URL, json=batch, headers={"Authorization": "Bearer meter-token"}
        ).status_code
        == 204
    )
    state = client.get("/api/state", headers={"Authorization": "Bearer meter-token"}).json()
    assert "levels" not in state and "input_levels" not in state


@pytest.mark.parametrize("peak, expected", [(0, 0), (1, 0), (-3277, 2 / 3), (-32768, 1)])
def test_block_level_uses_the_absolute_pcm_peak(peak, expected):
    assert block_level(struct.pack("<hhh", 0, peak, 0)) == pytest.approx(expected, abs=2e-5)


def test_batch_carries_samples_across_frames_and_skips_empty_posts():
    session = Mock(spec=requests.Session)
    session.post.return_value = Mock(status_code=204)
    sender = LevelSender("http://server", AudioPipelineConfig(api_token="meter-token"))
    pcm = (
        b"\x00\x00" * (BLOCK_SAMPLES - 1) + struct.pack("<h", -32768) + b"\x00\x00" * BLOCK_SAMPLES
    )

    sender.note_frame(pcm[:918])
    sender._send_batch(session, sender._buffer.levels)
    session.post.assert_not_called()
    sender.note_frame(pcm[918:1856])
    sender.note_frame(pcm[1856:])
    sender._send_batch(session, sender._buffer.levels)
    sender._send_batch(session, sender._buffer.levels)

    session.post.assert_called_once_with(
        "http://server/api/pipeline/levels",
        json={"levels": [1.0, 0.0]},
        headers={"Authorization": "Bearer meter-token"},
        timeout=1.0,
    )


def test_worker_reuses_a_session_and_throttles_failure_runs(monkeypatch, caplog):
    session_context = MagicMock()
    open_session = Mock(return_value=session_context)
    monkeypatch.setattr(level_sender.requests, "Session", open_session)
    session = session_context.__enter__.return_value
    ok = Mock(status_code=204)
    session.post.side_effect = [
        requests.Timeout(),
        Mock(status_code=500),
        ok,
        requests.Timeout(),
        ok,
        requests.Timeout(),
        ok,
    ]
    sender = LevelSender("http://server", AudioPipelineConfig())
    timestamps = iter((0, 1, 2, 3, 4, 61, 62))
    now = 0

    def wait(_delay):
        nonlocal now
        next_time = next(timestamps, None)
        if next_time is None:
            return True
        now = next_time
        sender.note_frame(b"\x00\x00" * BLOCK_SAMPLES)
        return False

    monkeypatch.setattr(level_sender.time, "monotonic", lambda: now)
    with caplog.at_level(logging.DEBUG, logger=level_sender.__name__):
        sender._run(Mock(wait=wait), sender._buffer)

    open_session.assert_called_once_with()
    session_context.__exit__.assert_called_once()
    assert session.post.call_count == 7
    assert [
        record.levelno for record in caplog.records if record.name == level_sender.__name__
    ] == [
        logging.WARNING,
        logging.INFO,
        logging.DEBUG,
        logging.DEBUG,
        logging.WARNING,
        logging.INFO,
    ]


@pytest.mark.parametrize("status", [401, 403])
def test_auth_rejection_pauses_and_discards_levels(monkeypatch, caplog, status):
    session_context = MagicMock()
    monkeypatch.setattr(level_sender.requests, "Session", Mock(return_value=session_context))
    session = session_context.__enter__.return_value
    sender = LevelSender("http://server", AudioPipelineConfig())
    buffer = sender._buffer
    quiet = bytes(2 * BLOCK_SAMPLES)
    loud = struct.pack("<h", -32768) * BLOCK_SAMPLES
    now = 0.0
    post_times = []
    wait_delays = []

    def post(*_args, **_kwargs):
        post_times.append(now)
        if len(post_times) < 3:
            sender.note_frame(loud + loud[:2])  # Capture continued while the request was in flight.
            return Mock(status_code=status)
        return Mock(status_code=204)

    def wait(delay):
        nonlocal now
        wait_delays.append(delay)
        now += delay
        if delay == 30.0:
            assert buffer.partial is None and not buffer.levels
            sender.note_frame(loud)
            assert buffer.partial is None and not buffer.levels
        elif len(post_times) < 3:
            sender.note_frame(quiet)
        else:
            return True
        return False

    session.post.side_effect = post
    monkeypatch.setattr(level_sender.time, "monotonic", lambda: now)
    sender._run(Mock(wait=wait), buffer)

    assert wait_delays == [0.2, 30.0, 0.2, 30.0, 0.2, 0.2]
    assert post_times == pytest.approx([0.2, 30.4, 60.6])
    assert [call.kwargs["json"] for call in session.post.call_args_list] == [{"levels": [0.0]}] * 3
    assert [message for message in caplog.messages if "pausing" in message] == [
        f"Could not send microphone levels: HTTP {status}; pausing for 30 s"
    ]


def test_timed_out_worker_cannot_block_or_drain_next_start(monkeypatch):
    thread = Mock(return_value=Mock())
    monkeypatch.setattr(level_sender.threading, "Thread", thread)
    sender = LevelSender("http://server", AudioPipelineConfig())
    sender.start()
    old_stop, old_buffer = thread.call_args.kwargs["args"]

    sender.stop()
    assert sender._thread is None
    assert old_stop.is_set()

    sender.start()
    stop, buffer = thread.call_args.kwargs["args"]
    assert thread.call_count == 2
    assert not stop.is_set()
    assert buffer is not old_buffer
    sender.note_frame(b"\x00\x00" * BLOCK_SAMPLES)
    assert list(buffer.levels) == [0.0]
    assert not old_buffer.levels
