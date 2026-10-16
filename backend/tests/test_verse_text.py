"""Tests for core.verse_text: word stems, the verse picked from spoken words, and the loader.

The verses are synthetic, not Bible text. Luke 16 has the distinctive words;
the Genesis filler repeats its words in every verse, so they are common
(IDF below 2) and the Luke words stay rare (IDF above 2).
"""

import sqlite3
from contextlib import closing

import pytest

from core.import_bible import BibleImporter
from core.verse_text import VerseTextIndex, stems

LUKE_16 = [
    "išmanusis valdytojas apkaltintas mokiniams",
    "šeimininkas reikalauja sąskaitų atsiskaitymo",
    "skolininkams sumažino skolas aliejaus statinės",
]
FILLER = ["Dievas kūrė dangų ir žemę"] * 20
ROWS = [("Luke", 16, v, text) for v, text in enumerate(LUKE_16, 1)] + [
    ("Genesis", 1, v, text) for v, text in enumerate(FILLER, 1)
]
INDEX = VerseTextIndex(ROWS)


class TestStems:
    def test_diacritics_and_case_fold(self):
        # Inflected forms of "Viešpats" share one stem once folded.
        assert stems("Viešpaties") == {"viesp"}
        assert stems("viespaties") == stems("VIEŠPATIES") == {"viesp"}

    def test_short_words_are_dropped(self):
        assert stems("ir tai yra") == set()
        assert stems("Jono 3:16") == {"jono"}

    def test_endings_are_cut_to_a_five_letter_stem(self):
        assert stems("mokiniams mokiniai mokinių") == {"mokin"}
        assert stems("mokinys") == {"mokin"}


class TestBestVerse:
    def test_picks_the_verse_whose_words_were_heard(self):
        match = INDEX.best_verse("Luke", 16, LUKE_16[0])
        assert (match.verse, match.verse_end) == (1, None)
        assert match.score == pytest.approx(1.0)
        assert match.runner_up == 0.0

    def test_partial_reading_scores_the_share_heard(self):
        match = INDEX.best_verse("Luke", 16, "skolininkams sumažino skolas aliejaus")
        assert (match.verse, match.verse_end) == (3, None)
        assert match.score == pytest.approx(0.8)  # four of the verse's five distinctive stems

    def test_no_match_returns_none(self):
        assert INDEX.best_verse("Luke", 99, LUKE_16[0]) is None  # unknown chapter
        assert INDEX.best_verse("Mark", 16, LUKE_16[0]) is None  # unknown book
        assert INDEX.best_verse("Luke", 16, "dievas kūrė dangų ir žemę") is None  # common words
        assert INDEX.best_verse("Luke", 16, "ir kaip tai") is None  # no distinctive word
        assert INDEX.best_verse("Genesis", 1, LUKE_16[0]) is None  # heard, but not in chapter

    def test_reading_on_into_the_next_verse_extends_the_range(self):
        text = f"{LUKE_16[0]} {LUKE_16[1]}"
        match = INDEX.best_verse("Luke", 16, text)
        assert (match.verse, match.verse_end) == (1, 2)
        # Verse 2 is inside the range and does not count; the runner-up is verse 3.
        assert match.runner_up == 0.0

    def test_extend_min_decides_how_far_the_range_runs(self):
        text = f"{LUKE_16[0]} šeimininkas reikalauja sąskaitų"  # verse 2 scores 0.75
        assert INDEX.best_verse("Luke", 16, text).verse_end == 2
        match = INDEX.best_verse("Luke", 16, text, extend_min=0.8)
        assert (match.verse, match.verse_end) == (1, None)
        assert match.runner_up == pytest.approx(0.75)


class TestScores:
    def test_every_verse_of_the_chapter_is_scored(self):
        scores = INDEX.scores("Luke", 16, LUKE_16[2])
        assert set(scores) == {1, 2, 3}
        assert scores[3] == pytest.approx(1.0)
        assert scores[1] == 0.0 and scores[2] == 0.0

    def test_agrees_with_best_verse(self):
        text = "skolininkams sumažino skolas aliejaus"
        scores = INDEX.scores("Luke", 16, text)
        match = INDEX.best_verse("Luke", 16, text)
        assert match is not None
        assert scores[match.verse] == pytest.approx(match.score)

    def test_empty_when_nothing_can_match(self):
        assert INDEX.scores("Luke", 99, LUKE_16[0]) == {}  # unknown chapter
        assert INDEX.scores("Luke", 16, "ir kaip tai") == {}  # no distinctive word
        assert INDEX.scores("Luke", 16, "dievas kūrė dangų ir žemę") == {}  # common words only


class TestFromSqlite:
    def test_reads_the_import_schema(self, tmp_path):
        db = tmp_path / "bible_ltb.sqlite"
        importer = BibleImporter(str(db))
        importer.connect()
        try:
            importer.create_schema()
        finally:
            importer.close()

        book_ids = {"Luke": 1, "Genesis": 2}
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.executemany(
                "INSERT INTO books (id, name) VALUES (?, ?)",
                [(book_id, name) for name, book_id in book_ids.items()],
            )
            conn.executemany(
                "INSERT INTO verses (book_id, chapter, verse, text) VALUES (?, ?, ?, ?)",
                [(book_ids[book], c, v, text) for book, c, v, text in ROWS],
            )

        match = VerseTextIndex.from_sqlite(db).best_verse("Luke", 16, LUKE_16[1])
        assert match is not None
        assert (match.verse, match.score) == (2, pytest.approx(1.0))

    def test_warns_when_no_book_name_is_canonical(self, tmp_path, caplog):
        db = tmp_path / "local_names.sqlite"
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute(
                "CREATE TABLE verses (book_id INTEGER, chapter INTEGER, verse INTEGER, text TEXT)"
            )
            conn.execute("INSERT INTO books VALUES (1, 'Luko')")
            conn.execute("INSERT INTO verses VALUES (1, 16, 1, 'valdytojas')")
        with caplog.at_level("WARNING", logger="core.verse_text"):
            VerseTextIndex.from_sqlite(db)
        assert "canonical" in caplog.text

    def test_missing_file_is_not_created(self, tmp_path):
        db = tmp_path / "missing.sqlite"
        with pytest.raises(sqlite3.OperationalError):
            VerseTextIndex.from_sqlite(db)
        assert not db.exists()
