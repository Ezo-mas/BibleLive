"""Run with:  python3 -m unittest -v"""

import unittest

from core.verse_parser import BOOKS, CHAPTERS, ReferenceParser, StreamingParser, tokenize

P = ReferenceParser()


def refs(text):
    return [str(r) for r in P.parse(text)]


def one(text):
    found = P.parse(text)
    assert len(found) == 1, f"expected 1 reference in {text!r}, got {[str(r) for r in found]}"
    return found[0]


class BookData(unittest.TestCase):
    def test_66_books_1189_chapters(self):
        self.assertEqual(len(BOOKS), 66)
        self.assertEqual(sum(CHAPTERS.values()), 1189)


class Numbers(unittest.TestCase):
    def test_number_words(self):
        self.assertEqual(tokenize("twenty-eight"), [28])
        self.assertEqual(tokenize("twenty eight"), [28])
        self.assertEqual(tokenize("one hundred and nineteen"), [119])
        self.assertEqual(tokenize("one hundred fifty"), [150])
        self.assertEqual(tokenize("a hundred and nineteen"), [119])
        self.assertEqual(tokenize("three sixteen"), [3, 16])  # two numbers
        self.assertEqual(tokenize("eight twenty eight"), [8, 28])  # two numbers
        self.assertEqual(tokenize("8:28-30"), [8, ":", 28, "-", 30])


class Explicit(unittest.TestCase):
    def test_digits(self):
        self.assertEqual(refs("Romans 8:28"), ["Romans 8:28"])
        self.assertEqual(refs("Romans 8:28-30"), ["Romans 8:28-30"])
        self.assertEqual(refs("Romans 8:28 - 30"), ["Romans 8:28-30"])
        self.assertEqual(refs("John 3:16."), ["John 3:16"])

    def test_spoken_full(self):
        r = one("turn with me to Romans chapter eight verse twenty-eight")
        self.assertEqual(str(r), "Romans 8:28")
        self.assertEqual(r.confidence, "high")

    def test_commas_and_fillers(self):
        self.assertEqual(refs("Romans chapter 8, verse 28"), ["Romans 8:28"])
        self.assertEqual(refs("Romans 8, beginning in verse 28"), ["Romans 8:28"])
        self.assertEqual(refs("Romans chapter eight and verse twenty-eight"), ["Romans 8:28"])

    def test_ranges(self):
        self.assertEqual(
            refs("first Corinthians chapter thirteen verses four through seven"),
            ["1 Corinthians 13:4-7"],
        )
        self.assertEqual(refs("Genesis 1 verses 1 and 2"), ["Genesis 1:1-2"])
        self.assertEqual(refs("John 3:16 to 18"), ["John 3:16-18"])

    def test_and_is_not_always_a_range(self):
        # "and 30" is not the next verse after 28, so it is not a range
        self.assertEqual(refs("Romans 8:28 and 30 people came"), ["Romans 8:28"])

    def test_bare_numbers_are_chapter_and_verse(self):
        r = one("John three sixteen")
        self.assertEqual(str(r), "John 3:16")
        self.assertEqual(r.confidence, "medium")
        self.assertEqual(refs("Romans eight twenty eight"), ["Romans 8:28"])

    def test_chapter_only(self):
        r = one("turn to Psalm 23")
        self.assertEqual(str(r), "Psalms 23")
        self.assertIsNone(r.verse_start)
        self.assertEqual(refs("Romans chapter eight"), ["Romans 8"])

    def test_multiple_in_one_sentence(self):
        self.assertEqual(refs("compare John 3:16 with Romans 5:8"), ["John 3:16", "Romans 5:8"])

    def test_cross_reference_range_does_not_swallow_next_book(self):
        self.assertEqual(
            refs("from Romans 8:28 to 1 Corinthians 13:4"), ["Romans 8:28", "1 Corinthians 13:4"]
        )


class NumberedBooks(unittest.TestCase):
    def test_prefix_styles(self):
        self.assertEqual(refs("first Corinthians 13:4"), ["1 Corinthians 13:4"])
        self.assertEqual(refs("1st Corinthians 13:4"), ["1 Corinthians 13:4"])
        self.assertEqual(refs("1 Corinthians 13:4"), ["1 Corinthians 13:4"])
        self.assertEqual(refs("one Corinthians 13:4"), ["1 Corinthians 13:4"])
        self.assertEqual(refs("second Timothy three sixteen"), ["2 Timothy 3:16"])
        self.assertEqual(refs("third John verse four"), ["3 John 1:4"])
        self.assertEqual(refs("1 John 4:8"), ["1 John 4:8"])
        self.assertEqual(refs("John 4:8"), ["John 4:8"])
        self.assertEqual(refs("second Kings chapter five"), ["2 Kings 5"])

    def test_ordinal_word_before_ordinary_text_is_ignored(self):
        self.assertEqual(refs("the second time I said it"), [])


class Psalms(unittest.TestCase):
    def test_psalm_119_spoken_as_one_nineteen(self):
        self.assertEqual(refs("Psalm one nineteen"), ["Psalms 119"])
        self.assertEqual(refs("Psalm one nineteen verse five"), ["Psalms 119:5"])
        self.assertEqual(refs("Psalm 119:105"), ["Psalms 119:105"])
        self.assertEqual(refs("Psalm one hundred nineteen verse 105"), ["Psalms 119:105"])
        self.assertEqual(refs("Psalm one twenty three"), ["Psalms 123"])

    def test_psalm_1_verses_still_work(self):
        self.assertEqual(refs("Psalm 1:3"), ["Psalms 1:3"])
        self.assertEqual(refs("Psalm one three"), ["Psalms 1:3"])
        self.assertEqual(refs("Psalm twenty three four"), ["Psalms 23:4"])


class SingleChapterBooks(unittest.TestCase):
    def test_jude(self):
        self.assertEqual(refs("Jude verse 3"), ["Jude 1:3"])
        self.assertEqual(refs("Jude 3"), ["Jude 1:3"])
        self.assertEqual(refs("Jude 1:3"), ["Jude 1:3"])
        self.assertEqual(refs("Jude chapter 1"), ["Jude 1"])
        self.assertEqual(refs("Philemon verses 4 through 7"), ["Philemon 1:4-7"])


class BookNames(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(refs("Revelations 21:4"), ["Revelation 21:4"])
        self.assertEqual(refs("Song of Solomon 2:4"), ["Song of Solomon 2:4"])
        self.assertEqual(refs("Song of Songs chapter two verse four"), ["Song of Solomon 2:4"])
        self.assertEqual(refs("Acts of the Apostles chapter two verse thirty-eight"), ["Acts 2:38"])
        self.assertEqual(refs("Ecclesiastes 3:1"), ["Ecclesiastes 3:1"])

    def test_fuzzy_book_names_are_lower_confidence(self):
        r = one("Habakuk 3:17")
        self.assertEqual(str(r), "Habakkuk 3:17")
        self.assertEqual(r.confidence, "medium")  # high, minus one level

    def test_fuzzy_can_be_turned_off(self):
        self.assertEqual([str(r) for r in ReferenceParser(fuzzy=False).parse("Habakuk 3:17")], [])


class NoFalsePositives(unittest.TestCase):
    def test_ordinary_sentences(self):
        for text in [
            "I have a job to do",
            "there were 3 people in the room",
            "mark my words",
            "Mark 50",  # Mark only has 16 chapters
            "the numbers are important",
            "number 5 on the list",
            "on your mark, get set",
            "he acts like he owns the place",
            "in the first place",
            "the book of John is about love",  # book, but no chapter/verse spoken
            "mic check one two",
        ]:
            self.assertEqual(refs(text), [], text)

    def test_verse_word_without_context_is_ignored(self):
        self.assertEqual(refs("in verse 5 it says"), [])


class Context(unittest.TestCase):
    def test_verse_uses_last_book_and_chapter(self):
        sp = StreamingParser()
        self.assertEqual([str(r) for r in sp.feed("turn to Romans chapter 8")], ["Romans 8"])
        out = sp.feed("look at verse 28 with me")
        self.assertEqual([str(r) for r in out], ["Romans 8:28"])
        self.assertTrue(out[0].inferred)
        self.assertEqual(out[0].confidence, "low")
        self.assertEqual(
            [str(r) for r in sp.feed("and verses thirty through thirty-two")], ["Romans 8:30-32"]
        )

    def test_chapter_and_verse_with_context_book(self):
        sp = StreamingParser()
        sp.feed("open to Romans 8")
        self.assertEqual(
            [str(r) for r in sp.feed("then chapter twelve verse two")], ["Romans 12:2"]
        )

    def test_new_book_replaces_context(self):
        sp = StreamingParser()
        sp.feed("Romans 8:28")
        sp.feed("now John 3:16")
        self.assertEqual([str(r) for r in sp.feed("and verse 17")], ["John 3:17"])


class Streaming(unittest.TestCase):
    def test_reference_split_across_segments(self):
        sp = StreamingParser()
        self.assertEqual(sp.feed("turn with me to Romans"), [])
        self.assertEqual(
            [str(r) for r in sp.feed("chapter eight verse twenty eight")], ["Romans 8:28"]
        )

    def test_chapter_then_verse_in_later_segment_upgrades(self):
        sp = StreamingParser()
        self.assertEqual([str(r) for r in sp.feed("turn to Romans 8")], ["Romans 8"])
        self.assertEqual([str(r) for r in sp.feed("verse 28")], ["Romans 8:28"])

    def test_no_duplicate_after_carry(self):
        sp = StreamingParser()
        self.assertEqual([str(r) for r in sp.feed("Romans 8:28")], ["Romans 8:28"])
        self.assertEqual(sp.feed("all things work together for good"), [])
        self.assertEqual(sp.feed("to those who love God"), [])


class DashAfterChapter(unittest.TestCase):
    """The chapter-verse dash rule ("Mark 12-30") is Lithuanian-only; English
    keeps reading a dash-joined pair as a chapter."""

    def test_mark_dash_is_chapter_only(self):
        found = ReferenceParser().parse("Mark 12-30")
        self.assertEqual([str(r) for r in found], ["Mark 12"])

    def test_genesis_dash_past_chapter_count_is_chapter_only(self):
        found = ReferenceParser().parse("Genesis 50-51")
        self.assertEqual([str(r) for r in found], ["Genesis 50"])


class Validator(unittest.TestCase):
    def test_validator_can_drop_references(self):
        only_john = ReferenceParser(validator=lambda r: r.book == "John")
        self.assertEqual(
            [str(r) for r in only_john.parse("John 3:16 and Romans 8:28")], ["John 3:16"]
        )


if __name__ == "__main__":
    unittest.main()
