"""Per-model spelling maps: a model's Lithuanian spellings -> ones the parser knows.

Each speech model mishears book names and verse words in its own way
("Psmė" from Kmynas, "Salme" from Canary). The Lithuanian parser stays shared;
a small map per model rewrites that model's known spellings before parsing.
Whisper needs none (its prompt carries the book names), so it has no map.

The maps were fitted on the benchmark recordings (benchmarks/BASELINE_NOTES.md,
"Other speech models for Lithuanian"), so their gains are optimistic until
measured on new recordings. Changing a rule is a detection-accuracy change:
benchmark it. The benchmark caches the model's own text and applies the map
when replaying, so a map change replays in seconds.
"""

import re
from collections.abc import Callable

from core.verse_parser import Reference, StreamingParser

# (pattern, replacement), applied in order to Lithuanian transcripts.
KMYNAS_RULES: list[tuple[str, str]] = [
    (r"\b[pP]smė\b", "Psalmė"),  # Psmė
    (r"\b[ŠšSs]?[iI]zajo\b", "Izaijo"),  # Šizajo, Izajo
    (r"\b[aA]?[fF]iziečiams\b", "efeziečiams"),
    (r"\b[gG]avatams\b", "galatams"),
    (r"\b[eE]utį\b", "eilutį"),
]

CANARY_RULES: list[tuple[str, str]] = [
    (r"\b[aA]?[pP]?[sS]alm(?=[eėiųęoa])", "psalm"),  # Salme, apsalmę
    (r"\bš?[iI]zajo\b", "Izaijo"),
    (r"\b[ŠšSs]izajo\b", "Izaijo"),
    (r"\b[jJ]ebraiams\b", "Jebrajams"),
    (r"\b[pP]oštal", "apaštal"),  # poštalų darbų
    (r"\b[sS]amail", "Samuel"),
    (r"\b[tT]imotiev", "Timotiej"),
    (r"\b[eE]fez\s+[dD]ie", "Efezie"),
    (r"\b[šŠ]eimo\b", "Išėjimo"),
    (r"\b[jJ]onų\b", "Jono"),
    # Canary writes numbers as digits in its own formats.
    (r"(\d+)\.(?=\d)", r"\1, "),  # 17.17 -> 17, 17
    (r"(\d+)-(?:ame|ąją|ąjį|ą|oji|asis|ojo|ojoje|uoju)\b-?", r"\1"),  # 25-ame-
    (r"(\w+)ąjūt", r"\1ą eilut"),  # antrąjūtę
    (r"(\w{3,}?)(?:ia|a)ilut", r"\1ia eilut"),  # trečiailutė
    (r"(\w+)aj\s+l[uū]t", r"\1ai eilut"),  # trečiaj lutė
    (r"\b(?:i|ei|į|ie)?l[uū]t(ė|ę|ėje|ės|ei|e|es)\b", r"eilut\1"),  # lutėje, ilūtė
]

SPELLING_MAPS: dict[str, list[tuple[str, str]]] = {
    "kmynas": KMYNAS_RULES,
    "canary": CANARY_RULES,
}
# Every map is for Lithuanian transcripts; other languages pass through.
MAP_LANGUAGE = "lt"

_COMPILED: dict[str, list[tuple[re.Pattern[str], str]]] = {
    source: [(re.compile(pattern), repl) for pattern, repl in rules]
    for source, rules in SPELLING_MAPS.items()
}


def spelling_map(source: str, language: str) -> Callable[[str], str] | None:
    """The text rewrite for ``source``'s transcripts in ``language``, or None."""
    rules = _COMPILED.get(source)
    if rules is None or language != MAP_LANGUAGE:
        return None

    def apply(text: str) -> str:
        for pattern, repl in rules:
            text = pattern.sub(repl, text)
        return text

    return apply


class MappedStreamingParser(StreamingParser):
    """A :class:`StreamingParser` that applies one model's spelling map first.

    The map runs on each fed segment, so the parser (and its held-reference
    context) only ever sees the corrected text.
    """

    def __init__(self, *args, spelling_map: Callable[[str], str], **kwargs):
        super().__init__(*args, **kwargs)
        self.spelling_map = spelling_map

    def feed(self, segment: str, t_s: float | None = None) -> list[Reference]:
        return super().feed(self.spelling_map(segment), t_s)
