# Phase 0 — transcript correction (owner's task)

About 30 minutes of audio: six 5-minute windows from three services with
three different speakers. Each service gives one window dense with Bible
citations and one of plain preaching, so the test covers both.

The windows were chosen from the gold reference timestamps in
`benchmarks/corpus/gold/`. **Nobody listened to them when choosing.** If a
window turns out to be music, prayer by the congregation, or mostly silence,
shift it by whole minutes inside the sermon range, keep it 5 minutes long, and
write the new times in the file header.

## Windows

Times are seconds from the start of the file in `bench_audio/`.

| Window id | File | Start–end (s) | As h:mm:ss | Speaker | Why this window |
| --- | --- | --- | --- | --- | --- |
| `1011-cite` | `benchmark_audio_10-11-norm.wav` | 5150–5450 | 1:25:50–1:30:50 | speaker-g | 2 Kings 6:24 and 6:26, Galatians 5:14, 1 John 3:15, Proverbs 18:20: narrative with citations from several books |
| `1011-plain` | `benchmark_audio_10-11-norm.wav` | 3300–3600 | 0:55:00–1:00:00 | speaker-g | no gold reference between 2461 s and 3900 s: plain preaching |
| `1004-cite` | `benchmark_audio_10-04-norm.wav` | 4360–4660 | 1:12:40–1:17:40 | speaker-h | Genesis 22 read aloud from verse 1: reading mixed with comment |
| `1004-plain` | `benchmark_audio_10-04-norm.wav` | 3400–3700 | 0:56:40–1:01:40 | speaker-h | no gold reference between 3100 s and 4157 s |
| `0924-cite` | `benchmark_audio_09-24-norm.wav` | 3470–3770 | 0:57:50–1:02:50 | speaker-i | Mark 1:7, Acts 1:4, 1:8, 1:14, 2:4, 2:14, 2:37: fast run of citations |
| `0924-plain` | `benchmark_audio_09-24-norm.wav` | 3950–4250 | 1:05:50–1:10:50 | speaker-i | no gold reference between 3832 s and 4291 s |

Not included: the 10-18 service (English speaker with a Lithuanian
interpreter). It mixes two languages and would need its own rules. If you want
it measured, add one 5-minute window as a separate stress set, scored apart.

## What you receive

For each window, from package P0.1:

- `results/translate/clips/<window id>.wav` — the 5-minute clip.
- `results/translate/drafts/<window id>.txt` — a draft transcript from Kmynas,
  one line per utterance, already in the format below.

You correct the draft while listening. Expect roughly four to six times the
audio length: two to three hours in total.

The drafts come from one of the systems being tested, which tilts the result
slightly in its favour: a reader tends to accept a plausible draft. Listen to
every line rather than skimming, and the effect stays small.

## File format

Plain UTF-8 text, one utterance per line.

```
# window: 1011-cite
# file: benchmark_audio_10-11-norm.wav
# range_s: 5150-5450
# corrected_by: <name>   date: <YYYY-MM-DD>
[5150.4-5157.9] Ir tada karalius išgirdo moters žodžius.
[5158.6-5166.2] Antra Karalių knyga, šeštas skyrius, dvidešimt šešta eilutė.
[5166.9-5171.0] <skip>
[5171.5-5178.8] Jis perplėšė savo drabužius (?) ir ėjo per sieną.
```

- Keep the `[start-end]` times as they are. Do not merge or split lines. If a
  word belongs to the next line, move the word, not the times.
- A line the draft missed entirely: add it with approximate times.

## Rules

1. **Write what was said**, in standard Lithuanian spelling with normal
   punctuation and capitals. Sentence punctuation matters: it is scored, and
   the corrected text is the clean input for the translation test.
2. **Numbers as words**: "dvidešimt šešta eilutė", not "26 eilutė". This is
   how the speech model writes them, and it keeps scoring fair.
3. **Repetitions and restarts stay**: "aš, aš noriu pasakyti". Leave out pure
   hesitation sounds ("ėėė", "mmm").
4. **Spoken forms stay as spoken** when they are real words: "antroj
   eilutėj" is not corrected to "antroje eilutėje".
5. **Names**: standard Lithuanian Bible spelling for people, places and books.
6. **Cannot make it out**: write your best guess followed by `(?)`. If there
   is no guess, write `(?)` alone for that word or phrase.
7. **Not Lithuanian** (a phrase in English or Russian): wrap it,
   `<en>Holy Spirit</en>`, `<ru>…</ru>`. It is left out of scoring.
8. **Not speech for translation** (music, the congregation, a long silence):
   replace the whole line's text with `<skip>`.
9. Do not fix the speaker's grammar or word choice. The reference is what was
   said, not what should have been said.

## Checking

`python benchmarks/translate/check_transcript.py <file>` (part of P0.1)
reports lines with broken time stamps, digits, unclosed tags or unknown
markers. A file is finished when it reports nothing.
