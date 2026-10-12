# BibleLive real-world benchmark — baseline notes and limitations

Status: **infrastructure complete, real-audio baseline pending authorized
recordings.** No true baseline numbers exist yet; every claim below about
quality is explicitly a hypothesis until a run against real sermons happens.

## Actual pipeline behavior (verified in source, Phase 1)

- Capture: ffmpeg mic → 10ms s16le chunks in a bounded queue (1000 ≈ 10s);
  `get_audio_frame()` re-frames to 30ms (960 B) frames with a carry-over
  buffer (`_frame_buffer`) so no bytes are dropped between frames.
- VAD thread: WebRTC VAD (aggressiveness 2) into `audio/segmenter.py`. An
  utterance accumulates speech frames until > 50 consecutive silent 30ms
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
