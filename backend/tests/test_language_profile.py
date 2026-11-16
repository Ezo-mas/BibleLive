"""Segment-boundary language hooks preserve the streaming parser's rules."""

import pytest

from core.verse_parser import EnglishProfile, Reference, ReferenceParser, StreamingParser
from core.verse_parser_lt import LithuanianProfile


@pytest.mark.parametrize("first", ["viena", "vienas"])
@pytest.mark.parametrize("following", [20, "nuo", "skyrius", "eilute"])
@pytest.mark.parametrize("carry", [["jobo"], ["kolosiec", 3]])
def test_lt_adjusts_one_only_in_citation_slot(first, following, carry):
    new = [first, following]
    assert LithuanianProfile().adjust_segment(new, carry, {"nuo"}, {"skyrius"}, {"eilute"}) == [
        1,
        following,
    ]
    assert new == [first, following]


@pytest.mark.parametrize(
    ("new", "carry"),
    [
        ([], ["jobo"]),
        (["viena", "moteris"], ["jobo"]),
        (["vienas", 20], ["pasakojo"]),
        (["vienas", 20], []),
        (["vienas", "a", "b", "c", 20], ["jobo"]),
        ([2, 20], ["jobo"]),
    ],
)
def test_lt_leaves_other_segment_starts_untouched(new, carry):
    assert LithuanianProfile().adjust_segment(new, carry, set(), set(), set()) is new


def test_lt_trims_dangling_carry_only_before_a_number():
    profile = LithuanianProfile()
    carry = ["jono", "skyrius", 12, "eilute", "tvinty", "dvide"]
    assert profile.trim_carry(carry, [20, 22]) == carry[:4]
    assert profile.trim_carry(carry, ["zodis"]) is carry
    assert profile.trim_carry(carry, []) is carry
    complete = ["jono", "skyrius", 12, "eilute", 20]
    assert profile.trim_carry(complete, [22]) is complete


@pytest.mark.parametrize(
    ("verse", "verse_end", "toks", "end", "expected"),
    [
        (2, None, ["skyrius", 11, 2], 3, True),
        (9, None, ["skyrius", 11, 9], 3, True),
        (1, None, ["skyrius", 11, 1], 3, False),
        (10, None, ["skyrius", 11, 10], 3, False),
        (None, None, ["skyrius", 11], 2, False),
        (3, 5, ["skyrius", 11, 3], 3, False),
        (3, None, ["skyrius", 11, 3, "zodis"], 3, False),
        (3, None, ["skyrius", 11, "trys"], 3, False),
        (3, None, ["skyrius", "vienuolika", 3], 3, False),
        (3, None, ["romiec", 11, 3], 3, False),
        (3, None, [11, 3], 2, False),
    ],
)
def test_lt_cut_off_verse_guards(verse, verse_end, toks, end, expected):
    ref = Reference("Romans", 11, verse, verse_end, end=end)
    assert LithuanianProfile().cut_off_verse(ref, toks, {"skyrius"}) is expected


def test_english_hooks_are_no_ops_even_with_lt_inputs():
    profile = EnglishProfile()
    new = ["vienas", "eilute", 20]
    carry = ["jobo", "skyrius", 1, "eilute", "dvide"]
    assert profile.adjust_segment(new, carry, {"nuo"}, {"skyrius"}, {"eilute"}) is new
    assert profile.trim_carry(carry, [20]) is carry
    assert not profile.cut_off_verse(
        Reference("Romans", 11, 3, end=3), ["skyrius", 11, 3], {"skyrius"}
    )


@pytest.mark.parametrize(
    ("language", "profile_type"), [("en", EnglishProfile), ("lt", LithuanianProfile)]
)
def test_stream_selects_profile_from_supplied_parser(language, profile_type):
    stream = StreamingParser(parser=ReferenceParser(language=language))
    assert isinstance(stream._profile, profile_type)
