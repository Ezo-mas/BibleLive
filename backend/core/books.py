"""Canonical 66-book Bible canon — the single source of truth.

The verse parser, the importer, and the speech-to-text correction regex all
derive their book data from :data:`CANONICAL_BOOKS`, so the copies cannot
drift (the ``timon``/``timothy`` bug came from maintaining them separately).

The Lithuanian book table in ``verse_parser_lt.py`` is intentionally separate
(see AGENTS.md) and does not belong here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Book:
    """One book of the Protestant canon.

    Attributes:
        name: Canonical English name, e.g. ``"1 Corinthians"``.
        chapters: Number of chapters in the book.
        order: 1-based canonical position (Genesis=1 .. Revelation=66).
        abbrev: KJV JSON abbreviation used by the importer, e.g. ``"1co"``.
        variants: Spoken/written name forms for the parser. Numbered books
            list the name without the number (``"corinthians"``); the parser
            reads the number from ``name``.
    """

    name: str
    chapters: int
    order: int
    abbrev: str
    variants: tuple[str, ...]


CANONICAL_BOOKS: tuple[Book, ...] = (
    Book("Genesis", 50, 1, "gn", ("genesis",)),
    Book("Exodus", 40, 2, "ex", ("exodus",)),
    Book("Leviticus", 27, 3, "lv", ("leviticus",)),
    Book("Numbers", 36, 4, "nm", ("numbers",)),
    Book("Deuteronomy", 34, 5, "dt", ("deuteronomy",)),
    Book("Joshua", 24, 6, "js", ("joshua",)),
    Book("Judges", 21, 7, "jd", ("judges",)),
    Book("Ruth", 4, 8, "rt", ("ruth",)),
    Book("1 Samuel", 31, 9, "1sm", ("samuel",)),
    Book("2 Samuel", 24, 10, "2sm", ("samuel",)),
    Book("1 Kings", 22, 11, "1kgs", ("kings",)),
    Book("2 Kings", 25, 12, "2kgs", ("kings",)),
    Book("1 Chronicles", 29, 13, "1ch", ("chronicles", "chronicle")),
    Book("2 Chronicles", 36, 14, "2ch", ("chronicles", "chronicle")),
    Book("Ezra", 10, 15, "ezr", ("ezra",)),
    Book("Nehemiah", 13, 16, "ne", ("nehemiah",)),
    Book("Esther", 10, 17, "et", ("esther",)),
    Book("Job", 42, 18, "job", ("job",)),
    Book("Psalms", 150, 19, "ps", ("psalms", "psalm")),
    Book("Proverbs", 31, 20, "prv", ("proverbs", "proverb")),
    Book("Ecclesiastes", 12, 21, "ec", ("ecclesiastes",)),
    Book(
        "Song of Solomon",
        8,
        22,
        "so",
        ("song of solomon", "song of songs", "songs of solomon", "canticles"),
    ),
    Book("Isaiah", 66, 23, "is", ("isaiah",)),
    Book("Jeremiah", 52, 24, "jr", ("jeremiah",)),
    Book("Lamentations", 5, 25, "lm", ("lamentations", "lamentation")),
    Book("Ezekiel", 48, 26, "ez", ("ezekiel",)),
    Book("Daniel", 12, 27, "dn", ("daniel",)),
    Book("Hosea", 14, 28, "ho", ("hosea",)),
    Book("Joel", 3, 29, "jl", ("joel",)),
    Book("Amos", 9, 30, "am", ("amos",)),
    Book("Obadiah", 1, 31, "ob", ("obadiah",)),
    Book("Jonah", 4, 32, "jo", ("jonah",)),
    Book("Micah", 7, 33, "mi", ("micah",)),
    Book("Nahum", 3, 34, "na", ("nahum",)),
    Book("Habakkuk", 3, 35, "hk", ("habakkuk",)),
    Book("Zephaniah", 3, 36, "zp", ("zephaniah",)),
    Book("Haggai", 2, 37, "hg", ("haggai",)),
    Book("Zechariah", 14, 38, "zc", ("zechariah",)),
    Book("Malachi", 4, 39, "ml", ("malachi",)),
    Book("Matthew", 28, 40, "mt", ("matthew",)),
    Book("Mark", 16, 41, "mk", ("mark",)),
    Book("Luke", 24, 42, "lk", ("luke",)),
    Book("John", 21, 43, "jn", ("john",)),
    Book("Acts", 28, 44, "act", ("acts", "acts of the apostles")),
    Book("Romans", 16, 45, "rm", ("romans",)),
    Book("1 Corinthians", 16, 46, "1co", ("corinthians", "corinthian")),
    Book("2 Corinthians", 13, 47, "2co", ("corinthians", "corinthian")),
    Book("Galatians", 6, 48, "gl", ("galatians",)),
    Book("Ephesians", 6, 49, "eph", ("ephesians",)),
    Book("Philippians", 4, 50, "ph", ("philippians",)),
    Book("Colossians", 4, 51, "cl", ("colossians",)),
    Book("1 Thessalonians", 5, 52, "1ts", ("thessalonians",)),
    Book("2 Thessalonians", 3, 53, "2ts", ("thessalonians",)),
    Book("1 Timothy", 6, 54, "1tm", ("timothy",)),
    Book("2 Timothy", 4, 55, "2tm", ("timothy",)),
    Book("Titus", 3, 56, "tt", ("titus",)),
    Book("Philemon", 1, 57, "phm", ("philemon",)),
    Book("Hebrews", 13, 58, "hb", ("hebrews", "hebrew")),
    Book("James", 5, 59, "jm", ("james",)),
    Book("1 Peter", 5, 60, "1pe", ("peter",)),
    Book("2 Peter", 3, 61, "2pe", ("peter",)),
    Book("1 John", 5, 62, "1jo", ("john",)),
    Book("2 John", 1, 63, "2jo", ("john",)),
    Book("3 John", 1, 64, "3jo", ("john",)),
    Book("Jude", 1, 65, "jud", ("jude",)),
    Book(
        "Revelation",
        22,
        66,
        "re",
        ("revelation", "revelations", "revelation of john"),
    ),
)


def find_book(name: str) -> Book | None:
    """Return the canonical book named ``name``, or None if there is no such book.

    Matching is case- and whitespace-insensitive but otherwise exact, so callers
    resolve against the same canon the parser, importer, and database use instead
    of keeping a list of their own. Abbreviations are deliberately not accepted:
    the canon stores them for import-time mapping only.
    """
    if not isinstance(name, str):
        return None
    wanted = name.strip().casefold()
    if not wanted:
        return None
    for book in CANONICAL_BOOKS:
        if book.name.casefold() == wanted:
            return book
    return None
