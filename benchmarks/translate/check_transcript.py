"""Phase 0: check a corrected transcript against docs/translate/phase0/TRANSCRIPT_CORRECTION.md.

Usage: python benchmarks/translate/check_transcript.py <file> [<file> ...]

Prints one problem per line as ``<file>:<line>: <message>``; prints nothing and
exits 0 when every file is clean.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_HEADERS = ("window", "file", "range_s", "corrected_by")
LINE_RE = re.compile(r"^\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\] (.+)$")
TAG_RE = re.compile(r"<(/?)([a-z]+)>")
LANG_TAGS = {"en", "ru"}


def check_text(lines: list[str]) -> list[tuple[int, str]]:
    """Return (1-based line number, message) for every problem found."""
    problems: list[tuple[int, str]] = []
    headers: dict[str, str] = {}
    prev_start = -1.0
    range_s: tuple[float, float] | None = None

    for no, raw in enumerate(lines, 1):
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if line.startswith("#"):
            m = re.match(r"#\s*(\w+):\s*(.*)$", line)
            if m:
                headers[m.group(1)] = m.group(2).strip()
                if m.group(1) == "range_s":
                    r = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", m.group(2).strip())
                    if r:
                        range_s = (float(r.group(1)), float(r.group(2)))
                    else:
                        problems.append((no, "range_s is not <start>-<end>"))
            continue

        m = LINE_RE.match(line)
        if not m:
            problems.append((no, "not '[start-end] text'"))
            continue
        start, end, text = float(m.group(1)), float(m.group(2)), m.group(3).strip()
        if end <= start:
            problems.append((no, f"end {end} is not after start {start}"))
        if start < prev_start:
            problems.append((no, "times go backwards"))
        prev_start = start
        if range_s and not (range_s[0] - 1 <= start and end <= range_s[1] + 1):
            problems.append((no, "times outside range_s"))

        if text == "<skip>":
            continue
        if "<skip>" in text:
            problems.append((no, "<skip> must be the whole line's text"))
        if re.search(r"\d", text):
            problems.append((no, "digits: write numbers as words"))

        open_tag: str | None = None
        for tm in TAG_RE.finditer(text):
            closing, name = tm.group(1) == "/", tm.group(2)
            if name not in LANG_TAGS:
                problems.append((no, f"unknown marker <{tm.group(1)}{name}>"))
            elif not closing:
                if open_tag:
                    problems.append((no, f"<{name}> opened inside <{open_tag}>"))
                open_tag = name
            elif open_tag != name:
                problems.append((no, f"</{name}> without matching <{name}>"))
            else:
                open_tag = None
        if open_tag:
            problems.append((no, f"unclosed <{open_tag}>"))
        stray = TAG_RE.sub("", text)
        if "<" in stray or ">" in stray:
            problems.append((no, "stray '<' or '>'"))
        if re.search(r"\(\?(?!\))|(?<!\()\?\)", text):
            problems.append((no, "malformed uncertainty marker: use (?)"))

    for name in REQUIRED_HEADERS:
        if name not in headers:
            problems.append((0, f"missing header '# {name}:'"))
    cb = headers.get("corrected_by", "")
    if "corrected_by" in headers and ("<name>" in cb or "<YYYY-MM-DD>" in cb):
        problems.append((0, "corrected_by/date not filled in"))
    return problems


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    bad = False
    for path in argv:
        text = Path(path).read_text(encoding="utf-8-sig").splitlines()
        for no, msg in check_text(text):
            bad = True
            print(f"{path}:{no}: {msg}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
