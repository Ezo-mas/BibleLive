"""Tests for the rolling-window commit logic (no model: words are simulated)."""

import numpy as np
import pytest

from audio.rolling import RollingWindow
from audio.stt import TimedWord

# Requires the optional audio extras (audio.stt imports faster-whisper).
pytestmark = pytest.mark.audio

RATE = 100  # samples per second; small so the arrays stay tiny


def _words_in(window_start: float, window_end: float, spoken: list[tuple[float, str]]):
    """What a perfect Whisper returns for a window: words fully inside it."""
    return [
        TimedWord(t - window_start, t - window_start + 0.3, f" {w}")
        for t, w in spoken
        if window_start <= t and t + 0.3 <= window_end
    ]


def _run(spoken, duration_s, window_s=15.0, hop_s=5.0, lookahead_s=2.0, chunk_s=1.0):
    rolling = RollingWindow(window_s, hop_s, lookahead_s, sample_rate=RATE)
    commits = []

    def process(final):
        audio, start = rolling.window()
        end = start + len(audio) / RATE
        commits.append(rolling.commit(_words_in(start, end, spoken), start, final=final))

    for _ in range(int(duration_s / chunk_s)):
        rolling.push(np.zeros(int(chunk_s * RATE), dtype=np.float32))
        if rolling.due:
            process(final=False)
    process(final=True)
    return commits


def test_every_word_is_committed_exactly_once_in_order():
    spoken = [(t * 0.7, f"w{i}") for i, t in enumerate(range(1, 140))]  # ~0.7s apart
    commits = _run(spoken, duration_s=100)

    committed = " ".join(c.text for c in commits if c.text).split()

    assert committed == [w for _, w in spoken]


def test_commits_tile_the_timeline_without_gaps_or_overlap():
    commits = _run([], duration_s=60)

    assert commits[0].start_s == 0.0
    for prev, cur in zip(commits, commits[1:], strict=False):
        assert cur.start_s == prev.end_s
    assert commits[-1].end_s == 60.0


def test_newest_audio_waits_for_the_lookahead():
    rolling = RollingWindow(window_s=15, hop_s=5, lookahead_s=2, sample_rate=RATE)
    rolling.push(np.zeros(5 * RATE, dtype=np.float32))
    audio, start = rolling.window()
    # A word at 4.0s is inside the window but within the lookahead: held back.
    commit = rolling.commit([TimedWord(1.0, 1.3, " early"), TimedWord(4.0, 4.3, " edge")], start)

    assert (commit.start_s, commit.end_s, commit.text) == (0.0, 3.0, "early")


def test_buffer_keeps_only_one_window():
    rolling = RollingWindow(window_s=15, hop_s=5, lookahead_s=2, sample_rate=RATE)
    for _ in range(10):
        rolling.push(np.zeros(5 * RATE, dtype=np.float32))

    audio, start = rolling.window()

    assert len(audio) == 15 * RATE
    assert start == 35.0


def test_low_confidence_word_marks_the_commit():
    rolling = RollingWindow(window_s=15, hop_s=5, lookahead_s=0, sample_rate=RATE)
    rolling.push(np.zeros(5 * RATE, dtype=np.float32))
    _, start = rolling.window()

    commit = rolling.commit(
        [TimedWord(1.0, 1.3, " Romans"), TimedWord(2.0, 2.3, " eight", "no_speech_prob 0.9")],
        start,
    )

    assert commit.low_confidence_reason == "no_speech_prob 0.9"
    assert commit.spoken_s == 1.0


@pytest.mark.parametrize(
    ("window_s", "hop_s", "lookahead_s"), [(10, 0, 2), (10, 9, 2), (10, 5, -1)]
)
def test_rejects_windows_that_cannot_cover_the_commit(window_s, hop_s, lookahead_s):
    with pytest.raises(ValueError):
        RollingWindow(window_s, hop_s, lookahead_s)
