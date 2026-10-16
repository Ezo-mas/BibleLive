"""Unit tests for the benchmark runner's windowed-mode helpers.

These cover only the pure functions (window slicing, silence check, overlap
dedup). No Whisper model is loaded. The runner module lives in ``benchmarks/``
at the repo root, which the root ``pythonpath`` setting puts on ``sys.path``.
"""

import numpy as np
import pytest

from benchmarks.run_benchmark import (
    BenchmarkingSender,
    Detection,
    GoldItem,
    SpanTracker,
    StubReceiver,
    dedupe_detections,
    feed_cache_key,
    feed_cache_path,
    feeds_from_cache,
    gold_distance_s,
    is_silent_window,
    match_detections_to_gold,
    parse_t_range,
    pcm_resample,
    read_feed_cache,
    register_feeds,
    verified_audio_sha,
    window_bounds,
    write_feed_cache,
)
from core.verse_parser import ReferenceParser, StreamingParser

SR = 16000


def _det(t: float, book: str = "Romans", chapter: int = 8, v1: int = 28, v2=None) -> Detection:
    return Detection(
        t_s=t,
        book=book,
        chapter=chapter,
        verse_start=v1,
        verse_end=v2,
        confidence="high",
        inferred=False,
    )


class TestWindowBounds:
    def test_hundred_seconds_30_window_15_hop(self):
        bounds = window_bounds(100 * SR, SR, 30.0, 15.0)
        starts = [s / SR for s, _ in bounds]
        assert starts == [0, 15, 30, 45, 60, 75]
        # Last window is clipped to the end of the audio and stops iteration.
        assert bounds[-1] == (75 * SR, 100 * SR)

    def test_consecutive_windows_overlap_by_window_minus_hop(self):
        bounds = window_bounds(300 * SR, SR, 30.0, 15.0)
        for (s1, e1), (s2, _) in zip(bounds, bounds[1:], strict=False):
            assert s2 - s1 == 15 * SR
            assert e1 - s2 == 15 * SR  # 50% overlap

    def test_audio_shorter_than_window_is_one_window(self):
        assert window_bounds(10 * SR, SR, 30.0, 15.0) == [(0, 10 * SR)]

    def test_hop_equal_to_window_has_no_overlap(self):
        bounds = window_bounds(90 * SR, SR, 30.0, 30.0)
        assert bounds == [(0, 30 * SR), (30 * SR, 60 * SR), (60 * SR, 90 * SR)]

    def test_exact_multiple_does_not_add_a_duplicate_tail(self):
        bounds = window_bounds(45 * SR, SR, 30.0, 15.0)
        assert bounds == [(0, 30 * SR), (15 * SR, 45 * SR)]

    def test_empty_audio_has_no_windows(self):
        assert window_bounds(0, SR, 30.0, 15.0) == []

    def test_hop_larger_than_window_is_rejected(self):
        with pytest.raises(ValueError):
            window_bounds(100 * SR, SR, 10.0, 15.0)

    @pytest.mark.parametrize("window_s, hop_s", [(0.0, 15.0), (30.0, 0.0), (-1.0, 1.0)])
    def test_non_positive_sizes_are_rejected(self, window_s, hop_s):
        with pytest.raises(ValueError):
            window_bounds(100 * SR, SR, window_s, hop_s)


class TestSilentWindow:
    def test_all_zero_is_silent(self):
        assert is_silent_window(np.zeros(SR, dtype=np.float32), 0.002)

    def test_empty_is_silent(self):
        assert is_silent_window(np.zeros(0, dtype=np.float32), 0.002)

    def test_loud_signal_is_not_silent(self):
        x = np.full(SR, 0.5, dtype=np.float32)
        assert not is_silent_window(x, 0.002)

    def test_threshold_is_strict_below(self):
        x = np.full(SR, 0.5, dtype=np.float32)  # RMS exactly 0.5 (float-exact)
        assert not is_silent_window(x, 0.5)
        assert is_silent_window(x, 0.6)


class TestDedupeDetections:
    def test_same_reference_within_one_window_counts_once(self):
        kept = dedupe_detections([_det(10.0), _det(25.0)], window_s=30.0)
        assert [d.t_s for d in kept] == [10.0]

    def test_same_reference_beyond_one_window_is_kept(self):
        kept = dedupe_detections([_det(10.0), _det(45.0)], window_s=30.0)
        assert [d.t_s for d in kept] == [10.0, 45.0]

    def test_chain_is_measured_from_last_kept_detection(self):
        # 25 is within 30 s of the kept 0; 50 is 50 s after the kept 0, so kept.
        kept = dedupe_detections([_det(0.0), _det(25.0), _det(50.0)], window_s=30.0)
        assert [d.t_s for d in kept] == [0.0, 50.0]

    def test_different_references_at_same_time_are_all_kept(self):
        kept = dedupe_detections(
            [_det(10.0), _det(10.0, chapter=5, v1=8), _det(10.0, v1=29)], window_s=30.0
        )
        assert len(kept) == 3

    def test_verse_range_is_part_of_the_key(self):
        kept = dedupe_detections([_det(10.0), _det(12.0, v2=30)], window_s=30.0)
        assert len(kept) == 2

    def test_book_match_is_case_insensitive(self):
        kept = dedupe_detections([_det(10.0, book="Romans"), _det(12.0, book="romans")], 30.0)
        assert len(kept) == 1

    def test_unsorted_input_keeps_the_earliest(self):
        kept = dedupe_detections([_det(25.0), _det(10.0)], window_s=30.0)
        assert [d.t_s for d in kept] == [10.0]


class TestParseTRange:
    def test_none_disables(self):
        assert parse_t_range(None) is None

    def test_start_end(self):
        assert parse_t_range("0:600") == (0.0, 600.0)

    def test_open_end_is_infinity(self):
        import math

        assert parse_t_range("48:") == (48.0, math.inf)

    def test_bad_specs_raise(self):
        for bad in ("600:0", "-5:10", "abc"):
            with pytest.raises(ValueError):
                parse_t_range(bad)


def _sender_pair():
    import time

    from audio.config import AudioPipelineConfig
    from audio.metrics import PipelineMetrics

    config = AudioPipelineConfig(control_enabled=False)
    metrics = PipelineMetrics()
    stub = StubReceiver(t0=time.time())
    return BenchmarkingSender(stub, config, metrics)


class TestFeedCache:
    def _key(self, **over):
        base = {
            "record_id": "r",
            "audio_sha256": "abc",
            "language": "lt",
            "model": "small",
            "device": "cuda",
            "compute_type": "int8",
            "mode": "windowed",
        }
        base.update(over)
        return feed_cache_key(**base)

    def test_round_trip(self, tmp_path):
        key = self._key()
        path = feed_cache_path(tmp_path, key)
        units = [[(0.0, "Mato 24"), (10.0, "23 eilutė")]]
        write_feed_cache(
            path, key, units=units, transcripts=[(0.0, "Mato 24 23 eilutė")], audio_duration_s=30.0
        )
        cached = read_feed_cache(path)
        assert cached is not None
        assert cached["key"] == key
        assert cached["units"] == [[[0.0, "Mato 24"], [10.0, "23 eilutė"]]]
        assert cached["audio_duration_s"] == 30.0

    def test_key_change_moves_file(self, tmp_path):
        assert feed_cache_path(tmp_path, self._key()) != feed_cache_path(
            tmp_path, self._key(model="medium")
        )

    def test_prompt_or_hotwords_change_moves_file(self, tmp_path, monkeypatch):
        # A cached transcript decoded under an old prompt must not be replayed
        # after the prompt/hotwords are edited.
        from audio import stt as stt_module

        before = feed_cache_path(tmp_path, self._key())
        monkeypatch.setitem(stt_module.WHISPER_HOTWORDS, "lt", "kiti žodžiai")
        after_hotwords = feed_cache_path(tmp_path, self._key())
        assert after_hotwords != before
        monkeypatch.setitem(stt_module.WHISPER_INITIAL_PROMPTS, "lt", "kitas pavyzdys")
        assert feed_cache_path(tmp_path, self._key()) != after_hotwords

    def test_wrong_version_is_ignored(self, tmp_path):
        path = tmp_path / "stale.json"
        path.write_text('{"key": {"cache_version": 0}}', encoding="utf-8")
        assert read_feed_cache(path) is None

    def test_missing_file_is_ignored(self, tmp_path):
        assert read_feed_cache(tmp_path / "nope.json") is None


class TestRegisterFeedsReplay:
    """Cache replay must build the same detections as the live feed path."""

    def _live(self):
        parser = StreamingParser(ReferenceParser(language="lt"))
        sender = _sender_pair()
        detections: list[Detection] = []
        register_feeds(parser, sender, [(0.0, "Mato 24"), (10.0, "23 eilutė")], detections)
        register_feeds(parser, sender, [(100.0, "Jono 3 16")], detections)
        return detections

    def test_replay_matches_live(self, tmp_path):
        live = self._live()
        key = feed_cache_key(
            record_id="r",
            audio_sha256="abc",
            language="lt",
            model="small",
            device="cuda",
            compute_type="int8",
            mode="offline",
        )
        path = feed_cache_path(tmp_path, key)
        write_feed_cache(
            path,
            key,
            units=[[(0.0, "Mato 24"), (10.0, "23 eilutė")], [(100.0, "Jono 3 16")]],
            transcripts=[],
            audio_duration_s=200.0,
        )
        cached = read_feed_cache(path)
        assert cached is not None
        parser = StreamingParser(ReferenceParser(language="lt"))
        sender = _sender_pair()
        replayed: list[Detection] = []
        for unit in cached["units"]:
            register_feeds(parser, sender, [(float(t), txt) for t, txt in unit], replayed)
        assert [(d.book, d.chapter, d.verse_start, d.t_s) for d in replayed] == [
            (d.book, d.chapter, d.verse_start, d.t_s) for d in live
        ]
        assert [str(f"{d.book} {d.chapter}:{d.verse_start}") for d in replayed] == [
            "Matthew 24:None",
            "Matthew 24:23",
            "John 3:16",
        ]


class TestSpokenSpanMatching:
    """Offline detections match gold by the speech they came from, not report time."""

    def _gold(self, t: float) -> GoldItem:
        return GoldItem("r", t, t + 8.0, "", "Romans", 8, 28, None)

    def test_midpoint_inside_span_is_zero(self):
        d = _det(40.0)
        d.spoken_from_s, d.spoken_to_s = 28.0, 40.0
        assert gold_distance_s(d, self._gold(28.0)) == 0.0  # midpoint 32

    def test_gap_to_span_edge(self):
        d = _det(40.0)
        d.spoken_from_s, d.spoken_to_s = 38.0, 40.0
        assert gold_distance_s(d, self._gold(26.0)) == 8.0  # midpoint 30

    def test_no_span_uses_report_time(self):
        assert gold_distance_s(_det(40.0), self._gold(26.0)) == 10.0

    def test_long_utterance_matches_within_5s(self):
        # Reported 12 s after gold at the end of a 12 s utterance: a miss by
        # report time, a match by span.
        d = _det(44.0)
        d.spoken_from_s, d.spoken_to_s = 32.0, 44.0
        matches, fps, fns = match_detections_to_gold([d], [self._gold(28.0)], 5.0)
        assert len(matches) == 1 and not fps and not fns

    def test_held_chapter_only_gets_previous_span(self):
        parser = StreamingParser(ReferenceParser(language="lt"), hold_chapter_only=True)
        spans = SpanTracker()
        dets: list[Detection] = []
        register_feeds(parser, None, [(20.0, "Romiečiams 5 skyrius", 12.0, "")], dets, spans)
        assert dets == []  # held one utterance
        register_feeds(parser, None, [(35.0, "ir toliau kalbame", 25.0, "")], dets, spans)
        assert [(d.chapter, d.verse_start, d.t_s) for d in dets] == [(5, None, 35.0)]
        assert (dets[0].spoken_from_s, dets[0].spoken_to_s) == (12.0, 20.0)

    def test_low_confidence_decode_marks_refs_low(self):
        # Mirrors audio/pipeline.py: refs from a shaky Whisper decode are "low".
        parser = StreamingParser(ReferenceParser(language="lt"))
        dets: list[Detection] = []
        register_feeds(parser, None, [(10.0, "Jono 3 skyrius 16 eilutė", 2.0, "avg_logprob")], dets)
        register_feeds(parser, None, [(30.0, "Mato 5 skyrius 3 eilutė", 22.0, "")], dets)
        assert [(str(d.book), d.confidence) for d in dets] == [("John", "low"), ("Matthew", "high")]

    def test_cache_key_marks_low_confidence_feeds(self):
        key = feed_cache_key(
            record_id="r",
            audio_sha256="abc",
            language="lt",
            model="small",
            device="cuda",
            compute_type="int8",
            mode="offline",
            segmentation={"max_silent_frames": 50},
        )
        assert key["stt_low_confidence"] is True

    def test_cache_round_trip_keeps_starts(self, tmp_path):
        key = feed_cache_key(
            record_id="r",
            audio_sha256="abc",
            language="lt",
            model="small",
            device="cuda",
            compute_type="int8",
            mode="offline",
        )
        path = feed_cache_path(tmp_path, key)
        write_feed_cache(
            path,
            key,
            units=[[(20.0, "Jono 3 16", 14.0, "no_speech_prob")]],
            transcripts=[],
            audio_duration_s=1.0,
        )
        cached = read_feed_cache(path)
        assert cached is not None
        assert feeds_from_cache(cached["units"][0]) == [(20.0, "Jono 3 16", 14.0, "no_speech_prob")]
        # A start without a verdict (pre-verdict cache entry) reads as trusted.
        assert feeds_from_cache([[20.0, "Jono 3 16", 14.0]]) == [(20.0, "Jono 3 16", 14.0, "")]
        # Entries written before starts were recorded still load.
        assert feeds_from_cache([[20.0, "Jono 3 16"]]) == [(20.0, "Jono 3 16")]


class TestVerifiedAudioSha:
    """The audio hash keys the transcript cache, so a stale manifest value must stop the run."""

    @staticmethod
    def _rec(sha):
        from benchmarks.run_benchmark import Recording

        return Recording(
            id="r",
            source="s",
            file="a.wav",
            duration_s=1.0,
            sha256=sha,
            language="lt",
            gold_file="",
            annotation_provenance="manual",
        )

    def test_matching_manifest_hash_is_returned(self, tmp_path):
        import hashlib

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"abc")
        digest = hashlib.sha256(b"abc").hexdigest()
        assert verified_audio_sha(self._rec(digest), audio) == digest

    def test_missing_manifest_hash_falls_back_to_the_file_hash(self, tmp_path):
        import hashlib

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"abc")
        digest = hashlib.sha256(b"abc").hexdigest()
        assert verified_audio_sha(self._rec(None), audio) == digest
        assert verified_audio_sha(self._rec(""), audio) == digest

    def test_mismatch_stops_the_run(self, tmp_path):
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"abc")
        with pytest.raises(SystemExit) as exc:
            verified_audio_sha(self._rec("0" * 64), audio)
        assert "does not match the manifest" in str(exc.value)


class TestPcmResample:
    """ffmpeg is replaced by a stub that writes its output file."""

    @staticmethod
    def _stub_ffmpeg(monkeypatch, calls, fail=False):
        import subprocess

        def fake_run(cmd, check):
            out = cmd[-1]
            calls.append(out)
            with open(out, "wb") as f:
                f.write(b"partial" if fail else b"complete")
            if fail:
                raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)

    def test_fresh_conversion_is_reused(self, tmp_path, monkeypatch):
        calls: list[str] = []
        self._stub_ffmpeg(monkeypatch, calls)
        src = tmp_path / "a.mp3"
        src.write_bytes(b"x")
        out = pcm_resample(src, src, tmp_path)
        assert out.read_bytes() == b"complete"
        pcm_resample(src, src, tmp_path)
        assert len(calls) == 1

    def test_source_newer_than_conversion_is_reconverted(self, tmp_path, monkeypatch):
        import os

        calls: list[str] = []
        self._stub_ffmpeg(monkeypatch, calls)
        src = tmp_path / "a.mp3"
        src.write_bytes(b"x")
        out = pcm_resample(src, src, tmp_path)
        old = out.stat().st_mtime - 100
        os.utime(out, (old, old))  # the resample predates the source
        pcm_resample(src, src, tmp_path)
        assert len(calls) == 2

    def test_failed_conversion_leaves_no_reusable_file(self, tmp_path, monkeypatch):
        import subprocess

        self._stub_ffmpeg(monkeypatch, [], fail=True)
        src = tmp_path / "a.mp3"
        src.write_bytes(b"x")
        with pytest.raises(subprocess.CalledProcessError):
            pcm_resample(src, src, tmp_path)
        assert not (tmp_path / "a.16k.wav").exists()
