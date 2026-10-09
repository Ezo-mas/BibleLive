# AGENTS.md — guide for AI coding agents

Read this before editing. It covers repo layout, commands, conventions, and
the architecture invariants that are easy to break by accident.

## Behavior rules

- **Never commit or push unless the user explicitly asks.** Leave changes in
  the working tree and report what changed.
- **Ask before changing**: the operator-approval flow, the WebSocket message
  protocol (`core/server/models.py` / `frontend/src/shared/types.ts`), or
  anything under `bible_data/`. These are load-bearing for live services.
- **Stay in scope.** Fix what was asked; don't refactor adjacent code,
  reformat files, or "improve" unrelated comments. If you spot other issues,
  report them separately instead of fixing them silently.
- **Report test results honestly.** If the audio tests *skip* because the
  optional extras (faster-whisper, webrtcvad) aren't installed, say "skipped"
  in your summary — a skip is not a pass, and the full suite can look green
  while covering nothing.
- If a command fails because of missing tooling or services, say so and stop
  instead of working around it. Running the documented install commands
  (`make install-dev`, `make frontend-install` / `npm install` from
  `frontend/`) is fine; installing anything else is not.
- **Log serious deferred issues in `TECH_DEBT.md`.** Only confirmed,
  out-of-scope issues: correctness/data integrity, security, realistic
  load/growth, or design flaws that make future changes much riskier. No
  style nits or speculation. Read the file first and extend an existing
  entry rather than duplicating it. Don't fix logged items unless asked,
  and don't remove or rewrite entries unless asked. Mention any entry you
  add in your summary.

## Definition of done

Before reporting done:

- **Backend changes**: run `make check` (ruff lint + format-check + pytest)
  and `make typecheck` (mypy — separate from `check`).
- **Frontend changes**: from `frontend/` run `npm run lint && npm run typecheck && npm test -- --run`.
- Report failures honestly, including which check failed and why; don't
  report "done" with a failing or unrun check (see Behavior rules on
  skipped tests).
- If a check fails with "command not found", run `make install-dev` once
  (permitted) and retry before reporting.

## What this project is

BibleLive listens to live audio (a sermon), detects spoken Bible references,
and shows them on an audience-facing display. An operator panel approves or
discards each detected reference before it goes on screen.

Pipeline: `ffmpeg → VAD → Whisper STT → reference parser → FastAPI server →
WebSocket → operator approval → display page`.

## Repo layout

```
backend/
  core/            FastAPI server, verse parser, Bible lookup (imported as core.*)
    server/app.py  The real server. Entry: uvicorn --factory core.server.app:create_app
    biblelive_server.py  Thin back-compat shim — do NOT edit; real code is in server/
  audio/           Capture/VAD/STT/sender pipeline (imported as audio.*)
    biblelive_audio_pipeline.py  Thin back-compat shim — same rule
  scripts/         DB import / maintenance scripts
  tests/           pytest suite
frontend/
  src/operator/    Operator panel (React)
  src/display/     Audience display page (React)
  src/shared/      Code shared by both pages (types, hooks, renderApp)
  src/components/  Shared UI components
  src/hooks/       Shared React hooks (useTheme)
  src/styles/      tokens.css + global.css (page CSS lives beside each App)
  scripts/screenshot.mjs  Visual regression snapshots
static/frontend/   Prebuilt frontend bundle served by FastAPI — regenerate
                   with `make frontend-build`, don't hand-edit. After
                   changing frontend/src, the UI only updates once the
                   bundle is rebuilt; don't rebuild it unless asked
                   (rebuild diffs are big and generated).
bible_data/        SQLite Bible database
data/              Source JSON files for Bible imports (kjv.json, ltkb.json)
```

## Commands

```bash
# Backend (from repo root; Makefile exports PYTHONPATH=backend)
make install-dev      # pip install -e "./backend[dev,audio]"
make test             # pytest backend/tests -v
make lint / format    # ruff check / ruff format (line-length 100, target py311)
make typecheck        # mypy core audio
make dev              # uvicorn --factory core.server.app:create_app --reload, port 8000
make check            # lint + format-check + tests in one go

# Frontend (from frontend/)
npm test -- --run     # vitest (plain `npm test` stays in watch mode)
npm run lint          # eslint, --max-warnings 0
npm run typecheck     # tsc --noEmit
npm run screenshots   # visual regression (needs the stack running)
./start.sh            # start the whole stack
```

Python tests need `backend/` on the import path — the Makefile and root
`pyproject.toml` (`pythonpath = ["backend", "."]`) handle it; bare
`python -m ...` invocations need `export PYTHONPATH="$PWD/backend"`.

## Where to look (common tasks)

| Task | Files |
| --- | --- |
| Add/adjust a verse-reference pattern | `backend/core/verse_parser.py` (+ `verse_parser_lt.py` for Lithuanian) and tests in `backend/tests/test_verse_parser*.py` |
| Change an HTTP endpoint or WS handler | `backend/core/server/app.py`, `state.py`; message shapes in `models.py` + mirrored in `frontend/src/shared/types.ts` |
| Change verse text lookup | `backend/core/bible_lookup.py` (SQLite at `bible_data/biblelive.sqlite`) |
| Change manual scripture selection (Find Scripture) | `backend/core/passage.py` (validation + resolution) over `backend/core/books.py` (canon); endpoints and the `display_reference` action in `backend/core/server/app.py`; UI in `frontend/src/operator/components/ScriptureSearch.tsx` |
| Import a new Bible translation | `backend/core/import_bible.py` |
| Change audio capture/VAD/STT/sending | the matching module in `backend/audio/` |
| Change operator UI | `frontend/src/operator/` (+ `operator.css`) |
| Change display UI / text fitting | `frontend/src/display/` (+ `display.css`, `fitText.ts`) |
| Change shared WS hook / message types | `frontend/src/shared/useWebSocket.ts`, `types.ts` |
| Change authentication or WS connection management | `backend/core/server/auth.py`, `connections.py` |
| Change WS message protocol or approval flow | **stop — ask first** (see Behavior rules) |

## Conventions

- Python: ruff config in root `pyproject.toml`; imports are grouped with
  `core`/`audio` as first-party. Tests live in `backend/tests/test_*.py`;
  markers: `integration`, `audio`.
- TypeScript: ESM, React 18. Prettier: no semicolons, single quotes, width
  100 (`frontend/.prettierrc.json`). ESLint runs with `--max-warnings 0`.
- Don't add dependencies without asking; the current set is minimal on
  purpose (FastAPI/uvicorn, faster-whisper, webrtcvad; React + Vite).

## Architecture invariants (don't break these)

1. **The server is `core.server.app`, not `core.biblelive_server`** — the
   latter (and `audio.biblelive_audio_pipeline`) are compat shims kept for
   old imports. Edit `core/server/`, not the shims.
2. **Port 8000 is the default everywhere** — `core/config.py`, the audio
   sender/CLI, the Vite dev proxy, and docs all assume it (`start.sh` allows
   a `BIBLELIVE_PORT` override, but 8000 stays the default). Don't introduce
   a config knob that defaults elsewhere. Frontend WS URLs use
   `window.location.host`, so they follow whatever port serves the page.
3. **Operator approval flow**: audio pipeline POSTs references to
   `/api/reference`; the server queues them and pushes to the operator over
   WebSocket; `_send_display_reference` (the only path that pushes a
   reference to display sockets) is called from exactly two
   operator-authorized places: `AppState.approve_pending()`, and
   `AppState.display_manual_passage()` — the `display_reference` action, which
   shows a passage the operator picked by hand. The display still never sees an
   unapproved detection, and a manual passage never enters the pending queue or
   carries a confidence (it is `source="manual"`, so nothing can auto-approve
   it). The supported operator actions are `approve`, `clear`, `clear_pending`,
   `set_verse_limit`, `set_display_theme`, `set_auto_mode`, `display_reference`
   — there is no `reject` action; discarding a wrong detection is done
   implicitly by approving the next one or via `clear_pending`. Covered by
   `backend/tests/test_websocket_approval.py` and
   `backend/tests/test_manual_reference.py`.
   Both doors resolve a passage the same way: `display_reference` carries
   coordinates only (no ID, no confidence, no text) and the server re-resolves
   them through `core/passage.py`, so a client cannot inject verse text or pass
   a manual selection off as a detection.
4. **VAD frame discipline**: WebRTC VAD requires exact 10/20/30 ms frames at
   16 kHz. `AudioCapture.get_audio_frame()` must return exactly that many
   bytes and must not drop carry-over bytes between calls (it keeps a
   `_frame_buffer`).
5. **No StrictMode in the frontend entry points** — it double-mounts in dev
   and would open two WebSockets. The shared `shared/renderApp.tsx` helper
   documents this; keep both `main.tsx` files using it.
6. **Frontend is served by the backend**: pages at `/display` and `/operator`,
   built assets under `/static/frontend/` (Vite `base` in
   `frontend/vite.config.ts`, which also proxies `/ws` to the backend in
   dev). Dev uses the same paths via a Vite middleware — don't "simplify" to
   `/operator.html` links.
7. **SQLite DB** at `bible_data/biblelive.sqlite` is opened with
   `check_same_thread=False` (cross-thread reads in FastAPI workers). Treat
   it as read-only: don't add writes on the request path.

## Known pitfalls / dead ends

- `core/verse_parser.py` and `core/verse_parser_lt.py` both exist: the `_lt`
  one is Lithuanian-language parsing. Don't merge them blindly.
- `bible_lookup.py` has no abbreviation-mapping dict — lookups go through the
  SQLite `books` table.
- Tests marked `audio` need the optional extras (faster-whisper, webrtcvad);
  they skip gracefully without them.
