# Translate — Russian Bible import and versification map (Phase 4)

Outside the MVP. This note exists so the work is not a surprise when Phase 4
starts. It changes `bible_data/`, which is an ask-first area in `AGENTS.md`:
the owner has agreed to source the text; the import itself still happens only
when Phase 4 package P4.2 is approved to start.

Claims about the Synodal text's numbering below are from general knowledge,
not checked against a file. Verify each against the text you obtain.

## What it is for

Scripture insertion: when a speaker reads a verse aloud, Translate can show
and speak the official text in the listener's language instead of a machine
translation. English already has a text (`bible_data/bible_web.sqlite`, the
World English Bible). Russian has none.

## What you need to prepare

1. **A Russian Bible file you are allowed to ship.**
   - The 1876 Synodal translation is generally treated as public domain.
     Modern revisions and other translations (for example the RBO
     "Современный русский перевод") are copyrighted and need a licence.
   - Keep the source, its edition and the licence or public-domain statement
     in a text file beside it. A commercial bundle needs this on record.
   - **Format:** the existing converter reads MyBible-format SQLite
     (`books(book_number, short_name, long_name, …)` and
     `verses(book_number, chapter, verse, text)`), which is how the Lithuanian
     LTB was imported. Synodal modules in this format are common.
   - **Canon:** it must map onto the 66 canonical books. Synodal editions
     often include the deuterocanonical books; the import must drop them, and
     the importer's validation requires all 66 canonical books to be present.
   - **Markup:** many modules carry footnote marks, Strong's numbers or
     formatting tags in the text. Note which, so the cleaner can strip them
     (`clean_verse_text` in `core/bible_lookup.py` already handles some).
2. **A decision on where the file lives.** BibleLive lists every `*.sqlite`
   in `bible_data/` as a selectable translation (`discover_versions` scans
   that folder). A Russian database placed there would appear in BibleLive's
   translation setting, while BibleLive's reference detection assumes
   KJV-style numbering. Recommended: keep it out of that scan, in
   `bible_data/translate/bible_rst.sqlite`, unless you also want Russian on
   BibleLive's display.
3. **About two hours for spot-checks** (list below).

## Import steps

From the repository root, with the backend environment active.

1. Put the source file under `data/` or another folder outside git if its
   licence does not allow redistribution in the repository.
2. Convert:
   ```bash
   python backend/scripts/convert_sqlite_bibles.py \
     --source <path>/RST.SQLite3 --translation rst \
     --output bible_data/translate/bible_rst.sqlite
   ```
   The converter takes book identity from the shared canon
   (`core/books.py`), writes the English canonical name to `books.name` and
   the module's own title to `books.local_name`. If the module's
   `book_number` values do not follow the MyBible scheme the converter
   expects, or it contains extra books, the converter needs a small change:
   that is a code change in Phase 4, with tests.
3. Verify:
   ```bash
   python -m core.import_bible --output bible_data/translate/bible_rst.sqlite --info
   ```
   Expect translation `rst`, 66 books, roughly 31,000 verses.
4. Spot-check the text of ten well-known verses by hand (John 3:16, Psalm 23
   in KJV numbering, Romans 8:28, Genesis 1:1, Revelation 22:21, and five of
   your choice), including one from each area in the table below.
5. Record the import in `DATABASE_SETUP.md` (source, edition, date), as the
   existing translations are.

Nothing here writes to `bible_data/biblelive.sqlite` or the other existing
databases. The server opens Bible databases read-only on the request path;
that stays true.

## The versification map

Three texts are involved, and they do not number verses the same way:

- **Canonical coordinates** — KJV-style, what BibleLive's parser, canon and
  WEB use.
- **LTB** (Lithuanian) — differs from KJV in a few places already seen in
  this project: 1 Samuel 23–24 and Ecclesiastes 4–5 (`docs/TECH_DEBT.md`).
- **Synodal** (Russian) — differs in many more places.

Scripture insertion finds the verse by matching Lithuanian speech against
LTB, so the chain is LTB coordinates → canonical → Synodal. Without the map,
a listener would be shown the wrong psalm.

### Where the Synodal text is known to differ (verify each)

| Area | Difference |
| --- | --- |
| Psalms, numbering | Follows the Greek numbering: KJV Psalms 10–146 are mostly one lower in Synodal (KJV 23 is Synodal 22); KJV 9 and 10 are one psalm; KJV 147 is split in two. |
| Psalms, verse numbers | A psalm's title is often counted as verse 1 (sometimes 1–2), so every verse in that psalm is shifted by one or two. |
| Romans | The closing doxology (KJV 16:25–27) stands at 14:24–26. |
| Chapter boundaries | A verse at a chapter end in one text is the first verse of the next chapter in the other: for example Numbers 12–13, 1 Samuel 23–24, Job 38–41, Ecclesiastes 4–5, Song of Solomon 6–7, Daniel 3–4, Hosea 13–14, Jonah 1–2. |
| Single-verse splits and merges | For example 2 Corinthians 13, 3 John, Acts 19. |
| Extra material | Some editions carry additions in Daniel and a Psalm 151; they are dropped with the non-canonical books. |

### Format to prepare

One CSV per pair of texts, one row per range that differs. Ranges that are
identical in both texts need no row.

```
book,src_chapter,src_verse_start,src_verse_end,dst_chapter,dst_verse_start,dst_verse_end,note
Psalms,23,1,6,22,1,6,"psalm number -1"
Psalms,51,1,19,50,3,21,"psalm number -1; title is vv 1-2"
Romans,16,25,27,14,24,26,"doxology placement"
```

- `book` is the English canonical name from `core/books.py`.
- `src_*` is canonical (KJV-style); `dst_*` is the other text.
- Files: `data/translate/versification/canonical_to_rst.csv` and
  `canonical_to_ltb.csv`.

### How to get there without typing it all

1. Start from a published mapping. Open versification data exists (for
   example the STEPBible "TVTMS" tables and the Copenhagen Alliance schemas);
   check the licence of whichever you use and keep its attribution.
2. Let a script propose the rest: for each chapter, compare verse counts
   between the texts and flag chapters where they differ. Most real
   differences show up as a count mismatch at a chapter boundary.
3. Review the flagged chapters by reading the first and last verses side by
   side. This is the two hours.

### Acceptance for the map (part of P4.2)

- Every chapter whose verse count differs between two texts is covered by map
  rows or by a written exception.
- A test walks 60 hand-checked verses across the areas in the table, in both
  directions.
- For a verse with no counterpart, scripture insertion falls back to machine
  translation with the reference label. It never guesses a neighbouring
  verse.
