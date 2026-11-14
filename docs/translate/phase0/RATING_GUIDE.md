# Phase 0 — translation rating guide (owner's task)

You rate how well each machine translation carries the meaning of the
Lithuanian sentence. You do not know which system produced which line.

## What you receive

Spreadsheets built by `benchmarks/translate/rating_sheet.py` from the
template `rating_sheet.template.csv`, one per stage and language. Each row is
one Lithuanian sentence with one translation. Rows for the same sentence sit
together, in shuffled system order, so you can compare them. Identical
translations from different systems appear once.

The key that maps the `system` letters to model names is in a separate file,
`results/translate/ratings/key.json`. Do not open it until you have finished.

## The 150 sentences

Fixed by rule, not picked by hand, so nobody can choose flattering ones: from
each of the six corrected windows, two runs of consecutive sentences, the
first starting at the first full sentence after 0:30 and the second after
3:00, each run 12 or 13 sentences long (25 per window). Consecutive sentences
are used so that context problems (a pronoun, a name carried over) show up.
Lines marked `<skip>` or containing `(?)` are passed over.

Each sentence exists in two versions:

- **clean** — your corrected text;
- **as recognized** — what the speech model wrote for the same stretch. This
  is what the product will really translate, so it is the version that
  decides.

The sheet shows your corrected sentence as `reference_lt` in both cases. Rate
the translation against what the speaker **said**, not against a faulty
recognition: if the recognizer got a word wrong and the translation
faithfully carries the wrong word, that is still a wrong translation for the
listener.

## Two stages, to keep it short

| Stage | What | Rows | Time |
| --- | --- | --- | --- |
| 1. Screen | 50 sentences (the first run of each window), as recognized, every Russian system; English: the two best by automatic score | about 200 Russian + 100 English | about 50 min |
| 2. Decide | all 150 sentences, the two best Russian systems plus the pivot if it is not one of them, as recognized and clean; English: the best system, as recognized | about 600–750 Russian + 150 English | about 2.5 h |

Stage 1 ratings are reused in stage 2, so those 50 sentences are not rated
twice for the same system. About ten seconds a row is the right pace; do not
polish.

## Columns you fill

| Column | Values | Meaning |
| --- | --- | --- |
| `adequacy` | 0, 1, 2, 3 | how much of the meaning arrives (scale below) |
| `critical` | 0 or 1 | 1 if the line would mislead or embarrass (types below) |
| `critical_type` | `meaning`, `name_number`, `invented`, `untranslated`, `offensive` | only when `critical` is 1 |
| `fluency` | 0, 1, 2 | optional: 2 natural, 1 understandable but clumsy, 0 broken |
| `note` | free text | optional; a few words |

Leave every other column as it is.

## Adequacy scale

| Score | Meaning | Test |
| --- | --- | --- |
| 3 | Everything that matters arrives. | A listener would understand what the speaker said. Wording may differ. |
| 2 | The main point arrives; something small is lost, added or awkward. | A listener understands the point but misses a detail or stumbles. |
| 1 | Only part arrives, or something important is distorted. | A listener gets the topic but not what was said about it. |
| 0 | Wrong, opposite, or nonsense. | A listener is misled or understands nothing. |

Judge meaning, not style. A stiff but correct sentence is a 3 for adequacy
and a 1 for fluency.

## Critical errors

Mark `critical = 1`, whatever the adequacy score, when the line has any of:

- `meaning` — says the opposite, or changes who did what ("God forgave him"
  for "he forgave God"), or drops a "not".
- `name_number` — a wrong person, place, Bible book, chapter or verse, or any
  wrong number.
- `invented` — content that was not said at all.
- `untranslated` — Lithuanian words left in the output, or the output is in
  the wrong language.
- `offensive` — wording that would be offensive or absurd in a church
  service.

## Russian-specific things to watch

- Address: the congregation should be addressed in the plural (вы). A switch
  to singular ты where the speaker addressed everyone is a 2 at best.
- Names of Bible books and people in their usual Russian (Synodal) form:
  Иоанн, not Йонас.
- Gender and case agreement lost in a way that changes who is meant counts
  against adequacy; a merely ungrammatical ending counts against fluency.

## Worked examples

| Lithuanian (said) | Translation | adequacy | critical | Why |
| --- | --- | --- | --- | --- |
| Dievas mus myli ne todėl, kad mes geri. | God loves us not because we are good. | 3 | 0 | complete |
| Dievas mus myli ne todėl, kad mes geri. | God loves us because we are good. | 0 | 1 `meaning` | the "not" is gone |
| Atsiverskime Jono evangelijos trečią skyrių. | Let us open the third chapter of the Gospel of John. | 3 | 0 | complete |
| Atsiverskime Jono evangelijos trečią skyrių. | Let's turn to Jonah chapter three. | 1 | 1 `name_number` | wrong book |
| Ir tada jis, na, jis suprato, kad klydo. | And then he realized he was wrong. | 3 | 0 | the restart may be smoothed away |
| Malonė nėra uždirbama. | Pleasure is not earned. | 1 | 1 `meaning` | "malonė" is grace here |

## When you finish

Save each sheet as CSV with the same file name and tell the builder. The
scoring script reports, per system and version: mean adequacy, the share of
critical errors by type, and mean fluency if filled, and only then joins the
key.
