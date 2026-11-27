# Translate — Phase 0 decision memo (P0.7)

Status: **draft for the owner's sign-off**, 2026-11-27.
Brief: [`PHASE0.md`](PHASE0.md). Plan: [`PLAN.md`](PLAN.md) (decisions T1–T9).
Raw results are in `results/translate/` (not in git); each section names its
folder. Dev PC = Ryzen 7 5800X, RTX 5070 Ti 16 GB. "Recognized" = the Kmynas
transcript of the sentence, as the live system would see it; "clean" = the
owner-corrected text.

## 1. What the owner is asked to approve

| # | Decision | Proposal |
| --- | --- | --- |
| D0 | Direction (reverses T1/T2) | Translate is a **standalone app in its own repository**, **local-first by default** (recognition and synthesis always run locally) with an **optional, opt-in OpenAI-compatible translation API**, built behind a **thin transcript-input boundary** (committed text units). Phase 1 runs its own capture → VAD → Kmynas pipeline copied from BibleLive (not imported); an optional transcript tap from BibleLive plugs into the same interface only if the two-pipeline run (§9) shows it is needed. Primary audience: phones in the room. |
| T6 | Translation models | Single `Translator` interface with three modes. **GPU tier (`local-gpu`, default on GPU):** EuroLLM-9B-Instruct, IQ3_XS GGUF, on `llama-server` (CUDA), v1 prompt, two sentences of context, for both English and Russian. **CPU tier (`local-cpu`, default without GPU / on 4 GB laptop):** OPUS-MT through CTranslate2 int8 — English `opus-mt-tc-bible-big-bat-en` (row G, marked "Reduced quality — GPU recommended" in the operator setup UI, §10), Russian `opus-mt-tc-base-bat-zle` (row B). **Optional API (`api`, opt-in):** any user-configured OpenAI-compatible `/v1/chat/completions` endpoint (off by default; v2 prompt, two sentences of context, automatic timeout failover to local OPUS), sharing the same HTTP client as `llama-server`. |
| T7 | Russian direct or pivot | **Direct** on both tiers. The pivot loses on quality and gains nothing on time. |
| T8 | Speech synthesis | **Russian:** Silero `v5_cis_base`, voices `ru_aigul` and `ru_miyau`, with `silero-stress`, CPU; Supertonic 3 as the fallback. **English:** Supertonic 3, CPU. |
| T9 | Codec and player | AAC in fragmented MP4, played through MediaSource (ManagedMediaSource on iOS), with silence fill and the clip audio sent inline in the WebSocket message (confirm AAC/fMP4 on iOS Chrome vs. MP3 fallback in Phase 2, §7). |
| — | Player pacing | 1.1× base speed, 1.2× while more than 6 s behind (borderline on listening, accepted for brief ~9% bursts to avoid skips; test 1.15× as an alternative in Phase 2, §6), skip a clip more than 15 s late, trim clip silence. |
| — | Segmentation | **GPU tier:** BibleLive's default segmentation plus S2 (re-transcribe the open segment every 1.5 s, commit what two passes agree on). **CPU tier:** BibleLive's default segmentation (1.5 s / 8 s / 12 s). |
| — | Dependencies | §11. |

Thresholds that fail or were recalibrated: §10. Open items that do not block
Phase 1: §12.

## 2. Direction change (T1/T2 reversed)

Decided by the owner on 2026-11-24, recorded here as the brief asks (with the
optional translation API mode added 2026-11-27).

- **Was:** T1 a separate app in the BibleLive monorepo; T2 one speech front end
  per machine, with BibleLive's pipeline feeding Translate through a tap.
- **Now:** a standalone app in its own repository with its own pipeline,
  **local-first by default** and fully functional offline. Not every church
  runs both; keeping BibleLive's pinned `backend/constraints.txt`, strict
  `pytest` warning policy, CI, release cycle, lean projection installers, and
  agent context isolated from Translate's heavier runtime stack (`llama-server`,
  CPU `torch` for Silero, `onnxruntime`/Supertonic, `ctranslate2`, `cloudflared`)
  are the real wins.
- **Transcript-input boundary (Phase 1 contract):** the translation/TTS hub
  consumes committed text units (full utterances on CPU default segments, or
  S2-stabilized prefixes on GPU) through a thin transcript-input interface
  rather than coupling directly to audio capture or STT. Phase 1 ships with a
  local adapter wrapping the small slice copied from BibleLive (`capture.py`,
  `vad.py`, `stt_gguf.py`, plus a stripped-down S2 loop). BibleLive's
  `audio/pipeline.py` already talks to `core.server` only over HTTP
  (`POST /api/transcript`), so an optional local tap later plugs into this same
  boundary with a small change, not a redesign. It is not free: BibleLive's
  `--partial-mode grow` partials (`audio/partials.py`) run only while
  follow-along is armed, keep only the newest snapshot, are skipped while a
  final is waiting, and have no two-pass agreement. A tap would need partials
  that are always on while Translate is attached, with S2's agreement step on
  the Translate side.
- **Translator boundary (local-first + optional API):** STT (Kmynas) and TTS
  (Silero / Supertonic) always run locally. Translation sits behind one
  `Translator` interface supporting `local-gpu` (`llama-server`), `local-cpu`
  (OPUS / CTranslate2), and an opt-in `api` mode. Because `llama-server`
  already speaks `/v1/chat/completions`, `local-gpu` and `api` share the same
  HTTP client (`base_url`, `model`, optional `api_key`) with backend-specific
  prompt profiles (`v1` for local EuroLLM-9B, `v2` for frontier APIs). API
  fields stay blank and disabled by default so no transcript leaves the church
  PC unless explicitly configured; when enabled, a tight per-sentence timeout
  (~2.5–3 s) falls back automatically to local OPUS if the network or API
  stalls. Turning it on sends the transcript to a third party under that
  provider's terms (free tiers may use it for training), so the setup UI
  should say so where the key is entered. Measured so far: §4.5.
- **Cost:** the copied capture/VAD/Kmynas slice can drift (extract a shared
  package only if drift actually hurts), and on one machine running both apps
  Kmynas runs twice. The P0.6 contention run measured the *tap* design; the
  two-pipeline design is not measured yet (§9).
- **Rejected:** an OBS plugin; extra YouTube/Facebook language streams (not
  worth running and maintaining separate streams just for alternate audio
  tracks); required cloud infrastructure or hosted cloud GPU pods.
- T3 (a second, translation-tuned segmenter on BibleLive's frames) no longer
  applies: Translate owns its own segmentation (§4).

## 3. Speech recognition (P0.2)

`results/translate/stt/README.txt`. Six owner-corrected 5-minute sermon
windows (30 min). The reference was corrected from Kmynas drafts made with the
default segmentation, so the absolute WER favours Kmynas and the default
setting: compare settings of one system, not systems. Word times are linear
interpolation inside each corrected line.

| System | Setting | WER % | Sentence-end F1 | RTF | Word → text p50 / p95 s | Memory |
| --- | --- | --- | --- | --- | --- | --- |
| Kmynas GPU | default | 3.75 | 0.766 | 0.016 | 4.43 / 9.14 | +1.9 GB video |
| Kmynas GPU | t06 (0.6 s pause, 4/7 s) | 5.47 | 0.548 | 0.021 | 2.26 / 5.84 | |
| Kmynas GPU | **S2 on default** | 4.38 | 0.694 | 0.076 | **1.98 / 3.86** | +2.6 GB video peak |
| Kmynas CPU (3 threads) | default | 3.75 | 0.761 | 0.106 | 5.10 / 10.01 | 1.2 GB RAM |
| Kmynas CPU (3 threads) | t06 | 5.43 | 0.542 | 0.117 | 2.66 / 6.36 | |
| Canary GPU | default | 22.27 | 0.676 | 0.024 | 4.45 / 9.22 | +2.5 GB video |
| Whisper large-v3-turbo GPU | default | 25.30 | 0.708 | 0.043 | 4.61 / 9.42 | +2.5 GB video |

FLEURS `lt_lt` test (986 clips): Kmynas 25.7, Canary 32.7, Whisper 43.1 WER.
These are inflated by the segmenter, not the models: WebRTC VAD drops quiet
frames on clean read speech (whole clip without VAD 13.8 vs 29.9 through the
segmenter, first 150 clips). On sermon audio this does not happen
(`keep_pauses` changes nothing that matters). FLEURS ranks the models; it
does not describe live performance.

**Segment choice.** The short translation settings (t05/t06/t08) cost
+1.7–1.9 WER points (pass) but fail sentence-end F1 (~0.55: Kmynas puts a full
stop at most cuts). On the CPU tier, OPUS B on t06 segments lost 10.2 chrF++
against default segments (CI −12.9..−7.8; −14.0 on corrected text, so it is
the segmentation); joining t06 pieces before translation recovered only 2
points and gave back most of the delay gain. Hence: GPU tier S2, CPU tier
default segments (owner, 2026-11-19).

## 4. Translation (P0.3)

### 4.1 Russian, as recognized, 150 sentences — the direct-or-pivot table

Ratings: cross-evaluated / agent-assisted, reconciled and owner-reviewed
(`ratings/sol-stage1-ru-reconciled.csv` 78 sentences + `stage2-ru-reconciled.csv`
72). Times: dev PC, warm, sermon set. CPU times for B/C/E are 8 threads (PHASE0
asked for 6); B was also run on 3 threads. Text and audio delay are from the
P0.6 run (§6): measured per sentence from the end of its segment, with t06
segments, and the audio start re-simulated with the chosen player pacing.

| Row | Adequacy (0–3) | Critical | FLORES chrF++ / COMET | Time p50 / p95, GPU | Same, CPU | Text delay p50 / p95 | Audio start p50 / p95 | Video memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B direct OPUS | 2.27 | 14.0% | 46.5 / 0.869 | 0.08 / 0.15 s | 0.08 / 0.18 s (8t); 0.13 / 0.29 s (3t) | 0.62 / 1.09 s (CPU tier) | 1.0 / 4.8 s | 0 (CPU) |
| C direct MADLAD-400 3B | 2.28 (78 only) | 12.8% (78) | not run (dropped after stage 1) | 0.56 / 1.14 s | 2.75 / 6.86 s | not run | not run | not recorded |
| D direct EuroLLM-9B IQ3_XS | **2.57** | **10.7%** | 48.1 / 0.887 | 0.21 / 0.39 s | 6.85 / 12.45 s (fail) | 0.45 / 0.94 s (GPU tier) | 1.0 / 7.5 s | +4.2 GB |
| E pivot, own hop 1 | 2.10 | 20.7% | 48.5 / 0.874 | 0.16 / 0.31 s (hops 0.08 + 0.08) | 0.47 / 0.89 s (hops 0.23 + 0.23) | not run | not run | 0 (CPU) |
| E pivot, reusing English | 2.10 | 20.7% | same | 0.08 / 0.16 s (hop 2 only) | 0.23 / 0.47 s | not run | not run | 0 |

- D beats B by **+0.30** adequacy (CI +0.19..+0.41). E loses to both, with the
  most critical errors; its delay was not run through P0.6 because it lost on
  quality. Under the default rule (pivot must win by 0.15 with no more critical
  errors) **direct wins**.
- Clean text, 150 sentences (`stage2`): D 2.74 / 7.3% critical, B 2.41 / 10.0%,
  E 2.26 / 18.7%.
- **Quantization** (`stage3-ru-quants-rated.csv`): IQ3_XS equals Q4_K_M
  (−0.04, CI −0.13..+0.04). Video memory over idle: Q4_K_M +5.7 GB, IQ3_XS
  +4.2, IQ2_M +3.6. IQ3_XS is the proposal.
- EuroLLM on the CPU fails (12.5 s p95). Offloading part of it to system memory
  was tried (36 of 42 layers on GPU → p95 1.2 s): too slow.
- D shows signs of FLORES contamination (it completes "S. Danius" to "Sara
  Danius"), so its FLORES numbers flatter it; the sermon ratings decide.

### 4.2 English

Same 150 sentences as recognized. Agent-rated / cross-evaluated, reconciled and
owner-reviewed; D-vs-A sheet `ratings/stage1-en-user.csv`, G-vs-A sheet
`ratings/bible-full-en.csv`.

| Row | Adequacy | Critical | Fluency (0–2) | FLORES BLEU / chrF++ / COMET | Time p50 / p95, GPU | Same, CPU 3 threads |
| --- | --- | --- | --- | --- | --- | --- |
| A OPUS `tc-big-lt-en` | 2.17 / 2.13 ¹ | 24.0% / 32.0% ¹ | 1.32 / 1.03 ¹ | 34.5 / 58.8 / 0.860 | 0.08 / 0.16 s | 0.31 / 0.67 s |
| G OPUS `tc-bible-big-bat-en` | 2.17 | 25.3% | 1.24 | 33.4 / 58.2 / 0.862 | not run | 0.30 / 0.63 s |
| D EuroLLM-9B IQ3_XS | **2.54** | **10.7%** | 1.77 | 34.6 / 59.2 / 0.873 | 0.20 / 0.36 s | fail (as Russian) |

¹ A was rated twice, in two blind sheets a day apart (first figure with D, second
with G). The gap is rater drift between sessions; each comparison is paired
inside its own sheet.

- **D vs A:** +0.37 adequacy (CI +0.25..+0.49); D better on 53 sentences, A on 8.
- **G vs A:** adequacy +0.04 (CI −0.07..+0.15, a tie); critical −6.7 points
  (CI −13.3..−0.7); fluency +0.21 (CI +0.12..+0.29). Untranslated Lithuanian:
  A 12 sentences, G 1. G reads in a KJV-like register ("eightscore shekels"),
  which suits sermon quotes. Same speed. Hence G for the English CPU tier.
- Clean-text English was translated (`mt/s150-en-clean-gpu-iq3_xs`) but not
  rated.

### 4.3 Newer models screened (2026-11-26) — none replaces D

FLORES devtest, GPU, Q4_K_M (EuroLLM-22B IQ3_XS). Sermon column = adequacy
minus D on 78 sentences, owner-reviewed agent ratings
(`ratings/screen-{en,ru}-user.csv`; "+v2" = the owner's v2 prompt, §4.4).

| Model | EN chrF++ / COMET | RU chrF++ / COMET | GPU p95 | Video memory | Sermon vs D (EN / RU) | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| EuroLLM-9B (D) | 59.2 / 0.873 | 48.1 / 0.887 | 0.36–0.39 s | +4.2 GB | 0 / 0 | **keep** |
| TranslateGemma 12B | 58.3 / 0.873 | 48.9 / 0.897 | 0.74 s | ~8.1 GB | −0.19 / −0.19; +v2 −0.03 / +0.03 | ties D at twice the cost |
| TranslateGemma 4B | 55.1 / 0.863 | 44.9 / 0.884 | 0.38 s | ~2.1 GB | −0.64 / −0.71; +v2 −0.51 / −0.63 | out (below OPUS) |
| Qwen3.5 4B | 56.6 / 0.866 | 46.0 / 0.880 | 0.39 s | ~2.5 GB | −0.26 / −0.42; +v2 −0.19 / −0.36 | about OPUS level |
| Qwen3.5 9B | 58.5 / 0.871 | 47.8 / 0.889 | 0.49 s | ~4.6 GB | not rated | D matches it cheaper |
| EuroLLM-22B IQ3_XS | 58.3 / 0.868 | 48.8 / 0.890 | 0.78–0.90 s | ~9.6 GB | not rated | no gain, too big |
| EuroLLM-1.7B | 51.6 / 0.810 | no Russian ² | — | — | not rated | fails |

² Asked for Russian it answers in English or not at all (300 of 1,012 FLORES
sentences empty).

On the CPU (3 threads) the 4B models take 3.9–4.0 s p95: they fail the CPU
tier. Smaller OPUS-family CPU candidates (3 threads; FLORES BLEU / chrF++ /
COMET; sermon p95): HPLT `lt-en` 26.7 / 51.5 / 0.815, 0.23 s (out);
`opus-mt-tc-bible-big-mul-mul` to Russian 14.0 / 39.9 / 0.759, 0.81 s (out);
`opus-mt-lt-ru` (2020) not run, it ships only a pickle checkpoint and B
supersedes it. Not considered: Hunyuan-MT (no Lithuanian), TildeOpen (base model
only), NLLB (non-commercial), LTEngine (an AGPL server around Gemma GGUFs:
TranslateGemma covers its quality), Firefox's small lt-en model (needs the
Bergamot runtime).

### 4.4 Prompt

`benchmarks/translate/prompts/v2.json` is the owner's use-case prompt (live
church subtitles, speech-recognition input, never add facts, keep speaker and
addressee, standard biblical names and citation format, вы for the
congregation and Ты for God in prayer, preserve negations, output only). It
helps TranslateGemma 12B (+0.16 EN / +0.21 RU) and Qwen a little, is neutral
for EuroLLM in English and **hurts EuroLLM in Russian** (−0.24; −0.14 without
one cascade, below). **Keep v1 for local EuroLLM** and bind `v2` as the
default prompt profile for the optional frontier `api` mode; per-model prompt
tuning is Phase 1 work. Note: v2 has **not** been measured on an API model yet;
the cloud check in §4.5 used v1.

**Context cascade.** With v2, EuroLLM once prefixed "Язык - русский. Перевожу
с литовского…" and, because its own output is fed back as context, repeated it
through 13 following sentences. Phase 2 needs a guard (shared by `local-gpu`
and `api` modes): strip such preambles and never feed a rejected or suspicious
output back as context.

### 4.5 Cloud check for the optional `api` mode (2026-11-27)

Harness: `mt_bench.py` row K, any OpenAI-compatible `/chat/completions`
endpoint, with row D's v1 prompt and two sentences of context; same 78
sentences as §4.3. Ratings `ratings/cloud-en-user.csv` (blind, D rows reused
from the earlier owner-reviewed sheets).

| Model (Gemini API, free tier) | Adequacy, EN | Critical | vs D | Time p50 / p95 / max |
| --- | --- | --- | --- | --- |
| D EuroLLM-9B, local GPU | 2.53 | 10.3% | — | 0.20 / 0.36 s |
| Gemma 4 26B-A4B, thinking `minimal` | 2.46 | 10.3% | −0.06 (CI −0.23..+0.09), tie | 3.8 / 20.3 / 30.1 s |

- **Quality:** the one hosted model measured ties D in English; none beats it.
  Russian was not rated. An `api` mode is a way to reach D-level English
  without a GPU, not an upgrade over the GPU tier.
- **Time:** free-tier response times are far outside the 0.7 s line, hence the
  timeout and OPUS fallback in §2. A paid tier is expected to be faster but is
  **not measured** (open item 9). Thinking must be off: with Gemma's default
  thinking one sentence took 45 s and ran out of tokens before translating.
- **Not run:** Gemini 4 Hydrogen (free-tier rate limit; no billing), Gemini 3.5
  Flash Lite, Claude Haiku 5.5 and GPT-6 Luna (no API keys). Gemini 3.5 Live
  Translate is speech-to-speech with no text input or prompt, so it does not fit
  the `api` mode.

## 5. Speech synthesis (P0.4)

`results/translate/tts/`. CPU 4 threads unless noted. First audio = a 12-word
sentence.

| Engine | Language | RTF p50 / p95 | First audio | Sample rate | Listening | Licence (weights) |
| --- | --- | --- | --- | --- | --- | --- |
| Silero `v5_cis_base` ru_aigul | RU | 0.037 / 0.041 | 0.25 s | 48 kHz | 60/60 accepted, 0 stress errors, 0 name errors (both voices) | MIT |
| Silero `v5_cis_base` ru_miyau | RU | 0.037 / 0.043 | 0.23 s | 48 kHz | (same sheet) | MIT |
| Supertonic 3 M1 | RU | 0.20 / 0.24 | 1.16 s | 44.1 kHz | nicer voices; fallback | OpenRAIL-M |
| Supertonic 3 M1 / F1 | EN | 0.15 / 0.19 | 0.84–0.85 s | 44.1 kHz | **owner's choice** | OpenRAIL-M |
| Kokoro-82M af_heart / am_michael | EN | 0.26–0.27 / 0.27–0.33 | 1.26–1.45 s | 24 kHz | rejected: sounds like generic "AI video" narration | Apache-2.0 |
| Kokoro-82M, GPU | EN | 0.02 / 0.04 | 0.09 s | 24 kHz | (same voice) | Apache-2.0 |
| Qwen3-TTS 0.6B, GPU | both | 3.3 | — | 24 kHz | out (slower than real time) | — |

Chatterbox Multilingual: not run (time). Supertonic English first audio
(0.84 s) is just over the 0.8 s pass line; with one clip per sentence and the
player's backlog this does not change listener delay (§6).

## 6. Contention and end-to-end delay (P0.6)

`results/translate/contention/README.txt`. Dev PC, 20 min of the 05-31 sermon
in real time, **tap design** (one Kmynas shared with BibleLive), t06 translation
segments, no OBS. The 4 GB laptop was not run.

| | CPU base | CPU + Translate (B, Silero) | GPU base | GPU + Translate (D, Silero) |
| --- | --- | --- | --- | --- |
| BibleLive detections | 19 | 19 (identical) | 20 | 20 (identical) |
| Queue wait p95 / max | 0.00 / 0.05 s | 0.45 / 0.52 s | 0.00 / 0.00 s | 0.00 / 0.59 s |
| Result after utterance p95 | 0.87 s | 1.30 s | 1.45 s | 1.64 s |
| Video memory, mid-run | — | — | +2.3 GB | +6.5 GB (Translate +4.3) |
| Text delay p50 / p95 | | 0.62 / 1.09 s | | 0.45 / 0.94 s |
| Translation time p50 / p95 | | 0.07 / 0.13 s | | 0.21 / 0.36 s |
| Audio start p50 / p95, player at 1.0× | | 2.4 / 8.8 s | | 6.9 / 24.4 s |
| Audio start, **chosen pacing** (re-simulated) | | 1.0 / 4.8 s, max 7.6, 0 skipped | | 1.0 / 7.5 s, max 14.0, 0 skipped, 1.2× 9% of the time |

**Where the delay comes from.** Translation itself is small (≤0.4 s p95). Two
things dominate:

1. **Waiting for the sentence to end.** Delays above are per sentence from the
   end of its segment; a listener hears a word later by up to the segment's
   length. P0.2's word-level figures (§3) are the floor: GPU with S2 1.98 /
   3.86 s, CPU with default segments 5.10 / 10.01 s. Note that P0.6 ran on t06
   segments (`2.66 / 6.36 s` word-to-text floor); because the CPU tier uses
   default segments (1.5 s / 8 s / 12 s), longer utterances also delay when TTS
   can start speaking, so real-world CPU listener lag will feel closer to
   ~6–12 s behind the speaker rather than the `1.0 / 4.8 s` post-segment figure
   (**estimate** from the P0.2 floor plus the P0.6 post-segment delay; not
   measured as one run with default segments).
2. **Audio backlog.** Russian speech is nearly as long as the Lithuanian
   (1,152 s out for 1,200 s in on the GPU tier, EuroLLM writes longer), so the
   player falls behind in bursts. The chosen pacing fixes this without skips;
   the owner listened: 1.25× too fast, 1.2× borderline, 1.15× fine. Because
   1.2× only triggers 9% of the time in bursts, it is proposed as the initial
   catch-up cap, with 1.15× to be tested in Phase 2 to see if it also avoids
   skips.

## 7. Phones (P0.5)

`results/translate/phone_spike/README.txt`. Cloudflare quick tunnel, mobile
data.

- **iPhone (iOS 27.2):** MediaSource + AAC with silence fill and inline audio:
  Safari 10.2 min locked, 59/59 played, 0 stalls; Chrome (MP3) 9.6 min, 56/56;
  Low Power Mode 11.3 min, 66/67. **Pass.** Before the silence fill, playback
  wedged after ~5.5 min locked. Confirm in Phase 2 whether AAC/fMP4 also holds
  on iOS Chrome or whether an MP3 fallback path is needed.
- **Android: emulator only** (no real phone available). MediaSource played
  under forced Doze and battery saver; the plain media element and Web Audio
  players stopped. Chrome's battery setting "Restricted" cuts the socket
  (fail): the listener page should detect missed clips while hidden and show
  how to set it back.
- Interruptions: another app's audio pauses ours correctly; a Resume button and
  a Media Session play handler bring it back. Wi-Fi → mobile data: the socket
  reconnects in ~16 s and 2 clips were lost → the gateway should replay clips
  missed since the last sequence number.
- Chrome's lock screen shows "--" for the timeline: show "Live" and disable
  seeking (Phase 2).

## 8. Licences (read 2026-11-26 and 2026-11-27)

| Component | Licence | Source read | Note |
| --- | --- | --- | --- |
| EuroLLM-9B-Instruct (and bartowski GGUF) | Apache-2.0 | HF model cards | |
| OPUS-MT `tc-bible-big-bat-en` (G) | Apache-2.0 | HF model card, 2026-11-27 | |
| OPUS-MT `tc-big-lt-en` (A), `tc-base-bat-zle` (B) | CC-BY-4.0 | HF model cards, 2026-11-27 | attribution required |
| Silero `v5_cis_base` (incl. ru_aigul, ru_miyau) | MIT | `LICENSE_CIS` in silero-models | **the rest of silero-models (v5_ru, CIS Ext) and the repository code, incl. `hubconf.py`, are CC BY-NC(-SA): ship the `.pt` with our own loader, never `torch.hub`** |
| silero-stress | MIT | its repository | |
| Supertonic 3 weights | BigScience OpenRAIL-M | HF model card | commercial use allowed; the use restrictions must be passed on to users and machine-generated content needs a disclaimer; SDK code MIT |
| Kokoro-82M | Apache-2.0 | HF model card | not proposed |
| TranslateGemma | Gemma terms | HF (gated) | not proposed |
| NLLB-200 | CC-BY-NC | — | reference only, never shipped (not run) |

**Required Phase 1/2 licence hooks:** (1) load Silero `v5_cis_base.pt` via a
clean `torch.package` loader without importing `silero-models` repo code or
`hubconf.py`; (2) pass on Supertonic 3's OpenRAIL-M use restrictions in
distribution docs and display a machine-generated audio disclaimer in the
listener UI.

## 9. Hardware tiers as proposed

| Tier | Recognition | Translation | Synthesis | Measured |
| --- | --- | --- | --- | --- |
| GPU (comfortable) | Kmynas GPU + S2 | EuroLLM-9B IQ3_XS, `llama-server`, both languages | Silero RU, Supertonic EN, CPU | tap design: +6.5 GB with BibleLive; standalone: **not measured** |
| CPU | Kmynas CPU, default segments | OPUS G (EN), B (RU), CTranslate2 int8 (or optional `api` mode) | same | tap design, dev PC |
| 4 GB laptop | Kmynas GPU | OPUS on CPU (or optional `api` mode) | same, CPU | **not run** |

Not measured yet, and needed before the GPU tier's memory and concurrency
figures can be trusted:

- **Two separate pipelines with OBS** on the dev PC and the 4 GB laptop.
  **Estimate**, added up from separate runs, not measured together: Translate
  standalone with Kmynas S2 (+2.6 GB peak in P0.2) and EuroLLM-9B IQ3_XS
  (+4.2 GB) lands near **~6.8 GB** on its own. Alongside BibleLive (measured
  +2.3 GB mid-run in `--stt kmynas+canary` mode with Canary on the CPU, P0.6),
  two separate pipelines would land around **~9 GB before OBS**, against the
  **measured +6.5 GB** when Translate shares BibleLive's recognizer (P0.6).
  That fits a 16 GB GPU easily, but an 8 GB church GPU running both apps
  simultaneously will likely overflow—so this run decides whether the optional
  BibleLive tap (or running Translate's Kmynas on CPU, or using the optional
  `api` mode to drop EuroLLM's +4.2 GB) is needed for 8 GB dual-app setups.
- **English + Russian running concurrently:** on **GPU**, two requests per
  sentence to one `llama-server` will roughly double serialized translation p95
  (~0.75 s) unless parallel slots are configured (increasing KV-cache VRAM).
  On **CPU / 4 GB laptop**, the isolated benchmark thread counts sum to 17
  threads (`3t` Kmynas + `3t` OPUS EN + `3t` OPUS RU + `4t` Silero + `4t`
  Supertonic), which will thrash a 4–8 core laptop unless thread pools are
  explicitly capped and serialized.

## 10. Thresholds: results and recalibration

| Measure | Result | Verdict |
| --- | --- | --- |
| Sermon WER, Kmynas | 3.75% (reference biased towards Kmynas) | pass |
| WER cost of translation segments | +1.7–1.9 points | pass, but see F1 |
| Sentence-end F1 | default 0.77 (pass); S2 0.69 (conditional); t06 0.55 (fail) | proposal uses default / S2 |
| LT→EN FLORES | A 34.5 / 0.860, D 34.6 / 0.873 pass; G 33.4 BLEU (within 1 of A) | pass / G conditional |
| LT→RU FLORES | no row meets the chrF++ margin over B (D +1.53, E +1.99; E loses on the human ratings) ¹ | **fail as written → recalibrated** |
| Human adequacy, clean (RU) | D 2.74 / 7.3% | conditional (critical 7.3% > 5%) |
| Human adequacy, recognized | RU D 2.57 / 10.7%; EN D 2.54 / 10.7% | conditional (critical just over 10%) |
| | RU B 2.27 / 14.0% | conditional |
| | EN G 2.17 / 25.3% (A 24–32%) | **fail on critical** |
| Translation time p95 | GPU D 0.39 s; CPU B 0.29 s, G 0.63 s (3 threads) | pass |
| End to end, GPU tier | word → text p95 ≈ S2 floor 3.9 s + MT 0.4 s; audio start p50 1.0 s | pass on parts; not measured as one run with S2 |
| Video memory | +6.5 GB tap design | conditional; standalone not measured |
| BibleLive in contention | detections identical; queue wait p95 0.45 s | pass (tap design) |
| Synthesis | Silero RTF 0.04, 0.25 s, 0 stress errors; Supertonic EN RTF 0.19, 0.84 s | pass / pass except first audio by 0.04 s |
| Phones | iPhone 10 min locked pass; Android emulator only | pass on iPhone; Android unconfirmed |

¹ `PHASE0.md` grades it: pass needs +4 chrF++ over B (and COMET-22 ≥ 0.82),
conditional +2 to +4, fail below +2. Neither D (+1.53) nor E (+1.99) reaches +2.

**Recalibrated: LT→RU FLORES.** Requiring a fixed chrF++ jump over B assumes the
challenger is a similar MT system. An LLM paraphrases, which chrF++ punishes,
and COMET ranks D first (0.887 vs 0.869) while the blind sermon ratings put D
well ahead (+0.30, CI +0.19..+0.41). Proposal: for LT→RU, **pass = COMET-22 ≥
row B's and no chrF++ loss to B**; the human ratings stay the deciding test.
This was set after the baselines and D's FLORES run, and it changes no
decision: D wins on the ratings either way.

**Not recalibrated: critical errors on recognized text.** Many critical errors
on recognized text are names and numbers Kmynas misheard, which no translator
can fix; a 10% line may be too tight for any system. But the brief allows
recalibration only before looking at the candidates' ratings, which has
happened, so the line stands. Consequence: **English on the CPU tier fails
the critical-error threshold** (G 25.3%), and the GPU tier is conditional in
both languages. The owner chooses for CPU-tier English:

1. ship it labelled **"Reduced quality (GPU recommended)"** directly in the
   operator setup UI (proposal), with the untranslated-text guard below—while
   letting GPU-less setups opt into the optional OpenAI-compatible `api` mode
   for higher-quality English without buying a GPU;
2. ship the CPU tier as Russian only;
3. require a GPU for English.

**Cheap extra (owner agreed 2026-11-26):** detect Lithuanian left untranslated
in the output and drop or retry that sentence. On the G-vs-A sheet, it would
have removed 12 of A's 48 critical errors (32.0% → 24.0%); G already leaves
only 1, so for G its value is as a safety net.

## 11. Dependencies for Phase 1 and 2 (for approval)

For the new repository; licences to confirm at install.

| Package | Used for | Licence |
| --- | --- | --- |
| `fastapi`, `uvicorn[standard]`, `pydantic`, `websockets` | hub, gateway | MIT / BSD-3-Clause / MIT / BSD-3-Clause |
| `httpx` | `/v1/chat/completions` client (`llama-server` and optional `api` mode) | BSD-3-Clause |
| `transcribe-cpp` (as BibleLive's `gguf` extra) + Kmynas Q8 | recognition | MIT |
| WebRTC VAD (copied from BibleLive with the capture code) | VAD | as in BibleLive |
| `ctranslate2`, `sentencepiece` | OPUS-MT on the CPU tier (and API timeout fallback) | MIT / Apache-2.0 |
| `llama-server` binary from llama.cpp (CUDA build), shipped or downloaded | EuroLLM on the GPU tier | MIT |
| `torch` (CPU build), `silero-stress`, Silero `v5_cis_base.pt` | Russian synthesis | BSD-3-Clause / MIT / MIT |
| `supertonic` SDK + `onnxruntime` | English synthesis | MIT / MIT; weights OpenRAIL-M |
| ffmpeg (bundled, as in BibleLive) | AAC encoding | as bundled |
| `cloudflared` (external program) | quick tunnel | Apache-2.0 |

`llama-cpp-python` is **not** proposed: its CUDA wheel did not load on the dev
PC, while the `llama-server` binary worked throughout. `torch` is the heaviest
item (only for Silero); a later step can try exporting Silero to ONNX. Keep
WebRTC VAD (including its `setuptools<81` pin for `pkg_resources`), as in
BibleLive and the Phase 0 benchmarks.

## 12. Open items (do not block Phase 1)

1. Two-pipeline contention run with OBS, dev PC and 4 GB laptop (§9). Decides
   the optional BibleLive tap and the GPU tier's memory figure.
2. Dual-language concurrency (EN + RU through one `llama-server` on GPU;
   explicit thread-pool caps and serialization on CPU / 4 GB laptop, §9).
3. A real Android phone test, plus verifying AAC/fMP4 on iOS Chrome vs. an MP3
   fallback path (§7).
4. Test 1.15× catch-up pacing against 1.2× to confirm whether 1.15× still
   avoids skips (§6).
5. The context-cascade guard and the untranslated-text check (Phase 2, §4.4,
   §10).
6. Licence guardrails: clean `torch.package` loader for Silero `v5_cis_base.pt`
   (never `torch.hub` / repo code) and Supertonic 3 OpenRAIL-M disclaimers in
   the listener UI and docs (§8).
7. Per-model prompt tuning; v2 for EuroLLM Russian needs work.
8. Clean-text English ratings were not done (recognized text decided).
9. Optional `api` mode verification: paid-tier, single-sentence, non-thinking
   API latency (the free-tier figures in §4.5 do not count), Russian ratings for
   the chosen API model, v2 vs v1 on it, timeout failover to local OPUS, and
   ensuring API settings remain blank/off by default in the setup UI (§2, §4.5,
   §10).

## 13. Reviewer checkpoint

- **Blind sheets:** system names were only in `ratings/key.json`. Agent raters
  pre-rated and cross-evaluated the sheets (stated per sheet above); the owner
  reviewed and reconciled every sheet (`stage1-ru`, `stage2-ru`, `stage1-en`,
  `bible-full-en`, and `screen-{en,ru}`). A pre-filled copy of the G-vs-A sheet
  also appeared in the repo before the reconciled file came back (kept as
  `bible-full-en-prefilled.csv`) and points the same way (G − A adequacy +0.08,
  critical −11.3 points).
- **Tuning on sermon150:** no rule or setting was tuned on it. The v2 prompt
  was written by the owner from the use case, then compared with v1 on 78
  sermon sentences; the comparison chose v1 for EuroLLM. Segment settings
  were chosen from P0.2 metrics and one CPU-tier chrF++ comparison against the
  corrected text.
- **Re-runs from the manifest:** not done yet for this memo.
- **Licences:** §8, with dates.
- `bench_audio/` untouched; nothing under `backend/` or `frontend/` changed.
  The harness in `benchmarks/translate/` changed (rows G–K, prompt styles,
  `prompts/v2.json`), uncommitted.