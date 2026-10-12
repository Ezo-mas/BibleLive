"""Tests for the shared utterance segmenter (pure bookkeeping, no audio deps)."""

import random

import pytest

from audio.segmenter import Segmenter

VOICED, SILENT = b"v", b"s"


def _segmenter(
    max_silent_frames: int = 3,
    min_speech_frames: int = 2,
    soft_utterance_frames: int = 1000,
    max_utterance_frames: int = 1000,
) -> Segmenter:
    return Segmenter(
        lambda frame: frame.startswith(VOICED),
        max_silent_frames,
        min_speech_frames,
        soft_utterance_frames,
        max_utterance_frames,
    )


def _push_all(segmenter: Segmenter, frames: list[bytes]) -> list[tuple[int, str, list[bytes]]]:
    events = []
    for i, frame in enumerate(frames):
        event = segmenter.push(frame)
        if event is not None:
            events.append((i, event.kind, event.frames))
    return events


def test_utterance_closes_after_more_than_max_silent_frames():
    segmenter = _segmenter(max_silent_frames=3)
    frames = [b"v1", b"v2", SILENT, SILENT, SILENT, SILENT]

    events = _push_all(segmenter, frames)

    assert events == [(0, "start", []), (5, "end", [b"v1", b"v2"])]
    assert not segmenter.in_utterance


def test_short_pauses_continue_the_utterance_and_are_not_kept():
    segmenter = _segmenter(max_silent_frames=3)
    frames = [b"v1", SILENT, SILENT, b"v2", SILENT, SILENT, SILENT, SILENT]

    events = _push_all(segmenter, frames)

    assert events[-1] == (7, "end", [b"v1", b"v2"])


def test_too_short_utterance_is_a_blip():
    segmenter = _segmenter(max_silent_frames=1, min_speech_frames=2)

    events = _push_all(segmenter, [b"v1", SILENT, SILENT])

    assert events == [(0, "start", []), (2, "blip", [b"v1"])]


def test_end_event_reports_the_closing_silence():
    segmenter = _segmenter(max_silent_frames=2)
    for frame in [b"v1", b"v2", SILENT, SILENT]:
        assert segmenter.push(frame) is None or frame == b"v1"

    event = segmenter.push(SILENT)

    assert event is not None
    assert event.trailing_silent_frames == 3


def test_flush_closes_an_open_utterance_and_is_idempotent():
    segmenter = _segmenter(min_speech_frames=2)
    _push_all(segmenter, [b"v1", b"v2", SILENT])

    event = segmenter.flush()

    assert event is not None
    assert (event.kind, event.frames, event.trailing_silent_frames) == ("end", [b"v1", b"v2"], 1)
    assert segmenter.flush() is None


def test_reset_discards_a_partial_utterance():
    segmenter = _segmenter()
    _push_all(segmenter, [b"v1", b"v2", SILENT])

    assert segmenter.reset() == 2
    assert not segmenter.in_utterance
    assert segmenter.silent_frames == 0
    assert segmenter.push(b"v3").kind == "start"


def test_hard_cap_closes_an_utterance_that_never_pauses():
    """Ungated full-mix audio: the VAD never sees silence, so only the cap ends it."""
    segmenter = _segmenter(soft_utterance_frames=3, max_utterance_frames=4)
    frames = [b"v%d" % i for i in range(10)]

    events = _push_all(segmenter, frames)

    ends = [(i, kind, len(f)) for i, kind, f in events if kind != "start"]
    assert ends == [(3, "end", 4), (7, "end", 4)]
    assert [i for i, kind, _ in events if kind == "start"] == [0, 4, 8]
    assert segmenter.flush().frames == [b"v8", b"v9"]  # nothing lost or duplicated


def test_cap_event_reports_its_reason():
    segmenter = _segmenter(min_speech_frames=1, soft_utterance_frames=2, max_utterance_frames=3)
    for frame in [b"v1", b"v2"]:
        segmenter.push(frame)

    event = segmenter.push(b"v3")

    assert (event.kind, event.reason) == ("end", "cap")


def test_past_the_soft_limit_the_next_pause_ends_the_utterance():
    """Cut between words rather than mid-word when a gap comes before the cap."""
    segmenter = _segmenter(soft_utterance_frames=3, max_utterance_frames=10)

    events = _push_all(segmenter, [b"v1", b"v2", b"v3", b"v4", SILENT, b"v5"])

    assert events[1] == (4, "end", [b"v1", b"v2", b"v3", b"v4"])
    assert events[2] == (5, "start", [])
    assert segmenter.speech_frames == [b"v5"]


def test_short_utterances_end_on_silence_not_on_a_limit():
    segmenter = _segmenter(max_silent_frames=2, soft_utterance_frames=100)
    for frame in [b"v1", b"v2", SILENT, SILENT]:
        segmenter.push(frame)

    event = segmenter.push(SILENT)

    assert (event.kind, event.reason) == ("end", "silence")


def test_pause_before_the_soft_limit_does_not_cut():
    segmenter = _segmenter(max_silent_frames=5, soft_utterance_frames=10)

    events = _push_all(segmenter, [b"v1", SILENT, b"v2"])

    assert [kind for _, kind, _ in events] == ["start"]


def _legacy_vad_loop(frames, is_speech, max_silent_frames, min_speech_frames):
    """The pre-Segmenter ``pipeline._vad_loop`` bookkeeping, verbatim."""
    out = []
    speech_frames: list[bytes] = []
    silent_frames = 0
    for i, frame in enumerate(frames):
        if is_speech(frame):
            if not speech_frames:
                out.append((i, "start", []))
            silent_frames = 0
            speech_frames.append(frame)
        else:
            if speech_frames:
                silent_frames += 1
                if silent_frames > max_silent_frames:
                    kind = "end" if len(speech_frames) >= min_speech_frames else "blip"
                    out.append((i, kind, speech_frames.copy()))
                    speech_frames = []
                    silent_frames = 0
            else:
                silent_frames = 0
    return out


@pytest.mark.parametrize("seed", range(20))
def test_without_limits_matches_the_legacy_vad_loop(seed):
    """Refactor guard: with the length limits off, same events as the old loop."""
    rng = random.Random(seed)
    # Runs of voiced/silent frames, like real speech with pauses.
    frames: list[bytes] = []
    while len(frames) < 3000:
        kind = VOICED if rng.random() < 0.5 else SILENT
        frames += [kind + str(len(frames)).encode()] * rng.randint(1, 80)

    no_limit = 10**9
    segmenter = Segmenter(
        lambda f: f.startswith(VOICED),
        soft_utterance_frames=no_limit,
        max_utterance_frames=no_limit,
    )

    expected = _legacy_vad_loop(frames, lambda f: f.startswith(VOICED), 50, 10)
    assert _push_all(segmenter, frames) == expected
    assert any(kind == "end" for _, kind, _ in expected)


@pytest.mark.parametrize("seed", range(10))
def test_default_limits_bound_every_utterance(seed):
    """With the defaults, no utterance spans more than the hard cap, and every
    voiced frame lands in exactly one utterance."""
    rng = random.Random(seed)
    # Mostly voiced with rare one-frame gaps: what an ungated mix looks like.
    frames = [(SILENT if rng.random() < 0.01 else VOICED) + str(i).encode() for i in range(20000)]
    segmenter = Segmenter(lambda f: f.startswith(VOICED))

    closed = [e for e in (segmenter.push(f) for f in frames) if e and e.kind != "start"]
    final = segmenter.flush()
    closed += [final] if final else []

    assert max(len(e.frames) for e in closed) <= segmenter.max_utterance_frames
    kept = [f for e in closed for f in e.frames]
    assert kept == [f for f in frames if f.startswith(VOICED)]
