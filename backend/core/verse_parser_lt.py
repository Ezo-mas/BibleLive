"""
verse_parser_lt.py - Lithuanian language data for verse_parser.

Used via ReferenceParser(language="lt"). Handles things like:
    "Jono evangelijos 3 skyrius, 16 eilutė"          -> John 3:16
    "Laiškas romiečiams, aštuntas skyrius, dvidešimt aštunta eilutė"
                                                     -> Romans 8:28
    "Pirmasis laiškas korintiečiams 13, 4-7"         -> 1 Corinthians 13:4-7
    "Dvidešimt trečioji psalmė"                      -> Psalms 23
    "Pradžios knygos pirmas skyrius"                 -> Genesis 1
    "Evangelija pagal Matą 5:3"                      -> Matthew 5:3

Lithuanian is heavily inflected, so:
  * text is lowercased and diacritics are folded (ė -> e, š -> s, ...), so
    transcripts with or without diacritics both match;
  * book names are stem patterns ("romiec*" matches romiečiams, ...);
  * number words are recognized as stem + case ending, cardinal or ordinal
    ("aštuntas", "aštuntą", "aštuntame", "aštunta" all -> 8);
  * chapter/verse words usually come AFTER the number ("5 skyrius 3 eilutė").
    postprocess() moves them in front ("skyrius 5 eilute 3") so the
    language-neutral grammar in verse_parser can be reused.

Canonical book names are the English ones from verse_parser.BOOKS, so the
Bible database lookup/validator works unchanged.

No third-party dependencies (standard library only).
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import cast

Token = int | str


def fold(text: str) -> str:
    """Lowercase and strip diacritics: 'Eilutė' -> 'eilute', 'Šešta' -> 'sesta'."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# --------------------------------------------------------------------------
# Structural words (all case forms -> one normalized token)
# --------------------------------------------------------------------------
CHAPTER = "skyrius"
VERSE = "eilute"
PSALM = "psalme"

_WORD_FORMS: dict[str, set[str]] = {
    CHAPTER: {
        "skyrius",
        "skyriaus",
        "skyriui",
        "skyriu",
        "skyriumi",
        "skyriuje",
        "skyriai",
        "skyriams",
        "skyriais",
        "skyriuose",
        "sk",
        # frequent STT mangles of "skyrius"
        "skirius",
        "skiris",
        "skyris",
        "skyrus",
    },
    VERSE: {
        "eilute",
        "eilutes",
        "eilutei",
        "eiluteje",
        "eilutej",
        "eiluciu",
        "eilutems",
        "eilutemis",
        "eilutese",
        "eil",
        # frequent STT mangles of "eilute" ("aštuntai ilutė", "25 lūtė")
        "ilute",
        "ilutes",
        "lute",
        "posmas",
        "posmo",
        "posma",
        "posme",
        "posmai",
        "posmu",
        "posmus",
    },
    PSALM: {
        "psalme",
        "psalmes",
        "psalmei",
        "psalmeje",
        "psalmiu",
        "psalmems",
        "psalmynas",
        "psalmyno",
        "psalmynui",
        "psalmyna",
        "psalmyne",
    },
    "knyga": {"knyga", "knygos", "knygai", "knygoje", "knygoj", "knygu"},
    "laiskas": {"laiskas", "laisko", "laiskui", "laiska", "laiske", "laisku"},
    "evangelija": {
        "evangelija",
        "evangelijos",
        "evangelijai",
        "evangelijoje",
        "evangelijoj",
    },
}
_NORMALIZE: dict[str, str] = {form: canon for canon, forms in _WORD_FORMS.items() for form in forms}

CHAPTER_WORDS = frozenset({CHAPTER})
VERSE_WORDS = frozenset({VERSE})
RANGE_WORDS = frozenset({"iki", "ligi", "-"})
AND_WORDS = frozenset({"ir"})  # consecutive-verse range: "4 ir 5" -> 4-5
FILLERS = frozenset({"ir", "nuo", "is", "pradedant", "pradedame", "skaitome", "skaitykime"})
# Filler words that, right after a chapter number, announce a verse
# ("4 skyrius nuo 16 iki 20") even when "eilute" itself is not spoken.
VERSE_INTRO_WORDS = frozenset({"nuo"})

# Book prefixes ("Pirmasis laiškas korintiečiams", "Antroji ..."). Spoken
# ordinals ("pirmasis", "antrasis", "trečiasis", ...) are already converted to
# plain numbers 1/2/3 by read_number(), so only the digit forms are needed.
PREFIXES: dict[Token, int] = {1: 1, 2: 2, 3: 3}

# Words that may precede/follow a book name ("Evangelija pagal Matą",
# "Pirmasis laiškas korintiečiams", "Pradžios knyga").
_LEAD_WORDS = frozenset({"knyga", "laiskas", "evangelija", "pagal"})
_TRAIL_WORDS = frozenset({"knyga", "laiskas", "evangelija"})
_STRONG_WORDS = frozenset({"knyga", "laiskas", "evangelija"})

# --------------------------------------------------------------------------
# Number words (cardinal + ordinal, all case endings; diacritics folded)
# --------------------------------------------------------------------------
# Adjective-style endings: ordinals ("aštuntas", "aštuntą", "aštuntoji") and
# declined "vienas".
_ADJ_END = frozenset(
    {
        "as",
        "o",
        "ui",
        "a",
        "u",
        "e",
        "am",
        "ame",
        "ai",
        "us",
        "ais",
        "uose",
        "ams",
        "os",
        "oje",
        "oms",
        "omis",
        "ose",
        "i",
        "iems",
        "ieji",
        "asis",
        "ojo",
        "ajam",
        "aji",
        "uoju",
        "ajame",
        "uju",
        "iesiems",
        "uosius",
        "aisiais",
        "uosiuose",
        "oji",
        "osios",
        "ajai",
        "aja",
        "ojoje",
        "osioms",
        "asias",
        "osiomis",
        "osiose",
    }
)
# Endings of the cardinals 4-9 ("keturi", "keturių", "keturis", "keturios").
_CARD_END = frozenset(
    {
        "i",
        "iu",
        "iems",
        "is",
        "iais",
        "iuose",
        "ios",
        "ioms",
        "iomis",
        "iose",
        "ias",
    }
)
# 11-19: cardinal ("penkiolika", "penkiolikos") or ordinal ("penkioliktas").
_TEEN_END = frozenset({"a", "os", "ai", "oje"}) | frozenset("t" + e for e in _ADJ_END)
# 10, 20..90: cardinal ("dvidešimt", "dvidešimties") or ordinal ("dvidešimtas").
_TENS_END = frozenset({"", "ies", "imi", "yje", "is"}) | _ADJ_END

_EXACT_NUMBERS: dict[str, int] = {
    "du": 2,
    "dvi": 2,
    "dvieju": 2,
    "dviem": 2,
    "dviejose": 2,
    "dviejuose": 2,
    "trys": 3,
    "triju": 3,
    "trims": 3,
    "tris": 3,
    "trimis": 3,
    "trijose": 3,
    "trijuose": 3,
}

# (stem, value, kind, allowed endings). kind: unit | teen | tens | hundred
_NUMBER_STEMS: list[tuple[str, int, str, frozenset[str]]] = [
    ("vien", 1, "unit", _ADJ_END),
    ("pirm", 1, "unit", _ADJ_END),
    ("antr", 2, "unit", _ADJ_END),
    ("treci", 3, "unit", _ADJ_END),
    ("ketur", 4, "unit", _CARD_END),
    ("ketvirt", 4, "unit", _ADJ_END),
    ("penk", 5, "unit", _CARD_END),
    ("penkt", 5, "unit", _ADJ_END),
    ("ses", 6, "unit", _CARD_END),
    ("sest", 6, "unit", _ADJ_END),
    ("septyn", 7, "unit", _CARD_END),
    ("septint", 7, "unit", _ADJ_END),
    ("astuon", 8, "unit", _CARD_END),
    ("astunt", 8, "unit", _ADJ_END),
    ("devyn", 9, "unit", _CARD_END),
    ("devint", 9, "unit", _ADJ_END),
    ("desimt", 10, "teen", _TENS_END),
    ("vienuolik", 11, "teen", _TEEN_END),
    ("dvylik", 12, "teen", _TEEN_END),
    ("trylik", 13, "teen", _TEEN_END),
    ("keturiolik", 14, "teen", _TEEN_END),
    ("penkiolik", 15, "teen", _TEEN_END),
    ("sesiolik", 16, "teen", _TEEN_END),
    ("septyniolik", 17, "teen", _TEEN_END),
    ("astuoniolik", 18, "teen", _TEEN_END),
    ("devyniolik", 19, "teen", _TEEN_END),
    ("dvidesimt", 20, "tens", _TENS_END),
    ("trisdesimt", 30, "tens", _TENS_END),
    ("keturiasdesimt", 40, "tens", _TENS_END),
    ("penkiasdesimt", 50, "tens", _TENS_END),
    ("sesiasdesimt", 60, "tens", _TENS_END),
    ("septyniasdesimt", 70, "tens", _TENS_END),
    ("astuoniasdesimt", 80, "tens", _TENS_END),
    ("devyniasdesimt", 90, "tens", _TENS_END),
    ("simt", 100, "hundred", _ADJ_END),
]


@lru_cache(maxsize=4096)
def number_word(word: str) -> tuple[int, str] | None:
    """'aštuntą' / 'astunta' -> (8, 'unit'); 'dvidešimt' -> (20, 'tens')."""
    w = fold(word)
    if w in _EXACT_NUMBERS:
        return _EXACT_NUMBERS[w], "unit"
    for stem, value, kind, endings in _NUMBER_STEMS:
        if w.startswith(stem) and w[len(stem) :] in endings:
            return value, kind
    return None


def _classify(w: list[Token], j: int) -> tuple[int, str] | None:
    if j < len(w) and isinstance(w[j], str):
        return number_word(w[j])
    return None


def _below_100(w: list[Token], j: int) -> tuple[int, int] | None:
    c = _classify(w, j)
    if not c or c[1] == "hundred":
        return None
    value, kind = c
    if kind == "tens":
        nxt = _classify(w, j + 1)
        if nxt and nxt[1] == "unit":
            return value + nxt[0], j + 2
    return value, j + 1


def read_number(w: list[Token], i: int) -> tuple[int, int] | None:
    """Read one spoken number at w[i]. Returns (value, next_index).

    'dvidešimt aštunta' -> 28, 'šimtas devynioliktoji' -> 119,
    but 'trys šešiolika' -> 3 (then 16 separately), like "John three sixteen".
    """
    c = _classify(w, i)
    if not c:
        return None
    if c[1] == "hundred":
        base, j = 1, i + 1
    elif c[1] == "unit" and (_classify(w, i + 1) or (0, ""))[1] == "hundred":
        base, j = c[0], i + 2
    else:
        return _below_100(w, i)
    value = base * 100
    rest = _below_100(w, j)
    if rest:
        value += rest[0]
        j = rest[1]
    return value, j


# --------------------------------------------------------------------------
# Book name stems
# --------------------------------------------------------------------------
# Lithuanian book names are heavily inflected ("romiečiams", "Jono", ...), so
# tokenize() folds any word starting with a registered stem onto the stem
# token ("romiec*"). BOOKS below lists its variants over these stem tokens.
# The longest matching stem wins, keeping "jonas"/"jono"/"jona"/"jonui"
# distinct (John, Jonah, "pagal Matą"-style accusatives, Revelation).
BOOK_STEMS: frozenset[str] = frozenset(
    {
        "abdij",
        "agej",
        "amos",
        "apostol",
        "apreisk",
        "daniel",
        "efeziec",
        "ekleziast",
        "ezr",
        "ester",
        "ezechezio",
        "ezechiel",
        "filemon",
        "filipiec",
        "galat",
        "giesm",
        "habakuk",
        "hebraj",
        "isej",
        "izaj",
        "jeremij",
        "joel",
        "jokub",
        "jona",
        "jonas",
        "jono",
        "jonui",
        "jozu",
        "judo",
        "karali",
        "kolosiec",
        "korintiec",
        "kunig",
        "levit",
        "luk",
        "malachij",
        "mata",
        "mato",
        "metrasc",
        "mich",
        "mokytoj",
        "mork",
        "nahum",
        "nehemij",
        "ozejo",
        "ozej",
        "pakartot",
        "patarl",
        "petr",
        "pradz",
        "raudos",
        "raud",
        "romiec",
        "rut",
        "samuel",
        "skaici",
        "sofonij",
        "teisej",
        "tesalonikiec",
        "timotej",
        "tito",
        "titui",
        "zacharij",
    }
)
_STEMS_BY_LENGTH: tuple[str, ...] = tuple(sorted(BOOK_STEMS, key=len, reverse=True))
# Verb forms that merely start with a book stem: "skaičiau 24" is "I read 24",
# not the book of Numbers. Checked before the stem match.
_NOT_BOOK_PREFIXES = ("skaiciau", "skaiciav", "skaiciuoj", "skaiciuot", "skaiciuod")


def book_stem(word: str) -> str:
    """Fold an inflected book-name word onto its stem token.

    "romiečiams" -> "romiec"; words that match no stem are returned unchanged.
    """
    if word.startswith(_NOT_BOOK_PREFIXES):
        return word
    for stem in _STEMS_BY_LENGTH:
        if word.startswith(stem):
            return stem
    return word


# --------------------------------------------------------------------------
# Tokenizing
# --------------------------------------------------------------------------
def tokenize(text: str) -> list[Token]:
    """Lithuanian text -> list of ints (digits or number words) and words.

    Words are diacritic-folded; chapter/verse/psalm/book-label words are
    normalized to one form. Does NOT reorder postpositions; see postprocess().
    """
    text = fold(text).replace("\u2013", "-").replace("\u2014", "-")
    # "3-iasis", "16-oji" (ordinal written with digits) -> one number token
    raw = re.findall(r"\d+(?:-[^\W\d_]{1,7}(?![^\W\d_]))?|[^\W\d_]+|[:\-]", text)
    words: list[Token] = []
    for t in raw:
        if t[0].isdigit():
            # t starts with a digit, so the \d+ match always succeeds; cast
            # keeps the impossible None branch (and a silent 0) out of the path.
            words.append(int(cast(re.Match[str], re.match(r"\d+", t)).group()))
        else:
            words.append(_NORMALIZE.get(t, t))
    out: list[Token] = []
    i = 0
    while i < len(words):
        t = words[i]
        r = read_number(words, i) if isinstance(t, str) and t.isalpha() else None
        if r:
            out.append(r[0])
            i = r[1]
        else:
            out.append(book_stem(t) if isinstance(t, str) else t)
            i += 1
    return out


_POSTPOSITIONAL = frozenset({CHAPTER, VERSE, PSALM})
_JOINERS = RANGE_WORDS | AND_WORDS


def _collapse_psalm_stutter(toks: list[Token]) -> list[Token]:
    """psalme 119 psalme 142 eilute -> psalme 119 eilute 142.

    A speaker says "psalmynė 119 psalmė, 142 eilutė" for Psalm 119 verse 142;
    the repeated psalm word would otherwise start a second, bogus psalm. The
    same goes for a redundant chapter word ("psalmė 16 skyrius, 8 eilutė"):
    it would otherwise strand the verse number.
    """
    out: list[Token] = []
    i, n = 0, len(toks)
    while i < n:
        if (
            toks[i] == PSALM
            and i + 4 < n
            and isinstance(toks[i + 1], int)
            and toks[i + 2] in (PSALM, CHAPTER)
            and isinstance(toks[i + 3], int)
            and toks[i + 4] == VERSE
        ):
            out += [PSALM, toks[i + 1], VERSE, toks[i + 3]]
            i += 5
            continue
        out.append(toks[i])
        i += 1
    return out


def postprocess(toks: list[Token]) -> list[Token]:
    """Move postpositional chapter/verse/psalm words before their number.

        5 skyrius 3 eilute     -> skyrius 5 eilute 3
        4 iki 7 eilute         -> eilute 4 iki 7
        23 psalme              -> psalme 23
        skyrius 5 eilute 3     -> unchanged (already prepositional)

    Only reorders (apart from collapsing a stuttered psalm word, see
    _collapse_psalm_stutter). It is applied to each new segment only;
    StreamingParser keeps already-processed tokens as carry-over and does
    not re-run it.
    """
    toks = _collapse_psalm_stutter(toks)
    out: list[Token] = []
    n = len(toks)
    owned = False  # previous token is a chapter/verse word that owns the next number
    i = 0
    while i < n:
        t = toks[i]
        if isinstance(t, int) and not owned:
            # [num, joiner, num, VERSE] -> [VERSE, num, joiner, num]  ("4 iki 7 eilutes")
            if (
                i + 3 < n
                and toks[i + 1] in _JOINERS
                and isinstance(toks[i + 2], int)
                and toks[i + 3] == VERSE
            ):
                out += [VERSE, t, toks[i + 1], toks[i + 2]]
                i += 4
                owned = False
                continue
            if i + 1 < n and toks[i + 1] in _POSTPOSITIONAL:
                word = toks[i + 1]
                if i + 2 < n and isinstance(toks[i + 2], int):
                    if i + 3 < n and toks[i + 3] == VERSE:
                        # [num, CHAPTER, num2, VERSE] -> [CHAPTER, num, VERSE, num2]
                        # ("3 skyrius 16 eilute" -> "skyrius 3 eilute 16")
                        # ("119 psalme 142 eilute" -> "psalme 119 eilute 142")
                        out += [PSALM if word == PSALM else CHAPTER, t, VERSE, toks[i + 2]]
                        i += 4
                        owned = False
                        continue
                    if (
                        i + 5 < n
                        and toks[i + 3] in _JOINERS
                        and isinstance(toks[i + 4], int)
                        and toks[i + 5] == VERSE
                    ):
                        # [num, CHAPTER, num2, joiner, num3, VERSE] ->
                        # [CHAPTER, num, VERSE, num2, joiner, num3]
                        # ("8 skyrius 4 iki 7 eilutes" -> "skyrius 8 eilute 4 iki 7")
                        out += [CHAPTER, t, VERSE, toks[i + 2], toks[i + 3], toks[i + 4]]
                        i += 6
                        owned = False
                        continue
                    # [num, CHAPTER, num2] -> [CHAPTER, num, num2]  ("3 skyrius 16")
                    out += [word, t, toks[i + 2]]
                    i += 3
                    owned = False
                    continue
                # [num, WORD] -> [WORD, num]  ("16 eilute" -> "eilute 16")
                out += [word, t]
                i += 2
                owned = False
                continue
        out.append(t)
        owned = isinstance(t, str) and t in _POSTPOSITIONAL
        i += 1
    return out


# --------------------------------------------------------------------------
# Book data: (canonical name, chapter count, Lithuanian name variants).
# Canonical names/chapter counts mirror verse_parser.BOOKS exactly (checked
# by tests), so Bible database lookups/validators work unchanged. Variants
# are space-separated tokens as produced by tokenize(): structural words
# ("knyga", "laiskas", "evangelija", "pagal") normalized, book words folded
# to stems. Numbered books ("1 Samuel") list the name WITHOUT the number;
# the prefix comes from a leading 1/2/3 token (spoken "Pirmasis...").
# --------------------------------------------------------------------------
BOOKS: list[tuple[str, int, list[str]]] = [
    ("Genesis", 50, ["pradz knyga", "pradz"]),
    ("Exodus", 40, ["isej knyga", "isej"]),
    ("Leviticus", 27, ["kunig istatymo knyga", "kunig knyga", "kunig", "levit knyga", "levit"]),
    ("Numbers", 36, ["skaici knyga", "skaici"]),
    ("Deuteronomy", 34, ["pakartot istatymo knyga", "pakartot istatymo", "pakartot"]),
    ("Joshua", 24, ["jozu knyga", "jozu"]),
    ("Judges", 21, ["teisej knyga", "teisej"]),
    ("Ruth", 4, ["rut knyga", "rut"]),
    ("1 Samuel", 31, ["samuel knyga", "samuel"]),
    ("2 Samuel", 24, ["samuel knyga", "samuel"]),
    ("1 Kings", 22, ["karali knyga", "karali"]),
    ("2 Kings", 25, ["karali knyga", "karali"]),
    ("1 Chronicles", 29, ["metrasc knyga", "metrasc"]),
    ("2 Chronicles", 36, ["metrasc knyga", "metrasc"]),
    ("Ezra", 10, ["ezr knyga", "ezr"]),
    ("Nehemiah", 13, ["nehemij knyga", "nehemij"]),
    ("Esther", 10, ["ester knyga", "ester"]),
    ("Job", 42, ["job knyga", "job"]),
    ("Psalms", 150, ["psalme"]),
    ("Proverbs", 31, ["patarl knyga", "patarl"]),
    ("Ecclesiastes", 12, ["mokytoj knyga", "mokytoj", "ekleziast"]),
    ("Song of Solomon", 8, ["giesm giesm"]),
    ("Isaiah", 66, ["izaj knyga", "izaj"]),
    ("Jeremiah", 52, ["jeremij knyga", "jeremij"]),
    ("Lamentations", 5, ["raud knyga", "raud"]),
    ("Ezekiel", 48, ["ezechiel knyga", "ezechiel"]),
    ("Daniel", 12, ["daniel knyga", "daniel"]),
    ("Hosea", 14, ["ozej knyga", "ozej", "ozejo knyga", "ozejo"]),
    ("Joel", 3, ["joel knyga", "joel"]),
    ("Amos", 9, ["amos knyga", "amos"]),
    ("Obadiah", 1, ["abdij knyga", "abdij"]),
    ("Jonah", 4, ["jona knyga", "jona"]),
    ("Micah", 7, ["mich knyga", "mich"]),
    ("Nahum", 3, ["nahum knyga", "nahum"]),
    ("Habakkuk", 3, ["habakuk knyga", "habakuk"]),
    ("Zephaniah", 3, ["sofonij knyga", "sofonij"]),
    ("Haggai", 2, ["agej knyga", "agej"]),
    ("Zechariah", 14, ["zacharij knyga", "zacharij"]),
    ("Malachi", 4, ["malachij knyga", "malachij"]),
    ("Matthew", 28, ["mato evangelija", "evangelija pagal mata", "pagal mata", "mato", "mata"]),
    ("Mark", 16, ["mork evangelija", "evangelija pagal mork", "pagal mork", "mork"]),
    ("Luke", 24, ["luk evangelija", "evangelija pagal luk", "pagal luk", "luk"]),
    (
        "John",
        21,
        [
            "jono evangelija",
            "jonas evangelija",
            "evangelija pagal jona",
            "pagal jona",
            "jono",
            "jonas",
        ],
    ),
    ("Acts", 28, ["apostolu darbai", "apostolu"]),
    ("Romans", 16, ["laiskas romiec", "romiec"]),
    ("1 Corinthians", 16, ["laiskas korintiec", "korintiec"]),
    ("2 Corinthians", 13, ["laiskas korintiec", "korintiec"]),
    ("Galatians", 6, ["laiskas galat", "galat"]),
    ("Ephesians", 6, ["laiskas efeziec", "efeziec"]),
    ("Philippians", 4, ["laiskas filipiec", "filipiec"]),
    ("Colossians", 4, ["laiskas kolosiec", "kolosiec"]),
    ("1 Thessalonians", 5, ["laiskas tesalonikiec", "tesalonikiec"]),
    ("2 Thessalonians", 3, ["laiskas tesalonikiec", "tesalonikiec"]),
    ("1 Timothy", 6, ["laiskas timotej", "timotej"]),
    ("2 Timothy", 4, ["laiskas timotej", "timotej"]),
    ("Titus", 3, ["laiskas titui", "titui"]),
    ("Philemon", 1, ["laiskas filemon", "filemon"]),
    ("Hebrews", 13, ["laiskas hebraj", "hebraj"]),
    ("James", 5, ["jokub laiskas", "jokub"]),
    ("1 Peter", 5, ["petr laiskas", "petr"]),
    ("2 Peter", 3, ["petr laiskas", "petr"]),
    ("1 John", 5, ["jono laiskas", "jono"]),
    ("2 John", 1, ["jono laiskas"]),
    ("3 John", 1, ["jono laiskas"]),
    ("Jude", 1, ["judo laiskas", "judo"]),
    ("Revelation", 22, ["apreisk jonui", "apreisk"]),
]
