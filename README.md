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
│   + speech-to-  │  reference     │                      │
│   text)         │                │                      │
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

- Python 3.13 (the version the Windows installer bundles and CI tests)
- Node.js 22.12 or later, what CI uses (vitest needs it; the production build
  alone also runs on Node 20). Required to
  build the frontend into `static/frontend/`; that directory is generated and
  not committed, so it must be built once after cloning.
- ffmpeg (required by the audio pipeline)
- A microphone (for live reference detection)

On Windows use `python` instead of `python3` in the commands below (and
`start.ps1` instead of `start.sh`).

## Installation

```bash
# Backend as an editable install (core server + parser + dev/test tools)
pip install -e ./backend -c backend/constraints.txt

# Optional: audio pipeline extras (faster-whisper, webrtcvad, numpy)
pip install -e "./backend[audio]" -c backend/constraints.txt

# Optional: GPU transcription (--device cuda): CUDA 12 cuBLAS + cuDNN 9 wheels
pip install -e "./backend[audio,cuda]" -c backend/constraints.txt

# Optional: Lithuanian GGUF speech models (--stt kmynas|canary|kmynas+canary)
pip install -e "./backend[audio,gguf]" -c backend/constraints.txt
```

`-c backend/constraints.txt` holds every package, direct or indirect, to the
exact version the test suite was last run against, so a new machine gets a
known-working set instead of whatever is newest that day. (`make install`,
`install-audio`, `install-cuda`, `install-dev`, CI, and `scripts/setup-linux.sh`
all use it.) The file's header explains how to move the pins forward.

On Windows, `webrtcvad-wheels` is installed automatically (no C++ build tools
needed). The audio extras also pin `setuptools<81`, because the VAD package
imports `pkg_resources` at runtime.

With the `cuda` extra on Windows, `audio/stt.py` finds the NVIDIA libraries by
itself. On Linux, point the loader at them before starting the pipeline:

```bash
export LD_LIBRARY_PATH="$(python3 -c 'import os, nvidia.cublas.lib, nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ":" + os.path.dirname(nvidia.cudnn.lib.__file__))')"
```

```bash
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

### Windows installer

`scripts\windows\build-installer.ps1` builds `dist\BibleLive-Setup-<version>.exe`:
a self-contained per-user install (Python, ffmpeg, the app and the Bible
databases) with a launcher that starts the server, the audio pipeline and the
projector display. During setup you pick the Lithuanian speech model —
**Kmynas on the processor** (no graphics card needed), **Kmynas + Canary on a
GPU** (most accurate), or Whisper only — and the installer downloads just those
models and tests them. The build ships the Lithuanian Bible
(`bible_data\bible_ltb.sqlite`) when it is there. Every service is logged to
`%APPDATA%\BibleLive\logs\sessions` (transcript + audio, see "Session log").
Details, requirements and silent-install switches: `scripts/windows/README.md`.

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
| `/display?mode=obs` | Stream lower third for OBS (see below)          |
| `/operator`        | Operator panel: approve/clear pending references, manual passage, history |
| `/docs`            | Interactive API docs (Swagger UI)                |

### Stream overlay for OBS

Add a **Browser** source in OBS with the URL
`http://127.0.0.1:8000/display?mode=obs`, width 1920 and height 1080 (the
stream's resolution). It shows the passage on screen as a lower third over the
camera, on a transparent page, and follows the projector: same passages, same
verse limit, cleared with it. Options in the URL:

| Option | Values | Default |
|---|---|---|
| `height` | band height, % of the frame (15-60) | `30` |
| `position` | `bottom`, `top` | `bottom` |
| `bg` | `band` (dark translucent band), `none` (text with a shadow only) | `band` |

The text shrinks to fit the band but never below what a stream viewer can read
(about 28 px at 1080p); a passage longer than that shows the verses that fit
and "+N more verses". Raise `height` to fit more.

### Run the audio pipeline only

```bash
PYTHONPATH="$PWD/backend" python3 -m audio.cli [options]

Options:
  --bible-db PATH         Bible SQLite database (default: bible_data/biblelive.sqlite)
  --verse-text-db PATH    Lithuanian Bible SQLite database; when a Lithuanian
                          speaker names only a chapter and then reads a verse,
                          that verse is reported (default:
                          bible_data/bible_ltb.sqlite, skipped if missing)
  --server URL            BibleLive server URL (default: http://localhost:8000)
  --server-token TOKEN    Operator token sent with references when the server
                          requires auth (default: BIBLELIVE_OPERATOR_TOKEN from
                          the environment or .env; prefer that over the flag)
  --model SIZE            Whisper model: tiny, base, small, medium, large-v2,
                          large-v3, large-v3-turbo, distil-large-v3 (default: small)
  --device DEV            Whisper device: cpu or cuda (default: cuda)
  --compute-type TYPE     Whisper compute type: float16, float32, int8,
                          int8_float16 (default: int8; use float16 on RTX 20xx+)
  --language LANG         Spoken language: en or lt (default: en)
  --vad-aggressiveness N  WebRTC VAD aggressiveness 0-3 (default: 2)
  --beam-size N           Whisper beam width; lower is faster (default: 5)
  --cpu-threads N         Speech model CPU threads, 0 = library default (default: 0)
  --stt ENGINE            Lithuanian speech model: whisper, kmynas, canary or
                          kmynas+canary (default: whisper); English always uses
                          Whisper. See "Lithuanian speech models" below
  --kmynas-model PATH     Kmynas .gguf file (for kmynas, kmynas+canary)
  --canary-model PATH     Canary .gguf file (for canary, kmynas+canary)
  --session-log-dir DIR   Write a live session log (transcript + references) here
  --session-audio on|off  With --session-log-dir, also record the service as WAV
  --end-of-speech-s S     Silence that ends an utterance (default: 1.5)
  --min-speech-s S        Shorter utterances are dropped (default: 0.3)
  --soft-utterance-s S    Past this, end the utterance at the next pause (default: 8)
  --max-utterance-s S     Hard utterance length cap (default: 12)
  --pre-roll-s S          Audio kept from before the first voiced frame (default: 0)
  --post-roll-s S         Silence kept after the last voiced frame (default: 0)
  --keep-pauses on|off    Keep in-utterance frames the VAD calls silent (default: off)
  --start-voiced-frames N Voiced 30 ms frames needed to start an utterance...
  --start-window-frames M ...within the last M frames (default: 1 of 1)
  --vad webrtc|silero     VAD engine (default: webrtc); Silero is the v6 model
                          bundled with faster-whisper
  --silero-threshold P    Silero probability that starts speech (default: 0.5)
  --silero-neg-threshold P
                          Speech lasts until the probability drops below this
                          (default: threshold - 0.15, as in faster-whisper)
  --partial-transcripts on|off
                          Post transcripts of the utterance still being spoken,
                          for follow-along, while the server has it armed (default: on)
  --partial-interval-s S  Utterance time between partial transcripts (default: 1.5)
  --partial-mode grow|chunk
                          grow: the whole utterance so far; chunk: only the new
                          audio, texts joined (default: chunk)
  --partial-min-s S       No partial transcript until the utterance is this long
                          (default: 1.5)
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

#### Lithuanian speech models (`--stt`)

For Lithuanian, two GGUF models run through
[transcribe.cpp](https://github.com/handy-computer/transcribe.cpp) (the `gguf`
extra) instead of Whisper; English still uses Whisper, loaded only once an
English utterance arrives:

| `--stt` | Hardware | References found (of 158) | Wrong, medium/high |
| --- | --- | --- | --- |
| `whisper` (turbo, default) | NVIDIA GPU | 140 | 10 |
| `kmynas` | any CPU (`--device cpu`) | 148 | 10 |
| `kmynas+canary` | a GPU (`--device cuda`) | 153 | 5 |

Measured on 13 recorded Lithuanian services (benchmark details in
`benchmarks/BASELINE_NOTES.md`). `kmynas` is a Lithuanian fine-tune of NVIDIA
Parakeet: on a Ryzen 7 5800X it transcribes an 8 s utterance in about 0.5 s
(6 threads) using ~1.1 GB of RAM, so an 8th-gen i5 / Zen 1 PC keeps up.
`kmynas+canary` runs both models on every utterance: a reference only one of
them finds still reaches the operator, but as low confidence, so auto-approve
never shows it. With `--device cuda` the bundled library picks the best GPU
through Vulkan (NVIDIA, AMD or Intel; ~3.2 GB of video memory for both models,
~0.2 s per 8 s utterance); set `TRANSCRIBE_LIBRARY` to use a CUDA build instead.
Model files (Q8_0): `Ezomas/kmynas-parakeet-lt-v4-gguf` and
`handy-computer/canary-1b-v2-gguf` on Hugging Face, both CC BY 4.0. Each model
has a small spelling map for its known Lithuanian mishearings
(`audio/spelling_maps.py`); the maps were fitted on the benchmark services, so
expect slightly lower numbers on new ones.

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

#### Bible version (translation) switching

Each translation is its own SQLite file in the same folder as
`BIBLELIVE_DB_PATH` (`bible_data/`), with a `translation` row in its
`bible_metadata` table (`kjv`, `ltb`, `web`, ...). The server offers every
`*.sqlite` file there that has that metadata and the `books`/`verses` tables;
backups, raw source dumps and unlabelled files are ignored. To add a translation,
convert it to this schema (see `backend/scripts/convert_sqlite_bibles.py` and
`DATABASE_SETUP.md`) and drop it in the folder.

The operator console switches translation from **Settings → Audio → Bible
version**: `GET`/`POST /api/bible-version` (operator-authenticated), body
`{"version": "ltb"}`. The change applies to all verse text from then on, is saved
in `BIBLELIVE_SETTINGS_PATH`, and is restored on the next start (a saved version
that is gone falls back to `BIBLELIVE_DB_PATH`). The choice is saved together
with the `BIBLELIVE_DB_PATH` it was made under; if you later change that path, the
saved choice is ignored (with a warning in the log) and the configured database
is used. Detections and the display carry
coordinates only, but translations differ in which verses exist (KJV Acts 8:37 is
absent from WEB) and how they number them, so a switch that changes the version
**clears the pending queue** rather than let a detection validated under one
translation show another's text. The passage already on screen is not
re-rendered. Every operator console is told over the WebSocket
(`bible_version_confirmed`, then `pending_cleared`/`pending_update`).

Switching the spoken language to a *different* language also switches to that language's default
translation (`BIBLELIVE_LANGUAGE_BIBLE_VERSIONS`, default English → `kjv`,
Lithuanian → `ltb`). A default that is not installed is skipped. The operator can
pick another translation afterwards; it stays until the language changes again.
Set `BIBLELIVE_AUTO_BIBLE_VERSION=false` to turn the automatic switch off.

#### Follow-along

While a passage is on the display, the speaker often reads on into the next
verses. The pipeline posts each transcript line to `/api/transcript`; the server
matches it against the verses right after the displayed passage, in the spoken
language's default translation (`BIBLELIVE_LANGUAGE_BIBLE_VERSIONS`): a verse
counts when enough of its distinctive words were heard, or, for the next verse,
when its first few words are heard in order while the verse before it is read. It offers
the operator a "Reading now: Luke 16:13-15 · Show" line. Nothing changes on the
display until the operator clicks Show, which is the same request as picking that
passage by hand. Set `BIBLELIVE_FOLLOW_ALONG=false` to turn the suggestions off, and
the automatic switch below with them (it reads off and cannot be turned on).
Operators can instead have the server show such a verse itself: the "Follow the
reading automatically" switch under Settings → Auto-approve (off by default, shared by
every console, saved with the auto-approve mode) does it for a verse that starts at
most 3 verses past the passage on screen; a bigger jump stays a suggestion.

A whole utterance can end several seconds after the verse began, so the pipeline
also transcribes the utterance still being spoken: every `--partial-interval-s`
seconds (1.5) it transcribes the audio since the last one and posts the text so far,
but only while the server's reply to `/api/transcript` says follow-along is armed (a
passage with a verse on screen that the speaker may still be reading). In two-model
mode only Kmynas transcribes these; Whisper (English, or `--stt whisper`) takes
none, since it was not measured and runs close to real time on a CPU. Partial
transcripts only feed these hints: they never reach reference detection, the
operator's approval or the display. On the 14 benchmark services, a hint now
arrives a median of about 3 s after the verse begins (before: 9.5 s), and about 9 in
10 while it is still being read (before: 42%); see `benchmarks/BASELINE_NOTES.md`.
Turn partial transcripts off with `--partial-transcripts off`.

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

#### Session log (live transcript)

`--session-log-dir DIR` writes `DIR/<start time>.jsonl` for every run: a header
(speech models, device, language), then one line per utterance — where it sits in
the service (`start_s` / `end_s`), each speech model's transcript and verdict, and
the references sent with their confidence — plus language switches, capture
restarts and references flushed at shutdown. `--session-audio on` also records the
whole service as `DIR/<start time>.wav` (16 kHz mono, ~115 MB per hour); the
utterance times are positions in that file, so a logged service can be annotated
and added to the benchmark corpus. The WAV stays playable if the PC loses power,
and a logging failure never stops detection. The Windows launcher keeps sessions
in `%APPDATA%\BibleLive\logs\sessions`.

## HTTP API

| Method | Endpoint            | Description                              |
|--------|---------------------|------------------------------------------|
| POST   | `/api/reference`    | Submit a parsed Bible reference (pending; must exist in the loaded Bible; token when auth is on) |
| POST   | `/api/approve`      | Approve a pending reference (by reference_id) |
| POST   | `/api/clear`        | Clear the display                        |
| POST   | `/api/clear-pending`| Clear the pending queue                  |
| GET    | `/api/books`        | List canonical books (for Find Scripture) |
| GET    | `/api/lookup`       | Look up verse text by coordinates        |
| GET    | `/api/language`     | Current spoken language (requires auth if enabled) |
| POST   | `/api/language`     | Switch spoken language (requires auth if enabled) |
| GET    | `/api/ui-language`  | Operator page language set by the launcher: `lt`, `en`, or `null` (no token needed, even with auth on) |
| GET    | `/api/state`        | Current server state snapshot (requires auth if enabled) |
| POST   | `/api/transcript`   | Audio pipeline transcript line, used only for follow-along suggestions; not stored; the reply's `follow_along_active` says whether to keep sending partial transcripts (token when auth is on) |
| POST   | `/api/pipeline/heartbeat` | Audio pipeline health report, sent every 5 s (token when auth is on) |
| GET    | `/api/pipeline/status` | Pipeline health for the operator console: `unknown`, `ok`, `degraded` (with problems), or `offline` after three missed reports (requires auth if enabled) |
| GET    | `/healthz`          | Liveness probe                           |
| GET    | `/readyz`           | Readiness probe (database + frontend built checks) |

## WebSocket protocol

- `ws://<host>/ws/display` — display clients receive `reference`, `clear`,
  `display_settings`, and `connection_status` events
- `ws://<host>/ws/operator` — operator clients receive `pending_update`,
  `approval_confirmed`, `clear_confirmed`, `pending_cleared`, `state_snapshot`,
  `auto_mode_confirmed`, `follow_auto_confirmed`, `display_settings_confirmed`,
  `bible_version_confirmed` (`{version, available}`), and `connection_status`
  events; they send these actions:
  - `{action: 'authenticate', token}` — first, when authentication is enabled
    (ignored otherwise)
  - `{action: 'approve', reference_id: 'uuid'}`
  - `{action: 'clear'}`
  - `{action: 'clear_pending'}`
  - `{action: 'set_verse_limit', verse_limit: 3 | 5 | 10 | 'all'}`
  - `{action: 'set_display_theme', theme: 'dark' | 'light'}`
  - `{action: 'set_auto_mode', auto_mode: 'off' | 'high' | 'medium_and_up' | 'all'}`
  - `{action: 'set_follow_auto', follow_auto: true | false}` — follow-along auto-show
    (see Follow-along); the confirmation goes to every operator console
  - `{action: 'display_reference', book, chapter, verse_start?, verse_end?}` —
    show a passage the operator picked by hand (coordinates only; the server
    re-resolves them). Also used for Show Again from display history.
- With authentication enabled, the operator WebSocket's first message must be
  `{action: 'authenticate', token}`; the server sends nothing before it and
  closes with code 1008 on a wrong or missing token (`?token=` in the URL still
  works but is deprecated, because URLs are logged)

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
invalid log a warning and fall back to that default (a damaged file is first
copied beside it as `server_settings.damaged-<timestamp>.json`); an invalid
`BIBLELIVE_AUTO_MODE` means `off`. The follow-along auto-show switch is saved in the
same file.

## Configuration (environment variables)

| Variable                  | Default                     | Description              |
|---------------------------|-----------------------------|--------------------------|
| `BIBLELIVE_DB_PATH`       | `bible_data/biblelive.sqlite` | Bible SQLite database  |
| `BIBLELIVE_HOST`          | `0.0.0.0`                   | Server bind host         |
| `BIBLELIVE_PORT`          | `8000`                      | Server port              |
| `BIBLELIVE_RELOAD`        | `true`                      | Uvicorn auto-reload (dev)|
| `BIBLELIVE_AUTO_MODE`     | `off`                       | Default auto-approve mode (no persisted setting) |
| `BIBLELIVE_SETTINGS_PATH` | `data/server_settings.json` | Persisted operator settings |
| `BIBLELIVE_AUTO_BIBLE_VERSION` | `true`                 | Switch Bible version with the spoken language |
| `BIBLELIVE_LANGUAGE_BIBLE_VERSIONS` | `{"en":"kjv","lt":"ltb"}` | Default translation per spoken language (JSON) |
| `BIBLELIVE_FOLLOW_ALONG`  | `true`                      | Suggest the next verses as the speaker reads on |
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
| `make lint` / `format` / `typecheck` | ruff check / ruff format / mypy core+audio+launcher+benchmarks |
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
│   │   ├── verse_parser.py       # Reference parsing (English)
│   │   ├── verse_parser_lt.py    # Lithuanian language data for the parser
│   │   ├── passage.py, books.py  # Manual passage validation + canon
│   │   ├── bible_lookup.py       # SQLite verse text lookup
│   │   └── import_bible.py       # Bible DB importer
│   ├── audio/                    # Audio pipeline (ffmpeg + VAD + speech-to-text)
│   │   ├── cli.py                # Entry point
│   │   ├── pipeline.py           # BibleLiveAudioProcessor
│   │   ├── capture.py, vad.py    # ffmpeg capture + WebRTC/Silero VAD
│   │   ├── stt.py                # faster-whisper transcription
│   │   ├── stt_gguf.py           # Lithuanian GGUF models (--stt kmynas / canary)
│   │   ├── spelling_maps.py      # Per-model Lithuanian spelling fixes
│   │   ├── ensemble.py           # Two-model agreement (--stt kmynas+canary)
│   │   ├── session_log.py        # Live session log (--session-log-dir)
│   │   ├── sender.py             # Reference delivery (dedup + retry/backoff)
│   │   ├── control.py            # Runtime language-control HTTP server
│   │   └── config.py, metrics.py
│   ├── tests/                    # Pytest suite
│   ├── scripts/                  # Database conversion scripts
│   └── pyproject.toml            # Installable package metadata
├── frontend/                     # Vite + React + TypeScript frontend
│   ├── src/display/              # Audience screen
│   ├── src/operator/             # Operator panel
│   ├── src/shared/               # Types, reference formatting, WS hook
│   └── display.html, operator.html  # Dev-mode Vite entry points
├── static/frontend/              # Built frontend bundle (generated, not committed)
├── bible_data/                   # Bible SQLite databases (generated; source JSON in data/)
├── data/                         # KJV / LITV JSON text files
├── docs/                         # Agent guides (docs/agent-guides/), TECH_DEBT.md, BACKLOG.md
├── pyproject.toml                # Root tooling config (ruff, pytest, mypy)
├── Makefile                      # Backend + frontend task runner
├── start.sh                      # Start server + audio pipeline (POSIX)
└── start.ps1                     # Same, for Windows PowerShell
```

This is a two-part monorepo:

| Directory   | Contents                                                        |
|-------------|-----------------------------------------------------------------|
| `backend/`  | Python server, audio pipeline, tests, scripts                    |
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
- `docs/macos-arm64.md` — Apple Silicon spike results and the remaining build plan
- `docs/TECH_DEBT.md` — known deferred issues
- `docs/BACKLOG.md` — planned features and checks, with rough size
- `docs/agent-guides/` — conventions for AI coding agents (entry point: `AGENTS.md`)
