"""Tests for the laptop-benchmark helpers: benchmarks/hwprobe.py and cpu_report.py."""

import pytest

from benchmarks.cpu_report import (
    LIMIT_RTF,
    TARGET_RTF,
    clock_summary,
    load_clock,
    percentile,
    render,
    summarize_recording,
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


def _rec(times, tp=1, fp=0, fn=2):
    return {
        "record_id": "09-13-live-lt",
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "config": {"whisper_model": "small", "window_times_s": times},
    }


class TestSpeedReport:
    def test_rtf_is_compute_per_audio_second(self):
        row = summarize_recording(_rec([15.0] * 6), window_s=30.0)
        assert row["rtf"] == pytest.approx(0.5)
        assert row["rtf_p90"] == pytest.approx(0.5)

    def test_percentile_nearest_rank(self):
        assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9) == 10
        assert percentile([], 0.9) == 0.0

    def test_content_does_not_masquerade_as_throttling(self):
        # Dense speech first, quiet later: the late windows are faster. The report
        # must judge the mean, not flatter the machine by its last third.
        row = summarize_recording(_rec([25.0] * 10 + [5.0] * 10), window_s=30.0)
        assert row["rtf"] == pytest.approx(0.5)
        assert "keeps up" in verdict(row)

    def test_average_above_target_is_marginal(self):
        row = summarize_recording(_rec([20.0] * 6), window_s=30.0)  # RTF 0.67
        assert TARGET_RTF < row["rtf"] <= LIMIT_RTF
        assert "marginal" in verdict(row)

    def test_slower_than_real_time_is_too_slow(self):
        row = summarize_recording(_rec([36.0] * 6), window_s=30.0)
        assert "TOO SLOW" in verdict(row)

    def test_dense_speech_tail_is_called_out_even_if_average_is_fine(self):
        row = summarize_recording(_rec([5.0] * 8 + [36.0, 36.0]), window_s=30.0)
        assert row["rtf"] < TARGET_RTF
        assert "dense speech runs slower than real time" in verdict(row)

    def test_cached_replay_has_no_timings(self):
        row = summarize_recording(_rec([]), window_s=30.0)
        assert row["rtf"] is None
        assert "no per-window timings" in verdict(row)

    def test_overlapping_windows_warn(self):
        row = summarize_recording(_rec([15.0] * 6), window_s=30.0)
        row["overlap"] = True
        assert "WARNING" in render([row])


class TestClockReport:
    def test_stable_clock_is_not_throttling(self):
        line = clock_summary([(3500.0, 70.0)] * 12)
        assert "THROTTLING" not in line
        assert "3500 MHz early -> 3500 MHz late" in line

    def test_clock_drop_is_flagged_with_temperature(self):
        samples = [(3500.0, 70.0)] * 4 + [(3200.0, 85.0)] * 4 + [(2400.0, 95.0)] * 4
        line = clock_summary(samples)
        assert "THROTTLING" in line
        assert "peak 95 C" in line

    def test_too_few_samples_makes_no_claim(self):
        assert "too few samples" in clock_summary([(3500.0, 70.0)] * 3)

    def test_load_clock_skips_unreadable_rows(self, tmp_path):
        path = tmp_path / "clock.csv"
        path.write_text("1,3500,70\n2,,\n3,3400,\nnot,a,row\n", encoding="utf-8")
        assert load_clock(path) == [(3500.0, 70.0), (3400.0, 0.0)]
