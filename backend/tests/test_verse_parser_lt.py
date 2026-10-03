"""Tests for the Lithuanian verse parser (core.verse_parser with language="lt").

Canonical names/chapter counts mirror the English BOOKS, so the same
Bible database and validator work unchanged. Diacritics may be present
or absent in transcripts; both parse.
"""

from core.verse_parser import BOOKS as EN_BOOKS
from core.verse_parser import ReferenceParser, StreamingParser
from core.verse_parser_lt import BOOKS as LT_BOOKS

P = ReferenceParser(language="lt")


def refs(text):
    return [str(r) for r in P.parse(text)]


def one(text):
    found = P.parse(text)
    assert len(found) == 1, f"expected 1 reference in {text!r}, got {[str(r) for r in found]}"
    return found[0]


class TestBookData:
    def test_66_books_match_english_canon(self):
        assert [(name, n) for name, n, _ in LT_BOOKS] == [(name, n) for name, n, _ in EN_BOOKS]

    def test_unknown_language_rejected(self):
        try:
            ReferenceParser(language="de")
        except ValueError as e:
            assert "de" in str(e)
        else:
            raise AssertionError("expected ValueError for language='de'")


class TestNumbers:
    def test_spoken_numbers(self):
        # (folded) ordinals, teens, tens+units all read as numbers
        r = one("Jono evangelija trecias skyrius sesiolikta eilute")
        assert str(r) == "John 3:16"
        r = one("Romiečiams aštuntas skyrius dvidešimt aštunta eilutė")
        assert str(r) == "Romans 8:28"
        # hundred + teen: "šimtas devyniolikta psalmė" -> Psalms 119
        r = one("šimtas devyniolikta psalmė")
        assert str(r) == "Psalms 119"

    def test_written_numbers(self):
        assert refs("Jono 3:16") == ["John 3:16"]
        assert refs("Jono 3 skyrius 16 eilutė") == ["John 3:16"]

    def test_number_word_forms(self):
        # one stem, many endings: aštuntą / aštuntame / aštuntasis
        for ending in ("a", "ame", "as", "ojo", "oji"):
            r = P.parse(f"Jono 3 astunt{ending} eilutė")
            assert [str(x) for x in r] == ["John 3:8"], f"ending {ending!r}"


class TestExplicit:
    def test_docstring_examples(self):
        r = one("Jono evangelijos 3 skyrius, 16 eilutė")
        assert str(r) == "John 3:16"
        assert r.confidence == "high"

        r = one("Laiškas romiečiams, aštuntas skyrius, dvidešimt aštunta eilutė")
        assert str(r) == "Romans 8:28"

        r = one("Pirmasis laiškas korintiečiams 13, 4-7")
        assert str(r) == "1 Corinthians 13:4-7"

        r = one("Dvidešimt trečioji psalmė")
        assert str(r) == "Psalms 23"

        r = one("Pradžios knygos pirmas skyrius")
        assert str(r) == "Genesis 1"

        r = one("Evangelija pagal Matą 5:3")
        assert str(r) == "Matthew 5:3"

    def test_postpositional_reordering(self):
        assert refs("Romiečiams 8 skyrius 28 eilutė") == ["Romans 8:28"]
        assert refs("Romiečiams 8 skyrius 4 iki 7 eilutės") == ["Romans 8:4-7"]

    def test_ranges(self):
        assert refs("hebrajams 11 1 iki 3") == ["Hebrews 11:1-3"]
        assert refs("Pradžios knyga 1 skyrius 1 ir 2 eilutės") == ["Genesis 1:1-2"]

    def test_no_diacritics(self):
        assert refs("Laiskas romieciams, astuntas skyrius, dvidesimt astunta eilute") == [
            "Romans 8:28"
        ]

    def test_numbered_books(self):
        assert refs("Antrasis laiškas korintiečiams 5 skyrius") == ["2 Corinthians 5"]
        assert refs("1 Korintiečiams 13 skyrius") == ["1 Corinthians 13"]
        assert refs("Trečias Jono laiškas 1 eilutė") == ["3 John 1:1"]

    def test_single_chapter_books(self):
        r = one("Judo laiškas 3 eilutė")
        assert str(r) == "Jude 1:3"


class TestStreaming:
    def test_context_between_segments(self):
        sp = StreamingParser(language="lt")
        first = sp.feed("atsiverskime Jono evangeliją trečią skyrių")
        assert [str(r) for r in first] == ["John 3"]
        second = sp.feed("šešiolikta eilutė sako")
        assert [str(r) for r in second] == ["John 3:16"]

    def test_postpositional_across_segments(self):
        # "...trečias skyrius" + "šešiolikta eilutė" must combine even when
        # the verse word arrives in the next segment (postprocess re-runs
        # over carry + new tokens)
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("Jono evangelija 3")] == ["John 3"]
        got = [str(r) for r in sp.feed("16 eilutė")]
        assert got == ["John 3:16"], got

    def test_reset(self):
        sp = StreamingParser(language="lt")
        sp.feed("Romiečiams 8 skyrius")
        sp.reset()
        assert [str(r) for r in sp.feed("16 eilutė")] == []


class TestEnglishUnaffected:
    def test_english_default(self):
        en = ReferenceParser()
        assert en.language == "en"
        assert [str(r) for r in en.parse("Romans 8:28-30")] == ["Romans 8:28-30"]

    def test_english_streaming(self):
        sp = StreamingParser()
        assert [str(r) for r in sp.feed("turn to Romans")] == []
        assert [str(r) for r in sp.feed("chapter eight verse twenty-eight")] == ["Romans 8:28"]
