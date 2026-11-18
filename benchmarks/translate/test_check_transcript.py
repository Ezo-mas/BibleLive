from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_transcript import check_text  # noqa: E402

HEADER = [
    "# window: 1011-cite",
    "# file: benchmark_audio_10-11-norm.wav",
    "# range_s: 5150-5450",
    "# corrected_by: Tester   date: 2026-11-17",
]


def msgs(body: list[str], header: list[str] = HEADER) -> list[str]:
    return [m for _, m in check_text(header + body)]


def test_clean_file_reports_nothing():
    body = [
        "[5150.4-5157.9] Ir tada karalius išgirdo moters žodžius.",
        "[5158.6-5166.2] Antra Karalių knyga, šeštas skyrius, dvidešimt šešta eilutė.",
        "[5166.9-5171.0] <skip>",
        "[5171.5-5178.8] Jis perplėšė savo drabužius (?) ir sakė <en>Holy Spirit</en>.",
    ]
    assert msgs(body) == []


def test_digits_and_bad_times():
    out = msgs(["[5160.0-5155.0] dvidešimt 6 eilutė"])
    assert any("not after start" in m for m in out)
    assert any("digits" in m for m in out)


def test_backwards_and_outside_range():
    out = msgs(["[5200.0-5210.0] a", "[5190.0-5195.0] b", "[5500.0-5510.0] c"])
    assert any("backwards" in m for m in out)
    assert any("outside range_s" in m for m in out)


def test_tags():
    assert any("unclosed <en>" in m for m in msgs(["[5150.0-5151.0] <en>Holy"]))
    assert any("unknown marker" in m for m in msgs(["[5150.0-5151.0] <de>x</de>"]))
    assert any("whole line" in m for m in msgs(["[5150.0-5151.0] taip <skip>"]))


def test_unfilled_header():
    header = HEADER[:3] + ["# corrected_by: <name>   date: <YYYY-MM-DD>"]
    assert any("not filled" in m for m in msgs([], header))
    assert any("missing header" in m for m in msgs([], HEADER[:2]))
