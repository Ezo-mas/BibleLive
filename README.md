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
- Node.js 18+ (required to build the frontend into `static/frontend/`; that directory is generated and not committed, so it must be built once after cloning)
- ffmpeg (required by the audio pipeline)
- A microphone (for live reference detection)

On Windows use `python` instead of `python3` in the commands below.

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
| `/operator`        | Operator panel: approve/clear pending references |
| `/docs`            | Interactive API docs (Swagger UI)                |

### Run the audio pipeline only

```bash
PYTHONPATH="$PWD/backend" python3 -m audio.cli [options]

Options:
  --bible-db PATH         Bible SQLite database (default: bible_data/biblelive.sqlite)
  --server URL            BibleLive server URL (default: http://localhost:8000)
  --model SIZE            Whisper model: tiny, base, small, medium, large-v2,
                          large-v3, distil-large-v3 (default: small)
  --device DEV            Whisper device: cpu or cuda (default: cuda)
  --compute-type TYPE     Whisper compute type: float16, float32, int8,
                          int8_float16 (default: int8; use float16 on RTX 20xx+)
  --language LANG         Spoken language: en or lt (default: en)
  --vad-aggressiveness N  WebRTC VAD aggressiveness 0-3 (default: 2)
  --control-host HOST     Runtime language-control server host (default: 127.0.0.1)
  --control-port PORT     Runtime language-control server port (default: 8001)
  --control-token TOKEN   Bearer token the control server requires when set
  --no-control            Disable the runtime language-control server
  --log-level LEVEL       DEBUG, INFO, WARNING, ERROR (default: INFO, or
                          $BIBLELIVE_AUDIO_LOG_LEVEL)
```

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
| GET    | `/api/state`        | Current server state snapshot (requires auth if enabled) |

## WebSocket protocol

- `ws://<host>/ws/display` — display clients receive `reference` and `clear` events
- `ws://<host>/ws/operator` — operator clients receive `pending_update`,
  `approval_confirmed`, `clear_confirmed`, `pending_cleared`, `state_snapshot`,
  `auto_mode_confirmed`, and `connection_status` events; they send
  `{action: 'approve', reference_id: 'uuid'}`, `{action: 'clear'}`,
  `{action: 'clear_pending'}`, or
  `{action: 'set_auto_mode', auto_mode: 'off'|'high'|'medium_and_up'|'all'}`
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
| `BIBLELIVE_KJV_JSON_PATH` | `data/kjv2.json`             | KJV JSON text file       |
| `BIBLELIVE_HOST`          | `0.0.0.0`                   | Server bind host         |
| `BIBLELIVE_PORT`          | `8000`                      | Server port              |
| `BIBLELIVE_RELOAD`        | `true`                      | Uvicorn auto-reload (dev)|
| `BIBLELIVE_AUTO_MODE`     | `off`                       | Default auto-approve mode (no persisted setting) |
| `BIBLELIVE_SETTINGS_PATH` | `data/server_settings.json` | Persisted operator settings |

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
│   │   ├── verse_parser.py
│   │   └── bible_lookup.py
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
│   └── legacy/                   # Original self-contained HTML pages (reference)
├── static/frontend/              # Built frontend bundle (served by the server)
├── bible_data/                   # Bible SQLite databases
├── data/                         # KJV / LITV JSON text files
├── pyproject.toml                # Root tooling config (ruff, pytest, mypy)
├── Makefile                      # Backend + frontend task runner
├── start.sh                      # Start server + audio pipeline
└── start.ps1                     # Same, for Windows PowerShell
```

## Testing

```bash
pytest                    # runs backend/tests (configured in root pyproject.toml)
pytest backend/tests -q   # explicit path
make test                 # verbose via the Makefile
make check                # lint + format-check + tests (one route)
```

Tests that need a Bible database build one from the tracked `data/kjv2.json`,
so a fresh checkout and CI work without a pre-imported `bible_data/` file.
Audio tests require the optional extras: `pip install -e "./backend[audio]"`.

## Repository layout

This is a two-part monorepo:

| Directory   | Contents                                                        |
|-------------|-----------------------------------------------------------------|
| `backend/`  | Python server, audio pipeline, tests, scripts, requirements      |
| `frontend/` | React + TypeScript UI (Vite), built into `static/frontend/`     |

The root `pyproject.toml` only holds tool configuration (ruff, pytest, mypy);
`backend/pyproject.toml` holds the installable package metadata.

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
