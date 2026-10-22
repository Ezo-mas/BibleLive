"""Tests for the follow-along benchmark's helpers and its simulated worker (no cache, no model)."""

import math

import pytest

from audio.config import AudioPipelineConfig
from benchmarks.follow_along_bench import (
    BYTES_PER_FRAME,
    CachedFinals,
    DisplayPeriod,
    Final,
    FollowAlongReplay,
    Hint,
    ReplayResult,
    WordTimer,
    closed_utterances,
    confirm_by_timestamps,
    confirm_hints,
    display_periods,
    frame_bounds,
    load_corpus,
    percentile,
    render_table,
    spoken_by_period,
    summarize,
    utterance_time_s,
)
from benchmarks.run_benchmark import GoldItem
from benchmarks.verse_timing import VerseRead


class FakeIndex:
    """Stands in for VerseTextIndex: ``table[(book, chapter, text)]`` is the verse scores."""

    def __init__(self, table):
        self.table = table

    def scores(self, book, chapter, text):
        return self.table.get((book, chapter, text), {})


def test_load_corpus_reads_record_and_optional_t_range(tmp_path):
    corpus = tmp_path / "lt.txt"
    corpus.write_text(
        "# comment\n\nman-a.json rec-1\nman-b.json rec-2 2080:4565\n",
        encoding="utf-8",
    )

    entries = load_corpus(corpus)

    assert [(e.manifest.name, e.record_id, e.t_range) for e in entries] == [
        ("man-a.json", "rec-1", None),
        ("man-b.json", "rec-2", (2080.0, 4565.0)),
    ]
    assert entries[0].manifest == tmp_path / "man-a.json"


def test_percentile_interpolates_and_handles_no_values():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == pytest.approx(9.1)
    assert percentile([], 50) is None


def test_frame_bounds_follow_run_single_recording():
    assert frame_bounds(1000, None) == (0, 1000)
    assert frame_bounds(1000, (2.0, 10.0)) == (66, 333)
    assert frame_bounds(100, (0.0, float("inf"))) == (0, 100)


def _final(index, start, end, text, utterance=None):
    return Final(
        index=index,
        utterance=index + 1 if utterance is None else utterance,
        start_s=start,
        end_s=end,
        close_s=end + 1.5,
        audio_s=end - start,
        text=text,
        confident=True,
    )


def _hint(t, verse, root, utterance, book="Jonas", chapter=3):
    return Hint(
        t_s=t,
        source="partial",
        utterance=utterance,
        book=book,
        chapter=chapter,
        verse=verse,
        verse_end=verse,
        follows="",
        root=root,
    )


def test_strict_confirmation_reads_the_hints_own_utterance_and_counts_once():
    index = FakeIndex({("Jonas", 3, "A"): {16: 0.9}, ("Jonas", 3, "B"): {}})
    finals = [_final(0, 10.0, 15.0, "A", utterance=1), _final(1, 30.0, 36.0, "B", utterance=2)]
    ends = {1: 15.0, 2: 36.0}
    hints = [
        _hint(12.0, 16, root=0, utterance=1),  # strict: final 0 is its own utterance
        _hint(24.0, 16, root=0, utterance=1),  # strict too, but (0, 16) is already counted
        _hint(26.0, 17, root=0, utterance=1),  # nothing reads verse 17
        _hint(31.0, 16, root=1, utterance=2),  # loose only: its own final does not read it
    ]

    pairs = confirm_hints(hints, finals, index, ends)

    assert [h.confirmed for h in hints] == [True, True, False, False]
    assert [h.confirmed_loose for h in hints] == [True, True, False, True]
    assert [h.counted for h in hints] == [True, False, False, False]
    assert [h.lag_s for h in hints[:2]] == [2.0, 14.0]
    assert [h.in_time for h in hints[:2]] == [True, False]
    assert len(pairs) == 1
    assert pairs[0][1].index == 0


def test_strict_confirmation_takes_the_next_final_only_within_three_seconds():
    index = FakeIndex({("Jonas", 3, "B"): {16: 0.9}})
    finals = [
        _final(0, 10.0, 15.0, "X", utterance=1),
        _final(1, 16.5, 20.0, "B", utterance=2),  # starts 1.5 s after utterance 1 ends
    ]
    ends = {1: 15.0, 2: 20.0}
    hint = _hint(18.0, 16, root=0, utterance=1)

    confirm_hints([hint], finals, index, ends)

    assert hint.confirmed is True
    assert hint.nearest == 1
    assert hint.lag_s == pytest.approx(1.5)  # from the next final's start
    assert hint.in_time is True


def test_a_next_final_more_than_three_seconds_away_is_loose_only():
    index = FakeIndex({("Jonas", 3, "B"): {16: 0.9}})
    finals = [_final(0, 10.0, 15.0, "X", utterance=1), _final(1, 19.0, 24.0, "B", utterance=2)]
    ends = {1: 15.0, 2: 24.0}  # the next starts 4 s after utterance 1 ends
    hint = _hint(20.0, 16, root=0, utterance=1)

    confirm_hints([hint], finals, index, ends)

    assert hint.confirmed is False
    assert hint.confirmed_loose is True  # the window rule still sees the reading


def test_the_own_final_is_preferred_when_both_read_the_verse():
    index = FakeIndex({("Jonas", 3, "A"): {16: 0.9}, ("Jonas", 3, "B"): {16: 0.8}})
    finals = [_final(0, 10.0, 15.0, "A", utterance=1), _final(1, 16.0, 20.0, "B", utterance=2)]
    hint = _hint(14.0, 16, root=0, utterance=1)

    confirm_hints([hint], finals, index, {1: 15.0, 2: 20.0})

    assert hint.nearest == 0
    assert hint.lag_s == pytest.approx(4.0)


def test_a_blip_source_still_takes_the_next_final_within_three_seconds():
    index = FakeIndex({("Jonas", 3, "B"): {16: 0.9}})
    finals = [_final(0, 10.0, 15.0, "X", utterance=1), _final(1, 16.0, 20.0, "B", utterance=3)]
    ends = {1: 15.0, 2: 15.5, 3: 20.0}  # utterance 2 was a blip: no final of its own
    hint = _hint(16.5, 16, root=0, utterance=2)

    confirm_hints([hint], finals, index, ends)

    assert hint.confirmed is True
    assert hint.nearest == 1


def test_loose_confirmation_needs_a_reading_inside_the_window():
    index = FakeIndex({("Jonas", 3, "A"): {16: 0.9}})
    finals = [_final(0, 10.0, 15.0, "A", utterance=1)]
    too_late = _hint(50.0, 16, root=0, utterance=9)  # 35 s after the final ended

    confirm_hints([too_late], finals, index, {1: 15.0})

    assert too_late.confirmed is False
    assert too_late.confirmed_loose is False


def _frames(*kinds):
    voiced = b"1" + bytes(BYTES_PER_FRAME - 1)
    silent = b"0" + bytes(BYTES_PER_FRAME - 1)
    return b"".join(voiced if kind == "v" else silent for kind in kinds)


class _ArmedServer:
    """Stands in for FollowAlong: never hints, reports armed after every post."""

    def __init__(self):
        self.posts = []

    def on_transcript(self, index, bible, display, text, now):
        self.posts.append((now, text))
        return None, True


class _ScriptedServer:
    """Stands in for FollowAlong: its i-th reply says armed as ``answers[i]`` says, then False."""

    def __init__(self, answers):
        self.answers = list(answers)

    def on_transcript(self, index, bible, display, text, now):  # noqa: ARG002
        return None, (self.answers.pop(0) if self.answers else False)


def _replay(pcm, *, partials, mode="chunk", server=None, partial_rtf=0.21):
    config = AudioPipelineConfig(control_enabled=False)
    config.partial_transcripts = partials
    config.partial_mode = mode
    config.partial_interval_s = 0.06  # two frames
    config.partial_min_s = 0.0
    calls = []

    def transcribe(audio):
        calls.append(len(audio))
        return f"part {len(calls)}", True

    replay = FollowAlongReplay(
        record_id="synthetic",
        language="lt",
        gold=[],
        feed=lambda end_s: (None, "final words", True),
        index=None,  # type: ignore[arg-type]
        bible=None,  # type: ignore[arg-type]
        server=server or _ArmedServer(),  # type: ignore[arg-type]
        config=config,
        vad_is_speech=lambda frame: frame[:1] == b"1",
        transcribe_partial=transcribe if partials else None,
        final_rtf=0.21,
        partial_rtf=partial_rtf,
        display_delay_s=3.0,
        operator_delay_s=1.5,
        chain=False,
    )
    result: ReplayResult = replay.run(pcm, 0, len(pcm) // BYTES_PER_FRAME)
    return result, calls


# Utterance A (12 voiced frames) is closed by silence and posts a final, which arms
# partials. Utterance B (200 voiced frames) is the one that gets partials.
SCENARIO = _frames(*(["v"] * 12 + ["s"] * 60 + ["v"] * 200 + ["s"] * 60))


def test_partials_are_transcribed_and_posted_while_armed():
    result, calls = _replay(SCENARIO, partials=True)

    assert len(result.finals) == 2
    assert result.partial_jobs == len(calls) > 0
    assert result.partial_posts == len(calls)  # chunk texts grow, so each one posts


def test_partials_off_transcribes_only_finals():
    result, calls = _replay(SCENARIO, partials=False)

    assert calls == []
    assert result.partial_jobs == 0
    assert len(result.finals) == 2


def test_a_final_waits_at_most_one_partial():
    result, _ = _replay(SCENARIO, partials=True)

    # Each chunk is two frames (60 ms of audio, 12.6 ms of worker time at 0.21), so the
    # worst wait is one partial in progress.
    worst = max(wait for wait, _ in result.waits)
    assert worst <= 0.21 * 0.06 + 1e-9


def test_a_snapshot_taken_while_armed_is_skipped_once_the_server_says_disarmed():
    """As live (pipeline._process_partial): the worker checks armed before it transcribes.

    Reply 1 (utterance A's final) arms partials. Reply 2 (the first partial's text) disarms
    them, while the slow first partial (10x real time) has a newer snapshot waiting: the
    newer one is skipped, not transcribed.
    """
    server = _ScriptedServer([True, False])
    result, calls = _replay(SCENARIO, partials=True, server=server, partial_rtf=10.0)

    assert len(result.finals) == 2
    assert result.partial_jobs == len(calls) == 1


def test_cached_finals_post_the_first_confident_text_of_the_two_models():
    cached = {
        "transcripts": [
            [15.0, "A || B"],  # Kmynas confident: its text is posted
            [30.0, "<none>"],  # neither model heard anything: no unit
            [50.0, "None || C"],  # Kmynas heard nothing, Canary is confident
            [70.0, "D || E"],  # Kmynas low-confidence, so Canary's text is posted
        ],
        "units": [
            [[15.0, "A", 10.0, "", "B", "repetition"]],
            [[50.0, None, 45.0, "", "C", ""]],
            [[70.0, "D", 65.0, "output repetition", "E", ""]],
        ],
    }
    finals = CachedFinals(cached)

    assert finals(15.0) == (10.0, "A", True)
    assert finals(30.0) == (None, None, False)
    assert finals(50.0) == (45.0, "C", True)
    assert finals(70.0) == (65.0, "E", True)
    finals.finished()


# -- timestamp truth ------------------------------------------------------------------------

# Frames 2-3 and 5-6 are voiced; frame 4 is a silent gap that the utterance's audio drops.
TIMING_KINDS = ["s", "s", "v", "v", "s", "v", "v", "s", "s", "s", "s", "s"]


def _timing_config():
    config = AudioPipelineConfig(control_enabled=False)
    config.end_of_speech_s = 0.12  # 4 silent frames end an utterance
    config.min_speech_s = 0.06
    return config


def _is_voiced(frame):
    return frame[:1] == b"1"


def test_closed_utterance_records_the_file_frame_of_each_frame_it_keeps():
    closed = list(closed_utterances(_frames(*TIMING_KINDS), 0, 12, _timing_config(), _is_voiced))

    assert len(closed) == 1
    utterance = closed[0]
    assert (utterance.kind, utterance.number) == ("end", 1)
    # The gap (frame 4) is dropped from the audio, so the audio's 4 frames sit at 2, 3, 5, 6.
    assert utterance.positions == [2, 3, 5, 6]
    assert utterance.speech_start_s == pytest.approx(2 * 0.03)
    assert utterance.end_s == pytest.approx(7 * 0.03)  # 5 trailing silent frames after frame 6


def test_utterance_sample_offsets_map_to_file_time_through_a_dropped_gap():
    positions = [10, 11, 15, 16]  # the file frames of an utterance's four frames
    frame = 480  # samples per 30 ms frame at 16 kHz

    assert utterance_time_s(0, positions) == pytest.approx(10 * 0.03)
    assert utterance_time_s(2 * frame + 100, positions) == pytest.approx(15 * 0.03 + 100 / 16000)
    # A start on a frame boundary is that frame's start; an end on one is the previous
    # frame's end, not the next frame's start (which would run over the dropped silence).
    assert utterance_time_s(2 * frame, positions) == pytest.approx(15 * 0.03)
    assert utterance_time_s(2 * frame, positions, end=True) == pytest.approx(11 * 0.03 + 0.03)
    # Past the last sample it stays at the end of the last frame.
    assert utterance_time_s(4 * frame, positions, end=True) == pytest.approx(16 * 0.03 + 0.03)
    assert utterance_time_s(9 * frame, positions) == pytest.approx(16 * 0.03 + 0.03)


def test_spoken_words_are_timed_in_the_file_across_the_gap():
    pcm = _frames(*TIMING_KINDS)
    feed = CachedFinals(
        {"transcripts": [[0.21, "tekstas"]], "units": [[[0.21, "tekstas", 0.06, ""]]]}
    )
    calls = []

    def words(audio):
        calls.append(audio)
        return [("pirmas", 0, 30), ("antras", 40, 80), ("trecias", 100, 120)]

    inside = [DisplayPeriod(0.0, math.inf, "Jonas", 3, 16, None)]
    spoken = spoken_by_period(pcm, 0, 12, _timing_config(), _is_voiced, feed, inside, words)
    feed.finished()

    assert calls == [_frames("v", "v", "v", "v")]
    assert [(w.text, round(w.start_s, 3), round(w.end_s, 3)) for w in spoken[0]] == [
        ("pirmas", 0.06, 0.09),
        ("antras", 0.10, 0.17),  # ends in frame 5, after the dropped frame 4
        ("trecias", 0.19, 0.21),
    ]

    # A period that starts after the utterance has begun does not take it.
    later = [DisplayPeriod(0.1, math.inf, "Jonas", 3, 16, None)]
    feed = CachedFinals(
        {"transcripts": [[0.21, "tekstas"]], "units": [[[0.21, "tekstas", 0.06, ""]]]}
    )
    calls.clear()
    spoken = spoken_by_period(pcm, 0, 12, _timing_config(), _is_voiced, feed, later, words)

    assert spoken == [[]]
    assert calls == []


def test_display_periods_run_from_each_show_time_to_the_next_one():
    gold = [
        GoldItem("rec", 5.0, 10.0, "a", "Jonas", 3, 16, None),
        GoldItem("rec", 45.0, 50.0, "b", "Jonas", 3, None, None),  # chapter only: not shown
        GoldItem("rec", 30.0, 40.0, "c", "Romans", 8, 28, 30),
    ]

    periods = display_periods(gold, 3.0)

    assert [(p.start_s, p.end_s, p.book, p.verse_start, p.verse_end) for p in periods] == [
        (13.0, 43.0, "Jonas", 16, None),
        (43.0, math.inf, "Romans", 28, 30),
    ]


def _read(verse, start, end, book="Jonas", chapter=3):
    return VerseRead(
        book=book,
        chapter=chapter,
        verse=verse,
        tokens=5,
        matched=4,
        coverage=0.8,
        start_s=start,
        end_s=end,
    )


def test_timestamp_confirmation_uses_the_verse_start_window_and_counts_once():
    reads = [_read(16, 100.0, 110.0)]
    hints = [
        _hint(105.0, 16, root=0, utterance=1),  # confirmed, 5 s after the verse began
        _hint(112.0, 16, root=0, utterance=2),  # confirmed, but after the reading ended
        _hint(131.0, 16, root=1, utterance=3),  # 131 - 30 = 101: the reading began too early
        _hint(120.0, 17, root=0, utterance=4),  # verse 17 was not read
    ]

    counted = confirm_by_timestamps(hints, reads)

    assert [h.ts_read is not None for h in hints] == [True, True, False, False]
    assert [h.ts_counted for h in hints] == [True, False, False, False]
    assert counted == [hints[0]]
    assert hints[0].ts_lag_s == 5.0
    assert hints[0].ts_in_time is True
    assert hints[1].ts_in_time is False


def test_timestamp_metrics_report_lag_from_the_verse_start_and_recall():
    reads = [_read(16, 100.0, 110.0), _read(17, 120.0, 125.0)]
    hints = [
        _hint(105.0, 16, root=0, utterance=1),
        _hint(112.0, 16, root=0, utterance=2),
        _hint(131.0, 16, root=1, utterance=3),
    ]
    confirm_by_timestamps(hints, reads)
    replay = ReplayResult(
        record_id="rec",
        replay_s=60.0,
        gold_count=1,
        displayed=1,
        finals=[],
        hints=hints,
        partial_jobs=0,
        partial_posts=0,
        partial_audio_s=0.0,
        final_busy_s=0.0,
        partial_busy_s=0.0,
        waits=[],
        verse_reads=reads,
    )

    m = summarize([replay], "timestamps")

    assert (m["confirmed_ts"], m["lag_start_n"]) == (2, 1)
    assert m["lag_start_median_s"] == 5.0
    assert (m["before_end"], m["before_end_share"]) == (1, 1.0)
    assert (m["verses_read"], m["hinted"]) == (2, 1)
    assert "lagStartMed" in render_table([("rec", m)], ts=True)
    assert "lagStartMed" not in render_table([("rec", summarize([replay], "final"))])


def test_word_timer_transcribes_each_utterance_once_per_model_and_audio(tmp_path, monkeypatch):
    config = AudioPipelineConfig(control_enabled=False)
    config.kmynas_model = str(tmp_path / "kmynas.gguf")
    calls = []

    def fake_transcribe(self, pcm, language):
        calls.append((len(pcm), language))
        return [("žodis", 0, 30)]

    monkeypatch.setattr(WordTimer, "_transcribe", fake_transcribe)
    timer = WordTimer(tmp_path / "cache", config)
    audio = b"\x01\x00" * 480

    assert timer("rec-1", "lt", audio) == [("žodis", 0, 30)]
    assert timer("rec-1", "lt", audio) == [("žodis", 0, 30)]  # from the cache
    assert timer("rec-1", "lt", b"\x02\x00" * 480) == [("žodis", 0, 30)]  # other audio

    assert calls == [(960, "lt"), (960, "lt")]
    assert (timer.transcribed, timer.from_cache) == (2, 1)
    assert len(list((tmp_path / "cache" / "follow-along-words").glob("*.json"))) == 2
