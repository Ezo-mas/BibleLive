# AGENTS.md — guide for AI coding agents

Read this before editing. Detailed guides live in `docs/agent-guides/`
(`architecture.md`, `backend.md`, `frontend.md`, `testing.md`); project
facts that are not agent rules stay in `README.md`, `DATABASE_SETUP.md`,
`DEPLOYMENT.md`, `SECURITY.md`, and `docs/TECH_DEBT.md`.

## Behavior rules

- **Never commit or push unless the user explicitly asks.** Leave changes
  in the working tree and report what changed.
- **Ask before changing**: the operator-approval flow, the WebSocket message
  protocol (`core/server/models.py` / `frontend/src/shared/types.ts`), or
  anything under `bible_data/`. These are load-bearing for live services.
- **Stay in scope.** Fix what was asked; don't refactor adjacent code,
  reformat files, or "improve" unrelated comments. Preserve user changes
  already in the working tree. If you spot other issues, report them
  separately instead of fixing them silently.
- **Report test results honestly.** A skip is not a pass: name each skipped
  test and its reason in your summary. Locally, tests skip when ffmpeg is not
  on PATH (live-path tests), there is no display (launcher window tests), the
  KJV source is missing, or no Whisper tokenizer is cached. Missing audio
  extras show up as import errors, not skips.
- If a command fails because of missing tooling or services, say so and stop
  instead of working around it. Running the documented install commands
  (`make install-dev`, `make frontend-install` / `npm install` from
  `frontend/`) is fine; installing anything else is not.
- **Log serious deferred issues in `docs/TECH_DEBT.md`.** Only confirmed,
  out-of-scope issues: correctness/data integrity, security, realistic
  load/growth, or design flaws that make future changes much riskier. No
  style nits or speculation. Read the file first and extend an existing
  entry rather than duplicating it. Don't fix logged items unless asked,
  and don't remove or rewrite entries unless asked. Mention any entry you
  add in your summary.

## Definition of done

Before reporting done:

- **Backend changes**: run `make check` (ruff lint + format-check + pytest)
  and `make typecheck` (mypy — separate from `check`). While iterating, run
  the touched test file or `pytest backend/tests -m "not slow"` (~20 s);
  the full suite once at the end.
- **Frontend changes**: from `frontend/` run `npm run lint && npm run typecheck && npm run format:check && npm test -- --run`.
- **Detection-accuracy changes** (Whisper prompt/hotwords/beam/model, the
  `--stt` speech models, per-model spelling maps or the two-model agreement
  rule, VAD tuning, utterance segmentation, transcript post-processing,
  parser patterns): unit tests are not enough — run the benchmark before and after
  on the same corpus and report TP/FP/FN and runtime for both
  ([testing guide](docs/agent-guides/testing.md#detection-accuracy-benchmark)).
- **Network, auth or token changes** (the list in `SECURITY.md`, "Changes
  that need a security review"): a security review is required. Run
  `/security-review` if you can; otherwise say it still needs running.
- Report failures honestly, including which check failed and why; don't
  report "done" with a failing or unrun check (see Behavior rules on
  skipped tests).
- If a check fails with "command not found", run `make install-dev` once
  (permitted) and retry before reporting. On Windows `make` itself is
  missing: run the same tools via `.venv\Scripts\python.exe -m ...` (exact
  commands in the testing guide) instead of stopping.
- Full command reference and caveats (incl. test skips):
  [testing guide](docs/agent-guides/testing.md).

## What this project is

BibleLive listens to live audio (a sermon), detects spoken Bible references,
and shows them on an audience-facing display. An operator panel approves or
discards each detected reference before it goes on screen.

Pipeline: `ffmpeg → VAD → speech-to-text (Whisper, or Kmynas/Canary GGUF
models for Lithuanian) → reference parser → FastAPI server → WebSocket →
operator approval → display page`.

## Guides: what to read for each task type

- [Architecture guide](docs/agent-guides/architecture.md) — **mandatory reading
  before** touching `backend/core/server/`, `backend/audio/` (esp. `vad.py`,
  `capture.py`), frontend entry points, anything SQLite- or port-related, or
  the WebSocket protocol. The approval-flow, VAD-frame, StrictMode,
  served-routes, and SQLite rules there are load-bearing; this file only
  summarizes them (see "Non-negotiables").
- [Backend guide](docs/agent-guides/backend.md) — layout,
  conventions, and which files own which backend behavior.
- [Frontend guide](docs/agent-guides/frontend.md) — the same for the React UI;
  do not rebuild `static/frontend/` unless asked.
- [Testing guide](docs/agent-guides/testing.md) — canonical verification
  commands and test caveats.
- Database import/commit questions → `DATABASE_SETUP.md`. Deployment,
  auth/tokens, production env → `DEPLOYMENT.md` + `SECURITY.md`.

## Non-negotiables (summary; details in architecture.md)

1. The web server's port is 8000 by default everywhere; don't add a server
   port setting that defaults elsewhere. (The pipeline's language-control
   server on 8001 is a separate service.)
2. `_send_display_reference` is called only from operator-authorized
   server paths (`approve_pending` / `display_manual_passage`); `display_reference`
   carries coordinates only and the server re-resolves them. No third door.
3. VAD frames must be exactly the size the active VAD requires at 16 kHz
   (WebRTC: 10/20/30 ms) with no dropped carry-over.
4. No StrictMode in frontend entry points (two WebSockets); both `main.tsx`
   files go through `shared/renderApp.tsx`.
5. Pages live at `/display` and `/operator`; the bundle at `/static/frontend/`
   is generated — don't hand-edit, don't rebuild unless asked.
6. The SQLite DB is read-only on the request path (no writes).

## Local directories

`results/` and `bench_audio/` are local and not committed; `bench_audio/`
(sermon recordings) is read-only.

Commands, conventions, and the task → files mapping live in the guides;
the root DoD above is the authority for required checks.
