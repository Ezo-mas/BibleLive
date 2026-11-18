from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_sermon150 import boundary_map, sentences_of, words_of  # noqa: E402


def texts(lines):
    return [s.text for s in sentences_of(words_of(lines))]


def test_sentences_span_lines_and_skip_breaks():
    lines = [
        (0.0, 4.0, "Jis atėjo ir"),
        (4.0, 8.0, "pasakė: „Aš čia.“ Ar girdit?"),
        (8.0, 9.0, "<skip>"),
        (9.0, 12.0, "Be taško"),
        (12.0, 14.0, "Naujas sakinys!"),
    ]
    assert texts(lines) == [
        "Jis atėjo ir pasakė: „Aš čia.“",
        "Ar girdit?",
        "Be taško Naujas sakinys!",
    ]


def test_uncertainty_marker_does_not_end_a_sentence():
    lines = [(0.0, 5.0, "Žmogus, kurie(?) turėjo patirtį."), (5.0, 6.0, "Kitas (?) sakinys.")]
    assert texts(lines) == ["Žmogus, kurie(?) turėjo patirtį.", "Kitas (?) sakinys."]


def test_sentence_start_time_is_interpolated():
    s = sentences_of(words_of([(10.0, 14.0, "Vienas du. Trys keturi.")]))
    assert [x.start for x in s] == [10.0, 12.0]


def test_boundary_map_follows_edits():
    clean = ["Jonas", "krikštijo", "vandeniu.", "Ir", "jis"]
    raw = ["Į", "Jonas", "krikščiojo", "vandeniu", "ir", "jis"]
    m = boundary_map(clean, raw)
    # sentence 1 = clean[0:3] -> raw words around it, sentence 2 = clean[3:5]
    assert raw[m[3] : m[5]] == ["ir", "jis"]
    assert raw[m[0] : m[3]][-2:] == ["krikščiojo", "vandeniu"]
