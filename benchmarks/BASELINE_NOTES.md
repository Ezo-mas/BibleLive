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
  the default. This run applied a plain per-frame threshold to
  WebRTC-tuned settings; Silero the way faster-whisper uses it is below.
- **WebRTC aggressiveness** (`--vad-aggressiveness`, 2026-10-17, same 13
  recordings; default 2 = 139 TP / 19 FN / 15 low / 10 medium+high FP,
  3551 utterances). 1: 136 / 22 / 18 / 9 (3396 utterances); 3: 121 / 37 /
  19 / 19 (3970). Level 1 is within noise of 2; level 3 loses clearly.
  Same pattern as Silero: the stricter the VAD, the more utterances and
  the fewer references. Default stays 2.
- **Silero the way faster-whisper uses it** (2026-10-17, same 13
  recordings and baseline). Hysteresis (`--silero-neg-threshold`, default
  threshold - 0.15: speech starts at the threshold and ends only below
  neg), 0.4 s padding both sides (`--pre-roll-s 0.4 --post-roll-s 0.4`),
  `--keep-pauses on`. Threshold 0.5, 2.0 s end of speech (faster-whisper's
  own settings): 134 / 24 / 14 / 16 (3696 utterances); 0.5 with 1.5 s:
  133 / 25 / 13 / 17 (3980); 0.35 with 2.0 s: 132 / 26 / 13 / 18 (3627).
  Much closer than the plain threshold (best was 128) but still 5-7 TP
  behind with 6-8 more medium/high FPs. About half the lost references
  were still detected, a few seconds late (utterance starts moved); the
  rest of the gap is 3-4 more wrong references per run. Re-scored at a
  10 s / 20 s window the best Silero run is 136 / 14 while WebRTC stays
  139 / 10, so the window does not explain the gap. Silero did better on
  10-11, 10-08 and 09-13 (2 Kings 6:24/26, Isaiah 26:3, Ephesians 2:5 and
  2:8, Luke 12:29-30) and worse on 04-19, 05-31, 07-19 and 10-04. Not
  adopted; WebRTC stays the default. Runtimes not comparable (runs shared
  the GPU). Still open: a WebRTC + Silero hybrid (WebRTC starts the
  utterance, Silero with post-roll ends it, or both must agree).

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

## Other speech models for Lithuanian (2026-10-17)

Same 13 recordings, offline, 5 s span tolerance, WebRTC segmentation
unchanged, so only the transcripts differ. Scores are TP / medium+high FP
(baseline `large-v3-turbo` + book prompt: 139 / 10). Run outside the repo:
the NVIDIA models through `onnx-asr` (onnxruntime-gpu 1.23.2, CUDA 12,
cuDNN 9.10) or `transcribe.cpp` (GGUF, CPU build), fed the same utterances
through a stand-in for `SpeechToText` that keeps its text clean-up.

- **Canary-1B-v2** (`nvidia/canary-1b-v2`, ONNX by istupakov; no prompt
  input): 124 / 12. Its misses are mostly spellings the parser did not
  know ("Salme", "poštalų darbų", "Jonų 17.17", "lutėje"); a 16-rule
  spelling map in front of the parser brings it to 140 / 11 (fp32 and
  fp16 identical). Found 10 references turbo misses (2 Kings 6:24/26,
  Isaiah 26:3, 45:18, Job 1:20, 1 Peter 5:10, Luke 12:29/30, Genesis
  22:13, Ephesians 2:5), where turbo misheard number words or book names.
  Lost 9 turbo finds, mostly to its built-in digit formatting ("devinta,
  dešimta" -> "90-ąją", "11, 12" -> "11.12.3") and to words dropped at
  utterance cuts (Colossians 3:23, Ephesians 1:13). The `pnc` switch does
  not turn the digit formatting off (tested), and canary-1b-v2 has no
  separate ITN control. 200 ms of zero padding per utterance: 127 / 12
  with the map (lost 15, mostly Genesis 22 verses), so no padding.
- **Canary GGUF on CPU** (handy-computer/canary-1b-v2-gguf, transcribe.cpp,
  6 threads): Q8_0 126 / 10 raw, 140 / 10 with the map; Q4_K_M 117 / 16
  raw, 130 / 14 with the map. Q4 loses about 10 references in Lithuanian.
- **Parakeet-TDT-0.6B-v3** (`nvidia/parakeet-tdt-0.6b-v3`): 64 / 28 (66 /
  27 with the map); too many ordinary words wrong to patch.
- **Kmynas v4** (`kristijonas/kmynas-parakeet-lt-v4`, Parakeet v3
  fine-tuned on LIEPA-3 and Lithuanian conference/podcast speech, numbers
  as words; GGUF converted with transcribe.cpp's `convert-parakeet.py`
  plus a local profile for its 8196-token vocabulary): Q8_0 on CPU 132 /
  17 raw, no phantom "Mums" in 3564 utterances. With the parser fixes
  below 143 / 11; with a 5-rule spelling map ("Psmė", "Šizajo",
  "fiziečiams", "gavatams", "eutį") 148 / 10, the best result so far.
  Lithuanian only.
- **svogunas/whisper-large-v3-turbo-lt** (LIEPA-3 fine-tune, no
  punctuation or capitals): our book prompt makes it return garbage, so
  no prompt: 132 / 13, the best prompt-less Whisper (turbo without the
  prompt 118 / 14, Paprika 126 / 17), still behind turbo with the prompt.
- Not usable: Canary-Qwen-2.5B (English only), Nemotron 3.5 ASR Streaming
  (Lithuanian needs fine-tuning first), sherpa-onnx int8 Canary (same
  dynamic int8, Lithuanian not selectable). The spelling maps were fitted
  on these recordings, so their scores are optimistic until checked on
  new ones.
- **Upelis 0.6B LT v1** (`datolabs/upelis-0.6b-lt-v1`, 2026-11-14; a
  LIEPA-3 fine-tune of `nemotron-labs-unified-asr-0.6b`, RNN-T with a
  language prompt, streaming or offline; FLEURS WER 8.07 vs Kmynas 10.55 on
  its card). NeMo-only release; converted to GGUF with `convert-parakeet.py`
  plus a local profile (the unified-en encoder and the nemotron-3.5 prompt
  MLP are both already supported), Q8_0 706 MB, runs on the stock
  `transcribe-cpp` 0.4.0 wheel. Run offline as `--stt kmynas --kmynas-model
  <upelis gguf>`, so with Kmynas's spelling map; 14 recordings, CPU, 6
  threads, 171 gold: **158 / 6** (FN 13) vs Kmynas Q8 161 / 4 (FN 10), CPU
  rtf 0.067-0.127 (mostly ~0.08, about 30 % slower than Kmynas: RNN-T
  decodes every frame where TDT skips). Lost 5, won 2. Its number format
  hurts: 10 and up come out as digits, 1-9 as words, so references merge or
  split ("Apreiškimo 214" for 21:4, "iš 10 trečios psalmės", "Izajo 10
  penki" -> wrong references). It also writes "eutė" for "eilutė" (2 lost
  references, a spelling-map fix). Not adopted for the CPU tier. As the
  second model of the GPU pair in place of Canary (`--stt kmynas+canary
  --canary-model <upelis gguf>`, so with Canary's spelling map, bundled
  Vulkan library): **164 / 4** (FN 7, 7 low FPs) vs Kmynas+Canary 167 / 1
  (FN 4, 13 low FPs), rtf 0.029-0.065. Fewer low FPs, but more references
  lost and more medium/high FPs, so Canary stays. Upelis's file is smaller
  than Canary's (706 MB vs 1.1 GB; VRAM not measured), the only point left
  in its favour, for 4 GB cards.

Speed and memory, timed alone on the same 160 s / 480 s of 10-11 (Ryzen 7
5800X, RTX 5070 Ti); real-time factor, lower is faster:

| Model | GPU RTF | Peak VRAM | CPU RTF 4 / 6 threads | Peak RAM |
| --- | --- | --- | --- | --- |
| Whisper turbo int8 + prompt, beam 5 | 0.031 | +1.4 GB | 0.92 / 0.90 | |
| Whisper small int8 + prompt, beam 5 | | | 0.49 / 0.49 | |
| Canary fp32 (onnxruntime) | 0.026 | +5.5 GB | | |
| Canary fp16 (converted; arena kSameAsRequested) | 0.027-0.032 | +2.5 GB | | |
| Canary int8 (onnxruntime) | 0.145 | +0.7 GB | 0.116 / 0.113 | |
| Canary GGUF Q8_0 (transcribe.cpp CUDA) | 0.025 | +1.6 GB | 0.123 / 0.106 | ~1.65 GB |
| Canary GGUF Q4_K_M | 0.025 | +1.2 GB | 0.100 / 0.083 | ~1.25 GB |
| Kmynas v4 GGUF F32 | 0.016 | +2.7 GB | | |
| Kmynas v4 GGUF Q8_0 | 0.017 | +1.1 GB | 0.075 / 0.062 | ~1.13 GB |
| Kmynas v4 GGUF Q4_K_M | | | 0.065 / 0.053 | |
| Kmynas Q8_0 + Canary Q8_0, both loaded | | +2.9 GB | | |

Dynamic int8 runs on the CPU even under the CUDA provider, hence its GPU
speed. On an 8th-gen i5 or Zen 1 the CPU figures should be roughly 1.5-2x
slower (estimate, not measured), which still leaves Canary Q8 and Kmynas
well inside real time; Whisper on CPU does not keep up.

**Lithuanian parser: spoken number forms** (2026-10-17, found on the
Kmynas and Canary transcripts): clipped "dešim" (10), the spoken locative
"-oj" / "-ėj" ("antroj", "penktoj eilutėj", "psalmėj", "skyriuj"),
"Psalmio", accusative "eilutį", "ir iki" ranges, and a declined tens
ordinal ("dvidešimta, penkta eilutė" = 20:5, while "dvidešimt penkta"
stays 25). Cached-transcript replays: turbo 139 / 10 -> 140 / 10, Canary
Q8 with its map 140 / 10 -> 141 / 9, Kmynas Q8 132 / 17 -> 143 / 11.

**GPU runs and the two-model ensemble** (2026-10-17): transcribe.cpp built
with CUDA 13.4 (`-DTRANSCRIBE_CUDA=ON`, sm_120), same GGUF files, full
corpus with Kmynas and Canary running side by side on one GPU (one
recording at a time each). Over all 9.4 h of audio, including VAD and
parsing: Kmynas Q8 RTF 0.018, Canary Q8 0.027 (CPU Q8 runs: 0.079 and
0.131), 16 min wall time for both; peak VRAM with both loaded +2.9 GB.
Accuracy matches the CPU runs: Kmynas Q8 with its map 148 / 9 (CPU 148 /
10), Canary Q8 with its map 140 / 10 (CPU 141 / 9; Canary's repetition
guard dropped one utterance). Merging the two (same reference within 15 s
counts once; a reference only one model found is kept as low confidence):
**153 / 5**, the same as the estimate from the CPU transcripts (union
without the confidence rule 153 / 14). Kmynas F32 148 / 9, the same as Q8
(one reference won, one lost), so Q8 costs nothing. Q4_K_M costs 8
references for each model (Kmynas 140 / 10, Canary 132 / 13 with maps;
pair 149 / 5) and saves only 0.3 GB, so Q8 stays. The merge is still
offline (`union.py` over the two runs' detections); the pipeline has no
two-model mode yet.

**`--stt` in the pipeline** (2026-10-17): the engines, per-model spelling maps
and the agreement rule now live in `audio/stt_gguf.py`, `spelling_maps.py` and
`ensemble.py`, and `run_benchmark.py --stt ...` runs them through the same
code as live. Same 13 recordings, TP / medium+high FP:

| Run | Result | Notes |
| --- | --- | --- |
| `whisper` (turbo + prompt), cached replay | 140 / 10 | identical to before: the default path is unchanged |
| `kmynas`, CPU, Q8_0 | 148 / 10 | identical to the scratch-tool run |
| `kmynas+canary`, CUDA build | 153 / 5 | the offline estimate, confirmed; 30 low FPs |
| `kmynas+canary`, bundled library (Vulkan) | 152 / 5 | lost Philippians 4:7, a borderline reference |

Agreement is judged per utterance: both models found the same reference in
the same utterance 149 times out of 150, so nothing is held back. Speed on
the 60 x 8 s clips of 10-11, one process, idle machine: `kmynas+canary` rtf
0.026 on Vulkan (+3.15 GB video memory) vs 0.039 on the CUDA build (+2.9 GB);
`kmynas` alone on Vulkan 0.011 (+1.9 GB); `kmynas` on CPU 0.059 (6 threads) /
0.076 (4 threads). On the GPU the CPU thread count makes no measurable
difference (0, 2 or 4). The bundled `transcribe-cpp` library is the default:
it needs no CUDA toolkit and also runs on AMD and Intel GPUs (untested here).
The benchmark cache key includes the native library, since CUDA and Vulkan
decode slightly differently.

## Follow-along: partial transcripts (2026-10-21)

Before, a follow-along hint came from the transcript of a whole utterance, so it
arrived after the utterance that read the verse had ended (one verse behind in 37
of 43 checked cases). Now, while the server says follow-along is armed, the
pipeline also transcribes the utterance still being spoken and posts that text.
Measured with `benchmarks/follow_along_bench.py` on the 14 services (164 gold).
Each gold reference goes on screen 3 s after it was said; the simulated operator
clicks Show on every hint 1.5 s after it arrives. Finals replay from the detection
cache; partials are transcribed by Kmynas (alone, also on the GPU tier), and
every transcription costs `rtf x audio` on one simulated transcription thread.
CPU tier at the service laptop's 0.21 (Kmynas); GPU tier at the GTX 1070's
measured 0.09 for Kmynas+Canary finals and 0.035 for Kmynas partials. A hint is
wrong unless the final transcript of its utterance (or the next one, within 3 s)
reads that verse. Lag = arrival minus the start of that utterance; in time = it
arrived before that utterance ended.

| CPU tier, rtf 0.21 | hints (wrong) | median lag | in time | partial audio | busy | final waits p50 / p95 |
| --- | --- | --- | --- | --- | --- | --- |
| off (before) | 92 (0) | 11.1 s | 0 | - | 18% | - |
| grow, 2 s | 99 (0) | 9.5 s | 52 | 0.51x | 28% | 0.80 / 1.66 s |
| grow, 3 s | 98 (1) | 9.8 s | 48 | 0.32x | 25% | 0.94 / 1.66 s |
| grow, 4 s | 99 (0) | 9.6 s | 32 | 0.32x | 24% | 1.13 / 2.41 s |
| chunk, 3 s | 98 (1) | 9.6 s | 49 | 0.18x | 22% | 0.26 / 0.55 s |
| chunk, 2 s | 99 (0) | 8.5 s | 63 | 0.20x | 22% | 0.21 / 0.39 s |
| **chunk, 1.5 s (adopted)** | 99 (1) | 7.8 s | 65 | 0.21x | 22% | 0.14 / 0.29 s |

| GPU tier, rtf 0.09 / 0.035 | hints (wrong) | median lag | in time | partial audio | busy | final waits p50 / p95 |
| --- | --- | --- | --- | --- | --- | --- |
| off (before) | 92 (0) | 10.0 s | 0 | - | 8% | - |
| chunk, 2 s | 99 (0) | 8.1 s | 65 | 0.20x | 8% | 0.03 / 0.04 s |
| **chunk, 1.5 s (adopted)** | 99 (1) | 7.6 s | 69 | 0.21x | 8% | 0.02 / 0.04 s |

- The adopted rows are from the final code (`cpu-` / `gpu-sweep3-chunk-i1.5`),
  where a chunk under 0.5 s (the speech models skip such audio) waits for the
  next snapshot instead of being lost; the other rows ran before that rule,
  which changed the adopted variant's numbers only in the final-wait p95
  (0.28 -> 0.29 s on CPU).
- Grow mode re-transcribes the whole utterance so far, so its partials cost
  more and arrive later; chunk mode transcribes only the audio since the last
  partial and posts the joined texts. Partial audio is the share of the
  services' audio transcribed again as partials (only while armed).
- The one "wrong" hint (06-28, 1 Kings 17:15 over 17:12) was read nearby, out
  of order; it fails only the same-or-next-utterance rule. Eight partial hints
  checked by hand against the verse text were all the verse being read.
- Finals and segmentation are unchanged: the detection benchmark replays
  identically (Kmynas 153 / 9, Kmynas+Canary 158 / 4).
- Tried and not adopted: a lower bar for the next verse (0.35: 10 wrong
  hints; 0.40: 6 wrong, both with grow 2 s / chunk 2 s). Picking the furthest
  verse instead of the best-scoring one changed nothing on its own; it is the
  default now, with the verse-start rule below.
- Kmynas has no streaming mode: transcribe.cpp reports `supports_streaming`
  false for it (an offline Parakeet-TDT-0.6B-v3 fine-tune with full
  attention); only the chunked-attention `parakeet-unified-en-0.6b` streams.
- Partials run only on the Kmynas engines: Whisper (English, `--stt whisper`)
  takes none. There is no English gold corpus, and Whisper turbo on a CPU runs
  close to real time (0.9 above), so extra work there could make references late.

### Verse-start rule and timestamp truth (2026-10-22)

The hint matcher scores a verse by the share of its distinctive words heard, so a
hint cannot come before about mid-verse. The verse-start rule
(`HintPolicy.start_*`, `core/server/follow_along.py`) also takes the first 3 of
a verse's first 6 stems heard in order (summed IDF >= 8) as its start. Lag is now
measured from when the verse began (`--truth timestamps`): the finals of the
display periods transcribed once more by Kmynas on the CPU with word timestamps
(`<cache-dir>/follow-along-words`), aligned to the verse text in order. 206
verses were read aloud while a passage was on screen. The alignment is noisy at
its edges (a verse counts as read from half of its words, which can be spread
out), so read these columns as a comparison, not exact times.

| CPU tier, rtf 0.21 | hints (unconfirmed) | lag from verse start p50 / p90 | before the verse ended | verses hinted |
| --- | --- | --- | --- | --- |
| partials off | 92 (0) | 9.5 / 14.3 s | 42% | 72 |
| chunk 1.5 s, no start rule | 99 (1) | 5.5 / 10.5 s | 84% | 77 |
| + start rule, any verse in the window | 112 (16) | 2.8 / 4.8 s | 96% | 81 |
| **+ start rule, next verse only (adopted)** | 109 (7) | 3.2 / 8.0 s | 89% | 82 |

| GPU tier, rtf 0.09 / 0.035 | hints (unconfirmed) | lag from verse start p50 / p90 | before the verse ended | verses hinted |
| --- | --- | --- | --- | --- |
| partials off | 92 (0) | 8.5 / 13.0 s | 51% | 72 |
| chunk 1.5 s, no start rule | 99 (1) | 5.2 / 10.1 s | 87% | 77 |
| **+ start rule, next verse only (adopted)** | 109 (7) | 3.0 / 7.5 s | 93% | 82 |

- "Unconfirmed" is the same-or-next-utterance rule above, which is biased
  against start hints (it wants half of the verse heard in that utterance).
  Every hint was also checked against the timestamp truth and the loose window,
  and the ones failing all three were read by hand next to the verse text.
- The first rule (a start counted for any verse in the window) gave 8 real
  wrong hints in 112: a later verse that opens with words of the one being read
  or quoted (Genesis 22:11 "Viešpaties angelas ... iš dangaus" fired 22:15,
  Romans 8:4 fired 8:9, John 6:9 fired 6:13). The adopted rule takes a start
  only for the sequentially next verse, and only while the verse before it is
  heard in the same transcript (>= 0.25 of its words) or is the verse
  follow-along last offered. Its 7 unconfirmed hints: 3 confirmed by the
  timestamp truth, 2 the out-of-order 1 Kings 17:15 (also there without the
  rule), and 2 checked by hand and correct (Exodus 20:5 "nesilenk prieš juos ir
  netarnauk jiems", 2 Kings 3:3 "tačiau jis laikėsi nuodėmių Nebato sūnaus
  Jeroboamo"), so no wrong hint was added.
- Variants of the adopted rule barely moved: summed IDF 12, previous-verse bar
  0.15 or 0.35 are all within 0.2 s and 1 hint; 4 stems needed instead of 3
  was 0.8 s slower.
- With word timestamps on, transcribe.cpp's Vulkan backend ran Kmynas's decoder
  about 15x slower than the CPU on the GTX 1070 (GPU idle, CPU busy), so the
  benchmark always takes word timestamps on the CPU.
- The adopted rows are the shipped defaults (`cpu-` / `gpu-ts-shipped`): best-score
  selection, and "already read" (the last hint, or a hinted verse the display put
  up) counting for 30 s only. A first run with furthest-verse selection
  (`ts-seq8`) gave nearly the same numbers here, but a review found it offers a
  later verse worded like the one being read (Matthew 7:24 -> 7:26, 5:29 -> 5:30;
  533 such verse pairs within 3 verses in the whole Bible), which auto-show would
  put on screen; this corpus has no such passage, so the benchmark could not see it.

## Book named again restores its chapter (2026-10-24)

The parser now remembers the last chapter cited for each book. A book named
again with no number ("Taigi, Antra karalių ta istorija", after a Proverbs
18:20 quote) switches the context back to that book and chapter, so the next
bare "trisdešimta eilutė" is 2 Kings 6:30 and not Proverbs 18:30. Only names of
two or more tokens count, where a numbered book's prefix is a token ("Antra
karalių"): one-token names are also verbs and people ("Dievas mato", "kaip
Danielius") and moved the context to Matthew or Daniel 9 times in the corpus.
Ordinary two-token phrases still match once the book has been cited ("du
karaliai", "pirmas karalius Saulius", "pagal Joną"; in English "the two
kings", "the first John"). A remembered chapter expires 600 s after that book
was last cited (the context TTL, on each book's own clock), and a bare verse
takes a chapter word only from between where its book was named and the verse
(review of #75: "Patarlių 18 skyrius" before the switch gave 2 Kings 18:30).
Cached replay, 14 services, 164 gold, on all refactor PRs merged:
`kmynas` 153 / 9 -> 154 / 9, `kmynas+canary` 158 / 4 -> 159 / 4 (TP / medium+
high FP; low FPs unchanged). Fixed 10-11's 2 Kings 6:30; nothing else moved.

## Repeats scored consistently, and "Ievutė" (2026-10-24)

Gold listed repeats inconsistently (09-13 has Ephesians 4:25 twice; 05-31 and
07-19 list Deuteronomy 32:11 and Isaiah 1:16 once though each is said twice),
so the same pipeline behavior scored as an FN in one service and an FP in
another. The scorer now treats a citation said again as correct
(`credit_repeats` in `run_benchmark.py`, rule in `README.md`): a gold repeat
counts as found when its other citation is, within 30 s, and a detection naming
a gold citation (its reference, chapter or a verse of its range) from speech
that does not end before it and starts at most 120 s after it is a repeat, not
an FP, unless its speech is where a different gold citation was said (07-05: Hosea 4:16 reported on the Psalm 16:8
utterance stays an FP). TP + FN stays the gold count. The Kmynas map also reads
"Ievutė" as "eilutė" (10-11, GPU tier: "septinta Ievutė", Philippians 4:7);
Canary's "įvate" for the same word is left alone (too ambiguous). Cached
replay, 14 services:

| Run | TP | FN | FP low | FP med+high | Repeats |
| --- | --- | --- | --- | --- | --- |
| `kmynas`, before | 153 | 11 | 14 | 9 | - |
| `kmynas`, book context + repeats | 155 | 9 | 11 | 5 | 7 |
| `kmynas+canary`, before | 158 | 6 | 29 | 4 | - |
| `kmynas+canary`, book context + Ievutė + repeats | 161 | 3 | 23 | 0 | 10 |

Results written before this change were scored without the repeat rule:
re-run the "before" side (a cached replay) rather than comparing against old
result files.

## Context verse both models inferred is medium; gold rule for reading on (2026-10-24)

**Gold rule:** a verse the speaker announces by number while reading on in the
cited passage is a gold reference (the corpus already counted Genesis 22:3 and
2 Kings 6:26/30 this way but not others). Added, timed from the transcripts
(`transcript-span`, not listened to): 07-05 Numbers 33:55; 07-12 1 Kings
19:10, 19:11 and 2 Corinthians 1:10; 10-11 Isaiah 26:4, Luke 7:39 and 7:50.
171 gold now. 05-31's Isaiah 40:27 stays excluded (the annotator's
"27 ignoruojama"). The repeat rule also counts one verse of a cited range said
again (09-24: "14, 2 ir 4" parses and is gold as 14:2-4; verse 4 is then read
out on its own).

**Two-model agreement:** a verse with no book name, resolved from the running
book and chapter ("Ir toliau trisdešimta eilutė"), is "low" from each parser;
when both models infer the same one it is now "medium" (`audio/ensemble.py`),
unless either model's decode was shaky (a repetition loop): those stay "low",
as `unique_references` intends (review of #75; no corpus promotion involved a
shaky decode).
Cached replay, 171 gold, `kmynas+canary`:

| Run | TP high / medium / low | FN | FP low | FP medium+high |
| --- | --- | --- | --- | --- |
| before | 61 / 45 / 61 | 4 | 14 | 0 |
| after | 61 / 78 / 28 | 4 | 13 | 1 |

33 more correct references can be auto-approved; the one medium FP is Isaiah
40:27 above. The risk is a wrong running book (both models then agree on a
wrong verse): the same-book check in "Book named again restores its chapter"
removed the case seen here (Proverbs 18:30). `kmynas` alone is unchanged (one
model, no agreement): 161 TP / 10 FN / 3 low / 4 medium+high FP, 49 TPs low.
The review fixes (shaky decodes, chapter word before a book switch, per-book
expiry, tighter repeat windows) replay identically on both tiers, including
the confidence split.

Tried and not adopted: dropping a reference only one model found when that
model's own parse was already low and not inferred (number shapes near a
book-like word, shaky decodes). Before the gold rule: 4 fewer low FPs for 1
lost TP (Ephesians 2:5-6, split across two utterances). Low references never
auto-show, and split citations are common, so a missed reference costs more
than a glance at a wrong low one.

## Re-check on new recordings

These were fitted and measured on the same 14 services, so their gains are
optimistic until checked on recordings added later. When new services join
the corpus, run both tiers and check each item; record the outcome here.

- **Context verse both models inferred is "medium"** (`audio/ensemble.py`).
  The risk is a wrong running book or chapter: both models then agree on a
  wrong verse and it can be auto-shown. List the medium/high FPs with
  `"inferred": true` in the result JSON and read each against the transcript.
  More than about 1 genuinely wrong per 30 promoted references is a reason to
  revert it.
- **Book named again restores its chapter** (`core/verse_parser.py`): only
  names of two or more tokens, and ordinary phrases such as "du karaliai" or
  "pirmas karalius" match too. Look for inferred FPs right after a bare book
  mention; if they show up, require a citation word ("knyga", a chapter or
  verse word) next to the name.
- **Kmynas "Ievutė" -> "eilutė"** (`audio/spelling_maps.py`): seen once. Any
  spelling-map rule can be dropped if new recordings never use it.
- **Repeat scoring** (`credit_repeats`: 30 s for gold repeats, detections up to
  120 s after a citation) and the gold rule for
  verses announced while reading on: new gold should follow the rule, so the
  scorer and gold keep matching.
