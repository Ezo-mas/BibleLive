"""Tests for the Lithuanian verse parser (core.verse_parser with language="lt").

Canonical names/chapter counts mirror the English BOOKS, so the same
Bible database and validator work unchanged. Diacritics may be present
or absent in transcripts; both parse.
"""

from core.verse_parser import BOOKS as EN_BOOKS
from core.verse_parser import ReferenceParser, StreamingParser
from core.verse_parser_lt import BOOKS as LT_BOOKS
from core.verse_parser_lt import number_word

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
        assert sp.feed("šešiolikta eilutė sako", 61.0) == []

    def test_split_citation_joins_within_ttl(self):
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("Mato 24", 0.0)] == ["Matthew 24"]
        assert [str(r) for r in sp.feed("23 eilutė", 10.0)] == ["Matthew 24:23"]

    def test_split_citation_dropped_past_ttl(self):
        sp = StreamingParser(language="lt")
        assert [str(r) for r in sp.feed("Mato 24", 0.0)] == ["Matthew 24"]
        assert sp.feed("23 eilutė", 100.0) == []

    def test_ttl_none_disables_expiry(self):
        sp = StreamingParser(language="lt", context_ttl_s=None)
        assert [str(r) for r in sp.feed("Mato 24", 0.0)] == ["Matthew 24"]
        assert [str(r) for r in sp.feed("23 eilutė", 10000.0)] == ["Matthew 24:23"]

    def test_carry_redetection_does_not_refresh_ttl(self):
        # "Romans 8" is only re-found inside the carry-over while "amen" keeps
        # arriving; that must not count as a new reported reference, or the
        # stale context would survive past the TTL.
        sp = StreamingParser(language="lt")
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
