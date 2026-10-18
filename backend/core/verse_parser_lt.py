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
        "kyriaus",  # dropped initial s ("20 kyriaus, 21 eilutės")
        "skyriuj",  # spoken locative ("antram skyriuj")
    },
    VERSE: {
        "eilute",
        "eilutes",
        "eilutei",
        "eiluteje",
        "eilutej",
        "eiluti",  # accusative "eilutį" ("15-16 eilutį")
        "eiluciu",
        "eilutems",
        "eilutemis",
        "eilutese",
        "eil",
        # frequent STT mangles of "eilute" ("aštuntai ilutė", "25 lūtė")
        "ilute",
        "ilutes",
        "iluti",  # "11 12 įlutį": į written for ę
        # "17 rūtį" for "17 eilutę"; not Ruth, whose forms are rūta/rūtos/rūtai
        "rute",
        "rutes",
        "ruti",
        "lute",
        "lutes",
        "lutei",
        "luteje",
        "lutej",
        "luti",
        "luciu",
        "lutems",
        "lutemis",
        "lutese",
        # "jūtė" for "eilutė", pervasive with some speakers ("26 jūtė")
        "jute",
        "jutes",
        "jutei",
        "juteje",
        "jutej",
        "juti",
        # "ūtė": the same mangle with the L dropped too ("Trisdešimtai ūtė")
        "ute",
        "utes",
        "utei",
        "uteje",
        "utej",
        "uti",
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
        "psalmej",  # spoken locative ("kitoj psalmėj")
        "psalmio",  # masculine genitive speakers use ("Psalmio 40 skyriaus")
        "psalmiu",
        "psalmems",
        "psalmiai",
        "psalmiais",
        "psalmiams",
        "psalmiuose",
        "psalmynas",
        "psalmyno",
        "psalmynui",
        "psalmyna",
        "psalmyne",
    },
    "knyga": {"knyga", "knygos", "knygai", "knygoje", "knygoj", "knygu"},
    "laiskas": {"laiskas", "laisko", "laiskui", "laiska", "laiske", "laisku", "laiskio"},
    "evangelija": {
        "evangelija",
        "evangelijos",
        "evangelijai",
        "evangelijoje",
        "evangelijoj",
    },
}
_NORMALIZE: dict[str, str] = {form: canon for canon, forms in _WORD_FORMS.items() for form in forms}
# Whisper drops the "u" of "nuo" ("no šešioliktos iki 20"); "no" is not a
# Lithuanian word, so reading it as "nuo" cannot hide a real one.
_NORMALIZE["no"] = "nuo"

CHAPTER_WORDS = frozenset({CHAPTER})
VERSE_WORDS = frozenset({VERSE})
RANGE_WORDS = frozenset({"iki", "ligi", "-"})
AND_WORDS = frozenset({"ir", "i"})  # consecutive-verse range: "4 ir 5" -> 4-5
# "i" is the unstressed reduced "ir" STT actually writes ("nuo pirmos i
# trečios" for 1-3). A preposition "į" between numbers ("3 i 16") is rare
# enough that the gap bound (verse pairs within 5) contains the damage.
FILLERS = frozenset(
    {"ir", "nuo", "is", "pradedant", "pradedame", "skaitome", "skaitykime", "pati", "pats"}
)
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
# Book-label words that may follow a bare book name postpositionally
# ("Romiečiams laiške, 8 skyrius"): the parser may bridge over one when a
# chapter/verse word with its number comes right after it.
LABEL_WORDS = frozenset({"knyga", "laiskas", "evangelija", "pagal"})
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
        "oj",  # spoken locative ("antroj", "trisdešim penktoj eilutėj")
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
    # STT drops the 'i' ("ketruoliktojai" for "keturioliktoji"); exact word
    # to avoid widening every stem's endings for one mangled form.
    "ketruoliktojai": 14,
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
    ("sepint", 7, "unit", _ADJ_END),  # STT drops the 't' ("sepinta" for "septinta")
    ("septant", 7, "unit", _ADJ_END),  # STT writes 'a' for 'i' ("septantai" for "septintai")
    ("astuon", 8, "unit", _CARD_END),
    ("astunt", 8, "unit", _ADJ_END),
    ("stunt", 8, "unit", _ADJ_END),  # STT drops the initial A ("štuntos" for "aštuntos")
    ("devyn", 9, "unit", _CARD_END),
    ("devint", 9, "unit", _ADJ_END),
    ("desimt", 10, "teen", _TENS_END),
    ("desim", 10, "teen", _TENS_END),  # STT drops the final 't' ("penki dešim")
    ("vienuolik", 11, "teen", _TEEN_END),
    ("dvylik", 12, "teen", _TEEN_END),
    ("trylik", 13, "teen", _TEEN_END),
    ("keturiolik", 14, "teen", _TEEN_END),
    ("ketrolik", 14, "teen", _TEEN_END),  # STT drops the 'u' ("ketrolika" for "keturiolika")
    ("penkiolik", 15, "teen", _TEEN_END),
    ("sesiolik", 16, "teen", _TEEN_END),
    ("septyniolik", 17, "teen", _TEEN_END),
    ("astuoniolik", 18, "teen", _TEEN_END),
    ("astiniolik", 18, "teen", _TEEN_END),  # STT drops the 'u' ("Aštiniolika")
    ("devyniolik", 19, "teen", _TEEN_END),
    ("dvidesimt", 20, "tens", _TENS_END),
    ("dvidesim", 20, "tens", _TENS_END),  # STT drops the final 't' ("dvidešim")
    ("trisdesimt", 30, "tens", _TENS_END),
    ("keturiasdesimt", 40, "tens", _TENS_END),
    ("penkiasdesimt", 50, "tens", _TENS_END),
    ("sesiasdesimt", 60, "tens", _TENS_END),
    ("septyniasdesimt", 70, "tens", _TENS_END),
    ("astuoniasdesimt", 80, "tens", _TENS_END),
    ("devyniasdesimt", 90, "tens", _TENS_END),
    # STT drops the final 't' of every tens word, like "dvidešim" above
    # ("devyniasdešim pirmą psalmę" for Psalm 91).
    ("trisdesim", 30, "tens", _TENS_END),
    ("keturiasdesim", 40, "tens", _TENS_END),
    ("penkiasdesim", 50, "tens", _TENS_END),
    ("sesiasdesim", 60, "tens", _TENS_END),
    ("septyniasdesim", 70, "tens", _TENS_END),
    ("astuoniasdesim", 80, "tens", _TENS_END),
    ("devyniasdesim", 90, "tens", _TENS_END),
    ("simt", 100, "hundred", _ADJ_END),
]


@lru_cache(maxsize=4096)
def number_word(word: str) -> tuple[int, str] | None:
    """'aštuntą' / 'astunta' -> (8, 'unit'); 'dvidešimt' -> (20, 'tens')."""
    w = fold(word)
    # Nominative "viena"/"vienas" is the indefinite article ("dar viena
    # eilutę" = "one more verse"), not verse/chapter 1; genuine 1s are
    # "pirma(s)" or digits. Tens still compound with it ("dvidešimt
    # vienas" = 21, handled in _below_100).
    if w in ("viena", "vienas"):
        return None
    if w in _EXACT_NUMBERS:
        return _EXACT_NUMBERS[w], "unit"
    for stem, value, kind, endings in _NUMBER_STEMS:
        if w.startswith(stem) and w[len(stem) :] in endings:
            return value, kind
    roman = _roman_value(w)
    if roman is not None:
        return roman, "unit"
    return None


_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _roman_value(word: str) -> int | None:
    """Roman numerals speakers read off ("XIX skyrius" for chapter 19).

    Only multi-letter tokens: lone "i" is the preposition "į" and lone
    "v"/"x" are initials too often. Capped at 150 (Psalms); larger values
    are years, not citations.
    """
    if len(word) < 2 or any(ch not in _ROMAN_VALUES for ch in word):
        return None
    total, prev = 0, 0
    for ch in reversed(word):
        value = _ROMAN_VALUES[ch]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    if not 1 <= total <= 150:
        return None
    return total


FUZZY_BOOKS = True
FUZZY_NUMBERS = True


def _lev(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, giving up (returning limit + 1) past ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def fuzzy_number_word(word: str) -> tuple[int, str] | None:
    """A number word STT misspelled by a letter or two ("ketrolika" -> 14).

    Only the stem may differ (1 edit, 2 for stems of 9+ letters); the ending
    must be a real one. Ambiguous words (two different values) return None.
    """
    w = fold(word)
    if len(w) < 5 or not w.isalpha():
        return None
    found: set[tuple[int, str]] = set()
    for stem, value, kind, endings in _NUMBER_STEMS:
        if stem == "vien" or len(stem) < 5:
            continue
        limit = 2 if len(stem) >= 9 else 1
        for cut in range(max(1, len(stem) - limit), min(len(w), len(stem) + limit) + 1):
            if w[cut:] in endings and _lev(w[:cut], stem, limit) <= limit:
                found.add((value, kind))
                break
    return found.pop() if len(found) == 1 else None


def _near_citation_word(w: list[Token], j: int) -> bool:
    return any(0 <= k < len(w) and w[k] in (CHAPTER, VERSE) for k in (j - 2, j - 1, j + 1, j + 2))


def _classify(w: list[Token], j: int) -> tuple[int, str] | None:
    word = w[j] if j < len(w) else None
    if isinstance(word, str):
        hit = number_word(word)
        if hit is None and FUZZY_NUMBERS and _near_citation_word(w, j):
            # Only next to "skyrius"/"eilutė": elsewhere a near-number word is
            # far more likely an ordinary word than a mangled number.
            hit = fuzzy_number_word(word)
        return hit
    return None


def _below_100(w: list[Token], j: int) -> tuple[int, int] | None:
    c = _classify(w, j)
    if not c or c[1] == "hundred":
        return None
    value, kind = c
    if kind == "tens" and not _is_tens_ordinal(w[j]):
        nxt = _classify(w, j + 1)
        following = w[j + 1] if j + 1 < len(w) else None
        if (
            nxt is None
            and isinstance(following, str)
            # Compounding still works with the article form ("dvidešimt
            # vienas" = 21) even though it is not a number on its own.
            and fold(following) in ("viena", "vienas")
        ):
            nxt = (1, "unit")
        if nxt and nxt[1] == "unit":
            return value + nxt[0], j + 2
    return value, j + 1


def _is_tens_ordinal(word: Token) -> bool:
    """'dvidešimta' (20th) is a whole ordinal; only the cardinal 'dvidešimt'
    compounds with a following unit ('dvidešimt penkta' = 25th, but
    'psalmė dvidešimta, penkta eilutė' = Psalm 20 verse 5). The plural
    ending -i is left out: STT writes "dvidešimti pirma" for "dvidešimt pirma"."""
    if not isinstance(word, str):
        return False
    w = fold(word)
    return any(
        kind == "tens" and w.startswith(stem) and w[len(stem) :] in _ADJ_END - {"i"}
        for stem, _value, kind, _endings in _NUMBER_STEMS
    )


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


def _split_glued_ordinal(word: str) -> tuple[int, int] | None:
    """Split two ordinal words STT glued into one.

    "devintadešimtai" (for "devinta dešimta") -> (9, 10); the caller emits
    them with a dash between so the range rules apply. Only when the whole
    word is not a number itself (genuine "devyniasdešimt", 90, always wins)
    and both parts are ordinal-marked units/tens: cardinals ("devynias")
    never split, so tens stay whole.
    """
    w = fold(word)
    if number_word(w) is not None:
        return None
    for i in range(4, len(w) - 4):
        first = None
        for stem, value, kind, _endings in _NUMBER_STEMS:
            if (
                kind == "unit"
                and 1 <= value <= 9
                and w.startswith(stem)
                and len(stem) < i
                and w[len(stem) : i] in _ADJ_END
            ):
                first = value
                break
        if first is None:
            continue
        for stem, value, kind, endings in _NUMBER_STEMS:
            if (
                kind in ("teen", "tens")
                and value % 10 == 0
                and w[i:].startswith(stem)
                and w[i + len(stem) :] in endings
            ):
                return first, value
    return None


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
        "apastal",  # standard spelling ("apaštalų darbai")
        "apastol",  # STT writes unstressed 'a' as 'o' ("apaštolų darbai")
        "pastal",  # STT drops the initial A ("paštalų" for "apaštalų")
        "apreisk",
        "daniel",
        "efeziec",
        "feziec",  # STT drops the initial E ("Feziečiams laiškio")
        "afeziec",  # STT writes A for E ("Afeziečiams 4.25")
        "ekleziast",
        "ezr",
        "ezdr",  # the LTB title ("Ezdro knyga")
        "ester",
        "ezechezio",
        "ezechiel",
        "filemon",
        "filipiec",
        "galat",
        "giesm",
        "habakuk",
        "hebraj",
        "hebrej",  # standard spelling ("Hebrėjams"), the one the Whisper prompt uses
        "isej",
        "izaj",
        "izaij",  # correct spelling ("Izaijo", "Izaijas"); "izaj" kept for the STT variant "Izajo"
        "jebraj",  # STT writes J for H ("Jebrajams" for "Hebrajams")
        "jeremij",
        "job",  # "Jobo", "Jobas": BOOKS listed Job but the stem was missing
        "joel",
        "jokub",
        "jona",
        "jonos",  # Jonah's genitive ("Jonos knyga"); "jono" is John
        "jonas",
        "jono",
        "jonui",
        "jozu",
        "judo",
        "karali",
        "kohelet",  # Ecclesiastes' LTB subtitle ("Mokytojo (Koheleto) knyga")
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
        "timotiej",  # correct dative/genitive ("Timotiejui", "Timotiejaus")
        "tito",
        "titui",
        "vuk",  # STT writes V for L ("Vuko" for "Luko")
        "zacharij",
        "zaj",  # STT drops the initial I ("Zajo" for "Izaijo")
    }
)
_STEMS_BY_LENGTH: tuple[str, ...] = tuple(sorted(BOOK_STEMS, key=len, reverse=True))
# Verb forms that merely start with a book stem: "skaičiau 24" is "I read 24",
# not the book of Numbers; "mes matom" is "we see", not Matthew.
# Checked before the stem match.
_NOT_BOOK_PREFIXES = (
    "skaiciau",
    "skaiciav",
    "skaiciuoj",
    "skaiciuot",
    "skaiciuod",
    "matau",
    "matai",
    "matom",
    "matome",
    "matote",
    # "išėjau" (I went out) is not Exodus: present ("išeina"), past
    # ("išėjo") and participle ("išėjęs") forms. The noun ("išėjimas",
    # "išėjimo" — the book) starts with "iseji" and still folds.
    "isejau",
    "isejai",
    "isejo",
    "iseje",
    "isei",
)


_FUZZY_SKIP_STEMS = frozenset({"pastal", "apastal", "apastol", "apostol", "pradz"})


def fuzzy_book_stem(word: str) -> str | None:
    """The one book stem a misspelled word starts with, at one edit
    ("vuko" -> no: stems under 4 letters are too short to guess;
    "afezieciams" -> "efeziec", "zajo" -> "izaj"). None if none or several.
    """
    if len(word) < 5 or not word.isalpha() or word.startswith(_NOT_BOOK_PREFIXES):
        return None
    found = set()
    for stem in _STEMS_BY_LENGTH:
        if len(stem) < 4 or stem in _FUZZY_SKIP_STEMS:
            continue
        for cut in (len(stem) - 1, len(stem), len(stem) + 1):
            if 0 < cut <= len(word) and _lev(word[:cut], stem, 1) <= 1:
                found.add(stem)
                break
    return found.pop() if len(found) == 1 else None


def book_stem(word: str) -> str:
    """Fold an inflected book-name word onto its stem token.

    "romiečiams" -> "romiec"; words that match no stem are returned unchanged.
    """
    if word.startswith(_NOT_BOOK_PREFIXES):
        return word
    for stem in _STEMS_BY_LENGTH:
        # "paštalai" (nominative, the people themselves) must not become
        # the book: only the genitive "paštalų" (elliptical "Acts ...")
        # folds onto the stem.
        if stem == "pastal" and word != "pastalu":
            continue
        # Same for the a/o apostle stems: only genitive -ų forms
        # ("apaštalų", "apaštolų", "apostolų") are book-elliptical;
        # nominatives ("apaštalas", "apostolas") are people.
        if stem in ("apastal", "apastol", "apostol") and not word.endswith("u"):
            continue
        # "pradžiai" (dative, "for starters") and "pradžioje" (locative,
        # "in the beginning") are ordinary words, not the book: only the
        # genitive "pradžios" ("Pradžios knyga") folds onto the stem.
        if stem == "pradz" and not word.startswith("pradzios"):
            continue
        if word.startswith(stem):
            return stem
    return word


# --------------------------------------------------------------------------
# Tokenizing
# --------------------------------------------------------------------------
def _unglue(word: str) -> list[str]:
    """Split two words Whisper wrote as one.

    "nuotrecios" (nuo tre\u010dios) -> ["nuo", "trecios"]: "nuo" + a number word.
    "pagaluka" (pagal Luk\u0105) -> ["pagal", "luka"]: "pagal" + a book name; the
    shared "l" may be written once.
    Only when the remainder is unambiguous, so ordinary words stay whole.
    """
    if word.startswith("nuo") and len(word) >= 6 and number_word(word[3:]):
        return ["nuo", word[3:]]
    if word.startswith("pagal"):
        for tail in (word[5:], word[4:]):  # "pagalluka", "pagaluka"
            if len(tail) >= 3 and book_stem(tail) in BOOK_STEMS:
                return ["pagal", tail]
    if word.startswith("is") and len(word) >= 5:
        # "Isozejo" ("Iš Ozėjo" glued) -> ["is", "ozejo"]: the preposition
        # "iš" glues onto the book name. The remainder must fold onto a
        # known stem (even unchanged, as "ozejo" does), so ordinary
        # "is"-words ("istorija", "istrauke") stay whole.
        tail = word[2:]
        if len(tail) >= 3 and book_stem(tail) in BOOK_STEMS:
            return ["is", tail]
    return [word]


def tokenize(text: str) -> list[Token]:
    """Lithuanian text -> list of ints (digits or number words) and words.

    Words are diacritic-folded; chapter/verse/psalm/book-label words are
    normalized to one form. Does NOT reorder postpositions; see postprocess().
    """
    text = fold(text).replace("\u2013", "-").replace("\u2014", "-")
    # "3-iasis", "16-oji" (ordinal written with digits) -> one number token
    raw = re.findall(r"\d+(?:-[^\W\d_]{1,7}(?![^\W\d_]))?|[^\W\d_]+|[:\-]", text)
    raw = [part for t in raw for part in (_unglue(t) if t.isalpha() else [t])]
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
            # Two ordinal words glued by STT ("devintadešimtai" for
            # "devinta dešimta"): emit with a dash so the range rules apply.
            g = _split_glued_ordinal(t) if isinstance(t, str) and t.isalpha() else None
            if g:
                out += [g[0], "-", g[1]]
            elif t in ("viena", "vienas") and _citation_slot(out):
                out.append(1)
            else:
                out.append(book_stem(t) if isinstance(t, str) else t)
            i += 1
    # "34 ir iki 37": an "ir" right before a range word adds nothing.
    return [
        t
        for k, t in enumerate(out)
        if not (t in AND_WORDS and k + 1 < len(out) and out[k + 1] in RANGE_WORDS)
    ]


def _citation_slot(out: list[Token]) -> bool:
    """True right after a book name or a book's chapter number: there
    "viena(s)" is the number 1 ("Jobo vienas", "Kolosiečiams, Tris, Vienas"),
    not the article it is everywhere else ("dar viena eilutė")."""
    if out and isinstance(out[-1], str):
        return any(out[-1].startswith(w) for w in _book_words())
    return (
        len(out) >= 2
        and isinstance(out[-1], int)
        and isinstance(out[-2], str)
        and any(out[-2].startswith(w) for w in _book_words())
    )


def _book_words() -> frozenset[str]:
    global _BOOK_WORDS
    if _BOOK_WORDS is None:
        _BOOK_WORDS = (
            frozenset(
                word
                for _canon, _chapters, names in BOOKS
                for name in names
                for word in name.split()
                if len(word) >= 3
            )
            - _LEAD_WORDS
            - _TRAIL_WORDS
        )
    return _BOOK_WORDS


_BOOK_WORDS: frozenset[str] | None = None


_POSTPOSITIONAL = frozenset({CHAPTER, VERSE, PSALM})
_JOINERS = RANGE_WORDS | AND_WORDS


def _collapse_psalm_stutter(toks: list[Token]) -> list[Token]:
    """psalme 119 psalme 142 eilute -> psalme 119 eilute 142.

    A speaker says "psalmynė 119 psalmė, 142 eilutė" for Psalm 119 verse 142;
    the repeated psalm word would otherwise start a second, bogus psalm. The
    same goes for a redundant chapter word ("psalmė 16 skyrius, 8 eilutė"):
    it would otherwise strand the verse number. A verse range collapses the
    same way ("psalmių 40 skyriaus 1 2 eilutė" -> psalme 40 eilute 1 - 2).
    """
    out: list[Token] = []
    i, n = 0, len(toks)
    while i < n:
        if (
            toks[i] == PSALM
            and i + 4 < n
            and isinstance(toks[i + 1], int)
            and toks[i + 2] in (PSALM, CHAPTER)
        ):
            verses = _verse_span_before_verse_word(toks, i + 3)
            if verses is not None:
                span, after = verses
                out += [PSALM, toks[i + 1], VERSE, *span]
                i = after
                continue
        out.append(toks[i])
        i += 1
    return out


# Longest range a stray middle digit may sit in ("35 3 42 eilutės" -> 35-42).
_STRAY_RANGE_SPAN = 15


def _verse_span_before_verse_word(toks: list[Token], j: int) -> tuple[list[Token], int] | None:
    """At toks[j]: ``a VERSE``, ``a b VERSE`` (a < b) or ``a joiner b VERSE``.

    Returns the verse tokens to emit after the verse word (``[a]`` or
    ``[a, "-"/joiner, b]``) and the index after the verse word.
    """
    n = len(toks)
    a = toks[j] if j < n else None
    if not isinstance(a, int):
        return None
    if j + 1 < n and toks[j + 1] == VERSE:
        return [a], j + 2
    b = toks[j + 1] if j + 1 < n else None
    if isinstance(b, int) and a < b and j + 2 < n and toks[j + 2] == VERSE:
        return [a, "-", b], j + 3
    if (
        j + 3 < n
        and toks[j + 1] in _JOINERS
        and isinstance(toks[j + 2], int)
        and toks[j + 3] == VERSE
    ):
        return [a, toks[j + 1], toks[j + 2]], j + 4
    return None


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
    # A dash right after the chapter word is punctuation, not a range: there
    # is no number before it ("pirmas skyrius – trisdešim penkta ...").
    toks = [t for k, t in enumerate(toks) if not (t == "-" and k and toks[k - 1] == CHAPTER)]
    out: list[Token] = []
    n = len(toks)
    owned = False  # previous token is a chapter/verse word that owns the next number
    i = 0
    while i < n:
        t = toks[i]
        first, second = (toks[i + 1], toks[i + 2]) if i + 2 < n else (None, None)
        if (
            t in VERSE_INTRO_WORDS
            # Only once the chapter is known ("15 skyrius nuo 1 3"): right
            # after a book, "nuo 8 28 eilutės" is chapter 8 verse 28.
            and out
            and (isinstance(out[-1], int) or out[-1] == CHAPTER)
            and i + 3 < n
            and isinstance(first, int)
            and isinstance(second, int)
            and first < second
            and toks[i + 3] == VERSE
        ):
            # [nuo, num, num2, VERSE] -> [VERSE, nuo, num, -, num2]
            # ("nuo pirmos trečios eilutės" = verses 1-3; "nuo" marks the range)
            out += [VERSE, t, first, "-", second]
            i += 4
            owned = False
            continue
        if isinstance(t, int) and not owned:
            # [num, VERSE, num2, PSALM] -> [PSALM, num2, VERSE, num]
            # ("pirmą eilutę 91 psalmės" = Psalm 91:1, the verse named first).
            # Not after another number, and not when a number follows the
            # psalm word: then the pair belongs to something else.
            if (
                i + 3 < n
                and toks[i + 1] == VERSE
                and isinstance(toks[i + 2], int)
                and toks[i + 3] == PSALM
                and not (out and isinstance(out[-1], int))
                and not (i + 4 < n and isinstance(toks[i + 4], int))
            ):
                out += [PSALM, toks[i + 2], VERSE, t]
                i += 4
                owned = False
                continue
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
                    # [num, CHAPTER, num2, num3, VERSE] ->
                    # [CHAPTER, num, VERSE, num2, -, num3]
                    # ("4 skyriuje 9 10 eilute" -> "skyrius 4 eilute 9 - 10").
                    # Only after an explicit chapter word: a bare
                    # "Jono 3 16 eilute" is chapter 3 verse 16, not a range.
                    num2 = toks[i + 2]
                    num3 = toks[i + 3] if i + 3 < n else None
                    if (
                        word == CHAPTER
                        and i + 4 < n
                        and isinstance(num2, int)
                        and isinstance(num3, int)
                        and num2 < num3
                        and toks[i + 4] == VERSE
                    ):
                        out += [CHAPTER, t, VERSE, num2, "-", num3]
                        i += 5
                        owned = False
                        continue
                    # [num, CHAPTER, a, x, b, VERSE] with a stray digit x ->
                    # [CHAPTER, num, VERSE, a, -, b]: a slurred join heard as
                    # a small number ("pirmas skyrius, trisdešim penkta, trys
                    # keturiasdešim antra eilutės" for 1:35-42; Canary heard
                    # "35-42"). Only a digit below both ends of a short rising
                    # pair; a list ("3 5 7 eilutės") rises and stays as is.
                    num4 = toks[i + 4] if i + 4 < n else None
                    if (
                        word == CHAPTER
                        and i + 5 < n
                        and isinstance(num2, int)
                        and isinstance(num3, int)
                        and isinstance(num4, int)
                        and num3 < 10 <= num2 < num4 <= num2 + _STRAY_RANGE_SPAN
                        and toks[i + 5] == VERSE
                    ):
                        out += [CHAPTER, t, VERSE, num2, "-", num4]
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
    ("Ezra", 10, ["ezr knyga", "ezr", "ezdr knyga", "ezdr"]),
    ("Nehemiah", 13, ["nehemij knyga", "nehemij"]),
    ("Esther", 10, ["ester knyga", "ester"]),
    ("Job", 42, ["job knyga", "job"]),
    ("Psalms", 150, ["psalme"]),
    ("Proverbs", 31, ["patarl knyga", "patarl"]),
    ("Ecclesiastes", 12, ["mokytoj knyga", "mokytoj", "ekleziast", "kohelet knyga", "kohelet"]),
    ("Song of Solomon", 8, ["giesm giesm"]),
    ("Isaiah", 66, ["izaj knyga", "izaj", "izaij knyga", "izaij", "zaj", "izaiu"]),
    ("Jeremiah", 52, ["jeremij knyga", "jeremij"]),
    ("Lamentations", 5, ["raud knyga", "raud"]),
    ("Ezekiel", 48, ["ezechiel knyga", "ezechiel"]),
    ("Daniel", 12, ["daniel knyga", "daniel"]),
    ("Hosea", 14, ["ozej knyga", "ozej", "ozejo knyga", "ozejo"]),
    ("Joel", 3, ["joel knyga", "joel"]),
    ("Amos", 9, ["amos knyga", "amos"]),
    ("Obadiah", 1, ["abdij knyga", "abdij"]),
    ("Jonah", 4, ["jona knyga", "jona", "jonos knyga", "jonos"]),
    ("Micah", 7, ["mich knyga", "mich"]),
    ("Nahum", 3, ["nahum knyga", "nahum"]),
    ("Habakkuk", 3, ["habakuk knyga", "habakuk"]),
    ("Zephaniah", 3, ["sofonij knyga", "sofonij"]),
    ("Haggai", 2, ["agej knyga", "agej"]),
    ("Zechariah", 14, ["zacharij knyga", "zacharij"]),
    ("Malachi", 4, ["malachij knyga", "malachij"]),
    ("Matthew", 28, ["mato evangelija", "evangelija pagal mata", "pagal mata", "mato", "mata"]),
    ("Mark", 16, ["mork evangelija", "evangelija pagal mork", "pagal mork", "mork"]),
    (
        "Luke",
        24,
        ["luk evangelija", "evangelija pagal luk", "pagal luk", "luk", "vuk evangelija", "vuk"],
    ),
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
    (
        "Acts",
        28,
        [
            "apostolu darbai",
            "apostolu",
            "apastal darbai",
            "apastal darbu",
            "apastal",
            "apastol darbai",
            "apastol",
            "pastal",
        ],
    ),
    ("Romans", 16, ["laiskas romiec", "romiec"]),
    ("1 Corinthians", 16, ["laiskas korintiec", "korintiec laiskas", "korintiec"]),
    ("2 Corinthians", 13, ["laiskas korintiec", "korintiec laiskas", "korintiec"]),
    ("Galatians", 6, ["laiskas galat", "galat"]),
    ("Ephesians", 6, ["laiskas efeziec", "efeziec", "laiskas feziec", "feziec", "afeziec"]),
    ("Philippians", 4, ["laiskas filipiec", "filipiec"]),
    ("Colossians", 4, ["laiskas kolosiec", "kolosiec"]),
    ("1 Thessalonians", 5, ["laiskas tesalonikiec", "tesalonikiec"]),
    ("2 Thessalonians", 3, ["laiskas tesalonikiec", "tesalonikiec"]),
    ("1 Timothy", 6, ["laiskas timotej", "timotej", "laiskas timotiej", "timotiej"]),
    ("2 Timothy", 4, ["laiskas timotej", "timotej", "laiskas timotiej", "timotiej"]),
    ("Titus", 3, ["laiskas titui", "titui"]),
    ("Philemon", 1, ["laiskas filemon", "filemon"]),
    (
        "Hebrews",
        13,
        ["laiskas hebraj", "hebraj", "laiskas hebrej", "hebrej", "laiskas jebraj", "jebraj"],
    ),
    ("James", 5, ["jokub laiskas", "jokub"]),
    ("1 Peter", 5, ["petr laiskas", "petr"]),
    ("2 Peter", 3, ["petr laiskas", "petr"]),
    ("1 John", 5, ["jono laiskas", "jono"]),
    ("2 John", 1, ["jono laiskas", "jono"]),
    ("3 John", 1, ["jono laiskas", "jono"]),
    ("Jude", 1, ["judo laiskas", "judo"]),
    ("Revelation", 22, ["apreisk jonui", "apreisk"]),
]
