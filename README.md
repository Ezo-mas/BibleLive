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

- Python 3.10+
- Node.js 18+ (only for building the frontend; a prebuilt bundle is included in `static/frontend/`)
- ffmpeg (required by the audio pipeline)
- A microphone (for live reference detection)

## Installation

```bash
# Backend as an editable install (core server + parser + dev/test tools)
pip install -e ./backend

# Optional: audio pipeline extras (faster-whisper, webrtcvad, numpy)
pip install -e "./backend[audio]"

# Optional: plain requirements files instead of an install
pip install -r backend/requirements.txt
pip install -r backend/requirements-audio.txt

# Optional: rebuild the frontend from source
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
  --kjv PATH              KJV JSON file (default: data/kjv2.json)
  --server URL            BibleLive server URL (default: http://localhost:8000)
  --model SIZE            Whisper model: tiny, base, small, medium, large-v2,
                          large-v3, distil-large-v3 (default: small)
  --device DEV            Whisper device: cpu or cuda (default: cuda)
  --compute-type TYPE     Whisper compute type: float16, float32, int8,
                          int8_float16 (default: int8; use float16 on RTX 20xx+)
  --language LANG         Spoken language: en or lt (default: en)
  --vad-aggressiveness N  WebRTC VAD aggressiveness 0-3 (default: 2)
  --log-level LEVEL       DEBUG, INFO, WARNING, ERROR (default: INFO, or
                          $BIBLELIVE_AUDIO_LOG_LEVEL)
```

#### Pipeline logging

The CLI configures logging once at startup (library modules never touch the root
logger), so audio capture, VAD, Whisper, parsing and reference delivery all
report progress:

```text
INFO - audio.capture  - First audio input received from device (320 bytes, 16000Hz mono s16le)
INFO - audio.capture  - Audio input active: 10s captured (1001 chunks, queue=1, dropped=0)
INFO - audio.pipeline - Voice activity detected, capturing utterance...
INFO - audio.pipeline - Utterance ended: 1.20s of speech (40 frames) queued for transcription
INFO - audio.pipeline - Processing utterance (40 frames, 1.20s audio)...
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
  `approval_confirmed`, `clear_confirmed`, `pending_cleared`, `state_snapshot`, and
  `connection_status` events; they send `{action: 'approve', reference_id: 'uuid'}`,
  `{action: 'clear'}`, or `{action: 'clear_pending'}`
- Operator WebSocket requires `?token=xxx` query parameter when authentication is enabled

## Configuration (environment variables)

| Variable                  | Default                     | Description              |
|---------------------------|-----------------------------|--------------------------|
| `BIBLELIVE_DB_PATH`       | `bible_data/biblelive.sqlite` | Bible SQLite database  |
| `BIBLELIVE_KJV_JSON_PATH` | `data/kjv2.json`             | KJV JSON text file       |
| `BIBLELIVE_HOST`          | `0.0.0.0`                   | Server bind host         |
| `BIBLELIVE_PORT`          | `8000`                      | Server port              |
| `BIBLELIVE_RELOAD`        | `true`                      | Uvicorn auto-reload (dev)|

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
└── start.sh                      # Start server + audio pipeline
```

## Testing

```bash
pytest                    # runs backend/tests (configured in root pyproject.toml)
pytest backend/tests -q   # explicit path
make test                 # verbose via the Makefile
```

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
