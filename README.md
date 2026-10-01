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
# Core server + parser + dev/test tools
pip install -r requirements.txt

# Optional: audio pipeline extras (faster-whisper, webrtcvad, numpy)
pip install -r requirements-audio.txt

# Optional: rebuild the frontend from source
cd frontend && npm install && npm run build
```

## Usage

### Run everything (server + audio pipeline)

```bash
./start.sh
```

### Run the server only

```bash
python3 core/biblelive_server.py
```

Then open:

| URL                | Purpose                                          |
|--------------------|--------------------------------------------------|
| `/display`         | Audience screen showing approved references      |
| `/operator`        | Operator panel: approve/clear pending references |
| `/docs`            | Interactive API docs (Swagger UI)                |

### Run the audio pipeline only

```bash
python3 audio/biblelive_audio_pipeline.py [options]

Options:
  --bible-db PATH         Bible SQLite database (default: bible_data/biblelive.sqlite)
  --kjv PATH              KJV JSON file (default: data/kjv.json)
  --server URL            BibleLive server URL (default: http://localhost:8000)
  --model SIZE            Whisper model: tiny, base, small, medium, large-v2 (default: tiny)
  --device DEV            Whisper device: cpu or cuda (default: cpu)
  --vad-aggressiveness N  WebRTC VAD aggressiveness 0-3 (default: 2)
```

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
| `BIBLELIVE_KJV_JSON_PATH` | `data/kjv.json`             | KJV JSON text file       |
| `BIBLELIVE_HOST`          | `0.0.0.0`                   | Server bind host         |
| `BIBLELIVE_PORT`          | `8000`                      | Server port              |
| `BIBLELIVE_RELOAD`        | `true`                      | Uvicorn auto-reload (dev)|

## Project layout

```
BibleLive/
├── core/                     # FastAPI server, verse parser, Bible lookup
│   ├── biblelive_server.py
│   ├── verse_parser.py
│   └── bible_lookup.py
├── audio/                    # Audio pipeline (ffmpeg + VAD + Whisper STT)
│   └── biblelive_audio_pipeline.py
├── frontend/                 # Vite + React + TypeScript frontend
│   ├── src/display/          # Audience screen
│   ├── src/operator/         # Operator panel
│   ├── src/shared/           # Types, reference formatting, WS hook
│   └── legacy/               # Original self-contained HTML pages (reference)
├── static/frontend/          # Built frontend bundle (served by the server)
├── bible_data/               # Bible SQLite databases
├── data/                     # KJV / LITV JSON text files
├── scripts/                  # Demo scripts
├── tests/                    # Pytest suite
├── requirements.txt          # Core dependencies
├── requirements-audio.txt    # Audio pipeline dependencies
└── start.sh                  # Start server + audio pipeline
```

## Testing

```bash
pytest tests/
```

## Development workflow (frontend)

```bash
# Terminal 1: backend
python3 core/biblelive_server.py

# Terminal 2: frontend dev server (proxies /ws to the backend, hot reload)
cd frontend && npm run dev
```

The dev server serves the pages with Vite's HMR; production pages are the
built bundle under `static/frontend/`, served by the backend at `/display`
and `/operator` (returns 503 with build instructions if not built).
