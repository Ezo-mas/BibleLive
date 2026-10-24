"""
verse_parser.py - find Bible references in spoken or written English text.

Handles things like:
    "Romans 8:28-30"
    "turn with me to Romans chapter eight verse twenty-eight"
    "first Corinthians thirteen verses four through seven"
    "John three sixteen"
    "Psalm one nineteen"            -> Psalms 119
    "Jude verse 3"                  -> Jude 1:3 (single-chapter book)
    "... and in verse 30 ..."       -> uses the last book/chapter mentioned

Two ways to use it:

    parser = ReferenceParser()
    parser.parse("Turn to John 3:16")            # one-shot, returns list[Reference]

    stream = StreamingParser()                    # for live transcription
    for segment in transcript_segments:
        for ref in stream.feed(segment):
            show(ref)

No third-party dependencies (standard library only).

For Lithuanian input use ReferenceParser(language="lt") / StreamingParser(language="lt");
the Lithuanian language data lives in verse_parser_lt.py.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import cast

if __package__:
    from . import verse_parser_lt as _lt
    from .books import CANONICAL_BOOKS
    from .verse_text import VerseTextIndex
else:
    import verse_parser_lt as _lt  # type: ignore[no-redef]
    from books import CANONICAL_BOOKS  # type: ignore[no-redef]
    from verse_text import VerseTextIndex  # type: ignore[no-redef]

Token = int | str

# --------------------------------------------------------------------------
# Book data: (canonical name, chapter count, name variants as spoken/written)
# comes from the shared canon in core/books.py so the parser, importer, and
# STT correction regex cannot drift apart. Numbered books list the name WITHOUT
# the number ("corinthians"); the number comes from the canonical name.
# Abbreviations (Rom, Matt, ...) are deliberately left out of the parser:
# speech-to-text writes out full names, and short abbreviations cause false
# positives ("mic 1 2" -> Micah 1:2). Add them if you parse typed text too.
# --------------------------------------------------------------------------
BOOKS: list[tuple[str, int, list[str]]] = [
    (book.name, book.chapters, list(book.variants)) for book in CANONICAL_BOOKS
]

MAX_VERSE = 176  # Psalm 119 is the longest chapter


def _build_book_tables(
    books: list[tuple[str, int, list[str]]],
) -> tuple:
    """Build lookup tables from a BOOKS list.

    Returns (chapters, index, max_name_tokens, fuzzy_pool) where index maps
    (prefix number or 0, name tokens) -> canonical name; numbered books
    ("1 Samuel") take the prefix from the canonical name.
    """
    chapters: dict[str, int] = {name: n for name, n, _ in books}
    index: dict[tuple[int, tuple[str, ...]], str] = {}
    for name, _n, variants in books:
        prefix = int(name[0]) if name[0].isdigit() else 0
        for v in variants:
            index[(prefix, tuple(v.split()))] = name
    max_name_tokens = max(len(k[1]) for k in index)
    # single-word names per prefix, used for fuzzy matching of mis-transcribed names
    fuzzy_pool: dict[int, list[str]] = {0: [], 1: [], 2: [], 3: []}
    for (p, name_tokens), _canon in index.items():
        if len(name_tokens) == 1:
            fuzzy_pool[p].append(name_tokens[0])
    return chapters, index, max_name_tokens, fuzzy_pool


CHAPTERS, _INDEX, _MAX_NAME_TOKENS, _FUZZY_POOL = _build_book_tables(BOOKS)

# Lithuanian tables (name stems + grammar come from verse_parser_lt).
_LT_TABLES = _build_book_tables(_lt.BOOKS)

PREFIXES: dict[Token, int] = {1: 1, 2: 2, 3: 3, "first": 1, "second": 2, "third": 3}
CHAPTER_WORDS = {"chapter", "chapters"}
VERSE_WORDS = {"verse", "verses"}
RANGE_WORDS = {"through", "thru", "to", "until", "-"}
AND_WORDS = {"and"}  # consecutive-verse range: "28 and 30" -> 28-30
FILLERS = {"and", "at", "in", "from", "beginning", "starting"}

# --------------------------------------------------------------------------
# Number words
# --------------------------------------------------------------------------
_UNITS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}
_TEENS = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


def _below_100(w: list[str], j: int) -> tuple[int, int] | None:
    n = len(w)
    if j < n and w[j] in _TENS:
        v = _TENS[w[j]]
        if j + 1 < n and w[j + 1] in _UNITS:
            return v + _UNITS[w[j + 1]], j + 2
        return v, j + 1
    if j < n and w[j] in _TEENS:
        return _TEENS[w[j]], j + 1
    if j < n and w[j] in _UNITS:
        return _UNITS[w[j]], j + 1
    return None


def _read_number(w: list[str], i: int) -> tuple[int, int] | None:
    """Read one spoken number starting at w[i]. Returns (value, next_index).

    "twenty eight" -> 28 (one number), but "three sixteen" -> 3 (then 16 is a
    separate number), which is how people say "John three sixteen".
    """
    n = len(w)
    t = w[i]
    if t == "a" and i + 1 < n and w[i + 1] == "hundred":
        base, j = 1, i + 2
    elif t in _UNITS and i + 1 < n and w[i + 1] == "hundred":
        base, j = _UNITS[t], i + 2
    else:
        return _below_100(w, i)
    value = base * 100
    rest = _below_100(w, j + 1) if j < n and w[j] == "and" else _below_100(w, j)
    if rest:
        value += rest[0]
        j = rest[1]
    return value, j


def tokenize(text: str) -> list[Token]:
    """Lowercase text -> list of ints (numbers, digits or words) and words."""
    text = text.lower().replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"(?<=[^\W\d_])-(?=[^\W\d_])", " ", text)  # twenty-eight -> twenty eight
    raw = re.findall(r"\d+(?:st|nd|rd|th)?|[^\W\d_]+|[:\-]", text)
    out: list[Token] = []
    i = 0
    while i < len(raw):
        t = raw[i]
        if t[0].isdigit():
            # t starts with a digit, so the \d+ match always succeeds; cast
            # keeps the impossible None branch (and a silent 0) out of the path.
            out.append(int(cast(re.Match[str], re.match(r"\d+", t)).group()))
            i += 1
            continue
        r = _read_number(raw, i) if t.isalpha() else None
        if r:
            out.append(r[0])
            i = r[1]
        else:
            out.append(t)
            i += 1
    return out


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
_LEVELS = ["low", "medium", "high"]


def _split_merged(number: int, chapters: int) -> tuple[int, int] | None:
    """The one valid chapter:verse reading of a merged number, if unique.

    46 in a 4-chapter book -> (4, 6); 425 in a 6-chapter book -> (4, 25), as
    42:5 is impossible. A verse never starts with 0. None when no split, or
    more than one, is valid.
    """
    digits = str(number)
    valid = [
        (int(digits[:i]), int(digits[i:]))
        for i in range(1, len(digits))
        if digits[i] != "0"
        and 1 <= int(digits[:i]) <= chapters
        and 1 <= int(digits[i:]) <= MAX_VERSE
    ]
    return valid[0] if len(valid) == 1 else None


def _split_glued_verse(
    number: int, canon: str, chapter: int, validator: Callable[[Reference], bool]
) -> tuple[int, int] | None:
    """The one valid verse range glued digits can be, if unique.

    56 after Psalms 126 (6 verses) -> (5, 6): verse 56 cannot exist and 5-6
    can. None when the number itself names a real verse (Psalms 119:56
    stays), when no split reads as real verses ("50" can only be 5 and 0),
    or when several do.
    """
    digits = str(number)
    if len(digits) != 2:
        return None
    if validator(Reference(canon, chapter, number, None)):
        return None
    a, b = int(digits[0]), int(digits[1])
    if 1 <= a < b <= 9 and validator(Reference(canon, chapter, a, b)):
        return a, b
    return None


def _drop_covered_chapters(refs: list[Reference], others: list[Reference]) -> list[Reference]:
    """Drop chapter-only refs whose chapter ``others`` also names with a verse.

    "Jono 10, atskirius 37 eilutė. Jono 10, 37": the mangled chapter word
    leaves a bare "John 10" next to "John 10:37"; the operator needs only
    the second.
    """
    with_verse = {(r.book, r.chapter) for r in others if r.verse_start is not None}
    return [r for r in refs if r.verse_start is not None or (r.book, r.chapter) not in with_verse]


def _downgrade(level: str) -> str:
    return _LEVELS[max(0, _LEVELS.index(level) - 1)]


@dataclass
class Reference:
    book: str
    chapter: int
    verse_start: int | None = None  # None -> whole chapter
    verse_end: int | None = None
    confidence: str = "high"  # "high" | "medium" | "low"
    inferred: bool = False  # book/chapter came from earlier context
    # The verse was picked from the words read aloud, not from a spoken number.
    from_text: bool = field(default=False, compare=False)
    text: str = field(default="", compare=False)
    start: int = field(default=0, repr=False, compare=False)  # token span
    end: int = field(default=0, repr=False, compare=False)

    def __str__(self) -> str:
        s = f"{self.book} {self.chapter}"
        if self.verse_start is not None:
            s += f":{self.verse_start}"
            if self.verse_end is not None:
                s += f"-{self.verse_end}"
        return s

    def verses(self) -> range | None:
        if self.verse_start is None:
            return None
        return range(self.verse_start, (self.verse_end or self.verse_start) + 1)


@dataclass
class Context:
    """The last book/chapter mentioned, so 'verse 28' can be resolved."""

    book: str | None = None
    chapter: int | None = None
    # Last chapter cited per book, so naming a book again on its own
    # ("Taigi, antra karalių ta istorija") returns the context to it.
    chapters: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------
class ReferenceParser:
    # Instance variable type annotations
    _chapters: dict[str, int]
    _index: dict[tuple[int, tuple[str, ...]], str]
    _max_name_tokens: int
    _fuzzy_pool: dict[int, list[str]]
    _tokenize_fn: Callable[[str], list[Token]]
    _postprocess_fn: Callable[[list[Token]], list[Token]] | None
    _prefixes: dict[Token, int]
    _chapter_words: frozenset[str] | set[str]
    _verse_words: frozenset[str] | set[str]
    _range_words: frozenset[str] | set[str]
    _and_words: frozenset[str] | set[str]
    _fillers: frozenset[str] | set[str]
    _verse_intro_words: frozenset[str] | set[str]
    _label_words: frozenset[str] | set[str]
    _label_danger: set[str]
    language: str
    fuzzy: bool
    validator: Callable[[Reference], bool] | None

    def __init__(
        self,
        fuzzy: bool = True,
        validator: Callable[[Reference], bool] | None = None,
        language: str = "en",
    ):
        """
        fuzzy:     also match slightly mis-transcribed book names ("Habakuk").
                   Forced off for "lt": stems already absorb ending variation.
        validator: optional hook, e.g. check the verse exists in your Bible DB.
                   Return False to drop a reference.
        language:  "en" (default) or "lt"; picks token tables, number grammar
                   and book name variants (see verse_parser_lt.py).
        """
        if language == "en":
            self._chapters, self._index, self._max_name_tokens, self._fuzzy_pool = (
                CHAPTERS,
                _INDEX,
                _MAX_NAME_TOKENS,
                _FUZZY_POOL,
            )
            self._tokenize_fn = tokenize
            self._postprocess_fn = None
            self._prefixes = PREFIXES
            self._chapter_words = CHAPTER_WORDS
            self._verse_words = VERSE_WORDS
            self._range_words = RANGE_WORDS
            self._and_words = AND_WORDS
            self._fillers = FILLERS
            self._verse_intro_words = frozenset()
            self._label_words = frozenset()
        elif language == "lt":
            self._chapters, self._index, self._max_name_tokens, self._fuzzy_pool = _LT_TABLES
            self._tokenize_fn = _lt.tokenize
            self._postprocess_fn = _lt.postprocess
            self._prefixes = _lt.PREFIXES
            self._chapter_words = _lt.CHAPTER_WORDS
            self._verse_words = _lt.VERSE_WORDS
            self._range_words = _lt.RANGE_WORDS
            self._and_words = _lt.AND_WORDS
            self._fillers = _lt.FILLERS
            self._verse_intro_words = _lt.VERSE_INTRO_WORDS
            self._label_words = _lt.LABEL_WORDS
            fuzzy = False
        else:
            raise ValueError(f"Unsupported language {language!r} (available: 'en', 'lt')")
        self.language = language
        self.fuzzy = fuzzy
        self.validator = validator
        # Bare book names that begin a numbered name through a label word
        # ("jono" -> "jono laiskas" = 1/2/3 John): bridging a label word
        # there could hide a dropped digit, so the label bridge below stays
        # off for every canon reachable by such a bare name.
        bare_single: dict[str, str] = {}
        for (prefix, name), canon in self._index.items():
            if prefix == 0 and len(name) == 1:
                bare_single[name[0]] = canon
        self._label_danger = set()
        labels = self._label_words
        for prefix, name in self._index:
            if prefix != 0 and len(name) >= 2 and name[1] in labels:
                hit = bare_single.get(name[0])
                if hit is not None:
                    self._label_danger.add(hit)

    # ---- public API -------------------------------------------------------
    def parse(self, text: str, context: Context | None = None) -> list[Reference]:
        return self._scan(self._prepare(text), context or Context())

    def _prepare(self, text: str) -> list[Token]:
        """Tokenize text; for postpositional languages, reorder the tokens."""
        toks = self._tokenize_fn(text)
        if self._postprocess_fn is not None:
            toks = self._postprocess_fn(toks)
        return toks

    # ---- helpers ----------------------------------------------------------
    @staticmethod
    def _is_num(toks: list[Token], k: int) -> bool:
        return 0 <= k < len(toks) and isinstance(toks[k], int)

    def _book_at(self, toks: list[Token], i: int) -> tuple[str, int, bool] | None:
        """If a book name starts at toks[i]: (canonical, tokens used, fuzzy?)."""
        n = len(toks)
        if (
            self._label_words
            and isinstance(toks[i], str)
            and i + 2 < n
            and toks[i + 1] in (1, 2, 3)
            and toks[i + 2] in self._label_words
        ):
            # Postfix digit plus label ("Timotiejaus 2 laiške", "Jono 2
            # laiškas"): the digit numbers the letter, never the chapter.
            # Checked before the normal lookup, which would read "Jono 2"
            # as John 2. Books without numbered names ("Romiečiams 8
            # laiške") never match, and a bare "Jono laiškas 5" (no digit)
            # still stays silent.
            canon = self._index.get((cast(int, toks[i + 1]), (cast(str, toks[i]),)))
            if canon:
                return canon, 3, False
        starts = [(0, i)]
        p = self._prefixes.get(toks[i])
        if p:
            starts.insert(0, (p, i + 1))
        for prefix, j in starts:
            for length in range(min(self._max_name_tokens, n - j), 0, -1):
                name = tuple(toks[j : j + length])
                if all(isinstance(x, str) for x in name):
                    canon = self._index.get((prefix, cast(tuple[str, ...], name)))
                    if canon:
                        return canon, (j + length) - i, False
        if self.language == "lt" and _lt.FUZZY_BOOKS:
            for prefix, j in starts:
                if j >= n or not isinstance(toks[j], str):
                    continue
                if not (
                    self._is_num(toks, j + 1) or (j + 1 < n and toks[j + 1] in self._chapter_words)
                ):
                    continue
                stem = _lt.fuzzy_book_stem(cast(str, toks[j]))
                canon = self._index.get((prefix, (stem,))) if stem else None
                if canon:
                    return canon, (j + 1) - i, True
        if self.fuzzy:
            for prefix, j in starts:
                if j >= n:
                    continue
                word = toks[j]
                if not isinstance(word, str) or len(word) < 7:
                    continue
                if not (
                    self._is_num(toks, j + 1) or (j + 1 < n and toks[j + 1] in self._chapter_words)
                ):
                    continue
                hit = difflib.get_close_matches(word, self._fuzzy_pool[prefix], n=1, cutoff=0.85)
                if hit:
                    return self._index[(prefix, (hit[0],))], (j + 1) - i, True
        return None

    def _prefix_restarts_book(self, toks: list[Token], ref: Reference) -> bool:
        """True for a chapter-only ref whose chapter number is the prefix of
        a different, numbered book starting right after it (Lithuanian only).
        E.g. [jono, 1, jono, laiskas, 5, ...]: "John 1" is a ghost, the
        real reference is "1 John 5:...".
        Only a numbered name that spans prefix + name + label word (>= 3
        tokens) counts: a bare "1 jono" is too weak, as in "Jono 1, Jono 3".
        """
        if self.language != "lt" or ref.verse_start is not None or ref.end < 1:
            return False
        k = ref.end - 1
        if toks[k] != ref.chapter or self._prefixes.get(toks[k]) != ref.chapter:
            return False
        # A number spoken as a chapter ("1 skyrius") is not a book prefix.
        if k >= 1 and toks[k - 1] in self._chapter_words:
            return False
        match = self._book_at(toks, k)
        return match is not None and match[0] != ref.book and match[1] >= 3

    def _verse_and_range(self, toks: list[Token], k: int) -> tuple[int, int | None, int]:
        """toks[k] is a verse number; optionally followed by 'through 30' / 'and 29'."""
        # Callers only pass a number token here (every call site checks _is_num).
        v1 = cast(int, toks[k])
        k += 1
        v2: int | None = None
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        if (
            isinstance(nxt, int)
            and not self._book_at(toks, k + 1)
            and (
                (toks[k] in self._range_words and nxt > v1)
                or (toks[k] in self._and_words and nxt == v1 + 1)
                # Lithuanian non-consecutive "ir" pair ("antra ir ketvirta"
                # for 2-4): the gap bound is the only guard, mirroring the
                # consecutive rule above (which also merges without asking
                # what follows).
                or (
                    self.language == "lt"
                    and toks[k] in self._and_words
                    and nxt > v1
                    and nxt - v1 <= 5
                )
            )
        ):
            v2, k = nxt, k + 2
        elif (
            k < len(toks)
            and isinstance(toks[k], int)
            and toks[k] == v1 + 1
            and not self._book_at(toks, k)
        ):
            # Bare consecutive pair ("ketvirtą penktą" for 4-5): two verse
            # numbers with no joiner. The +1 constraint keeps "Luko 10 25
            # 28" (25, then a new 28) from merging.
            v2, k = cast(int, toks[k]), k + 1
        elif (
            k + 1 < len(toks)
            and toks[k] in self._verse_words
            and toks[k + 1] == v1 + 1
            and not self._book_at(toks, k + 1)
            and not (k + 2 < len(toks) and toks[k + 2] in self._chapter_words)
        ):
            # Verse word wedged in the pair ("6 eilutę 7" for 6-7, the
            # shape postprocess leaves). A chapter word right after
            # ("16 eilutė 17 skyrius") vetoes: the second number starts
            # something new.
            v2, k = cast(int, toks[k + 1]), k + 2
        elif (
            k + 1 < len(toks)
            and toks[k] == v1
            and toks[k + 1] == v1 + 1
            and not self._book_at(toks, k + 1)
            and not (k + 2 < len(toks) and toks[k + 2] in self._chapter_words)
        ):
            # Stuttered restart ("13 eilutę 13, 14" for 13-14): the verse
            # number said twice, then the range end. A lone repeat
            # ("5 eilutė 5") has no third number and stays single.
            v2, k = cast(int, toks[k + 1]), k + 2
        return v1, v2, k

    def _connector(
        self, toks: list[Token], k: int, allow_bare: bool = True
    ) -> tuple[str | None, int]:
        """After a chapter number: find ':' / 'verse' / a bare second number.
        Returns (kind, index of the verse number)."""
        n = len(toks)
        if k < n and toks[k] == ":":
            return "colon", k + 1
        kk, fillers = k, 0
        while kk < n and toks[kk] in self._fillers and fillers < 3:
            kk += 1
            fillers += 1
        if kk < n and toks[kk] in self._verse_words:
            # skip fillers after the verse word too ("eilute nuo 4",
            # "verses beginning 4 through 7")
            k2, fillers = kk + 1, 0
            while k2 < n and toks[k2] in self._fillers and fillers < 3:
                k2 += 1
                fillers += 1
            return "word", k2
        # "4 skyrius nuo 16 iki 20": the intro word itself says a verse follows
        if (
            kk > k
            and self._is_num(toks, kk)
            and not self._book_at(toks, kk)
            and any(t in self._verse_intro_words for t in toks[k:kk])
        ):
            return "intro", kk
        if allow_bare and self._is_num(toks, k) and not self._book_at(toks, k):
            return "bare", k
        return None, k

    def _chapter_prefixed_verse(
        self, canon: str, chapter: int, v1: int, end: int
    ) -> Reference | None:
        """Verse number with the chapter restated inside ("Efeziečiams du,
        256" for 2:5-6): STT glued chapter + verses into one number.

        Only when the token starts with the chapter digits and the rest is a
        single verse or, with a validator, a real verse or verse pair.
        Anything else stays silent (no bare-chapter fallback either: the
        verse token was garbage, and inventing chapter-only here would bless
        every stray big number).
        """
        if self.language != "lt":
            return None
        digits = str(chapter)
        text = str(v1)
        if not text.startswith(digits) or len(text) <= len(digits):
            return None
        rest = text[len(digits) :]
        if not rest or rest[0] == "0":
            return None
        if len(rest) == 1:
            verse = int(rest)
            if 1 <= verse <= MAX_VERSE:
                return Reference(canon, chapter, verse, None, "low", end=end)
            return None
        if len(rest) == 2 and self.validator is not None:
            number = int(rest)
            if self.validator(Reference(canon, chapter, number, None)):
                return Reference(canon, chapter, number, None, "low", end=end)
            glued = _split_glued_verse(number, canon, chapter, self.validator)
            if glued is not None:
                return Reference(canon, chapter, glued[0], glued[1], "low", end=end)
        return None

    def _distant_chapter(self, toks: list[Token], k: int, canon: str) -> Reference | None:
        """Chapter named words after a book label ("Skaičiau iš Apreiskimo
        knygos ... norit 12 skyrių" for Revelation 12).

        Only a chapter word with its number, at least two tokens past the
        label (the adjacent shape belongs to the bridge above, including its
        refusals: "Jono laiške 4 skyriuje" stays silent), stopping at any
        book name or stray number. Chapter-only and low: the distance is
        the whole point.
        """
        n = len(toks)
        m = k + 2
        while m < n and m - k <= 8:
            if self._book_at(toks, m) or self._is_num(toks, m):
                return None
            if toks[m] in self._chapter_words and self._is_num(toks, m + 1):
                ch = cast(int, toks[m + 1])
                if 1 <= ch <= self._chapters[canon]:
                    return Reference(canon, ch, None, None, "low", end=m + 2)
                return None
            m += 1
        return None

    def _chapter_before_book(
        self, toks: list[Token], i: int, length: int, canon: str
    ) -> Reference | None:
        """Chapter named just before its book ("32 skyrius Pakartoto
        įstatymo knygos 11 eilutė" for Deuteronomy 32:11; Lithuanian only).

        Only when a verse follows the book right away (verse word + number,
        or a bare number that starts nothing new): a bare "[CHAPTER] N BOOK"
        with nothing after stays silent, so "Jono 1 skyrius, Jono laiškas"
        can never grow a verse by accident.
        """
        if self.language != "lt" or i < 2:
            return None
        if toks[i - 2] not in self._chapter_words or not self._is_num(toks, i - 1):
            return None
        chapter = cast(int, toks[i - 1])
        if not 1 <= chapter <= self._chapters[canon]:
            return None
        n = len(toks)
        j = i + length
        if j < n and toks[j] in self._verse_words and self._is_num(toks, j + 1):
            v1, v2, end = self._verse_and_range(toks, j + 1)
        elif (
            self._is_num(toks, j)
            and not (j + 1 < n and toks[j + 1] in self._chapter_words)
            and not self._book_at(toks, j)
        ):
            v1, v2, end = self._verse_and_range(toks, j)
        else:
            return None
        if not 1 <= v1 <= MAX_VERSE:
            return None
        return Reference(canon, chapter, v1, v2, "medium", end=end)

    def _parse_tail(
        self, toks: list[Token], j: int, canon: str, book_start: int = 0
    ) -> Reference | None:
        """Parse what follows a book name, starting at toks[j]."""
        n = len(toks)
        nch = self._chapters[canon]
        k = j
        # A few ordinary words may intervene between the book name and its
        # chapter ("metraščių knygoje, apie jį, ketvirtame skyriuje"): skip
        # them, but never a number or another book name, so nothing that
        # belongs to a different reference is stolen. A book-label word
        # ("laiške") is bridged only when a chapter/verse word with its
        # number comes right after it ("Romiečiams laiške, 8 skyrius");
        # a bare "Jono laiškas 5" stays silent instead of inventing John 5.
        bridged_label = False
        label_break = False
        while k < n and k - j < 4 and isinstance(toks[k], str):
            if toks[k] in self._label_words:
                # A bare book name plus a label word may be a numbered book
                # with its digit dropped ("Jono laiške 4" could be 1/2/3
                # John), so bridge the label only when no numbered name
                # starts that way (Romans, Timothy, Jude, ...). The bridge
                # lands on a chapter/verse word with its number ("laiške, 8
                # skyrius") or, failing that, on a bare number ("laiškas,
                # pirma septyni").
                bridged = canon not in self._label_danger and (
                    (
                        k + 2 < n
                        and toks[k + 1] in self._chapter_words | self._verse_words
                        and self._is_num(toks, k + 2)
                    )
                    or (k + 1 < n and self._is_num(toks, k + 1))
                )
                if not bridged:
                    label_break = True
                    break
                bridged_label = True
            elif (
                toks[k] in self._chapter_words
                or toks[k] in self._verse_words
                or self._book_at(toks, k)
            ):
                break
            k += 1
        # Ordinary words between the book and the number ("Jonas ir du jo
        # mokiniai", "Jona buvo žuvies pilve tris dienas"): book names are
        # people too, so a bare number reached that way with more words after
        # it is only a chapter when a chapter word says so; a verse after it
        # still counts.
        skipped_words = k > j and not bridged_label
        if (
            self.language == "lt"
            and canon == "Numbers"
            and self._is_num(toks, k)
            and not any(
                isinstance(t, str)
                and (t in self._chapter_words or t in self._verse_words or t in self._label_words)
                # From the book name itself (a "knyga" inside a two-token
                # match counts) through five tokens past the landing number.
                for t in toks[book_start : k + 6]
            )
        ):
            # "uždėjau skaičius 91" / "tie skaičiai ... padaryti 19": the
            # ordinary noun "skaičius" (a number) folds onto the Numbers
            # stem, and a bare count reached with no chapter/verse/label
            # word anywhere near is not a citation ("Skaičiai 14 skyrius"
            # and "Skaičių knyga 33 ..." still parse: the words follow).
            return None
        chapter_kw = False
        if k < n and toks[k] in self._chapter_words:
            chapter_kw = True
            k += 1
        elif (
            bridged_label
            and k + 1 < n
            and toks[k] in self._verse_words
            and self._is_num(toks, k + 1)
        ):
            # A label bridged onto a verse word ("laiškio 289 eilutės"):
            # the chapter number is glued into the verse number; split it
            # like a merge, then split glued verses the same way. Only
            # when the number cannot be a verse at all ("keturiolikta"
            # after "Apreiskimo knygos ... 12 skyrius" is verse 14 of the
            # uttered chapter, not 1:4): smaller numbers fall through to
            # the context verse rule below.
            merged = (
                _split_merged(cast(int, toks[k + 1]), nch)
                if cast(int, toks[k + 1]) > MAX_VERSE
                else None
            )
            if merged is not None:
                ch, v = merged
                v2: int | None = None
                if self.validator is not None:
                    glued = _split_glued_verse(v, canon, ch, self.validator)
                    if glued is not None:
                        v, v2 = glued
                return Reference(canon, ch, v, v2, "low", end=k + 2)

        # single-chapter books: "Jude verse 3", "Jude 3"
        if nch == 1 and not chapter_kw:
            if k < n and toks[k] in self._verse_words and self._is_num(toks, k + 1):
                v1, v2, end = self._verse_and_range(toks, k + 1)
                return Reference(canon, 1, v1, v2, "high", end=end)
            if self._is_num(toks, k):
                following_is_more = k + 1 < n and (
                    toks[k + 1] == ":"
                    or (self._is_num(toks, k + 1) and not self._book_at(toks, k + 1))
                )
                if not following_is_more:
                    v1, v2, end = self._verse_and_range(toks, k)
                    return Reference(canon, 1, v1, v2, "medium", end=end)

        if not self._is_num(toks, k):
            if label_break:
                return self._distant_chapter(toks, k, canon)
            return None
        chapter = cast(int, toks[k])
        k += 1
        kind, kk = self._connector(toks, k)
        # "Mark 12-30": a dash-joined pair whose second number cannot be a
        # chapter of this book is chapter-verse. A pair that runs backwards
        # ("Kolosiečiams 3-2") can never be chapters 3 through 2, so it is
        # chapter-verse too. When it could be ("Romans 8-12" may span
        # chapters) nothing is invented.
        if (
            kind is None
            and self.language == "lt"
            and k + 1 < n
            and toks[k] == "-"
            and self._is_num(toks, k + 1)
            and not self._book_at(toks, k + 1)
            and (cast(int, toks[k + 1]) > nch or cast(int, toks[k + 1]) <= chapter)
        ):
            kind, kk = "dash", k + 1

        # "Psalm one nineteen" means Psalm 119 (Psalm 1 only has 6 verses)
        if canon == "Psalms" and kind == "bare" and chapter == 1:
            second = toks[kk] if kk < n else None
            if isinstance(second, int) and 7 <= second <= 50:
                chapter = 100 + second
                k = kk + 1
                kind, kk = self._connector(toks, k, allow_bare=False)

        if not 1 <= chapter <= nch:
            # "Filipiečiams 46" / "Efeziečiams 425": STT merged chapter and verse
            # into one number that cannot be a chapter of this book. Split it
            # only when exactly one split is a valid chapter:verse. A verse
            # word with no verse number after it ("289 eilutės") counts as
            # bare: the number is still merged.
            if (
                not chapter_kw
                and not skipped_words
                and (kind is None or (kind == "word" and not self._is_num(toks, kk)))
            ):
                split = _split_merged(chapter, nch)
                if split is not None:
                    if self.validator is not None:
                        # The merged verse may itself be two glued verses
                        # ("289 eilutės" for 2:8-9): split again, keeping the
                        # plain reading whenever it names a real verse
                        # ("425" stays 4:25).
                        glued = _split_glued_verse(split[1], canon, split[0], self.validator)
                        if glued is not None:
                            return Reference(canon, split[0], glued[0], glued[1], "low", end=k)
                    return Reference(canon, split[0], split[1], None, "low", end=k)
            if nch == 1 and chapter_kw:
                # Speaker numbered verses as chapters of a single-chapter book
                # ("Judo laiško 20 skyrius, 21 eilutė" for Jude 1:20-21): the
                # "chapter" is verse_start, and a following verse number is
                # the range end.
                range_end: int | None = None
                tail_end = k
                if kind and self._is_num(toks, kk):
                    tail_v1, tail_v2, tail_end = self._verse_and_range(toks, kk)
                    range_end = tail_v2 if tail_v2 is not None else tail_v1
                if 1 <= chapter <= MAX_VERSE and (range_end is None or 1 <= range_end <= MAX_VERSE):
                    return Reference(canon, 1, chapter, range_end, "medium", end=tail_end)
            return None

        if kind and self._is_num(toks, kk):
            v1, v2, end = self._verse_and_range(toks, kk)
            if (
                v2 is None
                and kind == "bare"
                and self.language == "lt"
                and end < n
                and toks[end] == "nuo"
                and self._is_num(toks, end + 1)
                and not (end + 2 < n and toks[end + 2] in self._range_words)
            ):
                # Speaker restart ("Luko 11, 20 nuo 24" for Luke 11:24: the
                # speaker abandoned a word mid-stream and restarted with
                # "nuo 24", leaving a bare stutter number behind). A bare
                # verse followed by "nuo"+number restates the verse, and the
                # first number is the stutter. A genuine "nuo A iki B" range
                # never has a bare number before "nuo" (it parses as "intro"),
                # so this branch cannot steal one.
                v1 = cast(int, toks[end + 1])
                end += 2
            if not 1 <= v1 <= MAX_VERSE:
                return self._chapter_prefixed_verse(canon, chapter, v1, end)
            if v2 is None and v1 >= 10 and self.validator is not None:
                # STT glued two verse numbers ("56 eilutė" for verses 5 and
                # 6): split only when the glued verse cannot exist and the
                # split names real verses, mirroring _split_merged.
                glued = _split_glued_verse(v1, canon, chapter, self.validator)
                if glued is not None:
                    v1, v2 = glued
            conf = "medium" if kind in ("bare", "dash", "intro") else "high"
            return Reference(canon, chapter, v1, v2, conf, end=end)
        if skipped_words and not chapter_kw and k < n:
            # Narrative goes on after the count. A number ending the segment
            # stays: its chapter word may open the next one ("čia mato
            # Evangelija vėl apie tuos fariziejus, šeštą" + "Skyrius nuo
            # pirmos eilutės").
            return None
        return Reference(canon, chapter, None, None, "medium", end=k)

    def _context_ref(self, toks: list[Token], i: int, ctx: Context) -> Reference | None:
        """References with no book name, resolved from the last book mentioned."""
        if not ctx.book:
            return None
        t = toks[i]
        n = len(toks)
        if (
            self.language == "lt"
            and isinstance(t, str)
            and t in self._verse_intro_words
            and self._is_num(toks, i + 1)
            and i + 4 < n
            and toks[i + 2] in self._range_words
        ):
            # Range end stranded in the next breath ("Iki 16 eilutės"
            # after "... nuo aštuntos"): the carry holds the start. Two
            # shapes: raw [nuo, N1, iki, N2, VERSE], or postprocessed
            # [nuo, N1, iki, VERSE, N2] (rule 608 moves the verse word
            # forward). The verse word is required, so hours ("nuo 8 iki
            # 16 valandos") never merge; N2 > N1, same chapter throughout.
            v1 = cast(int, toks[i + 1])
            v2 = None
            if self._is_num(toks, i + 3) and toks[i + 4] in self._verse_words:
                v2 = cast(int, toks[i + 3])
            elif toks[i + 3] in self._verse_words and self._is_num(toks, i + 4):
                v2 = cast(int, toks[i + 4])
            if ctx.chapter and v2 is not None and 1 <= v1 < v2 <= MAX_VERSE:
                return Reference(ctx.book, ctx.chapter, v1, v2, "low", inferred=True, end=i + 5)
        if (
            ctx.chapter
            and isinstance(t, int)
            and i + 2 < n
            and toks[i + 1] in self._verse_words
            and toks[i + 2] == t + 1
            and not (i + 3 < n and toks[i + 3] in self._chapter_words)
        ):
            # Consecutive pair split by its verse word ("6 eilutę 7" for
            # 22:6-7 with Genesis 22 running; postprocess leaves
            # [num, VERSE, num]). Non-consecutive pairs stay split, a
            # chapter word right after vetoes, and book-led text
            # ("Mato 5 6 eilutė" is chapter+verse) never reaches here.
            return Reference(ctx.book, ctx.chapter, t, t + 1, "low", inferred=True, end=i + 3)
        kk = i + 1
        skipped = 0
        while kk < n and toks[kk] in self._fillers and skipped < 3:
            # Emphasis between the verse word and its number ("eilutė pati
            # 31"): skipped the same way _connector skips fillers.
            kk += 1
            skipped += 1
        if t in self._verse_words and self._is_num(toks, kk):
            # A number that starts a book name ("1 Petro" after "vieną
            # eilutę") is the book's prefix, not this verse: leave it for
            # the book-led parse instead of eating it into context.
            if self._book_at(toks, kk) is not None:
                return None
            # A chapter named in the same utterance ("6 skyrius ... 24
            # eilutė") beats a stale running chapter; without one the
            # running chapter applies. Either way the book comes from
            # context, so a bookless "5 skyrius 3 eilutė" out of nowhere
            # stays silent.
            chapter = ctx.chapter
            for j in range(len(toks)):
                if (
                    toks[j] in self._chapter_words
                    and self._is_num(toks, j + 1)
                    and 1 <= cast(int, toks[j + 1]) <= self._chapters[ctx.book]
                ):
                    chapter = cast(int, toks[j + 1])
                    break
            if chapter is None:
                return None
            v1, v2, end = self._verse_and_range(toks, kk)
            if 1 <= v1 <= MAX_VERSE:
                if v2 is None and v1 >= 10 and self.validator is not None:
                    # Same glued-verse split as _parse_tail, for a verse that
                    # arrives words after its chapter ("126 psalmės ...
                    # 56 eilutė").
                    glued = _split_glued_verse(v1, ctx.book, chapter, self.validator)
                    if glued is not None:
                        v1, v2 = glued
                return Reference(ctx.book, chapter, v1, v2, "low", inferred=True, end=end)
        if t in self._chapter_words and self._is_num(toks, i + 1):
            chapter = cast(int, toks[i + 1])
            kind, kk = self._connector(toks, i + 2, allow_bare=False)
            if kind and self._is_num(toks, kk) and 1 <= chapter <= self._chapters[ctx.book]:
                v1, v2, end = self._verse_and_range(toks, kk)
                if 1 <= v1 <= MAX_VERSE:
                    return Reference(ctx.book, chapter, v1, v2, "medium", inferred=True, end=end)
            # Fallback: "skyrių aštuntai, lutė" — the number is a verse of
            # the running chapter, not a new chapter. Only when a verse
            # word follows (a bare "[CHAPTER] N" stays chapter-only), and
            # the connector above found no verse, so a genuine
            # "7 skyrius, 8 eilutė" (verse found) never reaches here.
            if (
                ctx.chapter
                and i + 2 < n
                and toks[i + 2] in self._verse_words
                and 1 <= chapter <= MAX_VERSE
            ):
                return Reference(
                    ctx.book, ctx.chapter, chapter, None, "low", inferred=True, end=i + 3
                )
        return None

    def _scan(self, toks: list[Token], ctx: Context) -> list[Reference]:
        refs: list[Reference] = []
        seen: dict[tuple[str, int, int | None, int | None], int] = {}

        def accept(ref: Reference, start: int) -> None:
            ref.start = start
            ref.text = " ".join(str(t) for t in toks[start : ref.end])
            if self.validator and not self.validator(ref):
                return
            # One citation restated in the same breath ("... pirma
            # septyni, pirmas skyrius septintai lūtėje") is one reference.
            # Repeats farther apart are separate citations even with the
            # same key (carry-joined segments), so only adjacent spans
            # collapse.
            key = (ref.book, ref.chapter, ref.verse_start, ref.verse_end)
            if key in seen and start <= seen[key] + 1:
                return
            if ref.inferred and key in seen:
                # Same-feed inferred restatement of a key already claimed
                # book-led in this scan (a cross-boundary completion plus
                # its context twin): one citation, not two. Cross-feed
                # repeats use separate scans and still score separately,
                # and one-shot parses never produce inferred refs.
                return
            seen[key] = ref.end
            refs.append(ref)
            ctx.book, ctx.chapter = ref.book, ref.chapter
            ctx.chapters[ref.book] = ref.chapter

        i = 0
        while i < len(toks):
            m = self._book_at(toks, i)
            if m:
                canon, length, fuzzy = m
                ref = self._parse_tail(toks, i + length, canon, i)
                if ref is None:
                    ref = self._chapter_before_book(toks, i, length, canon)
                if ref and self._prefix_restarts_book(toks, ref):
                    # "Jono, pirmo Jono laiško 5 ...": the bare "Jono" + "1"
                    # is the prefix of the numbered book that follows.
                    i += length
                    continue
                if ref and fuzzy and self.language == "lt" and ref.verse_start is None:
                    # A guessed book name with only a chapter is mostly an
                    # ordinary word before a number; keep fuzzy hits that
                    # carry a verse.
                    ref = None
                if ref:
                    if fuzzy:
                        ref.confidence = _downgrade(ref.confidence)
                    accept(ref, i)
                    i = ref.end
                else:
                    # Only a name of two or more words ("Antra karalių"):
                    # one-word names are also people and verbs ("Dievas
                    # mato", "kaip Danielius").
                    if not fuzzy and length >= 2 and canon != ctx.book and canon in ctx.chapters:
                        ctx.book, ctx.chapter = canon, ctx.chapters[canon]
                    i += length
                continue
            ref = self._context_ref(toks, i, ctx)
            if ref:
                accept(ref, i)
                i = ref.end
                continue
            i += 1
        return _drop_covered_chapters(refs, refs)


# --------------------------------------------------------------------------
# Streaming (feed transcript segments as they arrive)
# --------------------------------------------------------------------------
class StreamingParser:
    """Keeps context between segments and a short tail of the previous one,
    so "turn to Romans" + "chapter eight verse twenty-eight" still works.

    With ``hold_chapter_only`` (the live pipeline and the benchmark turn it
    on), a chapter-only reference ("Romiečiams, 5 skyrius, mes ...") is held until
    the next segment: preachers often give the verse a few seconds later, and
    once "Romans 5" is sent it cannot be taken back. If the next segment names
    a verse in that chapter, only the full reference is reported; otherwise the
    held one is reported then (one segment late). ``flush()`` releases it at
    the end of a stream.

    With a ``verse_text`` index as well, a held chapter that gets no spoken
    verse is matched against the words of both segments: if the speaker is
    reading one of its verses aloud (score >= ``verse_text_min_score``), that
    verse is reported instead of the bare chapter, marked ``from_text`` and
    never above medium confidence.
    """

    def __init__(
        self,
        parser: ReferenceParser | None = None,
        carry_tokens: int = 12,
        language: str = "en",
        context_ttl_s: float | None = 600.0,
        hold_chapter_only: bool = False,
        verse_text: VerseTextIndex | None = None,
        verse_text_min_score: float = 0.6,
    ):
        self.parser = parser or ReferenceParser(language=language)
        self.ctx = Context()
        self.carry: list[Token] = []
        self.carry_tokens = carry_tokens
        self.hold_chapter_only = hold_chapter_only
        self._held: list[Reference] = []
        self._held_text = ""  # the segment the held refs were said in
        self.verse_text = verse_text
        self.verse_text_min_score = verse_text_min_score
        # Max age of the running book/chapter context. A stale context
        # (e.g. Romans still set 300 s later) turns stray numbers into
        # confident wrong references, so feeds timestamped later than this
        # after the last reported reference start over. None disables.
        self.context_ttl_s = context_ttl_s
        self._ctx_t: float | None = None

    def feed(self, segment: str, t_s: float | None = None) -> list[Reference]:
        # With a timestamp, expire a context older than the TTL before
        # scanning: the book/chapter on record belongs to a long-gone
        # passage, not to these tokens. Untimestamped feeds keep the
        # legacy never-expire behavior.
        released: list[Reference] = []
        if (
            t_s is not None
            and self.context_ttl_s is not None
            and self._ctx_t is not None
            and t_s - self._ctx_t > self.context_ttl_s
        ):
            released = self.flush()  # a held chapter belongs to the old passage
            self.reset()
        # Postprocess runs inside _prepare on the new segment only; carry was
        # already postprocessed when it arrived, so re-running it could
        # reshuffle already-ordered tokens.
        new = self.parser._prepare(segment)
        if (
            self.parser.language == "lt"
            and new
            and new[0] in ("viena", "vienas")
            and _lt._citation_slot(self.carry)
            and any(
                isinstance(t, int)
                or t in self.parser._verse_intro_words
                or t in self.parser._chapter_words
                or t in self.parser._verse_words
                for t in new[1:4]
            )
        ):
            # "...Jobo" | "Vienas nuo dvidešimtos": the number 1 split from its
            # book by an utterance break, which tokenize alone cannot see. A
            # citation word or number must follow ("Viena moteris" stays text).
            new = [1, *new[1:]]
        carry = self.carry
        if self.parser.language == "lt" and new and isinstance(new[0], int):
            carry = _lt.drop_dangling_tail(carry)
        toks = carry + new
        refs = self.parser._scan(toks, self.ctx)
        fresh = [r for r in refs if r.end > len(carry)]  # skip already-reported ones
        self.carry = toks[-self.carry_tokens :]
        if fresh and t_s is not None:
            # A newly reported reference means the context now holds this
            # book/chapter; its age counts from here. Refs re-found inside
            # the carry-over were already reported and must not refresh it.
            self._ctx_t = t_s
        if not self.hold_chapter_only:
            return released + fresh
        held_text = self._held_text
        # A held cut-off verse gives way to a verse of its chapter that follows.
        renamed = {(r.book, r.chapter) for r in fresh if r.verse_start is not None}
        for ref in _drop_covered_chapters(self.flush(), fresh):
            if ref.verse_start is not None and (ref.book, ref.chapter) in renamed:
                continue
            if segment.strip():  # an empty feed only releases, like flush()
                ref = self._verse_from_text(ref, f"{held_text} {segment}")
            released.append(ref)
        hold = [r for r in fresh if r.verse_start is None or self._cut_off_verse(r, toks)]
        self._held = hold
        self._held_text = segment
        return released + [r for r in fresh if not any(r is h for h in hold)]

    def _cut_off_verse(self, ref: Reference, toks: list[Token]) -> bool:
        """A bare digit verse ending the segment right after "skyrius N".

        An utterance cut inside a spoken number leaves its first syllables as a
        digit ("...vienuoliktame skyriuje trys" | "Trisdešim šeštoje eilutė" for
        Romans 11:36): reported at once it is a wrong verse. Held one segment
        like a chapter-only reference, it gives way to the verse that follows.
        The short form "Kolosiečiams trys du." has no chapter word and is a
        whole citation: it is never held.
        """
        return (
            self.parser.language == "lt"
            and ref.verse_start is not None
            and ref.verse_end is None
            and 2 <= ref.verse_start <= 9
            and ref.end == len(toks) >= 3
            and isinstance(toks[-1], int)
            and isinstance(toks[-2], int)
            and toks[-3] in self.parser._chapter_words
        )

    def _verse_from_text(self, ref: Reference, text: str) -> Reference:
        """``ref`` with the verse being read aloud filled in, if one matches."""
        if self.verse_text is None or ref.verse_start is not None:
            return ref
        match = self.verse_text.best_verse(ref.book, ref.chapter, text)
        if match is None or match.score < self.verse_text_min_score:
            return ref
        # The text's verse numbering can differ from the display Bible's
        # (LTB vs KJV in a few chapters): keep only verses that exist there.
        validator = self.parser.validator
        if validator is not None and not validator(Reference(ref.book, ref.chapter, match.verse)):
            return ref
        verse_end = match.verse_end
        if (
            verse_end is not None
            and validator is not None
            and not validator(Reference(ref.book, ref.chapter, verse_end))
        ):
            verse_end = None
        return replace(
            ref,
            verse_start=match.verse,
            verse_end=verse_end,
            confidence="medium" if ref.confidence == "high" else ref.confidence,
            from_text=True,
        )

    def flush(self) -> list[Reference]:
        """Release any held chapter-only reference (end of stream)."""
        held, self._held = self._held, []
        self._held_text = ""
        return held

    def reset(self) -> None:
        self.ctx = Context()
        self.carry = []
        self._ctx_t = None
        self._held = []
        self._held_text = ""


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    language = "en"
    if args and args[0] in ("-l", "--lang"):
        language = args[1]
        args = args[2:]

    if args:
        p = ReferenceParser(language=language)
        for ref in p.parse(" ".join(args)):
            print(f'{ref}  [{ref.confidence}]  <- "{ref.text}"')
    else:
        print(
            f"Type transcript lines (Ctrl-D to quit). Context carries between lines. [{language}]"
        )
        sp = StreamingParser(language=language)
        for line in sys.stdin:
            for ref in sp.feed(line):
                tag = " (from context)" if ref.inferred else ""
                print(f'  -> {ref}  [{ref.confidence}]{tag}  <- "{ref.text}"')
