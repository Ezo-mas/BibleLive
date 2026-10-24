# Backend guide — layout, conventions, and task mapping

Commands for verification live in `testing.md` (the canonical list);
architecture invariants live in `architecture.md`; project-level facts
(install, run, env vars) stay in `README.md`. This guide covers backend
layout, Python conventions, and which files own which behavior —
including the database lookup/import pointers.

## What this project is

BibleLive listens to live audio (a sermon), detects spoken Bible
references, and shows them on an audience-facing display. Pipeline:
`ffmpeg → VAD → speech-to-text (Whisper, or Kmynas/Canary GGUF models for
Lithuanian) → reference parser → FastAPI server → WebSocket → operator
approval → display page`.

## Repo layout (backend)

```
backend/
  core/            FastAPI server, verse parser, Bible lookup (imported as core.*)
    server/app.py  The real server. Entry: uvicorn --factory core.server.app:create_app
  audio/           Capture/VAD/STT/sender pipeline (imported as audio.*)
    segmenter.py   VAD frames → utterances; shared by pipeline.py and the benchmark
    partials.py    partial transcripts for follow-along (schedule, mailbox, grow/chunk); shared with the benchmark
  scripts/         DB import / maintenance scripts
  tests/           pytest suite
data/              Source JSON files for Bible imports (kjv2.json, ltkb.json)
```

## PYTHONPATH / platform notes

Python tests need `backend/` on the import path — the Makefile and root
`pyproject.toml` (`pythonpath = ["backend", "."]`) handle it; bare
`python -m ...` invocations need `export PYTHONPATH="$PWD/backend"`.

On Windows there is no `make`: use `.venv\Scripts\python.exe -m ...` with
`$env:PYTHONPATH = "$PWD/backend"`.

## Conventions

- Python: ruff config in root `pyproject.toml`; imports are grouped with
  `core`/`audio` as first-party. Tests live in `backend/tests/test_*.py`;
  markers: `integration`, `audio`.
- Don't add dependencies without asking; the current set is minimal on
  purpose (FastAPI/uvicorn, faster-whisper, webrtcvad).
- Whisper prompts (`WHISPER_INITIAL_PROMPTS` / `WHISPER_HOTWORDS` in
  `audio/stt.py`): keep each under 223 tokens — faster-whisper keeps only
  the *last* 223 tokens of the initial prompt and silently drops the start.
  No citations or numbers in them: Whisper echoes the prompt over silence
  or music, and an example citation becomes a false detection.

## Where to look (backend tasks)

| Task | Files |
| --- | --- |
| Add/adjust a verse-reference pattern | `backend/core/verse_parser.py` (+ `verse_parser_lt.py` for Lithuanian) and tests in `backend/tests/test_verse_parser*.py` |
| Change an HTTP endpoint or WS handler | `backend/core/server/app.py`, `state.py`; message shapes in `models.py` + mirrored in `frontend/src/shared/types.ts` |
| Change verse text lookup | `backend/core/bible_lookup.py` (SQLite at `bible_data/biblelive.sqlite`) |
| Change picking a verse from the words read aloud (Lithuanian chapter-only refs) | `backend/core/verse_text.py` (index over `bible_data/bible_ltb.sqlite`), used by `StreamingParser` in `verse_parser.py`; benchmark before/after |
| Change manual scripture selection (Find Scripture) | `backend/core/passage.py` (validation + resolution) over `backend/core/books.py` (canon); endpoints and the `display_reference` action in `backend/core/server/app.py` |
| Change display history source / Show Again state | `backend/core/server/state.py` (`AppState.reference_history`, newest first, `MAX_HISTORY_REFERENCES`; both paths that put something on screen record it through `_record_history`) |
| Change what an operator gets on connect or from `GET /api/state` | `AppState.state_snapshot()` in `backend/core/server/state.py` builds the `StateResponse` (`models.py`) both send; its history window is `SNAPSHOT_HISTORY` (20). The console's `HISTORY_WINDOW` in `useOperatorState.ts` matches it by hand. Changing the shape is a protocol change: ask first |
| Change audio capture/VAD/STT/sending | the matching module in `backend/audio/`; if it can change what gets detected, benchmark before/after (`testing.md`) |
| Change the Lithuanian speech models (`--stt`) | `backend/audio/stt_gguf.py` (Kmynas / Canary GGUF via transcribe.cpp; English falls back to Whisper), per-model spelling maps in `audio/spelling_maps.py`, two-model agreement in `audio/ensemble.py`; the rules live and the benchmark share (parser construction, duplicate collapse and low marking, language-switch flush, Whisper's start language) in `audio/detection.py`, kept equal by `tests/test_detection_parity.py`; pipeline wiring in `_process_utterance` / `_init_thread_local_parsers` (`pipeline.py`), benchmark wiring in `make_stt` / `make_parsers` (`benchmarks/run_benchmark.py`); benchmark before/after |
| Change runtime language switching | the pipeline side is `backend/audio/control.py` (loopback API) + `pipeline.py`/`stt.py`; the server's proxy for the console is `backend/core/server/language_control.py` and `/api/language` in `app.py` (config: `AUDIO_CONTROL_*`); the console side is `frontend/src/operator/languageApi.ts`, `hooks/useSpokenLanguage.ts`, `components/LanguageSettings.tsx` |
| Change book names shown on the display / in search (Lithuanian etc.) | names are the `local_name` column of the loaded translation's `books` table, read read-only by `backend/core/book_names.py` (canonical English names stay the only lookup key); `AppState.book_label` in `state.py` stamps the optional `book_label` on references and follow-along hints; `/api/books` serves `local_name`/`local_variants` for the loaded translation. Keyed to the loaded Bible, not the spoken language |
| Change follow-along (next-verse suggestions) | matching in `backend/core/server/follow_along.py` over `backend/core/verse_text.py`; `AppState.offer_follow_along` in `state.py` (auto-show by the operator's `set_follow_auto` switch: `_follow_auto_passage`, `FOLLOW_AUTO_MAX_JUMP`, persisted by `settings_store.py`); `/api/transcript` in `app.py` (config: `FOLLOW_ALONG`); pipeline side `backend/audio/transcript_sender.py` (incl. the armed flag from the reply); partial transcripts `backend/audio/partials.py`, handed over in `pipeline.py` (`_vad_loop`, `_process_partial`) and transcribed by `stt.py` / `stt_gguf.py` (`transcribe_partial`); console side `frontend/src/operator/components/FollowAlongHint.tsx` |
| Change Bible translation switching | discovery in `backend/core/bible_versions.py`; swap + broadcast + language default in `backend/core/server/state.py` (`switch_bible_version`, `change_bible_version`, `apply_language_default_version`); `/api/bible-version` in `app.py` (config: `AUTO_BIBLE_VERSION`, `LANGUAGE_BIBLE_VERSIONS`); choice persisted via `settings_store.py`; console side `frontend/src/operator/bibleVersionApi.ts`, `hooks/useBibleVersion.ts`, `components/BibleVersionSettings.tsx` |
| Measure detection accuracy | `benchmarks/run_benchmark.py`, manifests + gold in `benchmarks/corpus/`, method in `benchmarks/README.md` |
| Change the idle-screen picture (upload, fit, serving) | `backend/core/server/idle_screen.py` (formats by magic bytes, size cap, fixed file names, atomic write); the `/api/idle-screen*` routes in `app.py`; `AppState.set_idle_image` and friends in `state.py`; the record and fit in `settings_store.py` (`idle_screen_image`, `idle_screen_fit`); the public picture route is listed in `SECURITY.md` |
| Change authentication or WS connection management | `backend/core/server/auth.py`, `connections.py` |
| Change WS message protocol or approval flow | **stop — ask the user first** (Behavior rules) |

## Lithuanian parser: lessons from past fixes

How the parser works is in the module docstring of `core/verse_parser_lt.py`.
These are the mistakes that keep coming back; each was found on a real
recording or service.

**Working method**
- Start from the real transcript. Replay the service (`benchmarks/session_replay.py`)
  and add the exact text as a test in `test_verse_parser_lt.py`, with a comment
  naming the service or recording it came from, as the existing tests do.
- Next to each positive case, assert what must stay untouched (see
  `test_person_name_and_a_count_is_not_a_chapter`).
- Check every speech model's way of writing it. Kmynas writes numbers as words;
  Canary often writes digits in its own formats; Whisper's prompt makes it
  format citations. A model's own mishearings ("Psmė", "Salme") go in its map in
  `audio/spelling_maps.py`, not into the shared parser.
- Every false positive is a wrong verse offered to the operator, and a medium
  or high one can be auto-approved onto the screen. Prefer silence or a
  chapter-only reference over a guessed verse. Benchmark before and after;
  parser changes replay from the cache in seconds.

**Traps**
- Book names are also people and ordinary words. "Jonas ir du jo mokiniai" is
  not John 2; "skaičiau" (I read) is not Numbers, though "Skaičiuose" is;
  "versė" (overturned) folds to "verse". Words followed by a bare number with no
  chapter word is narrative. Diacritic folding creates more such collisions, so
  check the folded form.
- A wrong book is worse than a miss: the Bible-database check cannot catch a
  valid but wrong reference. A chapter 1–3 followed by "Jono" once became
  1 John at high confidence; a numbered-book restart needs an explicit label
  word ("laiškas").
- Utterance cuts (12 s maximum) split citations: the verse word in one
  utterance and its numbers in the next, or one spoken number cut in two
  ("trys" | "trisdešim šeštoje" is 36, not 3). That is why chapter-only
  references and a bare trailing verse digit are held one utterance
  (`hold_chapter_only`). Don't release them early.
- Ranges and lists:
  - A dash after the chapter word is punctuation ("skyrius – šešiolikta").
  - "12-30" is chapter:verse, but "8-12" may be chapters.
  - A stray digit inside a verse pair can be a slurred join, but "12, 3, 50"
    is a list.
  - "dvidešimta, penkta eilutė" is 20:5, but "dvidešimt penkta" is 25.
- Merged numbers ("Filipiečiams 46") are split only when exactly one
  chapter:verse reading is valid.
- Book and chapter context expires (600 s by default; live measures it on the
  wall clock), and only newly reported references refresh it, so a stale book
  cannot claim stray numbers.
- Interpreted services put English cue words into Lithuanian transcripts
  ("and verses 20-22"). An utterance is parsed with the parser for the language
  it was captured in.
- Open gap: the "Timotiej-" spelling of Timothy (`docs/TECH_DEBT.md`).

## Windows launcher (`backend/launcher/`)

`python -m launcher` (PYTHONPATH=backend) starts the server on 127.0.0.1 (all
interfaces with `--network on`) and the audio pipeline, waits for `/readyz`, and
kills both process trees on exit.
Defaults: `large-v3-turbo`, device `auto` (CUDA if present, else CPU `int8`
with a warning), compute type `auto` (float16 > int8_float16 > int8). Settings
live in `%APPDATA%\BibleLive\launcher.json` (server settings and `logs\`
beside it); CLI flags override them and `--save` persists them. Useful flags:
`--list-mics`, `--mic`, `--language`, `--port`, `--open operator|display`,
`--dry-run` (print resolved commands/env), `--network on|off` (other devices on
the network may open the pages; default off; the token lives in
`launcher.json`). Args after `--` go to `audio.cli`.
A `ffmpeg/` dir next to the app is prepended to PATH if present.
`start.ps1` is unchanged. Modules: `settings.py` (incl. allowed enum values,
shared with `cli.py`), `gpu.py`, `speech.py` (Lithuanian engine: `GGUF_MODELS`
holds the Kmynas/Canary repo ids and files, `resolve_speech` picks the engine and
falls back to Whisper when a GGUF file is missing, `speech_threads` = physical
cores − 1 capped at 6), `process.py` (UI-agnostic `Launcher`, thread-safe
`stop()`, `on_event` callback, `error_code` for translation; `build_plan` maps
the speech setting to `--stt`, `--kmynas-model`, `--canary-model`, `--cpu-threads`,
`--session-log-dir logs\sessions` and `--session-audio`), `cli.py`, `network.py`
(network mode: the operator token, the LAN address and the Windows Firewall rule;
`netsh` and `ShellExecuteW` are injectable, so tests never touch the firewall).
The window's network panel is in `gui.py`, with its row states in `viewmodel.py`.
Speech setting `speech_engine`: `auto` (Kmynas + Canary on an NVIDIA GPU, Kmynas
alone otherwise) | `whisper` | `kmynas` | `kmynas+canary`. The GGUF engines run on
the `device` setting (the audio CLI has one `--device` for Whisper and GGUF), and
English always goes to Whisper. `session_audio` (default on) records each service
under `logs\sessions`.
GUI: `python -m launcher --gui` (default when there is no console, e.g.
`pythonw`); Tk code is thin in `gui.py`, testable logic in `viewmodel.py`,
strings in `i18n.py` (en/lt, `ui_language` setting, default from the Windows UI
language). `monitors.py` finds the projector (first non-primary monitor unless
`projector_monitor` is saved); `projector.py` opens `/display` in an Edge (else
Chrome) full-screen app window on it (not kiosk mode, so F11 / Alt+F4 still work; Stop
closes every process on its private profile) and never falls back to the operator's screen.

Installer commands (`commands.py`): `--check-gpu` (exit 0, or 3 without a CUDA GPU), `--download-model [--no-whisper]` (the Lithuanian GGUF models of the engine via `hf_hub_download`, then Whisper unless `--no-whisper`), `--gpu-self-test` (loads each speech model the settings use on its device and transcribes 1 s of silence; exit 0/1; never downloads). `--speech-engine`, `--device`, `--model` and `--save` set what the installer chose. Models are cached under `HF_HOME=%LOCALAPPDATA%\BibleLive\models` (set by `build_plan`; the GGUF files are looked up in its `hub` folder without network access). The installer's speech page and `/SPEECH` switches are in `scripts/windows/installer/BibleLive.iss`; the portable build is `scripts/windows/build-portable.ps1` (see its README).

## Database lookup / import pointers

- Verse-text lookup: `backend/core/bible_lookup.py` over the SQLite DB at
  `bible_data/biblelive.sqlite`. It has **no** abbreviation-mapping dict —
  lookups go through the SQLite `books` table (also see `architecture.md`,
  known pitfalls).
- Importing a new Bible translation: `backend/core/import_bible.py`; the
  full procedure, schema, and rules live in [`../../DATABASE_SETUP.md`](../../DATABASE_SETUP.md).

## Related docs

- `testing.md` — canonical verification commands and caveats.
- importing a Bible translation, DB schema/import commands:
  [`../../DATABASE_SETUP.md`](../../DATABASE_SETUP.md)
- `SECURITY.md` — auth endpoints, tokens; `DEPLOYMENT.md` — production ops.
