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
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Callable, Union

Token = Union[int, str]

# --------------------------------------------------------------------------
# Book data: (canonical name, chapter count, name variants as spoken/written)
# Numbered books list the name WITHOUT the number ("corinthians"); the number
# comes from the canonical name.
# Abbreviations (Rom, Matt, ...) are deliberately left out: speech-to-text
# writes out full names, and short abbreviations cause false positives
# ("mic 1 2" -> Micah 1:2). Add them here if you parse typed text too.
# --------------------------------------------------------------------------
BOOKS: list[tuple[str, int, list[str]]] = [
    ("Genesis", 50, ["genesis"]),
    ("Exodus", 40, ["exodus"]),
    ("Leviticus", 27, ["leviticus"]),
    ("Numbers", 36, ["numbers"]),
    ("Deuteronomy", 34, ["deuteronomy"]),
    ("Joshua", 24, ["joshua"]),
    ("Judges", 21, ["judges"]),
    ("Ruth", 4, ["ruth"]),
    ("1 Samuel", 31, ["samuel"]),
    ("2 Samuel", 24, ["samuel"]),
    ("1 Kings", 22, ["kings"]),
    ("2 Kings", 25, ["kings"]),
    ("1 Chronicles", 29, ["chronicles", "chronicle"]),
    ("2 Chronicles", 36, ["chronicles", "chronicle"]),
    ("Ezra", 10, ["ezra"]),
    ("Nehemiah", 13, ["nehemiah"]),
    ("Esther", 10, ["esther"]),
    ("Job", 42, ["job"]),
    ("Psalms", 150, ["psalms", "psalm"]),
    ("Proverbs", 31, ["proverbs", "proverb"]),
    ("Ecclesiastes", 12, ["ecclesiastes"]),
    ("Song of Solomon", 8, ["song of solomon", "song of songs", "songs of solomon", "canticles"]),
    ("Isaiah", 66, ["isaiah"]),
    ("Jeremiah", 52, ["jeremiah"]),
    ("Lamentations", 5, ["lamentations", "lamentation"]),
    ("Ezekiel", 48, ["ezekiel"]),
    ("Daniel", 12, ["daniel"]),
    ("Hosea", 14, ["hosea"]),
    ("Joel", 3, ["joel"]),
    ("Amos", 9, ["amos"]),
    ("Obadiah", 1, ["obadiah"]),
    ("Jonah", 4, ["jonah"]),
    ("Micah", 7, ["micah"]),
    ("Nahum", 3, ["nahum"]),
    ("Habakkuk", 3, ["habakkuk"]),
    ("Zephaniah", 3, ["zephaniah"]),
    ("Haggai", 2, ["haggai"]),
    ("Zechariah", 14, ["zechariah"]),
    ("Malachi", 4, ["malachi"]),
    ("Matthew", 28, ["matthew"]),
    ("Mark", 16, ["mark"]),
    ("Luke", 24, ["luke"]),
    ("John", 21, ["john"]),
    ("Acts", 28, ["acts", "acts of the apostles"]),
    ("Romans", 16, ["romans"]),
    ("1 Corinthians", 16, ["corinthians", "corinthian"]),
    ("2 Corinthians", 13, ["corinthians", "corinthian"]),
    ("Galatians", 6, ["galatians"]),
    ("Ephesians", 6, ["ephesians"]),
    ("Philippians", 4, ["philippians"]),
    ("Colossians", 4, ["colossians"]),
    ("1 Thessalonians", 5, ["thessalonians"]),
    ("2 Thessalonians", 3, ["thessalonians"]),
    ("1 Timothy", 6, ["timothy"]),
    ("2 Timothy", 4, ["timothy"]),
    ("Titus", 3, ["titus"]),
    ("Philemon", 1, ["philemon"]),
    ("Hebrews", 13, ["hebrews", "hebrew"]),
    ("James", 5, ["james"]),
    ("1 Peter", 5, ["peter"]),
    ("2 Peter", 3, ["peter"]),
    ("1 John", 5, ["john"]),
    ("2 John", 1, ["john"]),
    ("3 John", 1, ["john"]),
    ("Jude", 1, ["jude"]),
    ("Revelation", 22, ["revelation", "revelations", "revelation of john"]),
]

CHAPTERS: dict[str, int] = {name: n for name, n, _ in BOOKS}

# (prefix number or 0, name tokens) -> canonical name
_INDEX: dict[tuple[int, tuple[str, ...]], str] = {}
for _name, _n, _variants in BOOKS:
    _prefix = int(_name[0]) if _name[0].isdigit() else 0
    for _v in _variants:
        _INDEX[(_prefix, tuple(_v.split()))] = _name
_MAX_NAME_TOKENS = max(len(k[1]) for k in _INDEX)

# single-word names per prefix, used for fuzzy matching of mis-transcribed names
_FUZZY_POOL: dict[int, list[str]] = {0: [], 1: [], 2: [], 3: []}
for (_p, _nt), _c in _INDEX.items():
    if len(_nt) == 1:
        _FUZZY_POOL[_p].append(_nt[0])

PREFIXES: dict[Token, int] = {1: 1, 2: 2, 3: 3, "first": 1, "second": 2, "third": 3}
CHAPTER_WORDS = {"chapter", "chapters"}
VERSE_WORDS = {"verse", "verses"}
RANGE_WORDS = {"through", "thru", "to", "until", "-"}
FILLERS = {"and", "at", "in", "from", "beginning", "starting"}
MAX_VERSE = 176  # Psalm 119 is the longest chapter

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
            out.append(int(re.match(r"\d+", t).group()))
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
    def __init__(
        self,
        fuzzy: bool = True,
        validator: Callable[[Reference], bool] | None = None,
    ):
        """
        fuzzy:     also match slightly mis-transcribed book names ("Habakuk").
        validator: optional hook, e.g. check the verse exists in your Bible DB.
                   Return False to drop a reference.
        """
        self.fuzzy = fuzzy
        self.validator = validator

    # ---- public API -------------------------------------------------------
    def parse(self, text: str, context: Context | None = None) -> list[Reference]:
        return self._scan(tokenize(text), context or Context())

    # ---- helpers ----------------------------------------------------------
    @staticmethod
    def _is_num(toks: list[Token], k: int) -> bool:
        return 0 <= k < len(toks) and isinstance(toks[k], int)

    def _book_at(self, toks: list[Token], i: int) -> tuple[str, int, bool] | None:
        """If a book name starts at toks[i]: (canonical, tokens used, fuzzy?)."""
        n = len(toks)
        starts = [(0, i)]
        p = PREFIXES.get(toks[i])
        if p:
            starts.insert(0, (p, i + 1))
        for prefix, j in starts:
            for length in range(min(_MAX_NAME_TOKENS, n - j), 0, -1):
                name = tuple(toks[j : j + length])
                if all(isinstance(x, str) for x in name):
                    canon = _INDEX.get((prefix, name))
                    if canon:
                        return canon, (j + length) - i, False
        if self.fuzzy:
            for prefix, j in starts:
                if j >= n or not isinstance(toks[j], str) or len(toks[j]) < 7:
                    continue
                if not (self._is_num(toks, j + 1) or (j + 1 < n and toks[j + 1] in CHAPTER_WORDS)):
                    continue
                hit = difflib.get_close_matches(toks[j], _FUZZY_POOL[prefix], n=1, cutoff=0.85)
                if hit:
                    return _INDEX[(prefix, (hit[0],))], (j + 1) - i, True
        return None

    def _verse_and_range(self, toks: list[Token], k: int) -> tuple[int, int | None, int]:
        """toks[k] is a verse number; optionally followed by 'through 30' / 'and 29'."""
        v1 = toks[k]
        k += 1
        v2 = None
        if (
            self._is_num(toks, k + 1)
            and not self._book_at(toks, k + 1)
            and (
                toks[k] in RANGE_WORDS
                and toks[k + 1] > v1
                or toks[k] == "and"
                and toks[k + 1] == v1 + 1
            )
        ):
            v2, k = toks[k + 1], k + 2
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
        while kk < n and toks[kk] in FILLERS and fillers < 3:
            kk += 1
            fillers += 1
        if kk < n and toks[kk] in VERSE_WORDS:
            return "word", kk + 1
        if allow_bare and self._is_num(toks, k) and not self._book_at(toks, k):
            return "bare", k
        return None, k

    def _parse_tail(self, toks: list[Token], j: int, canon: str) -> Reference | None:
        """Parse what follows a book name, starting at toks[j]."""
        n = len(toks)
        nch = CHAPTERS[canon]
        k = j
        chapter_kw = False
        if k < n and toks[k] in CHAPTER_WORDS:
            chapter_kw = True
            k += 1

        # single-chapter books: "Jude verse 3", "Jude 3"
        if nch == 1 and not chapter_kw:
            if k < n and toks[k] in VERSE_WORDS and self._is_num(toks, k + 1):
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
        chapter = toks[k]
        k += 1
        kind, kk = self._connector(toks, k)

        # "Psalm one nineteen" means Psalm 119 (Psalm 1 only has 6 verses)
        if canon == "Psalms" and kind == "bare" and chapter == 1 and 7 <= toks[kk] <= 50:
            chapter = 100 + toks[kk]
            k = kk + 1
            kind, kk = self._connector(toks, k, allow_bare=False)

        if not 1 <= chapter <= nch:
            return None

        if kind and self._is_num(toks, kk):
            v1, v2, end = self._verse_and_range(toks, kk)
            if not 1 <= v1 <= MAX_VERSE:
                return None
            conf = "medium" if kind == "bare" else "high"
            return Reference(canon, chapter, v1, v2, conf, end=end)
        return Reference(canon, chapter, None, None, "medium", end=k)

    def _context_ref(self, toks: list[Token], i: int, ctx: Context) -> Reference | None:
        """References with no book name, resolved from the last book mentioned."""
        if not ctx.book:
            return None
        t = toks[i]
        if t in VERSE_WORDS and self._is_num(toks, i + 1) and ctx.chapter:
            v1, v2, end = self._verse_and_range(toks, i + 1)
            if 1 <= v1 <= MAX_VERSE:
                return Reference(ctx.book, ctx.chapter, v1, v2, "low", inferred=True, end=end)
        if t in CHAPTER_WORDS and self._is_num(toks, i + 1):
            chapter = toks[i + 1]
            kind, kk = self._connector(toks, i + 2, allow_bare=False)
            if kind and self._is_num(toks, kk) and 1 <= chapter <= CHAPTERS[ctx.book]:
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
        return refs


# --------------------------------------------------------------------------
# Streaming (feed transcript segments as they arrive)
# --------------------------------------------------------------------------
class StreamingParser:
    """Keeps context between segments and a short tail of the previous one,
    so "turn to Romans" + "chapter eight verse twenty-eight" still works."""

    def __init__(self, parser: ReferenceParser | None = None, carry_tokens: int = 12):
        self.parser = parser or ReferenceParser()
        self.ctx = Context()
        self.carry: list[Token] = []
        self.carry_tokens = carry_tokens

    def feed(self, segment: str) -> list[Reference]:
        toks = self.carry + tokenize(segment)
        refs = self.parser._scan(toks, self.ctx)
        fresh = [r for r in refs if r.end > len(self.carry)]  # skip already-reported ones
        self.carry = toks[-self.carry_tokens :]
        return fresh

    def reset(self) -> None:
        self.ctx = Context()
        self.carry = []


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        p = ReferenceParser()
        for ref in p.parse(" ".join(sys.argv[1:])):
            print(f'{ref}  [{ref.confidence}]  <- "{ref.text}"')
    else:
        print("Type transcript lines (Ctrl-D to quit). Context carries between lines.")
        sp = StreamingParser()
        for line in sys.stdin:
            for ref in sp.feed(line):
                tag = " (from context)" if ref.inferred else ""
                print(f'  -> {ref}  [{ref.confidence}]{tag}  <- "{ref.text}"')
