"""Tests for partial transcripts: scheduling, the mailbox and grow/chunk accumulation."""

from types import SimpleNamespace

import pytest

from audio.config import AudioPipelineConfig
from audio.partials import (
    MIN_CHUNK_BYTES,
    PartialMailbox,
    PartialScheduler,
    PartialSnapshot,
    PartialTranscript,
)
from audio.segmenter import Segmenter, SegmentEvent

VOICED, SILENT = b"v", b"s"
# Chunk-mode audio long enough to transcribe (0.5 s each).
A, B, C, D = (bytes([n]) * MIN_CHUNK_BYTES for n in range(4))


def _segmenter() -> Segmenter:
    return Segmenter(
        lambda frame: frame.startswith(VOICED),
        max_silent_frames=3,
        min_speech_frames=1,
        soft_utterance_frames=1000,
        max_utterance_frames=1000,
    )


def _due_positions(
    scheduler: PartialScheduler, segmenter: Segmenter, frames: list[bytes]
) -> list[tuple[int, int]]:
    """(push index, utterance frames) of every push that is due."""
    due = []
    for i, frame in enumerate(frames):
        event = segmenter.push(frame)
        if scheduler.due(segmenter, event):
            due.append((i, segmenter.utterance_frames))
    return due


def _snap(utterance: int, *frames: bytes) -> PartialSnapshot:
    return PartialSnapshot(utterance, "lt", tuple(frames))


class TestPartialScheduler:
    def test_due_at_each_multiple_of_the_interval(self):
        scheduler = PartialScheduler(interval_frames=3, min_frames=0)
        frames = [b"v"] * 10

        due = _due_positions(scheduler, _segmenter(), frames)

        assert [utterance_frames for _, utterance_frames in due] == [3, 6, 9]

    def test_min_frames_holds_back_the_first_partial(self):
        scheduler = PartialScheduler(interval_frames=3, min_frames=5)
        frames = [b"v"] * 10

        due = _due_positions(scheduler, _segmenter(), frames)

        # The first multiple of 3 that is at least 5 frames long is 6.
        assert [utterance_frames for _, utterance_frames in due] == [6, 9]

    def test_nothing_is_due_outside_an_utterance(self):
        scheduler = PartialScheduler(interval_frames=2, min_frames=0)
        frames = [SILENT] * 6

        assert _due_positions(scheduler, _segmenter(), frames) == []

    def test_closing_an_utterance_resets_the_schedule_for_the_next(self):
        scheduler = PartialScheduler(interval_frames=3, min_frames=0)
        segmenter = _segmenter()
        # First utterance: 2 voiced frames, then 4 silent ones close it (due at 3
        # frames, while its trailing silence is still counted).
        first = [b"v"] * 2 + [SILENT] * 4
        # Second utterance starts after the close: its schedule begins again at 3,
        # not at the 6 that the first utterance would have reached next.
        second = [b"v"] * 7

        due = _due_positions(scheduler, segmenter, first + second)

        assert [utterance_frames for _, utterance_frames in due] == [3, 3, 6]

    def test_a_close_event_is_never_due(self):
        scheduler = PartialScheduler(interval_frames=1, min_frames=0)
        segmenter = _segmenter()
        closing = SegmentEvent("end", [], 0, "silence")

        assert scheduler.due(segmenter, closing) is False

    def test_a_long_jump_skips_the_multiples_it_passed(self):
        scheduler = PartialScheduler(interval_frames=3, min_frames=0)
        # Stands in for a segmenter whose start event already holds 10 frames
        # (a long pre-roll): one partial now, not one per multiple passed.
        segmenter = SimpleNamespace(in_utterance=True, utterance_frames=10)

        assert scheduler.due(segmenter, SegmentEvent("start")) is True
        segmenter.utterance_frames = 11
        assert scheduler.due(segmenter, None) is False
        segmenter.utterance_frames = 12
        assert scheduler.due(segmenter, None) is True

    def test_from_config_rounds_seconds_to_whole_vad_frames(self):
        config = AudioPipelineConfig(partial_interval_s=2.0, partial_min_s=1.5)

        scheduler = PartialScheduler.from_config(config)

        assert scheduler.interval_frames == 67  # 2.0 s / 30 ms
        assert scheduler.min_frames == 50  # 1.5 s / 30 ms

    @pytest.mark.parametrize("interval, minimum", [(0, 0), (-1, 0), (3, -1)])
    def test_rejects_bad_settings(self, interval, minimum):
        with pytest.raises(ValueError):
            PartialScheduler(interval, minimum)


class TestPartialMailbox:
    def test_put_replaces_a_waiting_snapshot_and_says_so(self):
        mailbox = PartialMailbox()

        assert mailbox.put(_snap(1, b"a")) is False
        assert mailbox.put(_snap(1, b"a", b"b")) is True

    def test_take_returns_the_newest_snapshot_once(self):
        mailbox = PartialMailbox()
        mailbox.put(_snap(1, b"a"))
        mailbox.put(_snap(1, b"a", b"b"))

        assert mailbox.take() == _snap(1, b"a", b"b")
        assert mailbox.take() is None

    def test_discard_without_an_utterance_drops_whatever_waits(self):
        mailbox = PartialMailbox()
        mailbox.put(_snap(2, b"a"))

        mailbox.discard()

        assert mailbox.take() is None

    def test_discard_for_another_utterance_keeps_the_snapshot(self):
        mailbox = PartialMailbox()
        mailbox.put(_snap(2, b"a"))

        mailbox.discard(utterance=1)

        assert mailbox.take() == _snap(2, b"a")

    def test_discard_for_the_same_utterance_drops_the_snapshot(self):
        mailbox = PartialMailbox()
        mailbox.put(_snap(2, b"a"))

        mailbox.discard(utterance=2)

        assert mailbox.take() is None


class TestPartialTranscript:
    def test_grow_transcribes_everything_so_far_and_posts_the_latest_text(self):
        partials = PartialTranscript("grow")
        first, second = _snap(1, b"a", b"b"), _snap(1, b"a", b"b", b"c")

        assert partials.audio_for(first) == b"ab"
        assert partials.add(first, "jonas trys") == "jonas trys"
        assert partials.audio_for(second) == b"abc"
        assert partials.add(second, "jonas trys šešioliktas") == "jonas trys šešioliktas"

    def test_grow_does_not_repost_the_same_text(self):
        partials = PartialTranscript("grow")
        first, second = _snap(1, b"a"), _snap(1, b"a", b"b")

        partials.add(first, "Jono evangelija")

        assert partials.add(second, "Jono evangelija") is None

    def test_grow_posts_nothing_for_an_empty_or_missing_text(self):
        partials = PartialTranscript("grow")

        assert partials.add(_snap(1, b"a"), None) is None
        assert partials.add(_snap(1, b"a", b"b"), "   ") is None

    def test_chunk_transcribes_only_new_audio_and_joins_the_texts(self):
        partials = PartialTranscript("chunk")
        first, second = _snap(1, A, B), _snap(1, A, B, C, D)

        assert partials.audio_for(first) == A + B
        assert partials.add(first, "Romiečiams") == "Romiečiams"
        assert partials.audio_for(second) == C + D
        assert partials.add(second, "aštuoniolika") == "Romiečiams aštuoniolika"

    def test_chunk_has_nothing_to_do_without_new_frames(self):
        partials = PartialTranscript("chunk")
        first = _snap(1, A, B)
        partials.add(first, "pirmas")

        assert partials.audio_for(first) is None

    def test_chunk_skips_an_empty_chunk_but_keeps_its_audio_done(self):
        partials = PartialTranscript("chunk")
        partials.add(_snap(1, A), "vienas")
        second = _snap(1, A, B)

        assert partials.audio_for(second) == B
        assert partials.add(second, None) is None
        third = _snap(1, A, B, C)
        assert partials.audio_for(third) == C
        assert partials.add(third, "trys") == "vienas trys"

    def test_chunk_keeps_audio_too_short_to_transcribe_for_the_next_snapshot(self):
        """Under 0.5 s the speech models skip audio: it waits, so its words are not lost."""
        partials = PartialTranscript("chunk")
        short = A[: MIN_CHUNK_BYTES // 2]
        partials.add(_snap(1, A), "vienas")

        assert partials.audio_for(_snap(1, A, short)) is None
        later = _snap(1, A, short, short)
        assert partials.audio_for(later) == short + short
        assert partials.add(later, "du") == "vienas du"

    def test_grow_has_no_minimum(self):
        partials = PartialTranscript("grow")

        assert partials.audio_for(_snap(1, b"a")) == b"a"

    def test_a_new_utterance_resets_the_state(self):
        partials = PartialTranscript("chunk")
        partials.add(_snap(1, A), "senas")

        new = _snap(2, B)

        assert partials.audio_for(new) == B
        assert partials.add(new, "naujas") == "naujas"

    def test_same_joined_text_is_not_reposted_within_an_utterance(self):
        partials = PartialTranscript("chunk")
        partials.add(_snap(1, b"a"), "vienas")

        assert partials.add(_snap(1, b"a", b"b"), None) is None

    def test_rejects_an_unknown_mode(self):
        with pytest.raises(ValueError):
            PartialTranscript("stream")  # type: ignore[arg-type]
