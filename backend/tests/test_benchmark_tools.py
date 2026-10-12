"""Tests for the laptop-benchmark helpers: benchmarks/hwprobe.py and cpu_report.py."""

import pytest

from benchmarks.cpu_report import (
    DRIFT_WARN,
    TARGET_RTF,
    drift,
    render,
    summarize_recording,
    thirds,
    verdict,
)
from benchmarks.hwprobe import parse_cpuinfo, verdicts

CPUINFO = """\
processor\t: 0
model name\t: Intel(R) Core(TM) i7-4710HQ CPU @ 2.50GHz
physical id\t: 0
core id\t\t: 0
flags\t\t: fpu sse sse2 avx avx2 fma
processor\t: 1
model name\t: Intel(R) Core(TM) i7-4710HQ CPU @ 2.50GHz
physical id\t: 0
core id\t\t: 0
flags\t\t: fpu sse sse2 avx avx2 fma
processor\t: 2
model name\t: Intel(R) Core(TM) i7-4710HQ CPU @ 2.50GHz
physical id\t: 0
core id\t\t: 1
flags\t\t: fpu sse sse2 avx avx2 fma
"""


class TestParseCpuinfo:
    def test_counts_logical_and_physical_cores(self):
        parsed = parse_cpuinfo(CPUINFO)
        assert parsed["logical_cores"] == 3
        assert parsed["physical_cores"] == 2  # two (package, core) pairs; core 0 has 2 threads

    def test_model_and_flags(self):
        parsed = parse_cpuinfo(CPUINFO)
        assert parsed["model"] == "Intel(R) Core(TM) i7-4710HQ CPU @ 2.50GHz"
        assert {"avx", "avx2", "fma"} <= parsed["flags"]
        assert "avx512f" not in parsed["flags"]

    def test_empty_input_reports_unknowns(self):
        parsed = parse_cpuinfo("")
        assert parsed["model"] is None
        assert parsed["flags"] == set()
        assert parsed["logical_cores"] is None and parsed["physical_cores"] is None


def _info(**over):
    info = {
        "cpu": {"avx2": True},
        "ram_gb": 16.0,
        "ffmpeg": {"path": "/usr/bin/ffmpeg", "mp3_decoder": True},
        "stack": {"ctranslate2": "4.8", "webrtcvad": True},
        "power": {"on_ac": True, "profile": "performance"},
    }
    for key, value in over.items():
        info[key] = value
    return info


class TestHardwareVerdicts:
    def test_clean_machine_has_no_problems(self):
        assert verdicts(_info()) == []

    def test_each_blocker_is_reported(self):
        bad = _info(
            cpu={"avx2": False},
            ram_gb=4.0,
            ffmpeg={"path": None, "mp3_decoder": None},
            stack={"ctranslate2": None, "webrtcvad": False},
            power={"on_ac": False, "profile": "power-saver"},
        )
        text = " ".join(verdicts(bad))
        for needle in (
            "AVX2",
            "RAM",
            "ffmpeg",
            "ctranslate2",
            "webrtcvad",
            "battery",
            "power-saver",
        ):
            assert needle in text

    def test_unknown_values_are_not_blamed(self):
        # None means "could not determine": never report it as a problem.
        unknown = _info(cpu={"avx2": None}, ram_gb=None, power={"on_ac": None, "profile": None})
        assert verdicts(unknown) == []


class TestThirds:
    def test_first_and_last_third_means(self):
        assert thirds([1, 1, 2, 2, 3, 3]) == (1.0, 3.0)

    def test_needs_three_windows(self):
        with pytest.raises(ValueError):
            thirds([1.0, 2.0])

    def test_drift(self):
        assert drift(1.0, 1.5) == pytest.approx(0.5)
        assert drift(0.0, 1.0) == 0.0  # no baseline: no claim


def _rec(times, tp=1, fp=0, fn=2):
    return {
        "record_id": "09-13-live-lt",
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "config": {"whisper_model": "small", "window_times_s": times},
    }


class TestReport:
    def test_rtf_uses_audio_seconds_per_window(self):
        row = summarize_recording(_rec([15.0] * 6), window_s=30.0)
        assert row["rtf"] == pytest.approx(0.5)
        assert row["drift"] == pytest.approx(0.0)

    def test_throttling_is_flagged(self):
        row = summarize_recording(_rec([10.0] * 3 + [14.0] * 3), window_s=30.0)
        assert row["drift"] == pytest.approx(0.4)
        assert row["drift"] > DRIFT_WARN
        assert "heats up" in verdict(row)

    def test_verdict_uses_the_late_rtf(self):
        # Fast start, slow finish: judged on how it ends, not how it begins.
        row = summarize_recording(_rec([5.0] * 3 + [20.0] * 3), window_s=30.0)
        assert row["rtf_first"] < TARGET_RTF < row["rtf_last"]
        assert "TOO SLOW" in verdict(row)

    def test_cached_replay_has_no_timings(self):
        row = summarize_recording(_rec([]), window_s=30.0)
        assert row["rtf"] is None
        assert "no per-window timings" in verdict(row)

    def test_overlapping_windows_warn(self):
        row = summarize_recording(_rec([15.0] * 6), window_s=30.0)
        row["overlap"] = True
        assert "WARNING" in render([row])
