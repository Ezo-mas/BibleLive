# BibleLive

Real-time Bible reference detection and display system. Listens to live audio
(e.g. a sermon), detects spoken Bible references, and shows them on an
audience-facing display — with an operator panel to review and approve
references before they go on screen.

## Architecture

```
┌─────────────────┐    HTTP/WS     ┌──────────────────────┐
│  Audio Pipeline │ ─────────────► │  FastAPI Server      │
│  (ffmpeg + VAD  │  POST /api/    │  (core/)             │
│   + Whisper)    │  reference     │                      │
└─────────────────┘                │  • verse parser      │
                                   │  • Bible lookup      │
┌─────────────────┐    WS          │  • approval queue    │
│  Display page   │ ◄───────────── │                      │
│  /display       │                └──────────────────────┘
├─────────────────┤    WS                    ▲
│  Operator page  │ ◄────────────────────────┘
│  /operator      │
└─────────────────┘
   Vite + React + TypeScript frontend (frontend/), built to
   static/frontend/ and served by the FastAPI server
```

## Requirements

- Python 3.11+
- Node.js 18+ (required to build the frontend into `static/frontend/`; that
  directory is generated and not committed, so it must be built once after
  cloning)
- ffmpeg (required by the audio pipeline)
- A microphone (for live reference detection)

On Windows use `python` instead of `python3` in the commands below (and
`start.ps1` instead of `start.sh`).

## Installation

```bash
# Backend as an editable install (core server + parser + dev/test tools)
pip install -e ./backend

# Optional: audio pipeline extras (faster-whisper, webrtcvad, numpy)
pip install -e "./backend[audio]"
```

On Windows, `webrtcvad-wheels` is installed automatically (no C++ build tools
needed). The audio extras also pin `setuptools<81`, because the VAD package
imports `pkg_resources` at runtime.

```bash
# Optional: plain requirements files instead of an install
pip install -r backend/requirements.txt
pip install -r backend/requirements-audio.txt

# Required: build the frontend (once after cloning; also re-run after frontend changes)
cd frontend && npm install && npm run build
```

Python packages live under `backend/` and are imported as `core.*` / `audio.*`.
Commands that run from the repository root need `backend/` on the import path:

```bash
export PYTHONPATH="$PWD/backend"     # start.sh and the Makefile do this for you
```

## Usage

### Run everything (server + audio pipeline)

```bash
./start.sh
```

### Run everything on Windows (PowerShell)

```powershell
Set-ExecutionPolicy -Scope Process Bypass   # only if script execution is blocked
.\start.ps1
```

Uses `.venv\Scripts\python.exe` when present, `BIBLELIVE_HOST`/`BIBLELIVE_PORT`
(default `8000`), and `BIBLELIVE_AUDIO_ARGS` for extra pipeline flags, e.g.
`$env:BIBLELIVE_AUDIO_ARGS = "--device cpu --model base"`.

### Run the server only

```bash
PYTHONPATH="$PWD/backend" python3 -m core.server

# or, from anywhere in the repo:
make dev
```

Then open:

| URL                | Purpose                                          |
|--------------------|--------------------------------------------------|
| `/display`         | Audience screen showing approved references      |
| `/operator`        | Operator panel: approve/clear pending references, manual passage, history |
| `/docs`            | Interactive API docs (Swagger UI)                |

### Run the audio pipeline only

```bash
PYTHONPATH="$PWD/backend" python3 -m audio.cli [options]

Options:
  --bible-db PATH         Bible SQLite database (default: bible_data/biblelive.sqlite)
  --server URL            BibleLive server URL (default: http://localhost:8000)
  --model SIZE            Whisper model: tiny, base, small, medium, large-v2,
                          large-v3, large-v3-turbo, distil-large-v3 (default: small)
  --device DEV            Whisper device: cpu or cuda (default: cuda)
  --compute-type TYPE     Whisper compute type: float16, float32, int8,
                          int8_float16 (default: int8; use float16 on RTX 20xx+)
  --language LANG         Spoken language: en or lt (default: en)
  --vad-aggressiveness N  WebRTC VAD aggressiveness 0-3 (default: 2)
  --beam-size N           Whisper beam width; lower is faster (default: 5)
  --cpu-threads N         Whisper CPU threads, 0 = library default (default: 0)
  --end-of-speech-s S     Silence that ends an utterance (default: 1.5)
  --min-speech-s S        Shorter utterances are dropped (default: 0.3)
  --soft-utterance-s S    Past this, end the utterance at the next pause (default: 8)
  --max-utterance-s S     Hard utterance length cap (default: 12)
  --input-device NAME     Microphone: exact DirectShow name on Windows, PulseAudio
                          source on Linux, avfoundation index on macOS (default:
                          first Windows input, system default elsewhere)
  --list-input-devices    Print the Windows audio inputs and exit
  --control-host HOST     Runtime language-control server host (default: 127.0.0.1)
  --control-port PORT     Runtime language-control server port (default: 8001)
  --control-token TOKEN   Bearer token the control server requires when set
  --no-control            Disable the runtime language-control server
  --log-level LEVEL       DEBUG, INFO, WARNING, ERROR (default: INFO, or
                          $BIBLELIVE_AUDIO_LOG_LEVEL)
```

**Choosing a model.** On an NVIDIA GPU, use
`--model large-v3-turbo --compute-type float16`, especially for Lithuanian.
On the two Lithuanian benchmark services (25 references), replayed through
the live segmentation (`run_benchmark.py --mode offline`), it reported 20
of them against 5 for `small`, counting a detection within 30 s of the
spoken reference (live detections arrive about 12 s after the words). It
also decoded faster than `small` int8 on an RTX 5070 Ti (about 0.06x real
time). The `distil-*` models are English-only. On a CPU-only machine
`small` (int8) is the practical ceiling.

#### Runtime language switching

While running, the spoken language can be switched between English and
Lithuanian without restarting (the Whisper model is language-agnostic and is not
reloaded):

```bash
curl http://127.0.0.1:8001/language                    # {"language":"en"}
curl -X POST http://127.0.0.1:8001/language \
     -H 'Content-Type: application/json' -d '{"language":"lt"}'
```

The control server is bound to loopback by default. Exposing it on another host
with `--control-host` should always be paired with `--control-token` (clients
send `Authorization: Bearer <token>`). A switch discards the partial utterance
being captured in the old language; utterances already queued keep the language
they were captured in — they are transcribed and parsed with their original
language and prompt.

The operator console does the same from **Settings → Audio → Spoken language**,
and shows the active language (`EN` / `LT`) in the header. A browser cannot call
the loopback control port itself, so the console goes through the server:
`GET`/`POST /api/language` (operator-authenticated like the other operator
endpoints) forwards to the pipeline and answers `503` when the pipeline is not
running or was started with `--no-control`. The server finds the pipeline at
`BIBLELIVE_AUDIO_CONTROL_URL` (default `http://127.0.0.1:8001`, the pipeline's
own default) and sends `BIBLELIVE_AUDIO_CONTROL_TOKEN` as the bearer token when
the pipeline runs with `--control-token`. If you change `--control-host` or
`--control-port`, change the URL to match.

#### Pipeline logging

The CLI configures logging once at startup (library modules never touch the root
logger), so audio capture, VAD, Whisper, parsing and reference delivery all
report progress:

```text
INFO - audio.capture  - First audio input received from device (320 bytes, 16000Hz mono s16le)
INFO - audio.capture  - Audio input active: 10s captured (1001 chunks, queue=1, dropped=0)
INFO - audio.pipeline - Voice activity detected, capturing utterance...
INFO - audio.pipeline - Utterance ended: 1.20s of speech (40 frames, en) queued for transcription
INFO - audio.pipeline - Processing utterance (40 frames, 1.20s audio, language=en)...
INFO - audio.stt      - Transcription (842ms): 'and he said read this today john three sixteen'
INFO - audio.pipeline - Found 1 Bible reference(s):
INFO - audio.pipeline -   - John 3:16 [high]
INFO - audio.sender   - Sent to server (31ms): John 3:16
```

Use `--log-level DEBUG` (or `BIBLELIVE_AUDIO_LOG_LEVEL=DEBUG`) to also trace ffmpeg
output, ignored short VAD blips, and skipped duplicates.

## HTTP API

| Method | Endpoint            | Description                              |
|--------|---------------------|------------------------------------------|
| POST   | `/api/reference`    | Submit a parsed Bible reference (pending) |
| POST   | `/api/approve`      | Approve a pending reference (by reference_id) |
| POST   | `/api/clear`        | Clear the display                        |
| POST   | `/api/clear-pending`| Clear the pending queue                  |
| GET    | `/api/books`        | List canonical books (for Find Scripture) |
| GET    | `/api/lookup`       | Look up verse text by coordinates        |
| GET    | `/api/state`        | Current server state snapshot (requires auth if enabled) |
| GET    | `/healthz`          | Liveness probe                           |
| GET    | `/readyz`           | Readiness probe (database + frontend built checks) |

## WebSocket protocol

- `ws://<host>/ws/display` — display clients receive `reference`, `clear`,
  `display_settings`, and `connection_status` events
- `ws://<host>/ws/operator` — operator clients receive `pending_update`,
  `approval_confirmed`, `clear_confirmed`, `pending_cleared`, `state_snapshot`,
  `auto_mode_confirmed`, `display_settings_confirmed`, and `connection_status`
  events; they send these actions:
  - `{action: 'approve', reference_id: 'uuid'}`
  - `{action: 'clear'}`
  - `{action: 'clear_pending'}`
  - `{action: 'set_verse_limit', verse_limit: 3 | 5 | 10 | 'all'}`
  - `{action: 'set_display_theme', theme: 'dark' | 'light'}`
  - `{action: 'set_auto_mode', auto_mode: 'off' | 'high' | 'medium_and_up' | 'all'}`
  - `{action: 'display_reference', book, chapter, verse_start?, verse_end?}` —
    show a passage the operator picked by hand (coordinates only; the server
    re-resolves them). Also used for Show Again from display history.
- Operator WebSocket requires `?token=xxx` query parameter when authentication is enabled

## Auto-approve mode

The operator console can show newly detected references without waiting for an
approval: `Off` (default — everything is reviewed), `High`, `Med+` (medium and
high confidence) or `All`. Only references that arrive *after* the change are
auto-approved; anything already in the pending queue stays pending, and manual
Approve keeps working as an override.

The mode is server-side state, persisted to `BIBLELIVE_SETTINGS_PATH`
(`data/server_settings.json`, written atomically). A persisted mode survives
restarts and takes precedence over the `BIBLELIVE_AUTO_MODE` default, which only
applies when nothing is persisted. Settings that are missing, malformed, or
invalid log a warning and fall back to that default; an invalid
`BIBLELIVE_AUTO_MODE` means `off`.

## Configuration (environment variables)

| Variable                  | Default                     | Description              |
|---------------------------|-----------------------------|--------------------------|
| `BIBLELIVE_DB_PATH`       | `bible_data/biblelive.sqlite` | Bible SQLite database  |
| `BIBLELIVE_HOST`          | `0.0.0.0`                   | Server bind host         |
| `BIBLELIVE_PORT`          | `8000`                      | Server port              |
| `BIBLELIVE_RELOAD`        | `true`                      | Uvicorn auto-reload (dev)|
| `BIBLELIVE_AUTO_MODE`     | `off`                       | Default auto-approve mode (no persisted setting) |
| `BIBLELIVE_SETTINGS_PATH` | `data/server_settings.json` | Persisted operator settings |
| `BIBLELIVE_REQUIRE_AUTH`  | `false`                     | Protect operator endpoints with a token |
| `BIBLELIVE_OPERATOR_TOKEN`| unset                       | Operator token (required if auth enabled) |
| `BIBLELIVE_CORS_ORIGINS`  | empty                       | Comma-separated allowed origins (empty = same-origin only) |
| `BIBLELIVE_LOG_LEVEL`     | `INFO`                      | Server log level         |
| `BIBLELIVE_DB_READONLY`   | `false`                     | Fail rather than write to the Bible DB |
| `BIBLELIVE_AUDIO_LOG_LEVEL` | `INFO`                    | Audio pipeline log level |

See `.env.example` for the full annotated list. `BIBLELIVE_AUDIO_LOG_LEVEL`,
`BIBLELIVE_AUDIO_ARGS` (start scripts), and the pipeline's model/device/language
options are audio-pipeline-side settings, passed as CLI flags on
`python3 -m audio.cli` — see the options table above.

## Makefile targets

| Target | Purpose |
| --- | --- |
| `make install` / `install-audio` / `install-dev` | Backend pip installs (core / +audio / +dev) |
| `make test` / `test-integration` | pytest (`-m integration` for the subset) |
| `make lint` / `format` / `typecheck` | ruff check / ruff format / mypy core+audio |
| `make check` | lint + format-check + tests (backend one-route) |
| `make dev` | uvicorn with reload on port 8000 |
| `make frontend-install` / `frontend-dev` / `frontend-build` | npm install / dev server / production build |
| `make frontend-lint` / `frontend-format` / `frontend-test` | npm run lint / format / test |
| `make check-frontend` | frontend lint + tests |
| `make clean` | remove build artifacts and caches |

## Project layout

```
BibleLive/                        # Monorepo root: tooling + shared assets
├── backend/                      # Python backend (importable as core.*, audio.*)
│   ├── core/                     # FastAPI server, verse parser, Bible lookup
│   │   ├── server/               # Server package (models, auth, state, app factory)
│   │   │   ├── app.py            # create_app() factory + main() entry point
│   │   │   ├── models.py         # Pydantic request/response models
│   │   │   ├── auth.py           # Operator token verification
│   │   │   ├── connections.py    # WebSocket connection management
│   │   │   ├── settings_store.py # Persisted operator settings (auto-approve mode)
│   │   │   └── state.py          # AppState (pending queue, display, history)
│   │   ├── config.py             # Env-driven configuration
│   │   ├── biblelive_server.py   # Compat shim -> core.server
│   │   ├── verse_parser.py       # Reference parsing (English)
│   │   ├── verse_parser_lt.py    # Lithuanian language data for the parser
│   │   ├── passage.py, books.py  # Manual passage validation + canon
│   │   ├── bible_lookup.py       # SQLite verse text lookup
│   │   └── import_bible.py       # Bible DB importer
│   ├── audio/                    # Audio pipeline (ffmpeg + VAD + Whisper STT)
│   │   ├── cli.py                # Entry point
│   │   ├── pipeline.py           # BibleLiveAudioProcessor
│   │   ├── capture.py, vad.py    # ffmpeg capture + WebRTC VAD
│   │   ├── stt.py                # faster-whisper transcription
│   │   ├── sender.py             # Reference delivery (dedup + retry/backoff)
│   │   ├── control.py            # Runtime language-control HTTP server
│   │   └── config.py, metrics.py
│   ├── tests/                    # Pytest suite
│   ├── scripts/                  # Demo and verification scripts
│   ├── requirements.txt          # Core dependencies
│   ├── requirements-audio.txt    # Audio pipeline dependencies
│   └── pyproject.toml            # Installable package metadata
├── frontend/                     # Vite + React + TypeScript frontend
│   ├── src/display/              # Audience screen
│   ├── src/operator/             # Operator panel
│   ├── src/shared/               # Types, reference formatting, WS hook
│   └── display.html, operator.html  # Dev-mode Vite entry points
├── static/frontend/              # Built frontend bundle (generated, not committed)
├── bible_data/                   # Bible SQLite databases (generated; source JSON in data/)
├── data/                         # KJV / LITV JSON text files
├── docs/                         # Agent guides (docs/agent-guides/) + TECH_DEBT.md
├── pyproject.toml                # Root tooling config (ruff, pytest, mypy)
├── Makefile                      # Backend + frontend task runner
├── start.sh                      # Start server + audio pipeline (POSIX)
└── start.ps1                     # Same, for Windows PowerShell
```

This is a two-part monorepo:

| Directory   | Contents                                                        |
|-------------|-----------------------------------------------------------------|
| `backend/`  | Python server, audio pipeline, tests, scripts, requirements      |
| `frontend/` | React + TypeScript UI (Vite), built into `static/frontend/`     |

The root `pyproject.toml` only holds tool configuration (ruff, pytest, mypy);
`backend/pyproject.toml` holds the installable package metadata.

## Testing

```bash
pytest                    # runs backend/tests (configured in root pyproject.toml)
pytest backend/tests -q   # explicit path
make test                 # verbose via the Makefile
make check                # lint + format-check + tests (one route)
make test-integration     # integration tests only
```

Tests that need a Bible database build one from the tracked `data/kjv2.json`,
so a fresh checkout and CI work without a pre-imported `bible_data/` file.
Audio tests require the optional extras: `pip install -e "./backend[audio]"`.
Agent-facing command conventions live in `docs/agent-guides/testing.md`.

## Development workflow (frontend)

```bash
# Terminal 1: backend
PYTHONPATH="$PWD/backend" python3 -m core.server

# Terminal 2: frontend dev server (proxies /ws to the backend, hot reload)
cd frontend && npm run dev
```

The dev server serves the pages with Vite's HMR; production pages are the
built bundle under `static/frontend/`, served by the backend at `/display`
and `/operator` (returns 503 with build instructions if not built).

## Related documentation

- `DATABASE_SETUP.md` — importing Bible data, DB schema and locations
- `DEPLOYMENT.md` — production deployment (systemd, nginx, health checks)
- `SECURITY.md` — authentication endpoints and tokens
- `docs/TECH_DEBT.md` — known deferred issues
- `docs/agent-guides/` — conventions for AI coding agents (entry point: `AGENTS.md`)
