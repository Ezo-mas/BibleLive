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
from dataclasses import dataclass, field
from typing import cast

if __package__:
    from . import verse_parser_lt as _lt
    from .books import CANONICAL_BOOKS
else:
    import verse_parser_lt as _lt  # type: ignore[no-redef]
    from books import CANONICAL_BOOKS  # type: ignore[no-redef]

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
            fuzzy = False
        else:
            raise ValueError(f"Unsupported language {language!r} (available: 'en', 'lt')")
        self.language = language
        self.fuzzy = fuzzy
        self.validator = validator

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
            )
        ):
            v2, k = nxt, k + 2
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

    def _parse_tail(self, toks: list[Token], j: int, canon: str) -> Reference | None:
        """Parse what follows a book name, starting at toks[j]."""
        n = len(toks)
        nch = self._chapters[canon]
        k = j
        chapter_kw = False
        if k < n and toks[k] in self._chapter_words:
            chapter_kw = True
            k += 1

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
            return None
        chapter = cast(int, toks[k])
        k += 1
        kind, kk = self._connector(toks, k)
        # "Mark 12-30": a dash-joined pair whose second number cannot be a
        # chapter of this book is chapter-verse. When it could be ("Romans
        # 8-12" may span chapters) nothing is invented.
        if (
            kind is None
            and self.language == "lt"
            and k + 1 < n
            and toks[k] == "-"
            and self._is_num(toks, k + 1)
            and not self._book_at(toks, k + 1)
            and cast(int, toks[k + 1]) > nch
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
            # only when exactly one split is a valid chapter:verse.
            if kind is None and not chapter_kw:
                split = _split_merged(chapter, nch)
                if split is not None:
                    return Reference(canon, split[0], split[1], None, "low", end=k)
            return None

        if kind and self._is_num(toks, kk):
            v1, v2, end = self._verse_and_range(toks, kk)
            if not 1 <= v1 <= MAX_VERSE:
                return None
            conf = "medium" if kind in ("bare", "dash", "intro") else "high"
            return Reference(canon, chapter, v1, v2, conf, end=end)
        return Reference(canon, chapter, None, None, "medium", end=k)

    def _context_ref(self, toks: list[Token], i: int, ctx: Context) -> Reference | None:
        """References with no book name, resolved from the last book mentioned."""
        if not ctx.book:
            return None
        t = toks[i]
        if t in self._verse_words and self._is_num(toks, i + 1) and ctx.chapter:
            v1, v2, end = self._verse_and_range(toks, i + 1)
            if 1 <= v1 <= MAX_VERSE:
                return Reference(ctx.book, ctx.chapter, v1, v2, "low", inferred=True, end=end)
        if t in self._chapter_words and self._is_num(toks, i + 1):
            chapter = cast(int, toks[i + 1])
            kind, kk = self._connector(toks, i + 2, allow_bare=False)
            if kind and self._is_num(toks, kk) and 1 <= chapter <= self._chapters[ctx.book]:
                v1, v2, end = self._verse_and_range(toks, kk)
                if 1 <= v1 <= MAX_VERSE:
                    return Reference(ctx.book, chapter, v1, v2, "medium", inferred=True, end=end)
        return None

    def _scan(self, toks: list[Token], ctx: Context) -> list[Reference]:
        refs: list[Reference] = []

        def accept(ref: Reference, start: int) -> None:
            ref.start = start
            ref.text = " ".join(str(t) for t in toks[start : ref.end])
            if self.validator and not self.validator(ref):
                return
            refs.append(ref)
            ctx.book, ctx.chapter = ref.book, ref.chapter

        i = 0
        while i < len(toks):
            m = self._book_at(toks, i)
            if m:
                canon, length, fuzzy = m
                ref = self._parse_tail(toks, i + length, canon)
                if ref and self._prefix_restarts_book(toks, ref):
                    # "Jono, pirmo Jono laiško 5 ...": the bare "Jono" + "1"
                    # is the prefix of the numbered book that follows.
                    i += length
                    continue
                if ref:
                    if fuzzy:
                        ref.confidence = _downgrade(ref.confidence)
                    accept(ref, i)
                    i = ref.end
                else:
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
    """

    def __init__(
        self,
        parser: ReferenceParser | None = None,
        carry_tokens: int = 12,
        language: str = "en",
        context_ttl_s: float | None = 60.0,
        hold_chapter_only: bool = False,
    ):
        self.parser = parser or ReferenceParser(language=language)
        self.ctx = Context()
        self.carry: list[Token] = []
        self.carry_tokens = carry_tokens
        self.hold_chapter_only = hold_chapter_only
        self._held: list[Reference] = []
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
        toks = self.carry + self.parser._prepare(segment)
        refs = self.parser._scan(toks, self.ctx)
        fresh = [r for r in refs if r.end > len(self.carry)]  # skip already-reported ones
        self.carry = toks[-self.carry_tokens :]
        if fresh and t_s is not None:
            # A newly reported reference means the context now holds this
            # book/chapter; its age counts from here. Refs re-found inside
            # the carry-over were already reported and must not refresh it.
            self._ctx_t = t_s
        if not self.hold_chapter_only:
            return released + fresh
        released += _drop_covered_chapters(self.flush(), fresh)
        self._held = [r for r in fresh if r.verse_start is None]
        return released + [r for r in fresh if r.verse_start is not None]

    def flush(self) -> list[Reference]:
        """Release any held chapter-only reference (end of stream)."""
        held, self._held = self._held, []
        return held

    def reset(self) -> None:
        self.ctx = Context()
        self.carry = []
        self._ctx_t = None
        self._held = []


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
