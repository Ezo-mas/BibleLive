# Live translation blueprint ("Translate")

Product exploration and execution plan, written 2026-11-12 against BibleLive
`main` at `3d261fb`, with the owner's answers of the same date recorded in
section 2. Written for the implementation lead and builder and reviewer
agents. No code has been written.

Companion files:

- [`PHASE0.md`](PHASE0.md) — the bake-off as a standalone brief; can start now.
- [`phase0/TRANSCRIPT_CORRECTION.md`](phase0/TRANSCRIPT_CORRECTION.md) — which
  audio to correct and how.
- [`phase0/RATING_GUIDE.md`](phase0/RATING_GUIDE.md) and
  [`phase0/rating_sheet.template.csv`](phase0/rating_sheet.template.csv) — how
  to rate translations.
- [`RUSSIAN_BIBLE_IMPORT.md`](RUSSIAN_BIBLE_IMPORT.md) — Phase 4 import steps
  and the versification map.

**How claims are marked.** (code) read from the repository at `3d261fb`.
(web) found online on 2026-11-12; sources at the end. (assumed) an estimate or
prior knowledge, not checked that day. Anything assumed that matters is
measured in Phase 0. "The blueprint" means `docs/IMPROVEMENT_PLAN.md`.
"Translate" is a working name.

## 1. Summary and recommendation

**Build it, as a separate app in the same repository, after blueprint
milestones M2 and M4. Run the Phase 0 bake-off now: it needs none of the
blueprint and it decides whether Russian is viable.**

- **Reuse is real but narrow.** Capture, VAD, the Lithuanian speech models and
  their management, the launcher, QR code generation, the token model and the
  frontend toolchain carry over. BibleLive's server, approval flow and
  WebSocket protocol do not, and are not touched.
- **One speech front end per machine, shared as a process.** BibleLive's audio
  pipeline gets one optional output, a local transcript tap. The translation
  hub subscribes. The tap carries segments cut for translation, not
  BibleLive's 8 to 12 second utterances.
- **Russian is the open question.** Lithuanian recognition is measured and
  strong (code). Lithuanian to English has a solid permissive baseline.
  Lithuanian to Russian does not: the only direct permissive model found
  scores 21.3 BLEU on FLORES-101 (web).
- **Phones in pockets are the second.** iOS interrupts Web Audio when the
  screen locks unless the page plays through a media element (web). Phase 0
  tests real phones before the audio contract is frozen.
- **Audience delivery is one small gateway program.** For now it runs on the
  operator machine behind a tunnel: a Cloudflare quick tunnel for testing. The
  public address is a setting. A named tunnel or a VPS relay comes later and
  changes only that setting and a key.
- **Committed text is never retracted.** Speech synthesis speaks only
  committed text; when the speaker outpaces it, synthesis speeds up and then
  drops whole sentences. The text stays complete.
- **Latency target:** text 4 s median and 8 s at the 95th percentile; audio
  start 7 s median and 10 s at the 95th percentile, from when a word is
  spoken.

Key figures: Kmynas real-time factor 0.017 on GPU and 0.06–0.21 on CPU
(code); Kmynas video memory 1.1 GB, 2.9 GB with Canary (code); OPUS-MT BLEU on
FLORES-101 34.3 for LT→EN and 21.3 for LT→RU (web); 50 listeners designed for.

## 2. Decision log

### Questions and the owner's answers (2026-11-12)

| ID | Question | Finding | Answer |
| --- | --- | --- | --- |
| Q1 | Phase 0 needs WER and COMET "on `bench_audio/`". | The folder holds audio only; gold files list Bible references, not transcripts; no reference translations (code). | The owner corrects the Lithuanian transcripts and rates the Russian (and English) output. Materials: `phase0/TRANSCRIPT_CORRECTION.md`, `phase0/RATING_GUIDE.md`. FLORES-200 supplies reference-based scores. |
| Q2 | Official Bible text in Russian. | `bible_data/` has KJV, WEB and LTB; no Russian (code). | The owner sources a Russian Bible. Phase 4, outside the MVP. Steps and the versification map: `RUSSIAN_BIBLE_IMPORT.md`. |
| Q3 | "A few euros a month." | The cheapest Hetzner server is about €5.49–5.99 after 2026 increases (web). | Tunnel only for now. Testing uses a Cloudflare quick tunnel (no account, no domain). The public address is a setting, not a constant. A named tunnel or VPS comes later. |
| Q4 | "No stored audio." | BibleLive's launcher records each service by default (code). | Translate and the relay never store audio. BibleLive's own session recording is unchanged and used only for local benchmarks. The join page must not claim "nothing is recorded". |
| Q5 | QR on "a screen on the projector". | BibleLive's display messages are ask-first (code). | Translate serves its own full-screen join page. Nothing is added to BibleLive's `/display`. |
| Q6 | Lithuanian only, no detection. | Services can switch to English at runtime (code). | Translate shows "paused: source language" while BibleLive is set to English. |
| Q7 | Licence preference. | Kmynas, Canary and OPUS-MT are CC-BY-4.0 (web). | CC-BY-4.0 with a NOTICE file is fine. GPL, restricted and non-commercial licences need the owner's sign-off. |
| Q8 | New dependencies. | `AGENTS.md` and blueprint D6 forbid them without asking (code). | Translate needs new dependencies. D6 applies to BibleLive core, not to Translate. They go in an optional `translate` extra so a BibleLive-only install is unchanged, and are listed per work package for approval before each phase (section 13). |
| Q9 | With BibleLive or alone? | — | Both. With BibleLive it subscribes to the pipeline's tap; alone, the launcher starts the same pipeline with reference sending off. |

### Decisions

Accepted by the owner on 2026-11-12 unless marked.

| ID | Decision | Rejected alternatives | Status |
| --- | --- | --- | --- |
| T1 | A separate app in the same monorepo: own processes, config, protocol and frontend entries, sharing libraries with BibleLive. | A module inside BibleLive's server; a separate repository. See section 11. | Decided |
| T2 | One speech front end per machine. BibleLive's pipeline gains an optional transcript tap; the hub subscribes over loopback HTTP. | A second capture and model in the hub; loading the hub inside the pipeline process. | Decided; re-checked in P0.6 |
| T3 | The tap carries segments from a second, translation-tuned segmenter over the same frames. BibleLive's segmentation and detection are untouched. | Reusing BibleLive's utterances; changing BibleLive's segmentation. | Decided; parameters from P0 |
| T4 | MVP commit policy: short segments, finals only, one sentence per committed unit, append-only. | Re-transcription with prefix agreement (kept as a GPU-tier upgrade); word-level streaming (Kmynas cannot stream). | Decided |
| T5 | Translation and synthesis sit behind two service interfaces; engines are chosen by config per language pair. | Calling a model library directly from the hub. | Decided |
| T6 | Which translation models ship. Hypothesis: MADLAD-400 on GPU, OPUS-MT on CPU, an LLM as challenger. | NLLB-200, SeamlessM4T (non-commercial); cloud APIs. | **Waits for Phase 0** |
| T7 | Russian direct or by pivot through English, decided from measured quality and measured delay side by side. | Deciding without sermon data. | **Waits for Phase 0** |
| T8 | Which speech-synthesis models ship. Hypothesis: CPU-class models. | XTTS-v2, F5-TTS, MMS-TTS, most Silero models (non-commercial); cloning the preacher's voice. | **Waits for Phase 0** |
| T9 | One audio clip per committed sentence per language, encoded once on the operator machine and fanned out unchanged. | Raw PCM; a continuous radio-style stream. | Decided; codec and player from P0 |
| T10 | Backlog: speed synthesis up to 1.3x, then drop whole oldest sentences. Text is never dropped. | Condensing with an LLM; an unbounded queue. | Decided |
| T11 | Text is shown when committed and is not held back for audio. The sentence being spoken is highlighted. | Delaying text to match audio. | Decided |
| T12 | Audience delivery is a standalone gateway program with one contract. Now: on the operator machine behind a tunnel, quick tunnel for testing, with the public address as a setting. Later: a named tunnel or a VPS relay. | Tunnelling to the hub itself; a hosted pub/sub service. | Decided |
| T13 | Three separate secrets: Translate's operator token, the tap's loopback token, the publisher key. The audience link is a fourth, read-only capability. BibleLive's operator token is used for none. | Reusing BibleLive's operator token. | Decided |
| T14 | Scripture insertion and the glossary are Phase 4. The MVP shows a detected reference as a label only. | Verse substitution in the MVP. | Decided |
| T15 | Translate never reads or drives BibleLive's approval state and never sends BibleLive WebSocket messages. | Showing the official verse when the operator approves a reference. | Decided |
| T16 | Retention: none on the gateway; no audio anywhere in Translate; an optional text log on the operator machine, off by default. | Keeping clips for replay. | Decided |
| T17 | Languages are data: a target is a config entry naming a translator, a synthesizer and a voice. The MVP ships two. | Hard-coded EN and RU paths; a plugin system. | Decided |

## 3. Blueprint against main

`main` is still at `3d261fb`, the commit the blueprint was written against
(code). Nothing read for this plan contradicts the blueprint's description of
the code. Three points:

- **The blueprint describes a future state.** The supervised launcher and
  version stamp (M2), the split of `pipeline.py` into a front end and an
  `UtteranceDetector` (M4), per-socket writers (M3) and the launcher platform
  layer (M7) are plans, not code. This plan depends on M2 and M4 and says so
  per package.
- **Blueprint D6 ("no new dependency")** applies to BibleLive core only, by
  the owner's answer to Q8.
- **A promise in the code limits reuse.** `POST /api/transcript` states that
  sermon text is "not stored, logged, or forwarded" (`app.py:666`) (code).
  Translate forwards sermon text to phones by design, so it must not hang off
  that endpoint. The tap is a separate, opt-in output, and BibleLive's promise
  stays true.

Also: the launcher downloads Kmynas from `Ezomas/kmynas-parakeet-lt-v4-gguf`,
a conversion of `kristijonas/kmynas-parakeet-lt-v4` (code), which is CC-BY-4.0
and trained only on LIEPA-3, also CC-BY-4.0 (web). A bundle must carry that
attribution.

## 4. Architecture

```
speech front end ──tap──▶ Translate hub ──publisher──▶ audience gateway ──▶ phones
(BibleLive's audio        (session, commit policy,     (one publisher in,     (HTTPS + WSS,
 pipeline process;         one translator and one       listener page and      read-only)
 two segmenters over       synthesizer per language,    fan-out per language;
 the same frames)          operator console, join page) no inference, no storage)
   loopback HTTP              new process                 new process: local now,
                                                          VPS later
```

- **Per-language work happens once.** One translation and one synthesis per
  committed sentence per target; the gateway copies the result to every
  listener of that language.
- **The publisher always dials out.** The gateway never connects to the hub.
- **BibleLive keeps working if Translate fails.** The tap is fire-and-forget
  with a bounded queue, like the existing transcript sender (code).

## 5. Interface contracts

Frozen in Phase 1 so Phase 2 can run in parallel. Every message has a pydantic
model and a checked-in example under `backend/translate/contracts/fixtures/`,
tested from Python and TypeScript. Rules: additive changes only; every hello
carries `v`; unknown fields are ignored; unknown message types are ignored by
clients and rejected by the gateway.

### C1. Tap: pipeline to hub (loopback HTTP)

```
POST /ingest/segment      Authorization: Bearer <tap token>
{ "v": 1, "seg_id": 412, "lang": "lt", "text": "…", "final": true,
  "t0": 1834.20, "t1": 1838.95,          // seconds on the pipeline's session clock
  "low_confidence": null | "repetition" | "…",
  "stt": { "engine": "kmynas", "ms": 81 } }
POST /ingest/status       { "v": 1, "language": "lt", "problems": [ {code, params, text} ] }
Reply 200 { "accepting": true }            // false = hub paused; pipeline may skip the work
```

`final: false` is reserved for draft segments (Phase 4). `/ingest/status`
carries the pipeline's language, which is how the hub knows to show "paused:
source language" (Q6).

### C2. Service interfaces inside the hub (Python protocols)

```
Translator
  pairs() -> set[(src, tgt)]
  warmup() / close()
  translate(units: list[SourceUnit], src, tgt,
            context: list[str], glossary: Glossary | None) -> list[Translation]
      Translation = { text, engine, ms, flags: set["glossary", "fallback", "pivot"] }

Synthesizer
  voices(lang) -> list[Voice]
  warmup() / close()
  estimate_seconds(text, lang) -> float
  synthesize(text, lang, voice, rate: float = 1.0) -> Pcm   // 16-bit mono, model's rate

AudioEncoder      encode(Pcm) -> Clip { codec, bytes, dur_ms }
CommitPolicy      feed(Segment) -> list[CommitUnit];  flush() -> list[CommitUnit]
AudienceTransport open_session(meta) -> JoinInfo;  publish(msg);  close_session();  stats()
                  implementations: RelayTransport (WSS out), LocalTransport (gateway on this machine)
```

Engines are blocking and run on worker threads owned by the hub, one queue per
target language. A fake of each ships in Phase 1 and stays as the test double.

### C3. Publisher to gateway (WSS, path `/pub`)

| Direction | Message | Fields |
| --- | --- | --- |
| hub → | `hello` (first message) | `v`, `key`, `install_id` |
| ← gateway | `hello_ok` | `v`, `max_listeners` |
| hub → | `session_open` | `session_id`, `join_code`, `title`, `source_lang`, `targets: [{lang, label, tts}]`, `started_at` |
| hub → | `segment` | `seq`, `lang`, `kind: "mt" \| "scripture" \| "notice"`, `text`, `final`, `src_t0`, `src_t1`, `ref?: {book, chapter, verse_start, verse_end, label}` |
| hub → | binary audio frame | 4-byte length, JSON header `{seq, lang, codec, dur_ms}`, then the clip |
| hub → | `audio_skip` | `lang`, `seqs`, `reason: "backlog" \| "muted" \| "tts_failed"` |
| hub → | `state` | `paused`, `pause_reason: "operator" \| "source_language" \| null`, `langs: {lang: {muted, tts}}` |
| hub → | `rotate_code`, `session_close` | `join_code` / `reason` |
| ← gateway | `stats` every 5 s | `listeners: {lang: n}`, `audio_listeners`, `refused` |
| ← gateway | `error` | `code`, `message` |

`seq` rises by one per committed unit and is shared across languages. The
public address is not in this contract: the hub builds the join link from its
own `public_base_url` setting.

### C4. Listener to gateway (page `/s/<join_code>`, WSS `/s/<join_code>/ws`)

| Direction | Message | Fields |
| --- | --- | --- |
| phone → | `join` (first message) | `v`, `lang`, `audio: bool`, `last_seq?` |
| ← gateway | `welcome` | `session: {title, targets, source_lang}`, `state`, `backlog: [segment…]` (last 60 s) |
| ← gateway | `segment`, binary audio, `audio_skip`, `state` | as C3, for the chosen language only |
| ← gateway | `ended`, `error` | `reason` / `code: "not_found" \| "full" \| "rate_limited" \| "bad_request"` |
| phone → | `set` | `lang?`, `audio?` |

Anything else from a phone, or a message over 1 KB, closes the socket with
1008.

### C5. Operator console to hub

BibleLive's model in new code: first message `authenticate`, close code 1008
on refusal, `Authorization: Bearer` on HTTP. Actions: `start_session {title,
targets}`, `end_session`, `pause`, `resume`, `mute_lang {lang, muted}`,
`set_tts {lang, enabled}`, `rotate_join_code`, `set_public_base_url {url}`.
Server messages: `state_snapshot`, `segment_preview {seq, source,
translations}`, `listeners`, `lag {lang, text_s, audio_s, backlog_s}`, `health
{issues: [{code, params, text}]}`, `error`.

## 6. Reuse

"Reuse" means import as is. "Generalize" means change once so both apps can
use it. Only the speech front end shares a running process.

| Area | Verdict | Reason | Files |
| --- | --- | --- | --- |
| ffmpeg capture | Reuse | Frame-exact capture with carry-over and restart is solved and tested (code). | `audio/capture.py` |
| VAD | Reuse | Same 30 ms frames. | `audio/vad.py` |
| Segmentation | Generalize | The `Segmenter` class is reusable; the pipeline must run a second instance and tag which stream an utterance belongs to. | `audio/segmenter.py`, `pipeline.py` |
| STT engines | Reuse | Kmynas writes punctuation and capitals itself (web). Spelling maps and number fixes stay BibleLive's. | `audio/stt.py`, `stt_gguf.py` |
| Model management | Generalize | Download, cache lookup and self-test are right but list speech models only. | `launcher/speech.py`, `commands.py` |
| Partial transcripts | Leave for the MVP | Built for follow-along. Phase 4 revisits it for draft lines. | `audio/partials.py` |
| Reference parser, follow-along, verse text | Reuse as a library, Phase 4 | The verse matcher is what scripture insertion needs; imported read-only. | `core/verse_text.py`, `verse_parser*.py`, `bible_lookup.py` |
| BibleLive server, AppState, approval flow | Leave | Different job and trust model. Nothing here imports `core.server`. | `core/server/*` |
| WebSocket and session layer | Copy the patterns | Token-in-first-message auth, bounded sends, pruning. | `core/server/connections.py`, `auth.py` |
| Senders | Generalize | The tap is the transcript sender with another payload and target. | `audio/transcript_sender.py`, `heartbeat.py` |
| QR codes | Reuse | `segno` is a dependency and `qr_png` exists (code). | `launcher/network.py` |
| Audience access | New | BibleLive's network mode is LAN, plain HTTP, and hands phones the operator page. | new `backend/gateway/` |
| Frontend toolchain and shared pieces | Reuse | Vite multi-entry, `useWebSocket`, `renderApp` (no StrictMode), i18n, tokens (code). Russian becomes a third interface language for the listener page. | `frontend/src/shared`, `components`, `styles` |
| Operator console and display page | Leave | BibleLive-specific. | `frontend/src/operator`, `display` |
| Config | Copy the pattern | pydantic-settings with its own prefix and settings file. | `core/config.py`, `settings_store.py` |
| Launcher and installer | Generalize | Translate is one or two more supervised children and an optional installer component. Needs blueprint M2. | `launcher/process.py`, `settings.py`, `BibleLive.iss` |
| Benchmarks | Reuse the harness ideas | Manifests, hashing, cache and offline replay carry over; scoring is new. | `benchmarks/run_benchmark.py` |

## 7. Models

### Lithuanian speech recognition

| Model | Quality | Speed and memory | Licence | Notes |
| --- | --- | --- | --- | --- |
| **Kmynas v4** (Parakeet-TDT 0.6B fine-tune), GGUF Q8 | Best reference detection of all models tried on 13 services (code). WER 13.4 on held-out LIEPA-3 (web); about 10.6 on FLEURS per a third-party page (web) | RTF 0.017 GPU, 0.06–0.08 on a Ryzen 5800X, 0.21 on the service laptop; +1.1 GB (code) | CC-BY-4.0 (web) | Punctuation and capitals in the output; numbers as words; no streaming (code) |
| **Canary-1B-v2**, GGUF Q8 | Slightly behind Kmynas; finds different references (code) | RTF 0.025 GPU, 0.11–0.12 CPU; +1.6 GB (code) | CC-BY-4.0 (web) | Translates speech to English in NeMo (web); the transcribe.cpp port documents transcription only (web). No LT→RU. |
| Whisper large-v3-turbo | Behind both on Lithuanian (code) | RTF 0.03 GPU, about 0.9 CPU; +1.4 GB (code) | MIT (assumed) | Too slow on CPU |
| Parakeet-TDT-0.6B-v3 (stock) | Poor on Lithuanian sermons (code) | as Kmynas | CC-BY-4.0 | Rejected already |

All run offline. Recommendation: Kmynas alone is the translation source.

### Translation

| Model | Pairs | Quality evidence | Size and runtime | Licence | Notes |
| --- | --- | --- | --- | --- | --- |
| **OPUS-MT tc-big lt-en** (2022) | LT→EN | FLORES-101 BLEU 34.3, newstest2019 32.3 (web) | 0.2B; CTranslate2, fast on CPU (assumed) | CC-BY-4.0 (web) | CTranslate2 is already a BibleLive dependency (code) |
| **OPUS-MT tc-base bat-zle** | LT→RU direct | FLORES-101 BLEU 21.3 (web) | small; CPU | CC-BY-4.0 (web) | The baseline every Russian option must beat |
| **MADLAD-400 3B** (2023) | any to any | A 2025 study found MADLAD, NLLB and OPUS beat LLMs on Lithuanian by automatic metrics (web) | 11.8 GB full, 1.65 GB 4-bit (web); about 3 GB 8-bit (assumed) | Apache-2.0 (web) | One model for both targets. Leading candidate |
| NLLB-200 | any to any | strong on mid-resource pairs (assumed) | 0.6B–3.3B | CC-BY-NC-4.0 (assumed): **non-commercial** | Reference only. Never shipped |
| **EuroLLM-9B-Instruct** | 35 languages incl. LT and RU (web) | no translation scores on its card (web) | about 6–7 GB 4-bit (assumed) | Apache-2.0, gated (web) | Context and glossary in the prompt. Challenger |
| TranslateGemma 4B / 12B (Jan 2026) | 55 languages; Lithuanian not confirmed (web) | WMT24++ COMET 81.6 / 83.5 (web) | about 3 / 8 GB 4-bit (assumed) | Gemma terms (web): **needs sign-off** | Bake-off row if Lithuanian is covered |
| Pivot LT→EN→RU | two hops | EN→RU models are far stronger than LT→RU (assumed) | two calls | as the parts | Loses tu/jūs and compounds errors; delay measured in P0.3 |
| TildeOpen 30B | Baltic focus | not instruction-tuned; a translation model is announced (web) | too large | CC-BY-4.0 (web) | Watch |

Prefer direct when it is close: a pivot hides Lithuanian's second-person
forms, gender and case from the Russian side.

### Speech synthesis

| Model | EN | RU | Runs on | Licence | Notes |
| --- | --- | --- | --- | --- | --- |
| **Kokoro-82M** v1.0 | yes | no (web) | CPU or small GPU; 24 kHz (web) | Apache-2.0 weights (web) | Falls back to espeak-ng, GPL-3 (assumed): **needs sign-off if used** |
| **Qwen3-TTS 0.6B** (Jan 2026) | yes | yes (web) | GPU; streaming; "97 ms" is the vendor's figure (web) | Apache-2.0 (web) | One model for both, nine voices |
| **Chatterbox Multilingual V3** | yes | yes (web) | GPU, 0.5B (web) | MIT (web) | Every clip is watermarked (web) |
| **Supertonic 3** | yes | yes (web) | CPU, ONNX, 99M (web) | weights OpenRAIL-M, SDK MIT (web): **needs sign-off** | Requires disclosing machine-generated speech |
| Silero `v5_cis_base` | no | yes (web) | CPU | MIT for this model only (web) | Silero's main Russian and English models are CC-BY-NC (web): **non-commercial** |
| Piper | yes | yes (web) | CPU | code GPL-3.0, embeds espeak-ng; voices licensed one by one (web): **needs sign-off** | Docs say personal use and research (web) |
| XTTS-v2, F5-TTS, MMS-TTS | yes | yes | GPU | **non-commercial** (assumed) | Excluded |

### Streaming partials

- Kmynas cannot stream (code); the Canary port is not streaming and exposes no
  timestamps (web).
- S1: short segments, finals only. S2: re-transcribe the growing segment every
  1 to 2 s and commit the prefix two passes agree on. S3: S1 plus an
  uncommitted draft line.
- S2 on a 12 s utterance re-transcribes about 3.5 to 4.5 times the audio:
  nothing on a GPU, nearly a full core's real time on the laptop (assumed from
  code figures). S2 is a GPU-tier feature.

## 8. Sermon-specific problems

### Segmentation and commitment

- **Segments.** End after about 0.6 s of silence, prefer the next pause after
  4 s, cut at 7 s. Starting values; Phase 0 tunes them. In the first live
  service 308 of 598 BibleLive utterances ran to the 12 s cap (code), so hard
  cuts are common.
- **Commit unit.** One sentence, split at the punctuation Kmynas writes. A
  trailing fragment from a hard cut is held up to 2 s for the next segment,
  then committed as is.
- **Retraction.** Committed text is never changed or removed. `final: false`
  is reserved for a draft line a later commit with the same `seq` replaces;
  the MVP sends none and synthesis never speaks one.
- **Guards.** A low-confidence segment is not translated. A translation that
  is empty, far longer than its source or repeats itself is retried once on
  the fallback engine, then dropped with a health issue.
- **Context.** LLM engines get the previous two source sentences and their
  translations; sentence-level engines none.

### Glossary and biblical terms

- Book names from BibleLive's canon and the translations' book tables (code).
- People and places: built once by aligning verses across LTB, WEB and the
  Russian text by coordinates.
- Theological terms: a hand-kept file of a few hundred entries, owned by the
  owner or the church.
- Applied in the prompt for LLM engines; as a post-translation check for
  sentence-level engines. Constrained decoding is not available in
  CTranslate2 (assumed).
- Russian register: formal plural address for the congregation.

### Scripture quotes

BibleLive already holds most of it: the parser finds announced references,
and `core/verse_text.py` with the follow-along matcher scores a Lithuanian
transcript against a passage's verses (code). The hub uses them as libraries
on its own transcripts.

- Substitute the official text only when the transcript covers most of a verse
  (start at 0.8 of its words) in the chapter just announced. Otherwise
  translate by machine and attach the reference as a label.
- Preachers paraphrase, quote half a verse, or read another translation;
  inserting a whole verse then puts words in the speaker's mouth.
- A versification map between LTB, canonical and Synodal numbering is
  required first (`RUSSIAN_BIBLE_IMPORT.md`).
- English uses WEB, not KJV.
- It never waits for, reads or affects the operator's approval in BibleLive
  (T15).

### When the speaker outpaces the synthesizer

- The hub tracks per language the seconds of committed speech not yet played.
- Under 3 s: normal rate. 3 to 8 s: rate rises in steps to 1.3x, applied in
  synthesis, and pauses are trimmed. Over 8 s: the oldest unsynthesized
  sentences are dropped whole until the backlog is under 4 s, and
  `audio_skip` tells the phones.
- Text is never dropped. Inserted scripture is dropped last.
- Condensing by LLM is rejected for the MVP.

### Text and audio in sync

- Every clip carries its sentence's `seq`; the page highlights the sentence
  while it plays and marks skipped ones.
- Text leads audio. A "follow the audio" toggle scrolls with the highlighted
  sentence.
- Clips play in `seq` order from one queue; a late clip is discarded.
- The operator sees text lag and audio lag per language.

## 9. Audience delivery and security

### Tunnel or relay

| | (a) Tunnel to a gateway on the operator machine | (b) Gateway on a VPS, operator dials out |
| --- | --- | --- |
| Monthly cost | Quick tunnel: €0. Named tunnel: a domain, about €1 (assumed) | about €5.50–6.50 plus a domain (web) |
| Exposed | the gateway process, through the tunnel; not the hub, not BibleLive | nothing on the operator machine |
| Extra parts | the `cloudflared` binary | a server to patch; TLS; a deploy script |
| Venue uplink | carries every listener: 50 × about 40 kbit/s = 2 Mbit/s (assumed) | one stream per language |
| As a product | each customer runs their own tunnel | one relay can serve many venues |

**Decision (Q3, T12): tunnel only for now.**

- **Testing: a Cloudflare quick tunnel.** `cloudflared tunnel --url
  http://127.0.0.1:<gateway port>` needs no account and no domain. It prints a
  random `https://….trycloudflare.com` address that changes on every start.
  Cloudflare describes quick tunnels as for testing and development, caps them
  at 200 concurrent requests and does not support Server-Sent Events on them
  (web). Sixty WebSocket listeners should fit under the cap (assumed; checked
  in P0.5 and the soak).
- **The public address is a setting**, `public_base_url`, never a constant.
  The hub builds the join link and QR code from it. The launcher fills it from
  the address `cloudflared` prints; the operator can also set it by hand. A
  change while running shows a new QR code.
- **Later:** a named tunnel (stable address, needs an account and a domain) or
  the VPS relay. Both change only the setting and, for the relay, the
  publisher key. A local-network mode is the same gateway reached by LAN
  address.

### Security design

- **Join link.** `<public_base_url>/s/<join_code>`; the code is 128 random
  bits, valid from `session_open` to `session_close`. No accounts, no
  cookies. The operator can rotate it.
- **Publisher key.** Random 32 bytes per installation, sent in the first
  message, compared in constant time; the gateway stores only its hash. In
  local mode it travels over loopback only.
- **Read-only audience.** Phones choose a language and switch audio on or off.
  Nothing a phone sends is shown to anyone or reaches the hub.
- **Limits.** 60 listeners per session, refused beyond with a clear message.
  Join attempts limited per address, generously (one mobile carrier address
  can hide many phones); wrong codes limited hard; 1 KB message cap; idle
  sockets pruned.
- **Page safety.** Text inserted as text, never HTML; strict content security
  policy; no third-party scripts, fonts or analytics.
- **No storage.** The gateway keeps the last 60 s of segments per language in
  memory and nothing on disk. Logs record counts and errors, not sermon text.
- **Hub and tap** bind to 127.0.0.1. The gateway binds to 127.0.0.1 too; only
  the tunnel reaches it.

### Fit with BibleLive's `SECURITY.md`

- BibleLive has one token with full operator rights, and says to add a
  separate token before the pipeline leaves the machine (code). Translate
  follows that from the start (T13).
- Translate's console uses the same mechanics: token in the first WebSocket
  message, Bearer on HTTP, the `#token=` hand-off, close code 1008.
- BibleLive's public endpoints are not routed through the tunnel: it maps one
  address to the gateway's port only.
- New routes, tokens, a network surface and launcher process handling are all
  on the security-review list. The review is a gate in Phases 2 and 3.
- Sermon text leaves the building. That is the product; the pages say so.

### Wording on the join and listener pages (Q4)

Accurate, and no more than is true:

> Automatic translation. It may contain errors.
> This translation service does not record or store audio. The venue may
> record the service separately.

Never "nothing is recorded".

## 10. Hardware and latency

Prices are US dollars from trackers that week and vary between sources; euro
prices were not found. GPU prices are well above launch prices (web).
Whole-machine prices are estimates.

| Tier | Machine | Rough price | Runs | With BibleLive | Expected delay, text / audio start (median) |
| --- | --- | --- | --- | --- | --- |
| Minimum | 6–8 core CPU, 16 GB RAM, no GPU or 4 GB (the GTX 1650 Ti service laptop) | what you own; a used office PC about €300–500 (assumed) | Kmynas, OPUS-MT and synthesis on CPU; finals only | Yes with Kmynas alone. On the 4 GB laptop both speech models use about 3.15 GB (code), so translation and synthesis stay on CPU | 4–5 s / 8–10 s (assumed) |
| Comfortable | 8-core CPU, 32 GB RAM, 8–12 GB NVIDIA GPU | used RTX 3060 12 GB about $350 (web); PC about €600–800 (assumed) | Kmynas on GPU, MADLAD-3B 8-bit or a 4B LLM, synthesis on CPU or small GPU model | Yes on 12 GB with both speech models. On 8 GB with OBS, drop Canary or keep translation on CPU | 3–4 s / 5–7 s (assumed) |
| Ideal | 8-core CPU, 32 GB RAM, 16 GB GPU (the development PC: Ryzen 5800X, RTX 5070 Ti) | RTX 5060 Ti 16 GB about $800; RTX 5070 Ti about $1,400 (web); PC about €1,500–2,300 (assumed) | both speech models, a 9B LLM or MADLAD-7B, GPU synthesis, S2 partials | Yes, with room for OBS | 2–3 s / 4–6 s (assumed) |

- An existing 8 GB gaming GPU runs the whole product without BibleLive, and
  with it if Canary is off. A 4–6 GB gaming laptop lands near the minimum
  tier.
- Three GPU runtimes may share one card (transcribe.cpp, CTranslate2, a
  synthesis runtime), each with a few hundred MB of overhead (assumed). P0.6
  measures it with BibleLive and OBS running.
- One loaded Kmynas serves both apps; BibleLive's utterances go first.

**Latency target: text 4 s median, 8 s p95; audio start 7 s median, 10 s
p95.** With 4 s segments a word waits about 2 s for its segment to close,
0.6 s for the pause, under 0.3 s for recognition on a GPU, 0.2–0.7 s for
translation and 0.2 s for delivery (assumed). The p95 is the first word of a
7 s hard-cut segment. Audio adds synthesis (0.3–1 s) and the wait behind the
previous clip.

## 11. Repository structure

```
backend/
  core/          BibleLive server, parser, Bible lookup          unchanged
  audio/         speech front end + BibleLive pipeline           gains tap.py and a second segmenter stream
  launcher/      starts and supervises children                  gains Translate as optional children
  translate/     NEW  hub: contracts/, hub/, mt/, tts/, audio_out/, publisher/
  gateway/       NEW  audience gateway; imports nothing from core, audio or translate.hub
frontend/src/
  translate/     NEW  operator console entry
  listen/        NEW  listener page entry, built to its own folder
benchmarks/translate/   NEW  Phase 0 harness
```

| Aspect | Same monorepo, separate app (chosen) | Module inside BibleLive | Separate repository |
| --- | --- | --- | --- |
| Process | Own processes; a translation fault cannot stop the projector | Internet-facing code and heavy inference in BibleLive's server | Same isolation |
| Shared code | Imported directly; one PR runs both apps' tests | Trivial | Audio package must be published or vendored |
| Frontend | Two more Vite entries; shared hooks, i18n, tokens | More tabs in a large console | Tooling duplicated |
| Config | Own prefix and settings file | BibleLive's config grows relay keys and model paths | Own everything |
| Deployment | Optional `translate` extra and installer component; gateway ships alone | Every install carries translation dependencies | Second installer and CI |
| Rules | One `AGENTS.md` with a Translate section | Translation work lands in ask-first files | Rules copied |

What would change this: toward a separate repository, if the product becomes
closed-source, gains separate contributors, or the gateway becomes a hosted
service (it is built to be lifted out first). Toward duplicating the speech
front end, if P0.6 shows translation segments slow BibleLive's detection on
the minimum tier, or Translate must run on a different machine.

## 12. Operations

**Operator.**

1. Open the launcher, tick "Translation", pick the microphone, press Start.
   The launcher starts the pipeline with the tap on, the hub, the gateway and
   the tunnel, and fills in the public address.
2. The Translate console shows the tunnel address, model readiness per
   language and a level check.
3. "Start session", choose targets. The console shows the QR code and a "Show
   join screen" button for a projector, second screen or OBS browser source.
4. During the service: source text and both translations, listeners per
   language, text and audio lag per language.
5. "End session". Listeners see "the service has ended"; the link stops
   working.

**Audience.** Scan; pick a language (remembered); tap "Start" (the tap is what
lets a phone browser play sound) (web); subtitles at once, audio if "Listen"
is on; the page reconnects and catches up the last 60 s of text.

**Live controls.**

| Control | Effect | Listeners see |
| --- | --- | --- |
| Pause / resume | Hub stops committing; tap told to stop; queued audio cleared | "Translation paused" |
| Kill switch (end session) | Session closed at the gateway, join code invalid, buffers cleared | "The service has ended" |
| Mute a language | That language's text and audio stop | "Russian is paused" |
| Speech off for a language | Text continues; no synthesis | Listen switch disabled with a note |
| Rotate join code | New link and QR | Disconnected until rescanned |
| (automatic) BibleLive set to English | Hub pauses with reason `source_language` | "Paused: the speaker is not speaking Lithuanian" |

Not offered: editing or retracting a sentence.

**Reliability.**

- Hub or gateway dies: the launcher restarts it (blueprint M2); the publisher
  reconnects and reopens the session with the same join code.
- Tunnel restarts: a quick tunnel comes back with a **new address**, so the
  QR code changes and listeners must rescan. This is the main reason a named
  tunnel follows the pilot.
- Internet drops: the hub keeps translating into a 60 s buffer; older text is
  not replayed as audio.
- Translation engine fails: one retry on the fallback engine, then a
  "translation unavailable" notice. Source-language text is never sent in its
  place.
- Synthesis fails or falls behind: text continues; `audio_skip` explains.
- Pipeline restarts: both products lose speech for a few seconds (the cost of
  T2).

## 13. Roadmap and work packages

Phase 0 can start now. Phases 1 onward assume blueprint M2 and M4. "Parallel"
packages own disjoint files. Nothing is committed by agents.

### Dependencies, for approval before each phase (Q8)

All go in an optional `translate` extra in `backend/pyproject.toml`; a
BibleLive-only install is unchanged. Licences are from memory unless marked
and are confirmed at install time. Final names for T6 and T8 come from the
Phase 0 memo.

| Phase | Package | New dependency | Licence | Why |
| --- | --- | --- | --- | --- |
| 0 | all | scratch environment only: `jiwer`, `sacrebleu`, `unbabel-comet`, `datasets`, `sentencepiece`, `transformers`, `llama-cpp-python`, one package per synthesis candidate | Apache-2.0 / MIT; per candidate | Measurement. Nothing enters the project |
| 1 | P1.1, P1.2 | none (FastAPI, pydantic, uvicorn are already dependencies) | — | — |
| 2 | P2.1 | none | — | reuses `requests` |
| 2 | P2.3 | `sentencepiece`; `ctranslate2` declared in the extra (already present through the `audio` extra); `llama-cpp-python` only if an LLM is chosen | Apache-2.0; MIT; MIT | tokenizer and inference |
| 2 | P2.4 | the chosen synthesis runtime, for example `onnxruntime` or `torch` plus the model's package. Encoding uses the bundled ffmpeg | per T8 | synthesis |
| 2 | P2.5 | none | — | — |
| 2 | P2.6 | `websockets`, declared explicitly (already installed through `uvicorn[standard]`) | BSD-3-Clause | outbound WSS client |
| 2 | P2.7, P2.8 | none (React and the existing dev tools) | — | — |
| 3 | P3.2 | `cloudflared`, an external program, not a Python package; downloaded by the operator for the pilot | Apache-2.0 | tunnel |
| 4 | P4.1–P4.4 | none expected | — | — |
| 5 | later | Caddy on a VPS, if the relay is built | Apache-2.0 | TLS |

### Phase 0 — bake-off

See [`PHASE0.md`](PHASE0.md): packages P0.1 to P0.7, thresholds, the
direct-or-pivot table with measured delay beside quality, and the reviewer
checkpoint.

### Phase 1 — contracts and a walking skeleton

| ID | Package | Files owned | Depends on | Mode | Acceptance | Tests |
| --- | --- | --- | --- | --- | --- | --- |
| P1.1 | Contracts C1–C5 as pydantic models with golden fixtures, mirrored TypeScript types, the C2 protocols | `backend/translate/contracts/`, `frontend/src/shared/translateTypes.ts`, `backend/pyproject.toml` (package list, `translate` extra) | P0.7 | Sequential, first | Every message has a model and a fixture; the owner approves the shapes | Fixture round trip in pytest; fixtures load against the TS types in vitest |
| P1.2 | Fake engines, a file-replay source emitting C1 segments, a localhost run of hub, gateway and a listener stub | `backend/translate/testing/`, minimal `hub/` and `gateway/` entry points | P1.1 | Sequential | One command replays a recorded service; a browser shows text and plays tones in two languages | End-to-end pytest with real WebSockets and no models |

Reviewer checkpoint: contracts are additive-safe; `gateway/` imports nothing
from `core`, `audio` or the hub; no BibleLive file changed except
`backend/pyproject.toml`.

### Phase 2 — core build

| ID | Package | Files owned | Depends on | Mode | Acceptance | Tests |
| --- | --- | --- | --- | --- | --- | --- |
| P2.1 | Transcript tap and second segmenter stream; CLI flags; language status to the hub | `backend/audio/tap.py`, the pipeline front-end module from blueprint M4, `audio/cli.py`, `audio/config.py` | P1.1, blueprint M4 | Parallel. The only package editing `audio/` | Tap off: byte-identical behaviour. Tap on: C1 segments arrive and BibleLive's detections do not change | Unit tests; a `test_live_path.py` case with the tap on; **detection benchmark both tiers, zero difference**; `session-replay` identical |
| P2.2 | Commit policy | `backend/translate/hub/commit.py` | P1.1 | Parallel | Replaying the corrected 30 minutes gives the same units every run; none over 40 words | Table-driven tests from real transcripts with the untouched cases beside them |
| P2.3 | Translation service | `backend/translate/mt/` | P1.1, P0.7 | Parallel | Reproduces P0.3 scores within noise; meets the time threshold | Contract tests shared with the fake; a golden set per pair; fallback test |
| P2.4 | Synthesis service, encoder, backlog controller | `backend/translate/tts/`, `backend/translate/audio_out/` | P1.1, P0.7 | Parallel | Clips decode on both test phones; the ladder behaves as section 8 under 2x overload | Backlog controller with a fake clock; encoder output decoded by ffmpeg |
| P2.5 | Audience gateway | `backend/gateway/` | P1.1 | Parallel | 60 simulated listeners for 90 min with no memory growth; a stalled listener delays nobody; every limit refuses as specified | Tests per message and refusal; load script; fuzzed listener input |
| P2.6 | Publisher and the two transports | `backend/translate/publisher/` | P1.1 | Parallel | Survives a gateway restart mid-session with the same join code and no duplicates | Tests against the real gateway in-process; a flaky-network test |
| P2.7 | Listener page | `frontend/src/listen/`, `frontend/listen.html`, `frontend/vite.config.ts` | P1.1, P0.5 | Parallel | Works at 360 px; bundle under 80 kB compressed; the P0.5 sheet passes on the real page; wording as section 9 | vitest with the socket harness; fixtures drive every message |
| P2.8 | Operator console and join page | `frontend/src/translate/`, `frontend/translate.html` | P1.1 | Parallel | Every control in section 12 works against the fake hub; follows `DESIGN.md` | vitest per control; optimistic controls revert on error |
| P2.9 | Hub: session state, per-language workers, operator API, settings incl. `public_base_url`, optional text log, health | `backend/translate/hub/` except `commit.py` | P1.1; integrates P2.2–P2.4, P2.6 | Parallel with fakes, then sequential integration | Recorded-service replay end to end with real engines meets the delay targets on the comfortable tier | State tests; one language failing does not stop the other; token tests mirroring `test_authentication.py` |

Reviewer checkpoints: after P2.1 the reviewer runs the detection benchmark and
`session-replay` themselves. After P2.5 and P2.9, `/security-review`. After
P2.7, a person tries the page on two real phones. Throughout: no import of
`core.server` under `translate/` or `gateway/`; no StrictMode; all text
through `t()`.

### Phase 3 — integration and a pilot

| ID | Package | Files owned | Depends on | Mode | Acceptance | Tests |
| --- | --- | --- | --- | --- | --- | --- |
| P3.1 | Launcher: Translation option, model download, hub and gateway as supervised children, QR window | `backend/launcher/` | Phase 2, blueprint M2 | Sequential. Only package editing `launcher/` | Option off: Start and Stop as today. Option on: all children start, restart and stop cleanly | `test_launcher_*` with injected process fakes; `--dry-run` unchanged with the option off |
| P3.2 | Quick tunnel: the launcher starts `cloudflared` mapped to the gateway port only, reads the printed address into `public_base_url`; setup notes | `backend/launcher/tunnel.py`, `docs/translate/TUNNEL.md` | P3.1 | Sequential | A phone on mobile data joins through the printed address; BibleLive's ports are not reachable through it; a tunnel restart updates the QR code | Parser test on recorded `cloudflared` output; a check that the command names one port; manual reachability test |
| P3.3 | Security review and fixes; a Translate section in `SECURITY.md` | `SECURITY.md`, fixes where found | P3.2 | Sequential | Review run; findings closed or accepted by the owner in writing | A test per fixed finding |
| P3.4 | Soak: 90 min recorded service, 50 simulated listeners through the quick tunnel, BibleLive running | `benchmarks/translate/soak.py` | P3.1 | Parallel with P3.3 | No memory growth; lag within target for 95% of the run; no refusals from the tunnel's request cap; BibleLive detections identical | The soak, with a pass or fail summary |
| P3.5 | Pilot checklist and one real service with volunteers | `docs/translate/PILOT.md` | P3.3, P3.4 | Sequential | The owner runs it; findings recorded | — |

Reviewer checkpoint: launcher behaviour with the option off is unchanged on
Windows; the join page shows only the QR code, the address and the wording in
section 9; no pilot with an open security finding.

### Phase 4 — sermon quality

| ID | Package | Files owned | Depends on | Mode | Acceptance | Tests |
| --- | --- | --- | --- | --- | --- | --- |
| P4.1 | Glossary | `backend/translate/glossary/`, `data/translate/terms.*` | Pilot | Parallel | Names correct on the rated set; adequacy not lower | Unit tests per rule; re-score the 150 sentences |
| P4.2 | Scripture: reference label, then verse substitution with the versification map. **The Russian Bible import changes `bible_data/`** (`RUSSIAN_BIBLE_IMPORT.md`) | `backend/translate/scripture/`, `data/translate/versification/` | owner's Russian Bible file, P4.1 | Parallel | No substitution where the speaker paraphrased; every substituted verse is the one read | Tests from real transcripts with the must-not-substitute cases; 60-verse map test |
| P4.3 | Draft line (S3) and, on the ideal tier, S2 | `backend/audio/` partial path, `hub/commit.py`, listener page | P2.1 | Sequential: edits `audio/` | Text delay median falls by at least 1 s on the ideal tier; committed text never changes | Detection benchmark zero difference; follow-along bench unchanged |
| P4.4 | Locked-screen audio hardening | `frontend/src/listen/` | Pilot | Parallel | 30 min locked on both phones | Manual sheet |

Reviewer checkpoint: P4.2 never reads BibleLive's pending queue or display
state. P4.3 leaves follow-along's partial transcripts measurably unchanged.

### Phase 5 — later, not scheduled

- Named tunnel (stable address): an account, a domain, and a config change.
- VPS relay: `deploy/relay/`, Caddy, key provisioning, a second security
  review. Switching is a settings change.
- Local-network mode: the gateway reached by LAN address, with the warnings
  BibleLive's network mode carries.
- A third target language by configuration only, as proof of T17.
- Licence audit, `NOTICE` file (CC-BY-4.0 attributions for Kmynas, LIEPA-3,
  Canary, OPUS-MT and whatever T6 and T8 choose), optional installer
  component. The NOTICE file is needed before anything is distributed.

### Changes that need the owner's approval first

- **BibleLive's WebSocket protocol:** none planned.
- **Operator-approval flow:** none planned; T15 forbids coupling to it.
- **`bible_data/`:** P4.2's Russian Bible import, sourced by the owner.
  Everything else only reads.
- **Also:** each phase's dependency list (table above); the edits to
  `backend/audio/` in P2.1 and P4.3 (detection-sensitive, gated by the
  benchmark); all network and token work (security review); any GPL,
  restricted or non-commercial component (Q7).

## 14. Risks, ranked

| # | Risk | Why | What limits it |
| --- | --- | --- | --- |
| 1 | Lithuanian to Russian quality on spoken sermons | No strong permissive direct model found; recognizer errors and unfinished sentences | Phase 0 gate with human rating on recognized text; pivot and LLM rows; a written fallback |
| 2 | Audio on a locked phone | The core listening posture; iOS behaviour varies by version (web) | P0.5 before the audio contract freezes; P4.4 |
| 3 | Delay | The speech model cannot stream; sentences end when they end | Translation-specific segments; S2 on capable hardware; targets measured per tier |
| 4 | A wrong translation presented as the preacher's words | No human in the loop | Standing "automatic translation" notice; guards; pause and kill switch; official verse text in Phase 4 |
| 5 | Licences | Many well-known models are non-commercial; Piper and espeak-ng are GPL; Gemma and OpenRAIL carry terms | Section 7; Q7; the NOTICE file and audit |
| 6 | One machine doing everything | Two speech models, OBS, translation and synthesis; the 4 GB laptop has no headroom | P0.6; CPU engines on the minimum tier; BibleLive first in the queue |
| 7 | Touching `backend/audio/` | A slip changes what BibleLive detects | One package owns those files; zero-difference benchmark and replay; off by default; after blueprint M4 |
| 8 | Quick-tunnel limits | A random address that changes on restart; "testing only"; a 200-request cap (web) | Address as a setting; soak through the tunnel; named tunnel or relay after the pilot |
| 9 | Synthesis quality and backlog | Russian stress and Bible names; a fast speaker for an hour | Listening test in P0.4; the rate and drop ladder; text always complete |
| 10 | Scripture substitution going wrong | A wrong official verse is worse than an imperfect translation | High coverage threshold; versification map; reference label as default |
| 11 | The owner's time | Phase 0 needs about 2–3 h of transcript correction and about 3.5 h of rating | Drafts and a staged rating plan keep it short; nothing else in Phase 0 waits for it except P0.2 and P0.3 scoring |

## Sources (read 2026-11-12)

Several price and comparison pages were aggregator sites that disagree with
each other; treat prices as rough and re-check model cards before shipping.

- Kmynas v4: https://huggingface.co/kristijonas/kmynas-parakeet-lt-v4 ;
  third-party FLEURS figure: https://huggingface.co/RobertasTa/android-lt
- Canary-1B-v2: https://huggingface.co/nvidia/canary-1b-v2 ;
  GGUF port: https://huggingface.co/handy-computer/canary-1b-v2-gguf ;
  transcribe.cpp: https://github.com/handy-computer/transcribe.cpp
- OPUS-MT: https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-lt-en ;
  https://huggingface.co/Helsinki-NLP/opus-mt-tc-base-bat-zle/blob/main/README.md
- MADLAD-400: https://huggingface.co/google/madlad400-3b-mt ;
  EuroLLM: https://huggingface.co/utter-project/EuroLLM-9B-Instruct ;
  TranslateGemma: https://huggingface.co/google/translategemma-4b-it ,
  https://arxiv.org/abs/2601.09012 ;
  TildeOpen: https://huggingface.co/TildeAI/TildeOpen-30b
- LLM and NMT on low-resource translation (2025):
  https://aclanthology.org/2025.r2lm-1.13.pdf
- Kokoro: https://huggingface.co/hexgrad/Kokoro-82M ;
  Qwen3-TTS: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice ;
  Chatterbox: https://huggingface.co/ResembleAI/chatterbox ;
  Piper: https://github.com/OHF-Voice/piper1-gpl ;
  Silero: https://github.com/snakers4/silero-models ;
  Supertonic 3 (mirror): https://huggingface.co/mlx-community/supertonic-3
- Cloudflare quick tunnels:
  https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/
- Hetzner pricing: https://www.eucloudcost.com/providers/hetzner/ ;
  https://betterstack.com/community/guides/web-servers/hetzner-cloud-review/
- iOS audio: https://github.com/sadiss-org/sadiss/issues/134 ;
  https://github.com/kenrick95/nikku/issues/62 ;
  https://www.forasoft.com/learn/video-streaming/articles-streaming/ios-safari-native-player
- GPU prices:
  https://bestvaluegpu.com/history/new-and-used-rtx-5060-ti-16gb-price-history-and-specs/ ;
  https://bestvaluegpu.com/history/new-and-used-rtx-5070-ti-price-history-and-specs/ ;
  https://cresvament.com/used-gpu-prices
