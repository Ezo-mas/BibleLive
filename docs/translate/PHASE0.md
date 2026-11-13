# Translate — Phase 0 bake-off brief

Standalone. It can start today: it needs nothing from the improvement plan
(`docs/IMPROVEMENT_PLAN.md`) and nothing from the rest of the Translate plan
(`docs/translate/PLAN.md`) except the definitions repeated here.

**Purpose.** Prove, on real sermon audio, that Lithuanian speech recognition
and Lithuanian-to-Russian translation are good and fast enough, and pick the
models, before any architecture is built. The outcome is a decision memo the
owner signs.

**Ground rules.**

- Work in a scratch virtual environment outside the project's dependency set,
  as the earlier speech-model trials were (`benchmarks/BASELINE_NOTES.md`,
  "Other speech models for Lithuanian"). Nothing under `backend/` or
  `frontend/` changes. No commits.
- `bench_audio/` is read-only. Derived clips, drafts and results go under
  `results/translate/` (not in git), each run with a unique name.
- Harness scripts live in `benchmarks/translate/`. They may import `audio.*`
  and `core.*` read-only (segmenter, speech engines), as `run_benchmark.py`
  does.
- Restricted-licence models (NLLB, CometKiwi) may be run for comparison only.
  They are never candidates to ship. Say so in every table they appear in.
- Report honestly: a row that could not be run is "not run" with the reason,
  never an estimate.

## Scratch-environment packages (for the owner's approval before starting)

None of these enters `backend/pyproject.toml` in this phase. Licences are from
memory and must be confirmed on install.

| Package | Used for | Licence (to confirm) |
| --- | --- | --- |
| `jiwer` | WER | Apache-2.0 |
| `sacrebleu` | BLEU, chrF++ | Apache-2.0 |
| `unbabel-comet` (pulls `torch`) | COMET-22 (`wmt22-comet-da`) | Apache-2.0 |
| `datasets` | FLORES-200 and FLEURS download | Apache-2.0 |
| `sentencepiece`, `transformers` | tokenizers, model conversion to CTranslate2 | Apache-2.0 |
| `ctranslate2` (already a BibleLive dependency) | OPUS-MT and MADLAD inference | MIT |
| `llama-cpp-python` | the LLM translation row | MIT |
| `transcribe-cpp` (already the `gguf` extra) | Kmynas, Canary | MIT |
| one package per speech-synthesis candidate (`kokoro`, `qwen-tts`, `onnxruntime`, …) | P0.4 | per candidate; record each |

## Packages

Sequential: P0.1 first, P0.6 and P0.7 last. P0.2 to P0.5 run in parallel once
P0.1's data exists (P0.4 and P0.5 need no data and can start at once).

### P0.1 — Reference data

Owner's part is described in `phase0/TRANSCRIPT_CORRECTION.md`.

1. **Builder:** cut the six 5-minute windows listed there from `bench_audio/`
   into `results/translate/clips/<window id>.wav` (16 kHz mono, ffmpeg, source
   untouched).
2. **Builder:** produce a draft transcript per window with Kmynas Q8 through
   BibleLive's default segmentation, in the correction format, at
   `results/translate/drafts/<window id>.txt`.
3. **Owner:** correct the six drafts (about 30 minutes of audio).
4. **Builder:** from the corrected files, select the 150 rating sentences by
   the fixed rule in `phase0/RATING_GUIDE.md` and freeze them in
   `results/translate/sermon150.jsonl`.
5. **Builder:** download FLORES-200 `devtest` for `lit_Latn`, `eng_Latn`,
   `rus_Cyrl` and the FLEURS `lt_lt` test split; record dataset versions and
   hashes in `benchmarks/translate/data/manifest.json`.

Files owned: `benchmarks/translate/data/`, `benchmarks/translate/make_drafts.py`.
Acceptance: manifest with hashes; six corrected files pass the format checker.
Tests: format checker and manifest loader unit tests.

### P0.2 — Speech recognition and segmentation

Systems: Kmynas Q8 (CPU and GPU), Canary Q8, Whisper large-v3-turbo.
Segmentation: BibleLive's default (1.5 s end of speech, 8 s soft, 12 s hard)
against three translation settings (end of speech 0.5 / 0.6 / 0.8 s; soft 4 s;
hard 7 s), all through `audio/segmenter.py`. Also S2 for Kmynas on GPU:
re-transcribe the open segment every 1.5 s and commit the prefix two
consecutive passes agree on.

Report per system and setting, on the sermon set and on FLEURS:

- WER, normalized (lower case, punctuation removed, `<skip>` and `<en>` spans
  excluded).
- Sentence-boundary F1 against the corrected punctuation (sermon set only).
- Real-time factor and peak memory (video and system).
- Text delay: for every reference word, the time from the word's end to the
  moment its segment's transcript is available; median and 95th percentile.
  Word times come from a forced alignment or, if none is available, from
  linear interpolation inside each corrected line; say which.

Files owned: `benchmarks/translate/stt_bench.py`.
Tests: scorer unit tests on fixed strings.

### P0.3 — Translation

Systems, each for LT→EN and LT→RU unless noted:

| Row | System | Notes |
| --- | --- | --- |
| A | OPUS-MT `tc-big-lt-en` | English baseline |
| B | OPUS-MT `tc-base-bat-zle` | Russian direct baseline |
| C | MADLAD-400 3B, CTranslate2 8-bit | both targets |
| D | one LLM: EuroLLM-9B-Instruct 4-bit, or TranslateGemma if it covers Lithuanian (Gemma terms: owner sign-off needed to ship) | both targets, with two sentences of context |
| E | Pivot: best LT→EN row, then EN→RU with the same family (OPUS `en-ru`, or MADLAD) | Russian only |
| F | Canary-1B-v2 speech-to-English, if the runtime exposes translation | English only, from audio |
| R | NLLB-200 3.3B | **reference only, non-commercial, never shipped** |

Inputs: FLORES-200 devtest (1,012 sentences, with references); `sermon150`
clean (the corrected text); `sermon150` as recognized (Kmynas output for the
same spans, from P0.2's chosen setting).

Report per row:

- FLORES: BLEU, chrF++, COMET-22.
- Sermon sets: the owner's blind ratings (mean adequacy, share of critical
  errors), per `phase0/RATING_GUIDE.md`.
- **Translation time per sentence**, median and 95th percentile, on the
  development PC's GPU and on CPU only (6 threads), measured on the sermon set
  with the model warm.
- Peak video and system memory.

**Pivot delay, measured, not estimated.** For row E record three numbers on
each device:

1. hop 1 time (LT→EN) and hop 2 time (EN→RU) per sentence, median and p95;
2. the chained time when Russian waits for its own hop 1;
3. the chained time when hop 1 is the English channel's translation, which is
   computed anyway, so Russian pays only hop 2 after English is done.

Then state, for Russian, the resulting **text delay** and **audio-start
delay** (median and p95) for direct and for pivot, using P0.2's segment timing
and P0.4's synthesis timing. The decision table below puts these beside the
quality scores.

Files owned: `benchmarks/translate/mt_bench.py`, `rating_sheet.py` (builds the
blind sheets and scores them).
Tests: metric wrappers against known values; sheet builder shuffles and
de-shuffles losslessly.

### P0.4 — Speech synthesis

Candidates: Kokoro-82M (English), Supertonic 3 and Silero `v5_cis_base`
(Russian), Qwen3-TTS 0.6B (both), Chatterbox Multilingual (both) if time
allows. Thirty sentences per language taken from P0.3's best output,
including book names, personal names and numbers.

Report: real-time factor on CPU and GPU, time to first audio for a 12-word
sentence, peak memory, sample rate, licence of code and of weights with the
link read. The owner listens to every clip and marks: acceptable voice
(yes/no), wrong word stress, mispronounced name.

Files owned: `benchmarks/translate/tts_bench.py`.

### P0.5 — Phone audio spike

A static page plus a small local WebSocket server that sends one clip every
few seconds. Two players: a Web Audio queue, and a media element fed in
segments. Two codecs: MP3 and AAC, encoded with the bundled ffmpeg (confirm
the build has each encoder). Reach the phone through a Cloudflare quick tunnel
(`cloudflared tunnel --url http://127.0.0.1:<port>`); this also tests the
tunnel with WebSockets.

The owner fills a sheet on one iPhone and one Android phone, each on mobile
data: tap to start; wired or Bluetooth headphones; 10 minutes with the screen
locked; switch from Wi-Fi to mobile data and back; another app playing audio.

Files owned: `benchmarks/translate/phone_spike/`.

### P0.6 — Contention

The winners of P0.2 to P0.4 running together on recorded audio in real time,
with BibleLive (Kmynas + Canary) and OBS recording, on the development PC and
on the 4 GB service laptop. Report peak video memory, each stage's real-time
factor, end-to-end text and audio-start delay per language, and BibleLive's
final-wait times and detections against a run without Translate.

Files owned: `benchmarks/translate/contention.py`.

### P0.7 — Decision memo

`docs/translate/BAKEOFF.md`: every table above, the thresholds with any
recalibration explained, the proposed models (decisions T6, T7, T8), the
codec and player, the segment settings, and the dependency list for Phase 1
and Phase 2 for approval.

## Thresholds

The absolute numbers are proposals from thin public evidence. Measure the
baselines (rows A and B) first; if a threshold turns out to be badly placed,
change it in the memo and say why, before looking at the candidates' human
ratings.

| Measure | Pass | Conditional | Fail |
| --- | --- | --- | --- |
| Sermon WER, Kmynas, normalized | ≤ 15% | 15–20% | > 20% |
| WER cost of translation segments against BibleLive's segmentation | ≤ +2 points | +2 to +4 | > +4 |
| Sentence-boundary F1, sermon set | ≥ 0.75 | 0.65–0.75 | < 0.65 |
| LT→EN, FLORES-200 devtest | BLEU ≥ 34 and COMET-22 ≥ 0.84 | within 1 BLEU of row A | below row A |
| LT→RU, FLORES-200 devtest | beats row B by ≥ 4 chrF++ and COMET-22 ≥ 0.82 | beats row B by 2–4 chrF++ | no row beats B by 2 |
| Human adequacy (0–3), 150 sentences, clean text | mean ≥ 2.2; critical ≤ 5% | mean ≥ 1.9; critical ≤ 8% | worse |
| Human adequacy, same sentences as recognized | mean ≥ 2.0; critical ≤ 10% | mean ≥ 1.7; critical ≤ 15% | worse |
| Translation time per sentence, p95 | ≤ 0.7 s on GPU; ≤ 1.5 s on CPU | up to 1.5 times that | beyond |
| End to end on the comfortable tier | text ≤ 4 s median and ≤ 8 s p95; audio start ≤ 7 s median | text ≤ 5 s median | beyond |
| Video memory: recognition + translation + synthesis | ≤ 6 GB | 6–8 GB | > 8 GB |
| BibleLive in the contention run | final-wait p95 ≤ 0.5 s; detections identical | p95 ≤ 1 s | detections change |
| Synthesis | RTF ≤ 0.3; first audio ≤ 0.8 s; voice accepted; Russian stress errors ≤ 1 in 10 sentences | RTF ≤ 0.5 | names unintelligible, or no acceptable licence |
| Phones | audio continues 10 min screen-locked on both | only with the screen on | no reliable playback |

A critical error is reversed meaning, a wrong name or number, invented
content, untranslated text, or wording that would offend in a service.

## Direct or pivot for Russian: the table the owner decides from

Fill one line per Russian row. Both the quality and the delay are measured.

| Row | Adequacy, as recognized (mean) | Critical errors | FLORES chrF++ / COMET | Translation time p50 / p95, GPU | Same, CPU | Russian text delay p50 / p95 | Russian audio start p50 / p95 | Video memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B direct OPUS | | | | | | | | |
| C direct MADLAD | | | | | | | | |
| D direct LLM | | | | | | | | |
| E pivot, own hop 1 | | | | | | | | |
| E pivot, reusing English | | | | | | | | |

Default rule if the owner wants one: ship direct unless the pivot wins by at
least 0.15 in mean adequacy on recognized text with no more critical errors,
**and** its Russian text delay stays within the target (4 s median, 8 s p95).

If no Russian row passes: the owner chooses between shipping English only,
shipping Russian marked experimental, or allowing a cloud translation service
for Russian (which breaks the local-inference constraint).

## Reviewer checkpoint

- Re-run one row of each bench from the manifest and get the same numbers.
- Confirm no rule, prompt or setting was tuned on `sermon150`.
- Check each licence stated in the memo against the model card or repository,
  with the date read.
- Confirm `bench_audio/` is untouched and nothing under `backend/` or
  `frontend/` changed.
- Confirm the rating sheets were blind: system names appear only in the key
  file, which the owner did not open before finishing.
