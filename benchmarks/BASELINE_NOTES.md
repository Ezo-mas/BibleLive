# BibleLive real-world benchmark — baseline notes and limitations

Status: **infrastructure complete, real-audio baseline pending authorized
recordings.** No true baseline numbers exist yet; every claim below about
quality is explicitly a hypothesis until a run against real sermons happens.

## Actual pipeline behavior (verified in source, Phase 1)

- Capture: ffmpeg mic → 10ms s16le chunks in a bounded queue (1000 ≈ 10s);
  `get_audio_frame()` re-frames to 30ms (960 B) frames with a carry-over
  buffer (`_frame_buffer`) so no bytes are dropped between frames.
- VAD thread: WebRTC VAD (aggressiveness 2) into `audio/segmenter.py`. An
  utterance starts at the first voiced frame (pre-roll and a stricter start
  trigger exist as flags; see "Tried and not adopted"). It accumulates speech frames until > 50 consecutive silent 30ms
  frames (~1.5s); past ~8s it ends at the next unvoiced frame, and at ~12s
  it ends regardless (ungated full-mix audio rarely gives 1.5s of silence:
  uncapped, the 07-05 service produced 6 utterances in 40 min). Utterances of
  < 10 speech frames (~300ms) are discarded as blips. Full `vad_queue`
  (> 100 segments) drops utterances silently (metric only).
- STT thread: faster-whisper `small`, cuda/int8 (falls back to int8 if the
  GPU rejects the type), beam=5, best_of=5, temperature=0,
  `condition_on_previous_text=False`, per-language initial prompt biasing
  Bible vocabulary, decoder-loop collapse, and merged-number regex fixes
  (`_REFERENCE_CORRECTIONS`). Whole-utterance transcription — no partials.
- Parser: `StreamingParser.feed(transcript)` on the complete utterance,
  emitting `Reference(book, chapter, verse_start, verse_end, confidence,
  inferred)`; same-reference collapse within an utterance.
- Sender: ReferenceSender with MD5 hash dedup window 5.0s, retry 3× with
  0.5→5s backoff, POST `/api/reference`, server-side pending queue and WS
  approval (unchanged for this benchmark).

## Benchmark methodology (Phases 2–3)

See `benchmarks/README.md` for format, matching rule (exact
book/chapter/verse + ±5.0s tolerance window), and metrics. Two modes:

- `rt` — original-speed replay through the real VAD/STT/parser/sender path
  with production-tuned sizes (30ms frames, 50-silence hangover, 10-minimum
  speech frames, 5.0s dedup window). Primary metric.
- `offline` — same components, no real-time pacing. Diagnostic only; always
  compare against `rt` to attribute failures to timing vs recognition.

## Environment findings (this machine, 2026-10-11)

- ffmpeg 8.1 (gyan full build) present; `faster_whisper`, `webrtcvad`,
  `numpy` importable; RTX 5070 Ti 16GB visible via nvidia-smi.
- **CUDA STT currently unavailable in this environment:** ctranslate2 4.8.2
  reports `Library cublas64_12.dll is not found or cannot be loaded`. No
  cublas DLLs were found under the Python install or System32. Production
  defaults (`--device cuda`) therefore cannot run here; the runner accepts
  `--device cpu --model <size>` overrides, and the smoke test below ran CPU.
- CPU STT verified working end-to-end (tiny model, real transcription call).

## Smoke-run results (`results/smoke-rt.json`, `results/smoke-offline.json`)

Synthetic tone/noise recording (no spoken words, no copyrighted content):
- Expected behavior: no detections, since pure tones produce no transcript.
- Observed: one utterance detected at t≈19.6s (matches tone at 15s plus VAD
  hangover and minimum-frame gating), STT returned `<none>` — no hallucinated
  references. Offline RTF 0.012; RT RTF 1.0 (realtime pacing preserved).
- Conclusion: mechanics verified end-to-end. **Not a quality result.**

## Failure taxonomy (Phase 4, to be populated on a real run)

[Any real run must fill this section from the `failures` rows in the JSON
output. Categories per benchmarks/README.md:
capture/chunk-boundary, VAD segmentation, STT error, book/abbrev recognition,
parsing, streaming/finalization timing, dedup/submission, annotation
uncertainty — each row carrying a `verified`/`hypothesis` confidence label
and timestamped evidence. No entries yet: no real-audio corpus has been run.]

## Limitations (honest)

- No real sermon recordings have been evaluated. The only runs so far are
  synthetic smoke tests that prove plumbing, not accuracy.
- CUDA STT cannot run in this environment (see above); any CPU-vs-GPU
  difference is unmeasured until the dependency issue is fixed.
- Matching tolerance ±5.0s treats bunched utterances as matching within a
  window; this favors detections, and a stricter tolerance would lower recall.
- No Word-Error-Rate runner path is wired yet (`--compute-wer` unimplemented);
  WER is secondary and deliberately excluded from blocking the baseline.
- `benchmarks/corpus/gold/example-schema-only.gold.jsonl` is schema-only; no
  real gold annotations have been written through the listen-and-verify
  process yet.

## Improvement hypotheses (ranked by expected measured impact, unverified)

1. UTTERANCE-LEVEL STT BATCHING: whole-utterance transcription delays
   detection until 1.5s hangover + full Whisper pass; partial/chunked
   transcription may cut latency at the cost of more decode passes.
2. VAD-QUEUE OVERFLOW DROP: bursts of utterances are silently dropped
   (`vad_queue` 100); metrics exist but no backpressure. Measure actual drop
   counts on long sermons before optimizing.
3. DEDUP WINDOW FALSE NEGATIVE: 5.0s sender dedup collapses genuine re-
   citations (pastor corrects or repeats); per-recording FP/FN comparison
   with and without the window will quantify this.
4. BOOK-NAME RECOGNITION: STT mis-hears book names (e.g. "basalms" for
   "Psalms"); fuzzy book matching at parser level is a hypothesis until
   failure counts confirm.
5. MIN-SPEECH-FRAME GATE (300ms): short inter-phrase mentions ("verse 5")
   without surrounding speech may be discarded; needs measured examples.

## Tried and not adopted (2026-10-12)

Measured on the two Lithuanian services (25 gold references), TP/FP/FN at
a 20 s tolerance unless noted. Check here before re-running one of these.

- **Rolling-window transcription instead of VAD utterances** (tests
  hypothesis 1 above). Every 5 s, transcribe the last 15 s with word
  timestamps and commit each word once (2 s lookahead). With `small` it
  matched the utterance cap's recall at about 3x the GPU time and no
  latency gain: rolling 5/4/20 at ~11.6 s vs cap 5/3/20 at ~12 s. Code:
  branch `proto/rolling-window` (`audio/rolling.py`, `--mode rolling`),
  closed PR #17. Worth re-testing only with a larger model or a much
  shorter hop.
- **Longer utterance cap (15 s soft / 25 s hard).** No recall gain over
  8/12 s, twice the false positives, ~6 s more latency (4/7/21).
- **Demucs vocal isolation of 09-13** (`htdemucs --two-stems=vocals`, an
  upper bound for a clean pulpit feed). The VAD found more real pauses
  (77% vs 85% of frames voiced), but detection did not improve
  (windowed 5/1/8 for both; capped offline 2/3/11 vs 3/1/10). Recognition,
  not noise, was the limit.
- **ffmpeg `afftdn` denoise, and denoise + `agate` (-30 dB) on 07-05.**
  Denoise: same hits, more false positives. Denoise + gate: no hits at
  all; the gate clips word onsets and mangles numbers. Do not gate the
  pipeline input.
- **End of speech 2.0 s instead of 1.5 s** (2026-10-15, large-v3-turbo
  offline, 13 Lithuanian recordings, 158 gold references, 5 s span
  tolerance, TTL 600 s). 126/34/32 vs 131/31/27 with 1.5 s; median report
  latency unchanged (4.8 vs 4.9 s). Longer pauses did not keep split
  citations together; 10-04 lost 3 Genesis 22 verses.
- **End of speech 1.0 s** (same setup, gold timestamps fixed). 137 TP and
  16 medium/high FPs vs 139 and 14 with 1.5 s; 7% more utterances and no
  change in median report latency (5.0 s, set by utterance length).
- **Second Whisper pass with `large-v3` (beam 10) on suspicious
  utterances** (a book name or chapter/verse word but no full reference;
  3–21% of utterances per recording). 5 of 13 recordings: about 100 retries, none produced
  a full reference; scores identical to the baseline. Where turbo mangles a
  citation, large-v3 mangles it the same way.
- **Previous utterance's tail appended to the Whisper prompt** (book prompt
  trimmed to 191 tokens to make room, ~28 tokens of context). 13
  recordings: 133 TP / 15 medium+high FP vs 138 / 14 with the same trimmed
  prompt alone.
- **Rolling window with `large-v3-turbo`** (re-test of the first entry;
  10 s window, 2 s hop, 3 s lookahead, chapter-only refs held 6 s; the
  proto/rolling-window code ported onto the current runner). 6 of 13
  recordings: 55 TP / 16 medium+high FP vs 70 / 5 for the VAD-utterance
  path. On the 52 references both found, rolling reported a median 1.5 s
  earlier (a quarter of them 2 s or more later); end to end about 1–2.5 s
  faster. Citations still split across 2 s commits ("Kolosiečiams 3, 2"
  committed as a lone "Du."). Shorter lookahead (1 s) dropped book names at
  commit boundaries outright.
- **Adding Hosea (Ozėjo) to the Lithuanian prompt** in place of Kunigų
  (221 tokens; Hosea is cited 4x in gold). 136 TP / 17 medium+high FP vs
  136 / 13 with the current prompt; 07-05, the Hosea recording, lost a hit.
  (Runs with confidence matching live: Whisper flagged 43 of 7,102
  utterances low-confidence, none carrying a reference, so earlier
  medium/high counts stand.)
- **Trimming the Lithuanian book prompt to 191 tokens** (dropping 8
  never-cited books) was neutral (138/14 vs 139/14). Only Ezros was dropped
  in the end, to bring the prompt (224 tokens, over faster-whisper's
  223-token window) back under the limit: 136 TP / 12 medium+high FP vs
  139 / 14, judged prompt-perturbation noise (any prompt edit shifts a few
  transcripts; 10-11 lost 2-3 TP under both trims) and kept.

- **Matching the words read aloud against every chapter named in the last
  60-180 s** (2026-10-16, transcripts of the 13 recordings, IDF word
  overlap). It finds 4 more gold verses but also 113-182 confident matches
  to non-gold verses, mostly the speaker reading on through a passage.
  That is a follow-along feature, not a reference detector; only the
  chapter-only fill below was adopted.

- **Lithuanian fine-tuned Whisper models** (2026-10-16, same 13
  recordings, offline, 5 s span tolerance). Ąžuolas
  (`akisviete/azuolas-whisper-lt-ct2`, large-v3, float16): 137 TP / 18
  medium+high FP vs 139 / 10 for turbo, about 2.4x slower, ~4.3 GB VRAM.
  Paprika v3 (`RobertasTa/paprika-whisper-lt-v3-ct2-int8`, turbo-sized):
  any initial prompt longer than a few words (even an empty string) makes
  it return nothing or loop on 8-12 s utterances, so it only runs with no
  prompt: 126 / 17. For scale, turbo without our prompt: 118 / 14 (the
  book-name prompt is worth about +21 TP). Both fine-tunes write numbers as
  words, which exposed parser gaps ("pirma antra eilutė", "nuo pirmos
  trečios eilutės", "trys dešimt", truncated "devyniasdešim").
- **Keeping in-utterance pauses** (frames the VAD calls silent between
  voiced ones; `--keep-pauses on`, 2026-10-16, with 0.3 s pre-roll): 141 /
  11, and with the 6-of-10 start trigger 137 / 10 against 143 / 9 without
  it. Lost 10-04's Genesis 22 verses and two chapter-only refs.

- **Pre-roll and a stricter utterance start** (`--pre-roll-s`,
  `--start-voiced-frames/--start-window-frames`, 2026-10-16; 13 recordings,
  offline, 5 s span tolerance; baseline 139 TP / 19 FN / 15 low / 10
  medium+high FP). Pre-roll 0.3 s alone: 140 / 18 / 14 / 10. Pre-roll + 6
  of 10 voiced, final version (pre-roll contiguous with the onset): 139 /
  19 / 14 / 10. An earlier version that skipped the 120 ms before the onset
  scored 143 / 15 / 15 / 9. In all three start-trigger runs the same 5
  misses were fixed (Ephesians 2:5-6, 1 Peter 5:10, Jude 1:20-21,
  Ephesians 2:8-9, Luke 7:47), but each change of utterance edges also
  moved about 5 other references (Matthew 6:1, James 4:13-14,
  1 Kings 17:8-16, Genesis 22 verses), so no net gain was measurable;
  defaults unchanged. Runtime was not comparable (a game shared the GPU).
- **Silero VAD instead of WebRTC** (`--vad silero`, the v6 model bundled
  with faster-whisper, 30 ms frames re-chunked to 512-sample windows;
  2026-10-16, same 13 recordings, offline, 5 s span tolerance; baseline
  139 TP / 19 FN / 15 low / 10 medium+high FP, 3551 utterances). Threshold
  0.5: 112 / 46 / 18 / 21 (3952 utterances); 0.35: 124 / 34 / 20 / 17
  (3839); 0.35 with `--keep-pauses on --pre-roll-s 0.3`: 128 / 30 / 15 /
  22 (3932). Silero cuts the speech into more, shorter utterances and
  loses references in 11 of 13 recordings. VAD cost is negligible (about
  0.1 ms per 32 ms window on CPU); about 50 min of STT for 13 recordings
  run two at a time (not timed side by side with WebRTC). WebRTC stays
  the default. Not tried yet: Silero the way faster-whisper uses it
  (end only below threshold - 0.15, ~400 ms padding, longer end silence);
  this run applied a plain per-frame threshold to WebRTC-tuned settings.
- **WebRTC aggressiveness** (`--vad-aggressiveness`, 2026-10-17, same 13
  recordings; default 2 = 139 TP / 19 FN / 15 low / 10 medium+high FP,
  3551 utterances). 1: 136 / 22 / 18 / 9 (3396 utterances); 3: 121 / 37 /
  19 / 19 (3970). Level 1 is within noise of 2; level 3 loses clearly.
  Same pattern as Silero: the stricter the VAD, the more utterances and
  the fewer references. Default stays 2.

What did help, for contrast: picking the verse read aloud for a Lithuanian
chapter-only reference (`core/verse_text.py`, 2026-10-16; same 13
recordings, 5 s span tolerance, cached transcripts): 139 TP / 10
medium+high FP vs 136 / 13 without it. It filled Luke 16:10, Matthew 18:20
and Psalms 40:1-2; no chapter-only gold reference was lost. Correct fills
scored 0.67-1.0, wrong candidates at most 0.5, hence the 0.6 threshold.
Lithuanian number-word and book-title fixes (2026-10-16: "nuo pirmos
trečios eilutės" as 1-3 once a chapter is known, "psalmių 40 skyriaus
pirma antra eilutė", clipped tens such as "devyniasdešim", "pirmą eilutę
91 psalmės", LTB titles "Ezdro", "Koheleto", "Jonos"; found by the
fine-tuned models, which write numbers as words): cached turbo
transcripts replay unchanged at 139 / 10, so they only matter for
transcripts that spell numbers out.
Also the utterance cap (live path 0/10/25 ->
5/3/20) and `large-v3-turbo` float16 (live path 20/2/5 at 30 s with the
Lithuanian parser fixes, against 5 for `small`).
