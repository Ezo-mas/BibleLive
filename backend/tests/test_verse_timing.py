"""Tests for benchmarks/verse_timing.py: aligning spoken words to a chapter's verses."""

import sqlite3

from benchmarks.verse_timing import (
    EXACT_SCORE,
    NEAR_SCORE,
    READ_ON_VERSES,
    SpokenWord,
    align,
    chapter_verse_texts,
    period_verse_reads,
    reading_window,
)


def _words(*items):
    """(text, start_s, end_s, utterance) tuples as SpokenWord."""
    return [SpokenWord(text, start, end, utt) for text, start, end, utt in items]


def test_align_skips_the_speakers_words_and_accepts_one_misheard_letter():
    ref = ["dieva", "mylej", "pasau", "taip"]
    spoken = ["ir", "dieva", "kazkas", "mylej", "pasau", "taip"]

    assert align(ref, spoken) == [
        (0, 1, EXACT_SCORE),
        (1, 3, EXACT_SCORE),
        (2, 4, EXACT_SCORE),
        (3, 5, EXACT_SCORE),
    ]
    # One letter off in a 5-letter stem is still the word (a misheard letter).
    assert align(["tiesa"], ["tiesi"]) == [(0, 0, NEAR_SCORE)]
    assert align(["tiesa"], ["tiesa", "kazkas"]) == [(0, 0, EXACT_SCORE)]


def test_align_keeps_the_verses_in_order():
    # "aaaaa" was spoken after "bbbbb": only one of the two can be aligned in order.
    pairs = align(["aaaaa", "bbbbb"], ["bbbbb", "aaaaa"])

    assert len(pairs) == 1


def test_a_verse_read_with_a_missed_word_gets_its_first_and_last_heard_word():
    texts = {
        1: "Dievas mylėjo pasaulį taip labai",  # stems: dieva mylej pasau taip labai
        2: "Ir tada jis davė vienatinį sūnų",  # stems: tada dave viena sunu
    }
    spoken = _words(
        ("Dievas", 10.0, 10.5, 1),
        ("mylėjo", 10.6, 11.0, 1),
        ("sakau", 11.1, 11.5, 1),  # the speaker's comment
        ("pasaulį", 12.0, 12.6, 1),
        ("labai", 13.0, 13.4, 1),  # "taip" was not heard
        ("tada", 14.0, 14.3, 2),
    )

    reads = period_verse_reads(
        book="Jonas",
        chapter=3,
        spoken=spoken,
        texts=texts,
        verse_start=1,
        verse_end=None,
        read_bar=0.5,
    )

    assert [r.verse for r in reads] == [1]  # verse 2 has 1 of 4 words: below the bar
    read = reads[0]
    assert (read.book, read.chapter, read.verse) == ("Jonas", 3, 1)
    assert (read.tokens, read.matched, read.coverage) == (5, 4, 0.8)
    assert read.start_s == 10.0  # first aligned word, not the verse's first word
    assert read.end_s == 13.4
    assert read.text == texts[1]
    assert [a[1] for a in read.aligned] == ["Dievas", "mylėjo", "pasaulį", "labai"]


def test_the_read_bar_decides_which_verses_count():
    texts = {1: "Dievas mylėjo pasaulį taip labai"}
    spoken = _words(("Dievas", 1.0, 1.4, 1), ("mylėjo", 1.5, 1.9, 1), ("pasaulį", 2.0, 2.4, 1))

    strict = period_verse_reads(
        book="Jonas",
        chapter=3,
        spoken=spoken,
        texts=texts,
        verse_start=1,
        verse_end=None,
        read_bar=0.9,
    )
    loose = period_verse_reads(
        book="Jonas",
        chapter=3,
        spoken=spoken,
        texts=texts,
        verse_start=1,
        verse_end=None,
        read_bar=0.5,
    )

    assert strict == []  # 3 of 5 words
    assert [r.verse for r in loose] == [1]


def test_reading_window_runs_on_past_the_display_within_the_chapter():
    assert reading_window(1, None, 3) == range(1, 4)  # the chapter ends first
    assert reading_window(5, 6, 100) == range(5, 6 + READ_ON_VERSES + 1)
    assert reading_window(20, None, 100) == range(20, 31)


def test_chapter_verse_texts_reads_one_chapter_read_only(tmp_path):
    db = tmp_path / "bible.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE verses (id INTEGER PRIMARY KEY, book_id INTEGER, chapter INTEGER,
                             verse INTEGER, text TEXT);
        INSERT INTO books VALUES (1, 'John'), (2, 'Romans');
        INSERT INTO verses (book_id, chapter, verse, text) VALUES
            (1, 3, 16, 'Dievas taip pamilo pasaulį'), (1, 3, 17, 'Dievas atsiuntė'),
            (1, 4, 1, 'kita glava'), (2, 3, 16, 'laiškas');
        """
    )
    conn.commit()
    conn.close()

    texts = chapter_verse_texts(db, "John", 3)

    assert texts == {16: "Dievas taip pamilo pasaulį", 17: "Dievas atsiuntė"}
    assert chapter_verse_texts(db, "John", 99) == {}
