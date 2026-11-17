"""Translation handoff preserves streaming context and owns SQLite on the worker."""

import json
import sqlite3
import threading
from contextlib import closing
from types import SimpleNamespace

import pytest
import requests
from processor_factory import make_processor

from audio.config import AudioPipelineConfig
from audio.heartbeat import HeartbeatSender
from audio.pipeline import BibleLiveAudioProcessor
from audio.session_log import SessionLog
from core.verse_parser import Reference
from core.version import version_string

pytestmark = pytest.mark.audio


def _database(path, version, last_verse):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE books (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE verses (book_id INTEGER, chapter INTEGER, verse INTEGER, text TEXT);
            CREATE TABLE bible_metadata (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO books VALUES (1, '1 Samuel');
        """)
        conn.execute("INSERT INTO bible_metadata VALUES ('translation', ?)", (version,))
        conn.executemany(
            "INSERT INTO verses VALUES (1, 24, ?, 'text')",
            [(verse,) for verse in range(22, last_verse + 1)],
        )


def _reply(monkeypatch, sender, body=b'{"bible_version":"ltb"}', status=200):
    response = requests.Response()
    response.status_code = status
    response._content = body
    monkeypatch.setattr("audio.heartbeat.requests.post", lambda *a, **kw: response)
    assert sender.beat()


@pytest.fixture
def processor(tmp_path):
    _database(tmp_path / "kjv.sqlite", "kjv", 22)
    _database(tmp_path / "ltb.sqlite", "ltb", 23)
    config = AudioPipelineConfig(stt_engine="kmynas+canary")
    sender = HeartbeatSender("http://server", config, lambda: {})
    proc = make_processor(
        config=config,
        bible_db_path=str(tmp_path / "kjv.sqlite"),
        heartbeat=sender,
        sender=SimpleNamespace(delivery_problem=None),
        stt=SimpleNamespace(language="en"),
    )
    proc._init_thread_local_parsers()
    yield proc
    proc._local.bible_lookup.close()


def test_reply_switches_validator_without_losing_context(processor, monkeypatch):
    proc = processor
    parser = proc._local.detector.streaming_parsers["en"]
    assert not parser.feed("1 Samuel 24:23.", t_s=1)
    assert parser.feed("1 Samuel 24:22.", t_s=2)
    parser.feed("amen " * 30, t_s=2.5)  # Expire the token carry, retaining book/chapter context.
    parsers = [
        *proc._local.detector.streaming_parsers.values(),
        *proc._local.detector.alternate_parsers.values(),
    ]
    contexts = [(p.ctx, p.carry[:], p._ctx_t, p._book_t.copy()) for p in parsers]
    old_lookup = proc._local.bible_lookup
    # Receipt on the heartbeat thread must not mutate worker-owned SQLite or parsers.
    thread = threading.Thread(target=lambda: _reply(monkeypatch, proc.heartbeat))
    thread.start()
    thread.join()
    assert proc._local.bible_lookup is old_lookup
    proc._follow_bible_version()
    assert not old_lookup.ping()
    for p, (ctx, carry, ctx_t, book_t) in zip(parsers, contexts, strict=True):
        assert p.ctx is ctx
        assert (p.carry, p._ctx_t, p._book_t) == (carry, ctx_t, book_t)
        assert p.parser.validator(Reference("1 Samuel", 24, 23, None))
    refs = parser.feed("verse 23.", t_s=3)
    assert any(r.book == "1 Samuel" and r.chapter == 24 and r.verse_start == 23 for r in refs)
    assert parser.context_ttl_s == 600


def test_transcription_loop_applies_handoff_before_utterance(processor, monkeypatch):
    proc = processor
    _reply(monkeypatch, proc.heartbeat)
    proc.vad_queue.put(("en", [b"audio"]))
    proc.is_running = True
    monkeypatch.setattr(proc, "_init_thread_local_parsers", lambda: None)
    monkeypatch.setattr(proc, "_flush_held", lambda language: [])
    seen = []

    def utterance(language, frames):
        seen.append(proc._local.bible_lookup.get_metadata()["translation"])
        proc.is_running = False

    monkeypatch.setattr(proc, "_process_utterance", utterance)
    proc._transcription_loop()
    assert seen == ["ltb"]
    # The loop closes the connection; fixture teardown still accepts the lookup.
    proc._local.bible_lookup = SimpleNamespace(close=lambda: None)


@pytest.mark.parametrize("recovery", ["kjv", "ltb"])
def test_missing_version_keeps_lookup_and_recovery_clears_issue(processor, monkeypatch, recovery):
    proc = processor
    old_lookup = proc._local.bible_lookup
    _reply(monkeypatch, proc.heartbeat, b'{"bible_version":"missing"}')
    proc._follow_bible_version()
    assert proc._local.bible_lookup is old_lookup
    assert proc.health_report()["issues"] == [
        {
            "code": "bible_version_unavailable",
            "params": {"version": "missing"},
            "text": "The pipeline cannot open translation missing; it keeps checking "
            "references against the previous one",
        }
    ]
    _reply(monkeypatch, proc.heartbeat, json.dumps({"bible_version": recovery}).encode())
    proc._follow_bible_version()
    assert proc.health_report()["issues"] == []
    assert proc._local.bible_lookup.get_metadata()["translation"] == recovery


@pytest.mark.parametrize(
    "status,body",
    [
        (204, b""),
        (200, b""),
        (200, b"garbage"),
        (200, b"[]"),
        (200, b'{"bible_version":null}'),
        (200, b'{"bible_version":42}'),
    ],
)
def test_legacy_or_null_reply_keeps_requested_version(monkeypatch, status, body):
    sender = HeartbeatSender("http://server", AudioPipelineConfig(), lambda: {})
    _reply(monkeypatch, sender)
    assert sender.requested_bible_version == "ltb"
    _reply(monkeypatch, sender, body, status)
    assert sender.requested_bible_version == "ltb"


def test_real_pipeline_heartbeat_wiring_includes_stamp(monkeypatch):
    monkeypatch.setattr(
        BibleLiveAudioProcessor, "_create_whisper", lambda self: SimpleNamespace(language="en")
    )
    monkeypatch.setattr(
        "audio.pipeline.create_vad", lambda *a, **kw: SimpleNamespace(is_speech=lambda f: False)
    )
    proc = BibleLiveAudioProcessor(config=AudioPipelineConfig(stt_engine="whisper"))
    assert proc.heartbeat.get_status()["version"] == version_string()


def test_session_header_includes_stamp(tmp_path):
    log = SessionLog(tmp_path, False, {"language": "en"}, 0.03)
    log.close()
    assert (
        json.loads(log.transcript_path.read_text().splitlines()[0])["version"] == version_string()
    )


@pytest.mark.parametrize("failure", ["empty", "open", "schema"])
def test_unusable_translation_keeps_previous_lookup(processor, monkeypatch, failure):
    proc = processor
    old_lookup = proc._local.bible_lookup
    if failure == "empty":
        with closing(sqlite3.connect(old_lookup.db_path.parent / "ltb.sqlite")) as conn, conn:
            conn.execute("DELETE FROM verses")
    elif failure == "schema":
        with closing(sqlite3.connect(old_lookup.db_path.parent / "ltb.sqlite")) as conn, conn:
            conn.execute("ALTER TABLE verses RENAME COLUMN verse TO invalid_column")
    else:

        def cannot_open(*args, **kwargs):
            raise OSError("unreadable")

        monkeypatch.setattr("audio.pipeline.BibleLookup", cannot_open)
    _reply(monkeypatch, proc.heartbeat)
    proc._follow_bible_version()
    assert proc._local.bible_lookup is old_lookup
    assert old_lookup.ping()
    assert proc.health_report()["issues"][0]["code"] == "bible_version_unavailable"


def test_missing_file_is_retried_without_another_version_change(processor, monkeypatch):
    proc = processor
    clock = [100.0]
    monkeypatch.setattr("audio.pipeline.time.monotonic", lambda: clock[0])
    _reply(monkeypatch, proc.heartbeat, b'{"bible_version":"new"}')
    proc._follow_bible_version()
    assert proc.health_report()["issues"][0]["code"] == "bible_version_unavailable"
    _database(proc._local.bible_lookup.db_path.parent / "new.sqlite", "new", 23)
    clock[0] += proc.config.heartbeat_interval
    proc._follow_bible_version()
    assert proc._local.bible_lookup.get_metadata()["translation"] == "new"
    assert proc.health_report()["issues"] == []


@pytest.mark.parametrize("invalid", ["", "../kjv", "LTB", "ltb\n", "x" * 17])
def test_invalid_heartbeat_version_is_ignored_and_logged_once(monkeypatch, caplog, invalid):
    sender = HeartbeatSender("http://server", AudioPipelineConfig(), lambda: {})
    _reply(monkeypatch, sender)
    body = json.dumps({"bible_version": invalid}).encode()
    _reply(monkeypatch, sender, body)
    _reply(monkeypatch, sender, body)
    assert sender.requested_bible_version == "ltb"
    assert sum("Ignoring invalid Bible translation id" in r.message for r in caplog.records) == 1


def test_retry_warning_is_logged_once_per_requested_id(processor, monkeypatch, caplog):
    proc = processor
    clock = [100.0]
    monkeypatch.setattr("audio.pipeline.time.monotonic", lambda: clock[0])
    for requested in ("missing", "missing", "other", "other", "missing"):
        _reply(monkeypatch, proc.heartbeat, json.dumps({"bible_version": requested}).encode())
        proc._follow_bible_version()
        clock[0] += proc.config.heartbeat_interval
    warnings = [r.message for r in caplog.records if "Could not switch pipeline Bible" in r.message]
    assert len(warnings) == 2
    assert "missing" in warnings[0]
    assert "other" in warnings[1]


def test_normalized_current_metadata_does_not_reopen_lookup(processor, monkeypatch):
    proc = processor
    lookup = proc._local.bible_lookup
    monkeypatch.setattr(lookup, "get_metadata", lambda: {"translation": "  KJV  "})
    _reply(monkeypatch, proc.heartbeat, b'{"bible_version":"kjv"}')
    proc._follow_bible_version()
    assert proc._local.bible_lookup is lookup
    assert proc._local.bible_version == "kjv"


def test_translation_switch_writes_session_event(processor, monkeypatch, tmp_path):
    proc = processor
    log = SessionLog(tmp_path, False, {}, 0.03)
    proc.session_log = log
    _reply(monkeypatch, proc.heartbeat)
    proc._follow_bible_version()
    log.close()
    records = [json.loads(line) for line in log.transcript_path.read_text().splitlines()]
    assert records[1]["type"] == "bible_version"
    assert records[1]["from"] == "kjv"
    assert records[1]["to"] == "ltb"


def test_failed_follow_does_not_stop_transcription_loop(processor, monkeypatch, caplog):
    proc = processor
    proc.vad_queue.put(("en", [b"audio"]))
    proc.is_running = True
    monkeypatch.setattr(proc, "_init_thread_local_parsers", lambda: None)
    monkeypatch.setattr(proc, "_flush_held", lambda language: [])

    def fail():
        raise RuntimeError("unexpected failure")

    seen = []

    def utterance(language, frames):
        seen.append(language)
        proc.is_running = False

    monkeypatch.setattr(proc, "_follow_bible_version", fail)
    monkeypatch.setattr(proc, "_process_utterance", utterance)
    proc._transcription_loop()
    assert seen == ["en"]
    assert "Could not follow the server's Bible translation" in caplog.text
    proc._local.bible_lookup = SimpleNamespace(close=lambda: None)


def test_follows_a_translation_from_the_user_folder(tmp_path, monkeypatch):
    app, user = tmp_path / "app", tmp_path / "user"
    app.mkdir()
    user.mkdir()
    _database(app / "kjv.sqlite", "kjv", 22)
    _database(user / "bible_ltb.sqlite", "ltb", 23)
    config = AudioPipelineConfig(stt_engine="kmynas+canary")
    sender = HeartbeatSender("http://server", config, lambda: {})
    proc = make_processor(
        config=config,
        bible_db_path=str(app / "kjv.sqlite"),
        user_bible_dir=str(user),
        heartbeat=sender,
        sender=SimpleNamespace(delivery_problem=None),
        stt=SimpleNamespace(language="en"),
    )
    proc._init_thread_local_parsers()
    try:
        _reply(monkeypatch, sender)
        proc._follow_bible_version()
        validator = proc._local.detector.streaming_parsers["en"].parser.validator
        assert validator(Reference("1 Samuel", 24, 23, None))  # only the user folder's has it
        assert not proc._parser_issues
    finally:
        proc._local.bible_lookup.close()
