"""Tests for the shared utterance segmenter (pure bookkeeping, no audio deps)."""

import random

import pytest

from audio import segmenter as segmenter_module
from audio.config import AudioPipelineConfig
from audio.segmenter import Segmenter

VOICED, SILENT = b"v", b"s"


def _segmenter(
    max_silent_frames: int = 3,
    min_speech_frames: int = 2,
    soft_utterance_frames: int = 1000,
    max_utterance_frames: int = 1000,
    **options: int | bool,
) -> Segmenter:
    return Segmenter(
        lambda frame: frame.startswith(VOICED),
        max_silent_frames,
        min_speech_frames,
        soft_utterance_frames,
        max_utterance_frames,
        **options,
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


def test_config_defaults_match_the_frame_defaults():
    """The live config (seconds) and the module defaults (frames) must agree."""
    built = Segmenter.from_config(lambda f: True, AudioPipelineConfig())

    assert (
        built.max_silent_frames,
        built.min_speech_frames,
        built.soft_utterance_frames,
        built.max_utterance_frames,
    ) == (
        segmenter_module.MAX_SILENT_FRAMES,
        segmenter_module.MIN_SPEECH_FRAMES,
        segmenter_module.SOFT_UTTERANCE_FRAMES,
        segmenter_module.MAX_UTTERANCE_FRAMES,
    )


def test_from_config_converts_seconds_to_frames():
    config = AudioPipelineConfig(end_of_speech_s=0.6, soft_utterance_s=6, max_utterance_s=10)

    built = Segmenter.from_config(lambda f: True, config)

    assert (built.max_silent_frames, built.soft_utterance_frames) == (20, 200)
    assert built.max_utterance_frames == 333


@pytest.mark.parametrize(("soft_s", "max_s"), [(12.0, 8.0), (0.0, 8.0)])
def test_from_config_rejects_inconsistent_limits(soft_s, max_s):
    config = AudioPipelineConfig(soft_utterance_s=soft_s, max_utterance_s=max_s)

    with pytest.raises(ValueError, match="soft_utterance_s"):
        Segmenter.from_config(lambda f: True, config)


def test_cli_tuning_flags_reach_the_config():
    from audio.cli import apply_tuning_arguments, build_parser

    args = build_parser().parse_args(["--max-utterance-s", "10", "--beam-size", "2"])
    config = AudioPipelineConfig()

    apply_tuning_arguments(config, args)

    assert (config.max_utterance_s, config.beam_size) == (10.0, 2)
    assert config.end_of_speech_s == AudioPipelineConfig().end_of_speech_s  # untouched


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


def test_constructor_defaults_keep_the_old_behaviour():
    segmenter = _segmenter()

    assert (segmenter.pre_roll_frames, segmenter.keep_pauses) == (0, False)
    assert (segmenter.start_voiced_frames, segmenter.start_window_frames) == (1, 1)
    frames = [SILENT, SILENT, b"v1", SILENT, b"v2", SILENT, SILENT, SILENT, SILENT]

    events = _push_all(segmenter, frames)

    # Starts on the first voiced frame; no leading or inner silence is kept.
    assert events == [(2, "start", []), (8, "end", [b"v1", b"v2"])]


def test_pre_roll_keeps_the_frames_just_before_the_first_voiced_one():
    segmenter = _segmenter(pre_roll_frames=2)
    frames = [SILENT, SILENT, SILENT, b"v1", b"v2", SILENT, SILENT, SILENT, SILENT]

    events = _push_all(segmenter, frames)

    # Only the two frames before v1 are kept; the trailing silence is not.
    assert events == [(3, "start", []), (8, "end", [SILENT, SILENT, b"v1", b"v2"])]


def test_pre_roll_is_shorter_at_stream_start():
    segmenter = _segmenter(pre_roll_frames=5)

    events = _push_all(segmenter, [SILENT, b"v1", b"v2", SILENT, SILENT, SILENT, SILENT])

    assert events == [(1, "start", []), (6, "end", [SILENT, b"v1", b"v2"])]


def test_pre_roll_sits_before_a_multi_frame_start_window_without_overlap():
    segmenter = _segmenter(pre_roll_frames=2, start_voiced_frames=2, start_window_frames=3)
    frames = [SILENT, SILENT, SILENT, b"v1", SILENT, b"v2"] + [SILENT] * 4

    events = _push_all(segmenter, frames)

    # The window is the last 3 frames (v1, silence, v2); the 2 frames before it are pre-roll.
    assert events == [(5, "start", []), (9, "end", [SILENT, SILENT, b"v1", b"v2"])]


def test_pre_roll_history_is_cleared_when_an_utterance_closes():
    segmenter = _segmenter(max_silent_frames=2, pre_roll_frames=5)
    frames = [b"v1", b"v2"] + [SILENT] * 5 + [b"v3", b"v4"] + [SILENT] * 3

    events = _push_all(segmenter, frames)

    # The second utterance gets only the two silent frames after the first closed.
    assert events == [
        (0, "start", []),
        (4, "end", [b"v1", b"v2"]),
        (7, "start", []),
        (11, "end", [SILENT, SILENT, b"v3", b"v4"]),
    ]


def test_keep_pauses_keeps_inner_gaps_and_drops_trailing_silence():
    segmenter = _segmenter(max_silent_frames=3, keep_pauses=True)
    frames = [b"v1", SILENT, SILENT, b"v2", SILENT, SILENT, SILENT, SILENT]

    events = _push_all(segmenter, frames)

    assert events == [(0, "start", []), (7, "end", [b"v1", SILENT, SILENT, b"v2"])]


@pytest.mark.parametrize(
    ("options", "frames", "closer"),
    [
        ({"max_silent_frames": 2}, [b"v1", SILENT, b"v2", SILENT, SILENT, SILENT], "silence"),
        (
            {"max_silent_frames": 9, "soft_utterance_frames": 5},
            [b"v1", SILENT, b"v2", SILENT, SILENT],
            "pause",
        ),
        ({"max_silent_frames": 9}, [b"v1", SILENT, b"v2", SILENT], "flush"),
    ],
)
def test_trailing_pause_is_dropped_however_the_utterance_closes(options, frames, closer):
    segmenter = _segmenter(min_speech_frames=1, keep_pauses=True, **options)

    events = [segmenter.push(frame) for frame in frames]
    closed = segmenter.flush() if closer == "flush" else events[-1]

    assert closed is not None
    assert (closed.kind, closed.frames) == ("end", [b"v1", SILENT, b"v2"])


def test_blip_is_decided_by_voiced_frames_not_by_audio_length():
    """Pre-roll and silence lengthen the audio; a single voiced frame is still a blip."""
    segmenter = _segmenter(max_silent_frames=2, min_speech_frames=2, pre_roll_frames=5)
    frames = [SILENT] * 5 + [b"v1", SILENT, SILENT, SILENT]

    events = _push_all(segmenter, frames)

    assert events[-1] == (8, "blip", [SILENT] * 5 + [b"v1"])


def test_a_lone_voiced_frame_does_not_start_an_utterance():
    segmenter = _segmenter(start_voiced_frames=3, start_window_frames=5)

    events = _push_all(segmenter, [SILENT, SILENT, b"v1", SILENT, SILENT, SILENT, SILENT])

    assert events == []
    assert not segmenter.in_utterance


def test_starts_once_enough_of_the_recent_window_is_voiced():
    """The voiced frames need not be consecutive; the window's frames join the utterance."""
    segmenter = _segmenter(start_voiced_frames=3, start_window_frames=5)

    events = _push_all(segmenter, [b"v1", SILENT, b"v2", SILENT, b"v3"])

    assert events == [(4, "start", [])]
    assert segmenter.speech_frames == [b"v1", b"v2", b"v3"]  # window silence dropped


@pytest.mark.parametrize(
    ("keep_pauses", "expected"),
    # Silence before the window's first voiced frame is pre-roll territory
    # (none here), not a pause: only the gap after the onset is a pause.
    [(False, [b"v1", b"v2"]), (True, [b"v1", SILENT, b"v2"])],
)
def test_start_window_silence_is_kept_only_with_keep_pauses(keep_pauses, expected):
    segmenter = _segmenter(
        max_silent_frames=1,
        start_voiced_frames=2,
        start_window_frames=4,
        keep_pauses=keep_pauses,
    )
    frames = [SILENT, b"v1", SILENT, b"v2", SILENT, SILENT]

    events = _push_all(segmenter, frames)

    assert events == [(3, "start", []), (5, "end", expected)]


def test_reset_while_an_utterance_is_open_clears_the_pre_roll_and_pending_pause():
    segmenter = _segmenter(pre_roll_frames=5, keep_pauses=True)
    _push_all(segmenter, [SILENT, SILENT, b"v1", SILENT])

    assert segmenter.reset() == 3  # the two pre-roll frames and v1
    events = _push_all(segmenter, [b"v2", b"v3", SILENT, SILENT, SILENT, SILENT])

    # The silence held back before the reset must not reappear in the next utterance.
    assert events == [(0, "start", []), (5, "end", [b"v2", b"v3"])]


def test_reset_while_silent_clears_the_pre_roll():
    segmenter = _segmenter(pre_roll_frames=5)
    _push_all(segmenter, [SILENT, SILENT, SILENT])

    assert segmenter.reset() == 0
    events = _push_all(segmenter, [b"v1", b"v2", SILENT, SILENT, SILENT, SILENT])

    assert events == [(0, "start", []), (5, "end", [b"v1", b"v2"])]


def test_flush_clears_the_pre_roll_after_closing_an_utterance():
    segmenter = _segmenter(pre_roll_frames=5)
    _push_all(segmenter, [SILENT] * 5 + [b"v1", b"v2"])

    assert segmenter.flush().frames == [SILENT] * 5 + [b"v1", b"v2"]
    assert _push_all(segmenter, [SILENT, b"v3", b"v4"]) == [(1, "start", [])]
    assert segmenter.flush().frames == [SILENT, b"v3", b"v4"]  # one silent frame, not five


def test_flush_while_silent_clears_the_pre_roll():
    """Same contract as reset(): flush() drops the history even with no utterance open."""
    segmenter = _segmenter(pre_roll_frames=3)
    _push_all(segmenter, [SILENT, SILENT, SILENT])

    assert segmenter.flush() is None
    events = _push_all(segmenter, [b"v1", b"v2", SILENT, SILENT, SILENT, SILENT])

    assert events == [(0, "start", []), (5, "end", [b"v1", b"v2"])]


@pytest.mark.parametrize(("voiced", "window"), [(0, 10), (11, 10), (1, 0)])
def test_start_voiced_frames_must_fit_in_the_start_window(voiced, window):
    with pytest.raises(ValueError, match="start_voiced_frames"):
        _segmenter(start_voiced_frames=voiced, start_window_frames=window)


def test_start_window_bounds_are_inclusive():
    Segmenter(lambda f: True, start_voiced_frames=1, start_window_frames=1)
    Segmenter(lambda f: True, start_voiced_frames=10, start_window_frames=10)


def test_pre_roll_cannot_be_negative():
    with pytest.raises(ValueError, match="pre_roll_frames"):
        _segmenter(pre_roll_frames=-1)


def test_pre_roll_and_start_window_count_toward_the_hard_cap():
    segmenter = _segmenter(pre_roll_frames=4, max_utterance_frames=6)

    events = [segmenter.push(frame) for frame in [SILENT] * 4 + [b"v1", b"v2", b"v3"]]

    assert events[4].kind == "start"  # the 4 pre-roll frames + v1 = 5 of 6
    assert (events[5].kind, events[5].reason, len(events[5].frames)) == ("end", "cap", 6)


def test_from_config_defaults_keep_the_old_segmentation():
    # Pre-roll and the start trigger measured level with the old behavior, so
    # the config defaults leave all of them off.
    built = Segmenter.from_config(lambda f: True, AudioPipelineConfig())

    assert (built.pre_roll_frames, built.keep_pauses) == (0, False)
    assert (built.start_voiced_frames, built.start_window_frames) == (1, 1)


def test_from_config_converts_a_0_3s_pre_roll_and_six_of_ten_start():
    config = AudioPipelineConfig(pre_roll_s=0.3, start_voiced_frames=6, start_window_frames=10)
    built = Segmenter.from_config(lambda f: True, config)

    assert built.pre_roll_frames == 10
    assert (built.start_voiced_frames, built.start_window_frames) == (6, 10)


def test_from_config_converts_pre_roll_and_passes_the_other_options():
    config = AudioPipelineConfig(
        pre_roll_s=0.5, keep_pauses=True, start_voiced_frames=2, start_window_frames=3
    )

    built = Segmenter.from_config(lambda f: True, config)

    assert (built.pre_roll_frames, built.keep_pauses) == (17, True)  # 0.5 s = 16.7 frames
    assert (built.start_voiced_frames, built.start_window_frames) == (2, 3)


def test_from_config_rejects_a_start_window_too_small_for_its_count():
    config = AudioPipelineConfig(start_voiced_frames=7, start_window_frames=6)

    with pytest.raises(ValueError, match="start_voiced_frames"):
        Segmenter.from_config(lambda f: True, config)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        (" On ", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
    ],
)
def test_cli_on_off_flags_accept_common_spellings(text, value):
    from audio.cli import _flag

    assert _flag(text) is value


@pytest.mark.parametrize("text", ["maybe", "", "2", "onn"])
def test_cli_on_off_flags_reject_anything_else(text):
    import argparse

    from audio.cli import _flag, build_parser

    with pytest.raises(argparse.ArgumentTypeError, match="expected on/off"):
        _flag(text)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--keep-pauses", text])


def test_tuning_flags_set_the_new_segmentation_fields():
    from audio.cli import apply_tuning_arguments, build_parser

    args = build_parser().parse_args(
        [
            "--pre-roll-s",
            "0.5",
            "--keep-pauses",
            "on",
            "--start-voiced-frames",
            "3",
            "--start-window-frames",
            "8",
        ]
    )
    config = AudioPipelineConfig()

    apply_tuning_arguments(config, args)

    assert (config.pre_roll_s, config.keep_pauses) == (0.5, True)
    assert (config.start_voiced_frames, config.start_window_frames) == (3, 8)


def test_explicit_zero_and_off_override_the_config_values():
    """0 and off are values, not "not given": they must still reach the config."""
    from audio.cli import apply_tuning_arguments, build_parser

    args = build_parser().parse_args(["--pre-roll-s", "0", "--keep-pauses", "off"])
    config = AudioPipelineConfig(pre_roll_s=0.3, keep_pauses=True)

    apply_tuning_arguments(config, args)

    assert (config.pre_roll_s, config.keep_pauses) == (0.0, False)


def test_new_tuning_flags_leave_the_config_alone_when_absent():
    from audio.cli import apply_tuning_arguments, build_parser

    config = AudioPipelineConfig(
        pre_roll_s=0.7, keep_pauses=True, start_voiced_frames=2, start_window_frames=4
    )

    apply_tuning_arguments(config, build_parser().parse_args([]))

    assert (
        config.pre_roll_s,
        config.keep_pauses,
        config.start_voiced_frames,
        config.start_window_frames,
    ) == (0.7, True, 2, 4)


def test_shipped_defaults_keep_the_pre_roll_contiguous_with_the_onset():
    """Pre-roll 10 + start 6 of 10: the audio runs unbroken up to the first voiced frame."""
    frames = [bytes([0, i]) for i in range(30)] + [bytes([1, i]) for i in range(30, 60)]
    segmenter = Segmenter(
        lambda f: f[0] == 1,
        max_silent_frames=50,
        min_speech_frames=2,
        pre_roll_frames=10,
        start_voiced_frames=6,
        start_window_frames=10,
    )
    starts = [(i, e) for i, f in enumerate(frames) if (e := segmenter.push(f)) is not None]
    [(at, start)] = starts
    assert (at, start.kind, start.onset_frames) == (35, "start", 6)  # onset was frame 30
    end = segmenter.flush()
    assert end is not None
    assert [f[1] for f in end.frames] == list(range(20, 60))
