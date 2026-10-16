# BibleLive real-world transcription benchmark

Reproducible evaluation of how well the existing BibleLive pipeline actually
detects spoken Bible references during sermons. The system under test is the
existing implementation (see `backend/audio/pipeline.py` and friends); this
directory adds evaluation only.

## Directory layout

```
benchmarks/
├── corpus/
│   ├── manifest.example.json   # schema; copy for your own manifest
│   └── gold/
│       └── example-schema-only.gold.jsonl  # gold annotation schema example
├── hash_audio.py               # fill in manifest sha256 fields
├── make_stub_audio.py          # generate a synthetic WAV for a smoke run
├── run_benchmark.py            # main entry point; see below
└── README.md
```

## Corpus design

Recordings vary by speaker/accent, audio quality, speaking rate, and reference
phrasing. The manifest (`benchmarks/corpus/manifest.json`, not committed if you
prefer; audio lives under a local dir pointed to by `BIBLELIVE_BENCH_AUDIO_DIR`
or beside the manifest) records per recording: id, source, duration, sha256,
sample rate, speakers, audio_quality, language, gold_file, and
`annotation_provenance`.

### Gold-standard process

Use existing captions/transcripts as a starting hint but manually verify by
listening; captions cannot be trusted for spoken references (mis-verse numbers,
paraphrase instead of citation, missed asides). Each gold annotation is a line
in `<gold_file>`:

```
{"record_id": "...", "t_start_s": 61.2, "t_end_s": 64.8,
 "text_spoken": "turn with me in your bibles to romans...",
 "book": "Romans", "chapter": 8, "verse_start": 28, "verse_end": null,
 "verified_by": "...", "verified_date": "..."}
```

- `t_start_s`/`t_end_s` bracket the spoken reference (why: needed for latency).
- `verse_end` is null when only a single verse is cited.
- `annotation_provenance` — `manual` (fully verified by listening), or
  `caption-derived-and-verified` / `partially-verified` (limitation noted).
- Re-listen to anything uncertain and lower provenance accordingly; never
  silently upgrade an unverified gold pointer to `manual`.

## Running the benchmark

### Real-time simulation (the primary metric)

Replays audio **at original speed** through the production path (real
`AudioCapture`-equivalent 30ms frame feed, real `VoiceActivityDetector`,
real `SpeechToText`, real parsers and `ReferenceSender` semantics), with
detection timestamps recorded by a local in-process stub receiver instead of
the live server.

```bash
export BIBLELIVE_BENCH_AUDIO_DIR="D:/sermons"   # or use --audio-dir
python benchmarks/run_benchmark.py \
    --manifest benchmarks/corpus/manifest.json \
    --mode rt --audio-dir "D:/sermons" \
    --out results/rt-baseline.json
```

### Offline transcription (diagnostic only)

Same runner, `--mode offline` (still uses utterance-sized buffers but without
real-time pacing, no VAD pacing), used to compare against RT and pinpoint
which failures come from VAD/timing.

```bash
python benchmarks/run_benchmark.py --manifest ... --mode offline \
    --audio-dir ... --out results/offline.json
```

### Windowed transcription (evaluation-only, no VAD gating)

Slices audio into fixed overlapping windows (`--window-s`, default 30;
`--hop-s`, default 15) and transcribes each; detections carry
window-start + Whisper-segment time. Nothing can fall in a VAD gap, so this
is the recall ceiling for the current STT + parser. Production is untouched.

```bash
python benchmarks/run_benchmark.py --manifest ... --mode windowed \
    --audio-dir ... --out results/windowed.json
```

### Parser context TTL

The streaming parser keeps the running book/chapter for continuations
(`taip pat 30 eilutė`). `--context-ttl-s` (default 60) drops a context older
than that after the last reported reference, so a stale book can't claim
stray numbers minutes later. Applies to all modes and to production.
`--context-ttl-s 0` disables expiry.

### Transcript cache and time ranges (fast iteration)

STT dominates runtime. Feed events are cached under
`results/.bench-cache` keyed by audio hash + model + segmentation settings +
a fingerprint of the Whisper initial prompt and hotwords (editing either one
re-runs STT instead of replaying stale transcripts);
repeat runs replay the cache in seconds with bit-identical detections
(`transcripts_cached: true` in the output). `--no-cache` forces a live run
(which still refreshes the cache). `--t-range START:END` restricts
processing to that span in file coordinates (iteration-only; gold outside
the span misses as usual).

```bash
python benchmarks/run_benchmark.py --manifest ... --mode windowed \
    --audio-dir ... --t-range 500:1100 --out results/slice.json
```

### Smoke test without copyrighted audio

Synthetic WAV (spoken-by-ffmpeg text-speech synthesis of a scripted reference,
no copyrighted content) — verifies the runner end-to-end, not model quality:

```bash
python benchmarks/make_stub_audio.py --out bench_audio/stub.wav
python benchmarks/run_benchmark.py --manifest benchmarks/corpus/manifest.example.json \
    --audio-dir bench_audio --out results/stub.json --mode rt
```

## Metrics (definitions)

- **TP**: predicted reference whose normalized (book, chapter, verse_start,
  verse_end) matches a gold item and whose **spoken time falls within
  ±5.0s** of the gold spoken reference window midpoint. In offline/rt mode the
  spoken time is the speech span of the utterance the reference came from
  (VAD start to end of speech; for a held chapter-only ref, the utterance
  before the one that reported it), so a reference said early in a 12 s
  utterance is not penalized for being reported at its end. Windowed mode,
  and caches written before spans were recorded, use the detection time.
  `latency_s` in the output is still report time minus gold midpoint.
- **Confidence** (per match/FP in the output) mirrors the live pipeline in
  offline/rt mode: references from a Whisper decode flagged low-confidence
  are marked `low`, as `audio/pipeline.py` does before auto-approve.
- **FP**: prediction matched to nothing in gold (within tolerance).
- **FN**: gold item matched by nothing.
- **Exact book/chapter/verse accuracy** = TP where all of book/chapter/verse
  match exactly (they must, by the TP rule) — reported as coverage of gold.
- **Partial accuracy**: TP that also matches book+chapter but off-resolution
  or end-verse-only mismatches are NOT handled specially here — a mismatched
  verse is a miss; report any partial credit via the per-recording detail rows.
- **Detection latency**: predicted wallclock reference time minus gold window
  midpoint (seconds, from recording playback start, reported per detection).
- **WER** (secondary diagnostic only): computed against the gold text when
  provided; it is not a production metric since production never sees gold
  text. Implements standard Levenshtein word error rate over the transcript
  aligned to the matched utterance, only when `--compute-wer` is passed.
- **Runtime/resources**: wallclock, audio duration, RTF (runtime / audio
  duration), queue depths, dropped counts.

Results are reported per recording and aggregated. Aggregate is a weighted
mean, so a long easy recording does not hide a short hard one; the
per-recording section lists every recording including micro-corpus ones.

## Failure taxonomy (Phase 4)

`run_benchmark.py --failures` cross-references detection timestamps with VAD
event logs and STT transcripts; failures are classified into:

1. capture/chunk-boundary loss
2. VAD segmentation / dropped speech
3. STT error
4. book/abbrev recognition
5. parsing error
6. streaming/finalization timing
7. dedup/candidate-submission
8. annotation uncertainty

Concrete per-failure rows with timestamps land in the JSON output's
`failures` and the console summary distinguishes verified from hypothesis
causes via a `confidence` field on each row.
