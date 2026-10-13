# Architecture guide — read before touching load-bearing code

Read this guide before changing anything under `backend/core/server/`,
`backend/audio/` (esp. `vad.py`, `capture.py`), frontend entry points
(`main.tsx`, `shared/renderApp.tsx`), anything SQLite- or port-related,
or the WebSocket protocol — and **when AGENTS.md sends you here, read it
*before* making the change, not as a post-hoc check.**

Full background: `README.md`. This guide lists the invariants that are
easy to break by accident.

## Architecture invariants (don't break these)

1. **The server is `core.server.app`, not `core.biblelive_server`** — the
   latter (and `audio.biblelive_audio_pipeline`) are compat shims kept for
   old imports. Edit `core/server/`, not the shims.
2. **Port 8000 is the default everywhere** — `core/config.py`, the audio
   sender/CLI, the Vite dev proxy, and docs all assume it (`start.sh`
   allows a `BIBLELIVE_PORT` override, but 8000 stays the default). Don't
   introduce a config knob that defaults elsewhere. Frontend WS URLs use
   `window.location.host`, so they follow whatever port serves the page.
3. **Operator approval flow**: audio pipeline POSTs references to
   `/api/reference` (token-protected when auth is on; the server rejects any
   book/chapter/verse not in its canon and Bible database, and discards
   submitted `text` — `_resolve_verses` only ever shows database text, since
   auto-approve can display a queued reference unseen); the server queues
   them and pushes to the operator over
   WebSocket; `_send_display_reference` (the only path that pushes a
   reference to display sockets) is called from exactly two
   operator-authorized places: `AppState.approve_pending()`, and
   `AppState.display_manual_passage()` — the `display_reference` action,
   which shows a passage the operator picked by hand. The display still
   never sees an unapproved detection, and a manual passage never enters
   the pending queue or carries a confidence (it is `source="manual"`, so
   nothing can auto-approve it). The supported operator actions are
   `authenticate` (the token, as the socket's first message; it carries no
   operation and is ignored once the socket is let in), `approve`, `clear`, `clear_pending`, `set_verse_limit`,
   `set_display_theme`, `set_auto_mode`, `display_reference` — there is no
   `reject` action; discarding a wrong detection is done implicitly by
   approving the next one or via `clear_pending`.
   Covered by `backend/tests/test_websocket_approval.py` and
   `backend/tests/test_manual_reference.py`.
   Both doors resolve a passage the same way: `display_reference` carries
   coordinates only (no ID, no confidence, no text) and the server
   re-resolves them through `core/passage.py`, so a client cannot inject
   verse text or pass a manual selection off as a detection.
   **Show Again** (Phase 4: showing a passage from display history) is
   that same action, not a third door: the console stages a history entry
   locally and sends its coordinates, so the server re-validates and
   re-resolves them. Consequences that are deliberate, not accidents: a
   replayed passage is recorded as a new `reference_history` entry
   (`source="manual"`, no confidence, new ID) — one entry per display,
   exactly like approving the same detection twice, so history grows
   linearly and stays bounded by `MAX_HISTORY_REFERENCES`; and an entry
   that has aged out of the server's 20-entry snapshot window can still
   be shown again, because the canon and the Bible database — not the
   history list — are the authority.
4. **VAD frame discipline**: frames must be exactly the size the active
   VAD requires at 16 kHz — WebRTC VAD (current): 10/20/30 ms. The
   load-bearing part is that no audio is lost: `AudioCapture.get_audio_frame()`
   must return exactly that many bytes and must not drop carry-over bytes
   between calls (it keeps a `_frame_buffer`). Swapping the VAD (e.g. to
   one with a different chunk size) is allowed, but is a detection-accuracy
   change: benchmark it (see `testing.md`).
5. **No StrictMode in the frontend entry points** — it double-mounts in
   dev and would open two WebSockets. The shared `shared/renderApp.tsx`
   helper documents this; keep both `main.tsx` files using it.
6. **Frontend is served by the backend**: pages at `/display` and
   `/operator`, built assets under `/static/frontend/` (Vite `base` in
   `frontend/vite.config.ts`, which also proxies `/ws` to the backend in
   dev). Dev uses the same paths via a Vite middleware — don't
   "simplify" to `/operator.html` links.
7. **SQLite DB** at `bible_data/biblelive.sqlite` is opened with
   `check_same_thread=False` (cross-thread reads in FastAPI workers).
   Treat it as read-only: don't add writes on the request path.
   Import procedures live in `../../DATABASE_SETUP.md`.

## Known pitfalls / dead ends

- `core/verse_parser.py` and `core/verse_parser_lt.py` both exist: the
  `_lt` one is Lithuanian-language parsing. Don't merge them blindly.
- `bible_lookup.py` has no abbreviation-mapping dict — lookups go through
  the SQLite `books` table.
- Tests marked `audio` need the optional extras (faster-whisper,
  webrtcvad); they skip gracefully without them.
