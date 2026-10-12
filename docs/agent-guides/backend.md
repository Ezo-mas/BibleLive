# Backend guide — layout, conventions, and task mapping

Commands for verification live in `testing.md` (the canonical list);
architecture invariants live in `architecture.md`; project-level facts
(install, run, env vars) stay in `README.md`. This guide covers backend
layout, Python conventions, and which files own which behavior —
including the database lookup/import pointers.

## What this project is

BibleLive listens to live audio (a sermon), detects spoken Bible
references, and shows them on an audience-facing display. Pipeline:
`ffmpeg → VAD → Whisper STT → reference parser → FastAPI server →
WebSocket → operator approval → display page`.

## Repo layout (backend)

```
backend/
  core/            FastAPI server, verse parser, Bible lookup (imported as core.*)
    server/app.py  The real server. Entry: uvicorn --factory core.server.app:create_app
    biblelive_server.py  Thin back-compat shim — do NOT edit; real code is in server/
  audio/           Capture/VAD/STT/sender pipeline (imported as audio.*)
    biblelive_audio_pipeline.py  Thin back-compat shim — same rule
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
| Change manual scripture selection (Find Scripture) | `backend/core/passage.py` (validation + resolution) over `backend/core/books.py` (canon); endpoints and the `display_reference` action in `backend/core/server/app.py` |
| Change display history source / Show Again state | `backend/core/server/state.py` (`AppState.reference_history`, newest first, `MAX_HISTORY_REFERENCES`, 20-entry snapshot window) |
| Change audio capture/VAD/STT/sending | the matching module in `backend/audio/`; if it can change what gets detected, benchmark before/after (`testing.md`) |
| Measure detection accuracy | `benchmarks/run_benchmark.py`, manifests + gold in `benchmarks/corpus/`, method in `benchmarks/README.md` |
| Change authentication or WS connection management | `backend/core/server/auth.py`, `connections.py` |
| Change WS message protocol or approval flow | **stop — ask the user first** (Behavior rules) |

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
