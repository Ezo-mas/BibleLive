"""Tests for the Lithuanian verse parser (core.verse_parser with language="lt").

Canonical names/chapter counts mirror the English BOOKS, so the same
Bible database and validator work unchanged. Diacritics may be present
or absent in transcripts; both parse.
"""

import pytest

from core.verse_parser import BOOKS as EN_BOOKS
from core.verse_parser import Reference, ReferenceParser, StreamingParser
from core.verse_parser_lt import BOOKS as LT_BOOKS
from core.verse_parser_lt import number_word
from core.verse_text import VerseTextIndex

P = ReferenceParser(language="lt")


def refs(text):
    return [str(r) for r in P.parse(text)]


def one(text):
    found = P.parse(text)
    assert len(found) == 1, f"expected 1 reference in {text!r}, got {[str(r) for r in found]}"
    return found[0]


class TestBookData:
    def test_66_books_match_english_canon(self):
        assert [(name, n) for name, n, _ in LT_BOOKS] == [(name, n) for name, n, _ in EN_BOOKS]

    def test_unknown_language_rejected(self):
        try:
            ReferenceParser(language="de")
        except ValueError as e:
            assert "de" in str(e)
        else:
            raise AssertionError("expected ValueError for language='de'")


class TestNumbers:
    def test_spoken_numbers(self):
        # (folded) ordinals, teens, tens+units all read as numbers
        r = one("Jono evangelija trecias skyrius sesiolikta eilute")
        assert str(r) == "John 3:16"
        r = one("Romiečiams aštuntas skyrius dvidešimt aštunta eilutė")
        assert str(r) == "Romans 8:28"
        # hundred + teen: "šimtas devyniolikta psalmė" -> Psalms 119
        r = one("šimtas devyniolikta psalmė")
        assert str(r) == "Psalms 119"

    def test_written_numbers(self):
        assert refs("Jono 3:16") == ["John 3:16"]
        assert refs("Jono 3 skyrius 16 eilutė") == ["John 3:16"]

    def test_number_word_forms(self):
        # one stem, many endings: aštuntą / aštuntame / aštuntasis
        for ending in ("a", "ame", "as", "ojo", "oji"):
            r = P.parse(f"Jono 3 astunt{ending} eilutė")
            assert [str(x) for x in r] == ["John 3:8"], f"ending {ending!r}"


class TestExplicit:
    def test_docstring_examples(self):
        r = one("Jono evangelijos 3 skyrius, 16 eilutė")
        assert str(r) == "John 3:16"
        assert r.confidence == "high"

        r = one("Laiškas romiečiams, aštuntas skyrius, dvidešimt aštunta eilutė")
        assert str(r) == "Romans 8:28"

        r = one("Pirmasis laiškas korintiečiams 13, 4-7")
        assert str(r) == "1 Corinthians 13:4-7"

        r = one("Dvidešimt trečioji psalmė")
        assert str(r) == "Psalms 23"

        r = one("Pradžios knygos pirmas skyrius")
        assert str(r) == "Genesis 1"

        r = one("Evangelija pagal Matą 5:3")
        assert str(r) == "Matthew 5:3"

    def test_postpositional_reordering(self):
        assert refs("Romiečiams 8 skyrius 28 eilutė") == ["Romans 8:28"]
        assert refs("Romiečiams 8 skyrius 4 iki 7 eilutės") == ["Romans 8:4-7"]

    def test_ranges(self):
        assert refs("hebrajams 11 1 iki 3") == ["Hebrews 11:1-3"]
        assert refs("Pradžios knyga 1 skyrius 1 ir 2 eilutės") == ["Genesis 1:1-2"]

    def test_no_diacritics(self):
        assert refs("Laiskas romieciams, astuntas skyrius, dvidesimt astunta eilute") == [
            "Romans 8:28"
        ]

    def test_numbered_books(self):
        assert refs("Antrasis laiškas korintiečiams 5 skyrius") == ["2 Corinthians 5"]
        assert refs("1 Korintiečiams 13 skyrius") == ["1 Corinthians 13"]
        assert refs("Trečias Jono laiškas 1 eilutė") == ["3 John 1:1"]

    def test_single_chapter_books(self):
        r = one("Judo laiškas 3 eilutė")
        assert str(r) == "Jude 1:3"

    def test_single_chapter_speaker_says_chapter(self):
        # Speaker numbers verses as chapters ("20 skyrius, 21 eilutė" for
        # Jude 1:20-21): the "chapter" becomes verse_start, the verse number
        # the range end.
        r = one("Ir noriu pradėti nuo Judo Laiško 20. Skyrius, 21. eilutės.")
        assert str(r) == "Jude 1:20-21"
        r = one("Judo 20 skyrius")
        assert str(r) == "Jude 1:20"

    def test_isaiah_spelling_variants(self):
        # "Izaijo"/"Izaijas" (correct) and the STT variant "Izajo" all match.
        assert refs("Izaijo 1,16 sako") == ["Isaiah 1:16"]
        assert refs("Izaijo, pirmas skyrius, šešioliktai, lūtė") == ["Isaiah 1:16"]
        assert refs("Izajo, pirmas skyrius, šešioliktai, lūtė") == ["Isaiah 1:16"]


class TestStreaming:
    def test_context_between_segments(self):
        sp = StreamingParser(language="lt")
        first = sp.feed("atsiverskime Jono evangeliją trečią skyrių")
        assert [str(r) for r in first] == ["John 3"]
        second = sp.feed("šešiolikta eilutė sako")
        assert [str(r) for r in second] == ["John 3:16"]

    def test_postpositional_across_segments(self):
        # "...trečias skyrius" + "šešiolikta eilutė" must combine even when
        # the verse word arrives in the next segment (postprocess re-runs
        # over carry + new tokens)
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("Jono evangelija 3")] == ["John 3"]
        got = [str(r) for r in sp.feed("16 eilutė")]
        assert got == ["John 3:16"], got

    def test_reset(self):
        sp = StreamingParser(language="lt")
        sp.feed("Romiečiams 8 skyrius")
        sp.reset()
        assert [str(r) for r in sp.feed("16 eilutė")] == []


class TestContextTTL:
    """The running book/chapter context expires `context_ttl_s` after the
    last reported reference, so a stale book can't claim stray numbers
    minutes later. Untimestamped feeds keep the legacy never-expire
    behavior."""

    def test_continuation_within_ttl(self):
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("atsiverskime Jono evangeliją trečią skyrių", 0.0)] == [
            "John 3"
        ]
        assert [str(r) for r in sp.feed("šešiolikta eilutė sako", 10.0)] == ["John 3:16"]

    def test_continuation_past_ttl_resets(self):
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("atsiverskime Jono evangeliją trečią skyrių", 0.0)] == [
            "John 3"
        ]
        assert sp.feed("šešiolikta eilutė sako", 601.0) == []

    def test_long_chain_persists_minutes(self):
        # Sermon read-throughs cite verses minutes apart with the book
        # named once: context lives 600 s now, not 60.
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("atsiverskime Pradžios knygą 22 skyrių", 0.0)] == [
            "Genesis 22"
        ]
        assert [str(r) for r in sp.feed("13 eilutė", 400.0)] == ["Genesis 22:13"]

    def test_utterance_chapter_beats_stale_context(self):
        # "6 skyrius ... 24 eilutė" with 2 Kings 3 running resolves to 6:24,
        # and the same-utterance chapter fixes the bare pair after it.
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("antrą karalių knygą trečias skyrius", 0.0)] == [
            "2 Kings 3"
        ]
        assert [
            str(r) for r in sp.feed("šeštas skyrius nuo dvidešimt ketvirtos eilutės", 200.0)
        ] == ["2 Kings 6:24"]

    def test_context_pair_and_jute(self):
        # Consecutive pair in the context path ("6 7 eilutę") and the
        # "jūtė" verse word ("26 jūtė") both resolve against running context.
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("Pradžios knyga 22 skyrius", 0.0)] == ["Genesis 22"]
        assert [str(r) for r in sp.feed("6 7 eilutę skaitome", 100.0)] == ["Genesis 22:6-7"]
        assert [str(r) for r in sp.feed("26 jūtė", 200.0)] == ["Genesis 22:26"]

    def test_izaiu_accusative(self):
        assert refs("Iš Izaių 26 skyrius") == ["Isaiah 26"]

    def test_vuko_for_luko_and_ute_verse_word(self):
        # STT writes V for L ("Vuko evangelija"); "ūtė" is the lūtė-mangle
        # with the L dropped too.
        assert refs(
            "Ir jie atėjo pas viešpatį ir vuko septintas skyrius, trisdešimt šešta, eilutė"
        ) == ["Luke 7:36"]
        assert refs("Vuko evangelija septintas skyrius") == ["Luke 7"]
        assert refs("Luko 7 36 ūtėje") == ["Luke 7:36"]

    def test_split_citation_joins_within_ttl(self):
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("Mato 24", 0.0)] == ["Matthew 24"]
        assert [str(r) for r in sp.feed("23 eilutė", 10.0)] == ["Matthew 24:23"]

    def test_split_citation_dropped_past_ttl(self):
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("Mato 24", 0.0)] == ["Matthew 24"]
        assert sp.feed("23 eilutė", 700.0) == []

    def test_ttl_none_disables_expiry(self):
        sp = StreamingParser(language="lt", context_ttl_s=None)
        assert [str(r) for r in sp.feed("Mato 24", 0.0)] == ["Matthew 24"]
        assert [str(r) for r in sp.feed("23 eilutė", 10000.0)] == ["Matthew 24:23"]

    def test_carry_redetection_does_not_refresh_ttl(self):
        # "Romans 8" is only re-found inside the carry-over while "amen" keeps
        # arriving; that must not count as a new reported reference, or the
        # stale context would survive past the TTL. Pinned at an explicit
        # short TTL so the default (600 s) never masks it.
        sp = StreamingParser(language="lt", context_ttl_s=60.0)
        assert [str(r) for r in sp.feed("Romiečiams 8 skyrius", 0.0)] == ["Romans 8"]
        assert sp.feed("amen", 55.0) == []
        assert sp.feed("amen", 110.0) == []
        assert sp.feed("amen", 165.0) == []
        assert sp.feed("28 eilutė", 220.0) == []


class TestEnglishUnaffected:
    def test_english_default(self):
        en = ReferenceParser()
        assert en.language == "en"
        assert [str(r) for r in en.parse("Romans 8:28-30")] == ["Romans 8:28-30"]

    def test_english_streaming(self):
        sp = StreamingParser()
        assert [str(r) for r in sp.feed("turn to Romans")] == []
        assert [str(r) for r in sp.feed("chapter eight verse twenty-eight")] == ["Romans 8:28"]


class TestObservedLiveMisses:
    """Phrasings from the 07-05 / 09-13 live recordings that used to drop the verse."""

    def test_nuo_introduces_verse_without_eilute(self):
        assert refs("išėjimo 16 nuo 16 iki 20") == ["Exodus 16:16-20"]
        assert refs("Ozėjo knygų 4 skyrius nuo šešiolikto") == ["Hosea 4:16"]

    def test_nuo_does_not_invent_verses_in_plain_speech(self):
        assert refs("nuo 3 iki 5 valandos") == []

    def test_nuo_intro_is_medium_confidence(self):
        # "nuo" alone announces a verse; that is less certain than an explicit
        # "eilutė", so it must not be reported as high confidence.
        found = P.parse("Mato 5 nuo 3 iki 5 valandos")
        verse_refs = [r for r in found if r.verse_start is not None]
        assert [str(r) for r in verse_refs] == ["Matthew 5:3-5"]
        assert verse_refs[0].confidence == "medium"
        found = P.parse("Išėjimo 16 nuo 16 iki 20")
        assert [str(r) for r in found] == ["Exodus 16:16-20"]
        assert found[0].confidence == "medium"

    def test_psalm_number_before_psalm_word_keeps_psalm_book(self):
        assert refs("119 psalmė 142 eilutė") == ["Psalms 119:142"]

    def test_first_john_without_laiskas(self):
        assert refs("atsiverskim 1 Jono 5 skyriu 6 eilutė") == ["1 John 5:6"]
        assert refs("1 Jono 5 6") == ["1 John 5:6"]
        assert refs("Jono 3 16") == ["John 3:16"]

    def test_mangled_skyrius_still_reads_as_chapter_word(self):
        assert refs("Mato 24 skirius 23 eilutė") == ["Matthew 24:23"]


class TestOrdinalCaseForms:
    """Genitive and other oblique-case ordinals/cardinals used for chapters
    and verses ("nuo sesiolikto", "dvidešimt pirmos eilutės"), with and
    without diacritics."""

    # value -> real inflected forms (nominative, genitive, dative, accusative,
    # instrumental, locative, definite)
    FORMS = {
        1: "pirmas pirmasis pirmo pirmos pirmam pirma pirmu pirmuoju pirmame pirmoje pirmosios",
        2: "antras antrasis antro antros antram antra antru antruoju antroje antrojo",
        3: "trecias treciasis trecio trecios treciam trecia treciu treciuoju trecioje",
        4: "ketvirtas ketvirtasis ketvirto ketvirtos ketvirtam ketvirta ketvirtu ketvirtame",
        5: "penktas penktasis penkto penktos penktam penkta penktu penktoje penktoji",
        6: "sestas sestasis sesto sestos sestam sesta sestu sestoje sestojo",
        7: "septintas septintasis septinto septintos septintam septinta septintu septintoje",
        8: "astuntas astuntasis astunto astuntos astuntam astunta astuntu astuntoje",
        9: "devintas devintasis devinto devintos devintam devinta devintu devintoje",
        10: "desimtas desimtasis desimto desimtos desimtam desimta desimtu desimtoje",
        11: "vienuoliktas vienuolikto vienuoliktos vienuolikta vienuoliktoje vienuoliktajam",
        12: "dvyliktas dvylikto dvyliktos dvylikta dvyliktame dvyliktoji",
        14: "keturioliktas keturiolikto keturioliktos keturiolikta keturioliktame",
        15: "penkioliktas penkiolikto penkioliktos penkiolikta penkioliktoje",
        16: "sesioliktas sesiolikto sesioliktos sesiolikta sesioliktame sesioliktoje",
        17: "septynioliktas septyniolikto septynioliktos septyniolikta",
        18: "astuonioliktas astuoniolikto astuonioliktos astuoniolikta",
        19: "devynioliktas devyniolikto devynioliktos devyniolikta",
        20: "dvidesimtas dvidesimto dvidesimtos dvidesimta dvidesimtame dvidesimtoji",
        30: "trisdesimtas trisdesimto trisdesimtos trisdesimta trisdesimtoje",
        100: "simtas simtasis simto simtos simtam simta simtu simtoje simtojo",
    }

    def test_oblique_ordinals_read_to_their_value(self):
        for value, forms in self.FORMS.items():
            for word in forms.split():
                got = number_word(word)
                assert got is not None and got[0] == value, f"{word!r} -> {got}, want {value}"

    def test_folded_and_diacritic_forms_match(self):
        # "šešioliktos" and "sesioliktos" are the same word to the parser
        assert number_word("šešioliktos") == number_word("sesioliktos") == (16, "teen")
        assert number_word("trečiosios") == number_word("treciosios") == (3, "unit")

    def test_compound_tens_and_units_in_genitive(self):
        assert refs("Jono 3 skyrius dvidešimt pirmos eilutės") == ["John 3:21"]
        assert refs("Jono 3 skyrius trisdešimt šeštos eilutės") == ["John 3:36"]
        assert refs("Psalmės šimto dvidešimt pirmo") == ["Psalms 121"]

    def test_ordinal_chapter_and_verse_sentences(self):
        assert str(one("Ozejo knygu 4 skyrius nuo sesiolikto")) == "Hosea 4:16"
        assert str(one("Luko 14 skyrius nuo sesioliktos eilutes")) == "Luke 14:16"
        assert str(one("Pirmo Jono laisko 5 skyriaus 6 eilute")) == "1 John 5:6"
        assert str(one("Pradžios knygos pirmo skyriaus dvidešimt pirmos eilutės")) == "Genesis 1:21"
        assert (
            str(one("Romiečiams aštuntame skyriuje dvidešimt aštuntoje eilutėje")) == "Romans 8:28"
        )

    def test_nuo_with_ordinal_verse_and_range(self):
        assert refs("Jono 3 skyrius nuo trečios iki penktos") == ["John 3:3-5"]
        assert refs("Mato 5 skyrius nuo penktos eilutės") == ["Matthew 5:5"]

    def test_no_false_positives_from_number_stems(self):
        # words that share a number stem but are not number words
        assert refs("sesijos metu sestas žmogus") == []
        assert refs("simtas žmonių atėjo") == []
        assert refs("nuo trečios iki penktos valandos") == []
        assert refs("nuo sesiolikto metų") == []

    def test_dropped_letter_ordinals(self):
        # STT drops letters inside ordinals ("sepinta", "štuntos") and off
        # the front of book names ("Zajo"): all seen in benchmark misses.
        assert number_word("sepinta") == (7, "unit")
        assert number_word("štuntos") == (8, "unit")
        assert number_word("septantai") == (7, "unit")
        assert refs("Tai būtų antras laiškas Korintiečiams, devintas skyrius, sepinta eilutė.") == [
            "2 Corinthians 9:7"
        ]
        assert refs(
            "Ir noriu paskaityti antras Korintiečiams, devintas skyrius, septantai eilutė."
        ) == ["2 Corinthians 9:7"]
        # ("antram Korintiečių laiškė" is stem-first inflected order; a bare
        # "Korintiečiams" with no number stays silent, 1 vs 2 ambiguous.)
        assert refs(
            "Jis kalba antram Korintiečių laiškė, Korintiečiams pirmas skyrius nuo štuntos eilutės."
        ) == ["2 Corinthians 1:8"]
        assert refs("kas mane įkvėpi į Zajo 58 skyrius") == ["Isaiah 58"]

    def test_bare_verse_stutter_before_nuo(self):
        # "Luko 11, 20 nuo 24": the "nuo 24" restates the verse and the
        # bare 20 is STT stutter. Genuine "nuo A iki B" ranges never have
        # a bare number before "nuo", so they are untouched.
        assert refs("tai Luko 11, 20 nuo 24, sako") == ["Luke 11:24"]
        assert refs("išėjimo 16 nuo 16 iki 20") == ["Exodus 16:16-20"]
        assert refs("Jono 3 skyrius nuo trečios iki penktos") == ["John 3:3-5"]

    def test_acts_apostle_spellings(self):
        # The table only had the o-form ("apostolų"); the standard a-form
        # ("apaštalų") never matched, losing every Acts citation from
        # a-form speakers. Bare "apaštalas" (the person) stays silent.
        assert refs("Ir apaštolų darbai pirmam skyriui, ketvirtą penktą") == ["Acts 1:4-5"]
        assert refs("Apaštalų darbu, taip pat pirmas skyrius, keturioliktoji") == ["Acts 1:14"]
        assert refs("paskaitoma paštalų 2.4") == ["Acts 2:4"]
        assert refs("apaštalas Jonas") == []
        # The people, not the book: nominative "paštalai" never folds.
        assert refs("jie tarnavo paštalai pirmoji bažnyčioj") == []
        # Bare genitive book ("apaštalų 2.4") reads Acts; other bare
        # case forms stay people.
        assert refs("paskaitom apaštalų 2.4") == ["Acts 2:4"]
        assert refs("apaštalas Petras sako") == []

    def test_dropped_u_teen_and_nonconsecutive_ir_pair(self):
        # "ketrolika" (dropped 'u') and "ketruoliktojai" (dropped 'i')
        # complete the 14 family; "antra ir ketvirta" names 2-4 only
        # with a verse word watching (hours never merge).
        assert refs("pirmas laiškas Korintiečiams, ketrolika, antra ir ketvirtai lūtes") == [
            "1 Corinthians 14:2-4"
        ]
        assert refs("Apaštalų darbu, taip pat pirmas skyrius, ketruoliktojai lūtė") == ["Acts 1:14"]
        # Far pairs never merge, whatever follows.
        assert refs("Jono 3 16 ir 25") == ["John 3:16"]

    def test_truncated_and_dropped_letters_in_teens(self):
        # "Aštiniolika" (dropped 'u') and truncated "dvidešim" both read
        # 18 / 20, and the truncated tens still compounds ("dvidešim
        # vienas" is 21, so the restated pair resolves).
        assert number_word("Aštiniolika") == (18, "teen")
        assert number_word("dvidešim") == (20, "tens")
        assert refs("Ir Patarlių, Aštiniolika, dvidešimt dvidešim vienas.") == ["Proverbs 18:20-21"]

    def test_bare_consecutive_verse_pair(self):
        # Two verse numbers with no joiner ("ketvirtą penktą") name a
        # range, but only when consecutive: "25 28" stays a single verse.
        assert refs("Ir apaštolų darbai pirmam skyriui, ketvirtą penktą") == ["Acts 1:4-5"]
        assert refs("Luko 10 25 28") == ["Luke 10:25"]

    def test_mato_verb_forms_are_not_matthew(self):
        # "matom" (we see) folds onto the "mato" stem; the verb forms
        # must not become the book. Bare "mato" itself stays Matthew.
        assert refs("mes matom sitas antras") == []
        assert refs("Mato 5 3") == ["Matthew 5:3"]

    def test_same_key_repeats_apart_restatements_collapse(self):
        # Adjacent restatement is one citation; the same key farther
        # apart is two (carry-joined repeats score separately).
        assert refs("Mato 17 2. Skaitome toliau. Mato 17 2") == [
            "Matthew 17:2",
            "Matthew 17:2",
        ]


class TestBareBookBeforeNumberedBook:
    """A bare book name right before a numbered book ("Jono, pirmo Jono
    laiško 5 ...") must not produce a ghost "John 1" from the prefix."""

    def test_bare_jono_then_first_john(self):
        assert refs("Jono, pirmo Jono laiško 5 skyrius nuo 21 eilutės") == ["1 John 5:21"]
        assert refs("Jono pirmo Jono laiško 5 skyrius nuo 21 eilutės") == ["1 John 5:21"]

    def test_chapter_only_book_before_other_book_kept(self):
        assert refs("Jono 3 Mato 5") == ["John 3", "Matthew 5"]

    def test_chapter_only_ref_followed_by_bare_jono_is_not_a_restart(self):
        # "Jono 1" followed by a plain "Jono" is not "1 Jono": only a numbered
        # book name (prefix + name + label word) restarts the chapter-only ref.
        assert refs("Luko 1, Jono 3 skyrius 16 eilutė") == ["Luke 1", "John 3:16"]
        assert refs("Mato 1, Jono 1") == ["Matthew 1", "John 1"]
        assert refs("Jono 1, Jono 3") == ["John 1", "John 3"]

    def test_streaming_chapter_only_then_bare_jono(self):
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("Luko 1 skyrius")] == ["Luke 1"]
        assert [str(r) for r in sp.feed("Jono 3 skyrius 16 eilutė")] == ["John 3:16"]

    def test_chapter_word_before_the_number_is_not_a_restart(self):
        # "1 skyrius" becomes [skyrius, 1]; that chapter 1 must not read as the
        # prefix of the following book name.
        assert refs("Pradžios 1 skyrius, Karalių knyga 3") == ["Genesis 1"]
        assert refs("Luko 2 skyrius, Petro laiškas 3") == ["Luke 2"]
        assert refs("Jono 1 skyrius, Jono laiškas 5") == ["John 1"]
        assert refs("Pradžios knygos 1 skyrius, Jono laiške 4 skyriuje") == ["Genesis 1"]
        assert refs("Luko 1 skyriuje, Jono laiške rašoma") == ["Luke 1"]
        assert refs("Jono 3 skyriuje Jono laiško mintis") == ["John 3"]


class TestBenchmarkMicroGaps:
    """Misses read off the medium-model transcripts of the 07-05 / 09-13
    recordings (see benchmarks/): each phrasing below was spoken as a real
    reference and came out chapter-only, attached to the wrong book, or split."""

    def test_dash_joined_chapter_verse(self):
        assert refs("Morkaus 12-30 sako, milėk") == ["Mark 12:30"]
        assert refs("Morkaus 12–30") == ["Mark 12:30"]

    def test_dash_between_plausible_chapters_stays_a_chapter(self):
        # Romans has 16 chapters, so "8-12" may be a chapter span: no verse invented.
        assert refs("Romiečiams 8-12") == ["Romans 8"]

    def test_mangled_chapter_and_verse_words(self):
        assert refs("Mato 24 skyris, 23 eilutė") == ["Matthew 24:23"]
        assert refs("Efeziečiams, ketvirtas skyrus 25 lūtė") == ["Ephesians 4:25"]
        assert refs("Psalmė, šešiolikta skyrius, aštuntai ilutė") == ["Psalms 16:8"]

    def test_skaiciau_is_a_verb_not_the_book_numbers(self):
        # "Kurią aš skaičiau 24" = "which I read 24"; it also leaked a ghost
        # context that turned the next "šešiolikta eilutė" into Numbers 24:16.
        assert refs("Kurią aš skaičiau 24. sakau, jums") == []
        sp = StreamingParser(language="lt")
        assert sp.feed("Kurią aš skaičiau 24.") == []
        assert sp.feed("Šešiolikta eilutė ir buvo pakvęsti") == []

    def test_book_numbers_still_recognised(self):
        assert refs("Skaičių 24 skyrius 16 eilutė") == ["Numbers 24:16"]
        assert refs("Skaičių knyga 33 skyrius 50") == ["Numbers 33:50"]
        assert refs("Skaičiai 14 skyrius") == ["Numbers 14"]

    def test_locative_book_name_is_not_a_verb(self):
        # "Skaičiuose" (in Numbers) starts with the verb-ish "skaičiuo" stem
        assert refs("Skaičiuose 24 skyriuje 16 eilutėje") == ["Numbers 24:16"]

    def test_psalm_word_repeated_before_verse(self):
        # "psalmynė 119 psalmė, 142 eilutė": the second psalm word is a stutter
        # of the first, not a new psalm; 142 is the verse.
        assert refs("Ir psalmynė 119 psalmė 142 eilutė") == ["Psalms 119:142"]

    def test_chapter_word_after_psalm_number_is_redundant(self):
        assert refs("Psalmė 16 skyrius 8 eilutė") == ["Psalms 16:8"]
        assert refs("Psalmė 16") == ["Psalms 16"]
        assert refs("Psalmė 16 skyrius") == ["Psalms 16"]


class TestWhisperMergedAndGluedWords:
    """Misses read off the small-model transcripts of 09-13 / 07-05: Whisper
    merged chapter and verse into one number, or glued two words together."""

    def test_merged_chapter_and_verse_number(self):
        # Philippians has 4 chapters, so 46 can only be 4:6.
        assert refs("Filipiečiams, 46. rašo") == ["Philippians 4:6"]
        assert refs("pirmas Jono 56. Jis") == ["1 John 5:6"]
        # Ephesians has 6 chapters: 42:5 is impossible, 4:25 is the reading.
        assert refs("Efeziečiams 425, sako") == ["Ephesians 4:25"]
        assert refs("Jono 316") == ["John 3:16"]

    def test_merged_number_is_a_low_confidence_guess(self):
        assert one("Filipiečiams 46").confidence == "low"

    def test_merged_number_with_two_readings_is_not_guessed(self):
        # Psalm 1191: 11:91 and 119:1 are both valid, so nothing is invented.
        assert refs("Psalmė 1191") == []

    def test_spoken_chapter_word_blocks_the_split(self):
        assert refs("Filipiečiams 46 skyrius") == []

    def test_valid_chapter_is_never_split(self):
        assert refs("Romiečiams 12") == ["Romans 12"]

    def test_nuo_glued_to_a_number_word(self):
        assert refs("išėjimo 20. Skyrius nuotrėčios, nedarik") == ["Exodus 20:3"]

    def test_no_is_whisper_for_nuo(self):
        # Without it "no" split the chapter from its verse range and an extra
        # chapter-only "Exodus 16" was reported.
        assert refs("išėjimo šešioliką, no šešioliktos iki 20. eilutės") == ["Exodus 16:16-20"]

    def test_pagal_glued_to_a_book_name(self):
        assert refs("evangelija pagaluką 12 skyrius 29 eilutė") == ["Luke 12:29"]

    def test_joinerless_verse_pair_after_chapter_word(self):
        # "devinta dešimta eilutė" after an explicit chapter: verses 9 to 10.
        assert refs("Pirmoje metraščių knygoje ketvirtame skyriuje devinta dešimta eilutė") == [
            "1 Chronicles 4:9-10"
        ]
        assert refs("Romiečiams 8 skyrius 4 7 eilutės") == ["Romans 8:4-7"]
        # Words between the book name and its chapter ("apie jį") do not
        # break the parse either.
        assert refs(
            "Pirmoje metraščių knygoje, apie jį, ketvirtame skyriuje, devinta dešimta eilutė"
        ) == ["1 Chronicles 4:9-10"]

    def test_gap_skip_stops_at_numbers_and_books(self):
        # "Mato" stops the skip: "Jono" finds nothing, "Mato 5" still parses.
        assert refs("Jono. Mato 5 skyrius") == ["Matthew 5"]
        # A number stops the skip and attaches locally: "Luko" takes 5, and
        # the farther "6 skyrius" is left alone, not stolen.
        assert refs("Luko. Penkis kartus 6 skyrius") == ["Luke 5"]
        # A postpositional label word bridges to its chapter: previously
        # nothing parsed here at all.
        assert refs("Romiečiams laiške 8 skyrius 28 eilutė") == ["Romans 8:28"]
        # ...including when a bare number follows the label ("laiškas,
        # pirma septyni"), for books whose name can't hide a dropped digit.
        assert refs("Antras Timotiejui, laiškas, pirma septyni") == ["2 Timothy 1:7"]
        assert refs(
            "Antras Timotiejui, laiškas, pirma septyni, pirmas skyrius septintai lūtėje."
        ) == ["2 Timothy 1:7"]

    def test_bare_jono_label_stays_silent(self):
        # "Jono laiškas" without its digit could be 1/2/3 John: no bridge,
        # no invented John 5.
        assert refs("Jono laiškas 5") == []

    def test_dropped_letter_forms(self):
        # STT drops initial letters ("Feziečiams", "20 kyriaus") and writes
        # odd genitives ("laiškio"): all normalize onto known stems/words.
        assert refs("Judo laiško 20 kyriaus 21 eilutė") == ["Jude 1:20-21"]

    def test_bare_chapter_verse_pair_is_not_a_range(self):
        # No chapter word: "Jono 3 16 eilutė" is chapter 3, verse 16.
        assert refs("Jono 3 16 eilutė") == ["John 3:16"]

    def test_glued_verse_digits_split_when_verse_cannot_exist(self):
        # "126 psalmės ... 56 eilutė": Psalm 126 has 6 verses, so 56 must be
        # verses 5 and 6 glued together.
        only_real = ReferenceParser(
            language="lt",
            validator=lambda r: (r.book, r.chapter, r.verse_start or 0) != ("Psalms", 126, 56),
        )
        assert [str(r) for r in only_real.parse("126 psalmės, tai būtų 56 eilutė")] == [
            "Psalms 126:5-6"
        ]

    def test_glued_verse_digits_kept_when_verse_exists(self):
        # Psalm 119 really has a verse 56: no split is attempted.
        assert refs("119 psalmė 56 eilutė") == ["Psalms 119:56"]

    def test_glued_verse_digits_with_no_valid_split(self):
        # "50" can only be 5 and 0, and a verse never starts with 0: nothing.
        john3 = ReferenceParser(
            language="lt",
            validator=lambda r: (
                (r.book, r.chapter) == ("John", 3)
                and 1 <= (r.verse_start or 0) <= 36
                and (r.verse_end is None or 1 <= r.verse_end <= 36)
            ),
        )
        assert [str(r) for r in john3.parse("Jono 3 50 eilutė")] == []

    def test_glued_chapter_and_verse_split_twice(self):
        # "289 eilutės" for Ephesians 2:8-9: chapter 2, then verses 8 and 9
        # glued as 89. The plain reading must fail first: "425" stays 4:25.
        eph2 = ReferenceParser(
            language="lt",
            validator=lambda r: (
                (r.book, r.chapter) == ("Ephesians", 2)
                and 1 <= (r.verse_start or 0) <= 22
                and (r.verse_end is None or 1 <= r.verse_end <= 22)
            ),
        )
        assert [str(r) for r in eph2.parse("Feziečiams laiškio 289 eilutės.")] == [
            "Ephesians 2:8-9"
        ]
        eph4 = ReferenceParser(
            language="lt",
            validator=lambda r: (
                (r.book, r.chapter) == ("Ephesians", 4)
                and 1 <= (r.verse_start or 0) <= 32
                and (r.verse_end is None or 1 <= r.verse_end <= 32)
            ),
        )
        assert [str(r) for r in eph4.parse("Efeziečiams 425, sako")] == ["Ephesians 4:25"]

    def test_glued_ordinal_words_split(self):
        # "devintadešimtai" (devinta+dešimta, both ordinal-marked) names
        # verses 9-10; genuine tens ("devyniasdešimt", 90, cardinal first
        # half) never split.
        assert refs("Pradžios 22 devintadešimtai lūtį") == ["Genesis 22:9-10"]
        assert refs("Izaijo 45 devyniasdešimt") == ["Isaiah 45:90"]

    def test_iluti_verse_word(self):
        # "11 12 įlutį": STT writes į for ę in the accusative verse word.
        assert refs("Pradžios 22 11 12 įlutį") == ["Genesis 22:11-12"]

    def test_roman_numeral_chapters(self):
        # Speakers read roman numerals off ("XIX skyrius"); lone "i" is
        # the preposition "į" and stays out.
        assert number_word("xix") == (19, "unit")
        assert number_word("iv") == (4, "unit")
        assert number_word("i") is None
        assert number_word("v") is None
        assert refs("Pirmo Karalių knyga, XIX skyrius, aprašytas") == ["1 Kings 19"]

    def test_held_chapter_joins_next_verse(self):
        # 1 Kings 19:7 across the segment split, through the chapter hold.
        sp = StreamingParser(language="lt", hold_chapter_only=True)
        assert sp.feed("Pirmo Karalių knyga, XIX skyrius, aprašytas", 4080.0) == []
        assert [str(r) for r in sp.feed("Čia sako nuo septintos eilutės.", 4095.0)] == [
            "1 Kings 19:7"
        ]

    def test_range_end_completes_across_segments(self):
        # "Iki 16 eilutės" after "... nuo aštuntos": the carry holds the
        # start. Hours ("nuo 8 iki 16 valandos") never merge: the verse
        # word is required.
        sp = StreamingParser(language="lt")
        assert [
            str(r) for r in sp.feed("Pirmas Karalių 17 kyriaus ir ten buvo nuo aštuntos", 0.0)
        ] == ["1 Kings 17"]
        assert [str(r) for r in sp.feed("Iki 16 eilutės, paskaitysiu", 10.0)] == ["1 Kings 17:8-16"]
        # Hours never merge without a running book to attach to.
        assert refs("nuo 8 iki 16 valandos vyks") == []

    def test_chapter_word_number_is_verse_of_running_chapter(self):
        # "skyrių aštuntai, lutė": the number is a verse of Acts 1, not a
        # new chapter 8. A bare "[CHAPTER] N" with nothing after stays
        # chapter business as usual.
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("Apaštalų darbai pirmas skyrius", 0.0)] == ["Acts 1"]
        assert [str(r) for r in sp.feed("tam pačiam skyriu aštuntai, lutė", 10.0)] == ["Acts 1:8"]
        sp2 = StreamingParser(language="lt")
        sp2.feed("Apaštalų darbai pirmas skyrius", 0.0)
        assert [str(r) for r in sp2.feed("astuntas skyrius", 10.0)] == []

    def test_ordinary_words_starting_with_nuo_or_pagal_stay_whole(self):
        assert refs("nuodėmės ir nuosprendis, pagalba 3 skyrius") == []


class TestRound13FreshRecording:
    """Misses and false positives read off the 05-31 turbo transcripts."""

    def test_pradziai_dative_is_not_genesis(self):
        # "pradžiai gal" (for starters) ghosted Genesis 32:11 onto a stale
        # chapter; only the genitive "pradžios" is the book. "Pradžioje
        # buvo Žodis" (John 1:1's opening) stays silent too.
        assert refs("Pradžioje buvo Žodis") == []
        assert refs("Ir pradžiai gal paskaitysiu") == []
        assert refs("Pradžios knygos pirmas skyrius") == ["Genesis 1"]

    def test_chapter_before_its_book(self):
        # "32 skyrių Pakartoto įstatymo knyga 11 eilutė": the chapter stands
        # before the book, the verse after it.
        assert refs("paskaitysiu 32 skyrių Pakartoto įstatymo knyga 11 eilutę") == [
            "Deuteronomy 32:11"
        ]
        # Bare "[CHAPTER] N BOOK" with nothing after stays silent.
        assert refs("Jono 1 skyrius, Jono laiškas") == ["John 1"]

    def test_skaicius_bare_count_is_not_numbers(self):
        # "uždėjau skaičius 91" (put the number 91 on a cake): the ordinary
        # noun, not the book. A chapter word nearby still parses.
        assert refs("kai ant torto uždėjau skaičius 91") == []
        assert refs("Skaičiai 14 skyrius") == ["Numbers 14"]

    def test_viena_indefinite_article_is_not_verse_1(self):
        # "dar viena eilutę" (one more verse) with a stale Psalms 91 context
        # phantomed Psalms 91:1. Genuine 1s are "pirma(s)" or digits.
        assert number_word("viena") is None
        assert number_word("vienas") is None
        assert refs("dar viena eilutę paskaitysim iš 103 psalmės penktą eilutę") == ["Psalms 103:5"]
        sp = StreamingParser(language="lt")
        sp.feed("Psalmėje 91, ketvirtą eilutę", 0.0)
        assert [
            str(r)
            for r in sp.feed("dar viena eilutę paskaitysim iš 103 psalmės penktą eilutę", 10.0)
        ] == ["Psalms 103:5"]
        # Tens still compound with it ("dvidešimt vienas" = 21).
        assert refs("Ir Patarlių, Aštiniolika, dvidešimt dvidešim vienas.") == ["Proverbs 18:20-21"]

    def test_jebrajams_is_hebrews(self):
        # STT writes J for H ("Jebrajams" for "Hebrajams").
        assert refs("Jebrajams, ten 12, 12 sita eilute yra") == ["Hebrews 12:12"]

    def test_is_glued_preposition_splits_off_book(self):
        # "Isozejo" ("Iš Ozėjo" glued) splits; ordinary "is"-words stay whole.
        assert refs("Čia yra Isozejo 12, 6") == ["Hosea 12:6"]
        assert refs("istorija apie Dovydą") == []

    def test_pati_emphasis_between_verse_word_and_number(self):
        # "tai eilutė pati 31": the emphasis wedges verse word and number.
        sp = StreamingParser(language="lt")
        sp.feed("Izaijo 40 nuo 20 skaitome", 0.0)
        assert [str(r) for r in sp.feed("tai eilutė pati 31, pagrindinė", 10.0)] == ["Isaiah 40:31"]

    def test_stuttered_verse_restart_joins_range(self):
        # "13 eilutė, 13, 14": the verse number said twice, then the range
        # end. A lone repeat ("Jono 3 16 16") stays single.
        assert refs("yra Jokubo, ketvirtas skyrius, 13 eilutė, 13, 14") == ["James 4:13-14"]
        assert refs("Jono 3 16 16") == ["John 3:16"]

    def test_chapter_prefixed_verse_glue(self):
        # "Efeziečiams du, 256": STT restated the chapter inside the verse
        # number; 56 must be verses 5 and 6 glued (Ephesians 2 has 22 verses).
        eph2 = ReferenceParser(
            language="lt",
            validator=lambda r: (
                (r.book, r.chapter) == ("Ephesians", 2)
                and 1 <= (r.verse_start or 0) <= 22
                and (r.verse_end is None or 1 <= r.verse_end <= 22)
            ),
        )
        assert [str(r) for r in eph2.parse("Biblija sako, Efeziečiams du, 256, kad")] == [
            "Ephesians 2:5-6"
        ]
        # A two-digit rest needs the validator: without it, silence.
        assert refs("Biblija sako, Efeziečiams du, 256, kad") == []
        # A single-digit rest needs none ("205" for chapter 20, verse 5).
        assert refs("Patarlių 20 skyrius 205 eilutė") == ["Proverbs 20:5"]

    def test_distant_chapter_after_book_label(self):
        # "Skaičiau iš Apreiskimo knygos ... norit 12 skyrių": the chapter
        # arrives a clause after the label.
        assert refs("Skaičiau iš Apreiskimo knygos ir jeigu norit 12 skyrių") == ["Revelation 12"]

    def test_backwards_dash_is_chapter_verse(self):
        # "3-2" can never be chapters 3 through 2; a forward pair inside the
        # chapter count ("Romiečiams 8-12") still stays a chapter.
        assert refs("Kolosiečiams, 3-2") == ["Colossians 3:2"]

    def test_postfix_digit_with_label_numbers_the_letter(self):
        # "Timotiejaus 2 laiške" (in the 2nd letter to Timothy): the digit is
        # the letter number, never a chapter. Unnumbered books never match.
        assert refs("skaitome Timotiejaus antram laiške trečiam skyriui") == ["2 Timothy 3"]
        assert refs("Jono 2 laiškas 5") == ["2 John 1:5"]
        assert refs("Romiečiams 2 laiške") == ["Romans 2"]
        # A bare label with no digit still stays silent ...
        assert refs("Jono laiškas 5") == []
        # ... and a chapter word after the digit is still a chapter.
        assert refs("Jono 2 skyrius") == ["John 2"]

    def test_prefixed_bare_jono_is_the_letter(self):
        # A leading 1/2/3 makes bare "jono" the letter, never the gospel.
        assert refs("pirmas Jono keturi nuo 18 eilutės") == ["1 John 4:18"]

    def test_four_ordinary_words_between_book_and_chapter(self):
        # "Mato evangelija vėl apie tuos fariziejus, šešta ...": four
        # ordinary words still bridge; five stay silent. Numbers, book
        # names and chapter/verse words stop the skip either way.
        assert refs(
            "Mato evangelija vėl apie tuos fariziejus, šešta skyrius nuo pirmos eilutės"
        ) == ["Matthew 6:1"]
        assert refs("Mato evangelija vėl apie tuos senuosius fariziejus, šeštas skyrius") == []

    def test_isejau_verb_is_not_exodus(self):
        # "Išėjau pas viešpatį ..." (I went out): the verb must not fold
        # onto the Exodus stem, whatever number follows. The noun still
        # does ("Išėjimo knyga").
        assert refs("Išėjau pas viešpatį mirti. 49 metai daug planų") == []
        assert refs("Išėjimo knyga 20 skyrius") == ["Exodus 20"]

    def test_verse_word_does_not_eat_a_book_prefix(self):
        # "vieną eilutę, 1 Petro 2, 9": the 1 starts 1 Peter; it is not
        # verse 1 of the running chapter.
        sp = StreamingParser(language="lt")
        sp.feed("Jokūbo 1 27", 0.0)
        assert [str(r) for r in sp.feed("Noriu paskityti vieną eilutę, 1 Petro 2, 9", 10.0)] == [
            "1 Peter 2:9"
        ]

    def test_reduced_ir_joiner_and_psalmiai(self):
        # STT writes the unstressed "ir" as "i" ("nuo pirmos i trečios"
        # for 1-3); "psalmiai" (psalms, nom. pl.) is the psalm word.
        assert refs("Jebrajams pirmas skyrius nuo pirmos i trečios eilutės") == ["Hebrews 1:1-3"]
        assert refs("Ir kito psalmiai, 62. devintai lute") == ["Psalms 62:9"]

    def test_numbers_count_after_skipped_words_is_not_a_citation(self):
        # "tie skaičiai ... padaryti 19": the skip loop walks three ordinary
        # words onto a bare count; still not the book. A label on the way
        # ("Skaičių knygos 24") still parses.
        assert refs("norėjo, kad būtų tie skaičiai apsukti ir padaryti 19") == []
        assert refs("Skaičių knygos 24") == ["Numbers 24"]

    def test_small_number_after_book_label_is_a_verse_not_a_split(self):
        # "Apreiskimo knygos ... keturiolikta eilutė": 14 is verse 14 of the
        # uttered chapter, not 1:4 (the 289-style split needs a number no
        # verse can be). Without context it stays silent instead of ghosting.
        assert refs("Apreiskimo knygos keturiolikta eilutė") == []
        sp = StreamingParser(language="lt")
        sp.feed("Apreiskimas Jonui 12 skyrius", 0.0)
        assert [
            str(r)
            for r in sp.feed(
                "keturiolikta iš dvylikto skyriaus Apreiskimo knygos, keturiolikta eilutė",
                10.0,
            )
        ] == ["Revelation 12:14"]


class TestChapterOnlyDuplicates:
    """A bare "Romans 5" next to "Romans 5:6" is a duplicate the operator has to
    discard. Seen in every benchmark run (Romans 5, John 10, Luke 12)."""

    @staticmethod
    def _held():
        return StreamingParser(language="lt", hold_chapter_only=True)

    def test_same_text_drops_the_chapter_only_twin(self):
        # The stray "k." splits chapter from verse: "John 10" plus a verse that
        # context resolves to "John 10:37". Only the full one is kept.
        assert refs("Jono, 10. k. 37. eilutė.") == ["John 10:37"]

    def test_same_text_keeps_a_different_chapter(self):
        assert refs("Jono 10 skyrius, o Jono 11 skyrius 3 eilutė") == ["John 10", "John 11:3"]

    def test_held_chapter_is_dropped_when_the_verse_follows(self):
        sp = self._held()
        assert sp.feed("Romiečiams, 5. Skyrius, mes po truputį") == []
        assert [str(r) for r in sp.feed("6 eilutė sako")] == ["Romans 5:6"]

    def test_held_chapter_is_released_when_no_verse_follows(self):
        sp = self._held()
        assert sp.feed("Romiečiams 5 skyrius") == []
        assert [str(r) for r in sp.feed("ir tada meldėmės")] == ["Romans 5"]

    def test_held_chapter_is_released_before_an_unrelated_reference(self):
        sp = self._held()
        sp.feed("Romiečiams 5 skyrius")
        assert [str(r) for r in sp.feed("Jono 3 skyrius 16 eilutė")] == ["Romans 5", "John 3:16"]

    def test_flush_releases_at_end_of_stream(self):
        sp = self._held()
        sp.feed("Psalmė 23")
        assert [str(r) for r in sp.flush()] == ["Psalms 23"]
        assert sp.flush() == []

    def test_full_references_are_never_held(self):
        assert [str(r) for r in self._held().feed("Jono 3 skyrius 16 eilutė")] == ["John 3:16"]


class TestCitationSlotOne:
    """ "viena(s)" is the article, except where a citation number belongs."""

    def test_after_book_chapter_is_verse_one(self):
        assert refs("Kolosiečiams, Tris, Vienas taip sako") == ["Colossians 3:1"]

    def test_article_stays_silent(self):
        assert refs("dar viena eilutė") == []
        assert refs("Jono 3 16, dar viena eilutė") == ["John 3:16"]

    def test_after_chapter_word_stays_article(self):
        assert refs("Romiečiams 8 skyrius, viena eilutė") == ["Romans 8"]


class TestMoreSttMangles:
    def test_rute_is_a_verse_word(self):
        sp = StreamingParser(ReferenceParser(language="lt"), hold_chapter_only=True)
        sp.feed("Pradžios knyga 22 skyrius 12 eilutė", 0.0)
        assert [str(r) for r in sp.feed("Ir toliau, 17 rūtį, aš laimintę", 10.0)] == [
            "Genesis 22:17"
        ]

    def test_ruth_still_parses(self):
        assert refs("Rūtos knyga 1 skyrius 16 eilutė") == ["Ruth 1:16"]

    def test_afezieciams_is_ephesians(self):
        assert refs("Afeziečiams 4.25 sako") == ["Ephesians 4:25"]


class TestFuzzyMatching:
    """One-letter STT slips in book names and number words, near citations only."""

    def test_misspelled_book_with_verse(self):
        ref = one("Kolosėčiams 3 skyrius 2 eilutė")
        assert (ref.book, ref.chapter, ref.verse_start) == ("Colossians", 3, 2)
        assert ref.confidence != "high"  # a guessed name is never high

    def test_misspelled_book_chapter_only_stays_silent(self):
        assert refs("Kolosėčiams 3 skyrius") == []
        assert refs("teisės 5 skyrius") == []

    def test_misspelled_number_next_to_verse_word(self):
        assert refs("Jono 3 skyrius penkiolka eilutė") == ["John 3:15"]

    def test_misspelled_number_away_from_citation_words_is_text(self):
        assert refs("Jono 3 skyrius. Ten susirinko penkiolka žmonių") == ["John 3"]


class TestPromptSpellingsParse:
    """Every book spelling in the Lithuanian Whisper prompt must parse exactly."""

    def test_job(self):
        assert refs("Jobo 1 skyrius 20 eilutė") == ["Job 1:20"]

    def test_hebrews_standard_spelling_is_exact(self):
        ref = one("Hebrėjams 11 skyrius 1 eilutė")
        assert (str(ref), ref.confidence) == ("Hebrews 11:1", "high")

    def test_every_prompt_book_parses_without_fuzzy(self):
        from audio.stt import WHISPER_INITIAL_PROMPTS

        for item in WHISPER_INITIAL_PROMPTS["lt"].replace(".", ",").split(","):
            name = item.strip()
            if not name or name.lower() in ("skyrius", "eilutė"):
                continue
            found = [
                P._book_at(P._prepare(f"{pre}{name} 1 skyrius 1 eilutė"), 0) for pre in ("", "1 ")
            ]
            assert any(m and not m[2] for m in found), name


class TestCitationSlotAcrossUtterances:
    def _run(self, *feeds):
        sp = StreamingParser(ReferenceParser(language="lt"), hold_chapter_only=True)
        out = []
        for t, text in enumerate(feeds):
            out += [str(r) for r in sp.feed(text, float(t * 10))]
        return out + [str(r) for r in sp.flush()]

    def test_book_then_vienas_in_next_utterance(self):
        assert self._run(
            "Tačiau, Jobas, kaip prašau, Bibliuje, Jobo...", "Vienas nuo dvidešimtos eilutės"
        ) == ["Job 1:20"]

    def test_article_after_book_in_next_utterance_stays_text(self):
        assert self._run("mes skaitėme Jobo", "Viena moteris atėjo") == []


class TestChapterFilledFromVerseText:
    """A held Lithuanian chapter ("Luko 16 skyrius") takes the verse read next.

    The index is synthetic, not Bible text: Luke 16 has the distinctive words,
    and the Genesis filler makes them rare enough to count (IDF above 2).
    """

    VERSE_TEXT = VerseTextIndex(
        [
            ("Luke", 16, 1, "išmanusis valdytojas apkaltintas mokiniams"),
            ("Luke", 16, 2, "šeimininkas reikalauja sąskaitų atsiskaitymo"),
            ("Luke", 16, 3, "skolininkams sumažino skolas aliejaus statinės"),
        ]
        + [("Genesis", 1, v, "Dievas kūrė dangų ir žemę") for v in range(1, 21)]
    )
    READ_V1 = "išmanusis valdytojas apkaltintas mokiniams"

    def _parser(self):
        return StreamingParser(
            ReferenceParser(language="lt"),
            language="lt",
            hold_chapter_only=True,
            verse_text=self.VERSE_TEXT,
        )

    def test_chapter_mention_takes_the_verse_read_next(self):
        sp = self._parser()
        assert sp.feed("Luko 16 skyrius, sako", 0.0) == []  # held for the next segment
        [ref] = sp.feed(self.READ_V1, 10.0)
        assert (str(ref), ref.verse_start, ref.confidence, ref.from_text) == (
            "Luke 16:1",
            1,
            "medium",
            True,
        )
        assert sp.flush() == []

    def test_verse_read_in_the_same_segment_counts(self):
        # The held text is the segment that said the chapter plus the next one.
        sp = self._parser()
        assert sp.feed(f"Luko 16 skyrius, {self.READ_V1}", 0.0) == []
        [ref] = sp.feed("ir toliau kalbame", 10.0)
        assert (str(ref), ref.from_text) == ("Luke 16:1", True)

    def test_verse_read_before_the_chapter_is_not_used(self):
        sp = self._parser()
        assert sp.feed(self.READ_V1, 0.0) == []
        assert sp.feed("Luko 16 skyrius", 10.0) == []
        [ref] = sp.feed("ir toliau kalbame", 20.0)
        assert (str(ref), ref.verse_start, ref.from_text) == ("Luke 16", None, False)

    def test_below_threshold_keeps_the_bare_chapter(self):
        sp = self._parser()
        assert sp.feed("Luko 16 skyrius", 0.0) == []
        [ref] = sp.feed("apkaltintas", 10.0)  # one of verse 1's four distinctive words
        assert (str(ref), ref.verse_start, ref.from_text) == ("Luke 16", None, False)

    def test_verse_named_in_next_segment_wins(self):
        sp = self._parser()
        assert sp.feed("Luko 16 skyrius", 0.0) == []
        [ref] = sp.feed(f"šešta eilutė, {self.READ_V1}", 10.0)
        assert (str(ref), ref.confidence, ref.from_text) == ("Luke 16:6", "high", False)

    def test_without_verse_text_the_chapter_is_reported_as_before(self):
        sp = StreamingParser(ReferenceParser(language="lt"), language="lt", hold_chapter_only=True)
        assert sp.feed("Luko 16 skyrius", 0.0) == []
        [ref] = sp.feed(self.READ_V1, 10.0)
        assert (str(ref), ref.verse_start, ref.from_text) == ("Luke 16", None, False)

    def test_flush_at_end_of_stream_does_not_fill(self):
        sp = self._parser()
        assert sp.feed(f"Luko 16 skyrius, {self.READ_V1}", 0.0) == []
        [ref] = sp.flush()
        assert (str(ref), ref.verse_start, ref.from_text) == ("Luke 16", None, False)

    def test_ttl_expiry_does_not_fill(self):
        sp = self._parser()
        assert sp.feed(f"Luko 16 skyrius, {self.READ_V1}", 0.0) == []
        [ref] = sp.feed("ir toliau kalbame", 700.0)  # past the 600 s context TTL
        assert (str(ref), ref.verse_start, ref.from_text) == ("Luke 16", None, False)

    def test_fill_lowers_high_to_medium_and_keeps_low(self):
        # The LT parser builds every chapter-only ref as medium, so the
        # high -> medium step is checked on the helper directly.
        sp = self._parser()
        high = sp._verse_from_text(Reference("Luke", 16, confidence="high"), self.READ_V1)
        assert (high.verse_start, high.confidence, high.from_text) == (1, "medium", True)
        low = sp._verse_from_text(Reference("Luke", 16, confidence="low"), self.READ_V1)
        assert (low.verse_start, low.confidence) == (1, "low")

    def test_empty_feed_releases_without_filling(self):
        # The benchmark's end-of-recording release, kept equal to live flush().
        sp = self._parser()
        assert sp.feed(f"Luko 16 skyrius, {self.READ_V1}", 0.0) == []
        [ref] = sp.feed("", 10.0)
        assert (str(ref), ref.verse_start, ref.from_text) == ("Luke 16", None, False)

    def test_verse_missing_from_the_display_bible_keeps_the_chapter(self):
        # Verse numbering differs between translations in a few chapters.
        sp = StreamingParser(
            ReferenceParser(language="lt", validator=lambda r: r.verse_start != 1),
            language="lt",
            hold_chapter_only=True,
            verse_text=self.VERSE_TEXT,
        )
        assert sp.feed("Luko 16 skyrius", 0.0) == []
        [ref] = sp.feed(self.READ_V1, 10.0)
        assert (str(ref), ref.from_text) == ("Luke 16", False)

    def test_range_end_missing_from_the_display_bible_is_dropped(self):
        sp = StreamingParser(
            ReferenceParser(language="lt", validator=lambda r: r.verse_start != 2),
            language="lt",
            hold_chapter_only=True,
            verse_text=self.VERSE_TEXT,
        )
        assert sp.feed("Luko 16 skyrius", 0.0) == []
        [ref] = sp.feed(f"{self.READ_V1} šeimininkas reikalauja sąskaitų atsiskaitymo", 10.0)
        assert (str(ref), ref.from_text) == ("Luke 16:1", True)


class TestFineTuneNumberWords:
    """Number-word shapes the Lithuanian Whisper fine-tunes write (all words,
    no digits); found by the Ąžuolas / Paprika benchmark runs."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # "nuo" + two bare ordinals = a range, gap or not
            ("hebrajams pirmas skyrius nuo pirmos trečios eilutės", ["Hebrews 1:1-3"]),
            ("Mato penktas skyrius nuo trečios penktos eilutės", ["Matthew 5:3-5"]),
            # Psalms with a chapter word, then a verse range
            ("iš psalmių keturiasdešimto skyriaus pirma antra eilutė sako", ["Psalms 40:1-2"]),
            ("psalmių keturiasdešimto skyriaus trečia ketvirta eilutė", ["Psalms 40:3-4"]),
            ("psalmių keturiasdešimtas skyrius trečia iki penktos eilutės", ["Psalms 40:3-5"]),
            ("psalmių keturiasdešimto skyriaus", ["Psalms 40"]),
            # tens with the final 't' dropped
            ("devyniasdešim pirmą psalmę", ["Psalms 91"]),
            ("psalmėje devyniasdešim pirmoje dar kartą ketvirtą eilutę", ["Psalms 91:4"]),
            ("psalmė septyniasdešim trečia", ["Psalms 73"]),
            # verse named before the psalm
            ("paskaitykim tą pirmą eilutę devyniasdešim pirmos psalmės", ["Psalms 91:1"]),
            ("Paskaitykim tą pirmą eilutę 91 psalmės.", ["Psalms 91:1"]),
        ],
    )
    def test_reads(self, text, expected):
        assert refs(text) == expected

    @pytest.mark.parametrize("stem", ["trisdesim", "keturiasdesim", "devyniasdesim"])
    def test_clipped_tens_are_the_full_tens(self, stem):
        full = number_word(stem + "t")
        assert full is not None and number_word(stem) == full

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Romiečiams nuo 8 28 eilutės", "Romans 8:28"),
            ("Jono evangelija nuo 3 16 eilutės", "John 3:16"),
            ("Jono evangelijos nuo trečio šešioliktos eilutės", "John 3:16"),
        ],
    )
    def test_nuo_right_after_a_book_is_chapter_and_verse(self, text, expected):
        assert refs(text) == [expected]

    def test_descending_pair_after_nuo_is_not_a_range(self):
        assert "Matthew 5:5-3" not in refs("Mato penktas skyrius nuo penktos trečios eilutės")

    def test_verse_before_psalm_needs_a_free_verse_number(self):
        # "3 16 eilutė" belongs to John; the psalm word after it is a new reference.
        assert refs("Jono 3 16 eilutė") == ["John 3:16"]
        assert refs("Jono 3 16 eilutė 23 psalmė")[0] == "John 3:16"


class TestLtbBookTitles:
    """The LTB's own book titles (bible_ltb.sqlite local_name) and their
    case forms; a sweep over every title found these three missing."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Ezdro knygoje 1 skyrius 1 eilutė", "Ezra 1:1"),
            ("Ezdro pirmas skyrius pirma eilutė", "Ezra 1:1"),
            ("Koheleto 3 skyrius 1 eilutė", "Ecclesiastes 3:1"),
            ("Jonos knygoje 2 skyrius 1 eilutė", "Jonah 2:1"),
            ("Jonos 2 skyrius", "Jonah 2"),
        ],
    )
    def test_title(self, text, expected):
        assert refs(text) == [expected]

    def test_jono_stays_john(self):
        assert refs("Jono 3 skyrius 16 eilutė") == ["John 3:16"]
