# Testing guide — canonical verification commands and caveats

One source of truth for how to verify a change. The *required* checks for
"done" are scoped in the root `AGENTS.md` ("Definition of done") by area;
this guide is the full command reference and the caveats around them.

## Backend (from repo root; Makefile exports PYTHONPATH=backend)

```bash
make install-dev      # pip install -e "./backend[dev,audio]" — permitted, once
make test             # pytest backend/tests -v
make check            # ruff lint + format-check + tests in one go
make typecheck        # mypy core audio (separate from check)
make lint / format    # ruff check / ruff format (line-length 100, target py311)
make dev              # uvicorn --factory core.server.app:create_app --reload, port 8000
```

Python tests need `backend/` on the import path — the Makefile and root
`pyproject.toml` (`pythonpath = ["backend", "."]`) handle it; bare
`python -m ...` invocations need `export PYTHONPATH="$PWD/backend"`.

On Windows there is no `make`: use `.venv\Scripts\python.exe -m ...` with
`$env:PYTHONPATH = "$PWD/backend"`.

## Frontend (from `frontend/`)

```bash
npm install           # or make frontend-install from the repo root
npm test -- --run     # vitest (plain `npm test` stays in watch mode)
npm run lint          # eslint, --max-warnings 0
npm run typecheck     # tsc --noEmit
npm run screenshots   # visual regression (needs the stack running)
```

## Caveats

- **Audio-test skips are not passes.** Tests marked `audio` need the
  optional extras (`faster-whisper`, `webrtcvad`) and skip gracefully
  without them. If a summary says a suite is green while audio tests
  skipped, count what was actually covered and say so plainly.
- Test markers: `integration`, `audio` (pytest config in the root
  `pyproject.toml`).
- If a check fails with "command not found", run `make install-dev` once
  (permitted) and retry before reporting.
- A check failure is reported, never suppressed. Don't skip tests,
  weaken assertions, or silence errors to make verification pass; fix
  the cause instead.

## Invariant coverage pointers

Two suites directly cover the load-bearing approval invariants (see
`architecture.md` invariant 3): `backend/tests/test_websocket_approval.py`
and `backend/tests/test_manual_reference.py`. When editing that flow,
run them explicitly.
