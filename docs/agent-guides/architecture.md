# Architecture guide — read before touching load-bearing code

Read this guide before changing anything under `backend/core/server/`,
`backend/audio/` (esp. `vad.py`, `capture.py`), frontend entry points
(`main.tsx`, `shared/renderApp.tsx`), anything SQLite- or port-related,
or the WebSocket protocol — and **when AGENTS.md sends you here, read it
*before* making the change, not as a post-hoc check.**

Full background: `README.md`. This guide lists the invariants that are
easy to break by accident.

## Architecture invariants (don't break these)

1. **The web server's port is 8000 by default everywhere** — `core/config.py`,
   the audio sender/CLI, the Vite dev proxy, and docs all assume it
   (`start.sh` allows a `BIBLELIVE_PORT` override, but 8000 stays the
   default). Don't introduce a server port setting that defaults elsewhere.
   The pipeline's language-control server (`--control-port`, default 8001)
   is a separate service. Frontend WS URLs use `window.location.host`, so
   they follow whatever port serves the page.
2. **Operator approval flow**: audio pipeline POSTs references to
   `/api/reference` (token-protected when auth is on; the server rejects any
   book/chapter/verse not in its canon and Bible database, and discards
   submitted `text` — `_resolve_verses` only ever shows database text, since
   auto-approve can display a queued reference unseen); the server queues
   them and pushes to the operator over
   WebSocket; `_send_display_reference` (the only path that pushes a
   reference to display sockets) is called from exactly two
   operator-authorized places: `AppState.approve_pending()`, and
   `AppState.display_manual_passage()` — the `display_reference` action,
   which shows a passage the operator picked by hand, and a third caller:
   follow-along auto-show (`offer_follow_along`), which shows the next verses
   by itself only while the operator's `follow_auto` switch is on (see
   `follow_along` below). The display still never sees an unapproved
   detection, and a manual passage never enters the pending queue or carries
   a confidence (it is `source="manual"`, or `source="follow"` for an
   auto-show, so nothing can auto-approve it). The supported operator actions are
   `authenticate` (the token, as the socket's first message; it carries no
   operation and is ignored once the socket is let in), `approve`, `clear`, `clear_pending`, `set_verse_limit`,
   `set_display_theme`, `set_auto_mode`, `set_follow_auto`, `display_reference` — there is no
   `reject` action; discarding a wrong detection is done implicitly by
   approving the next one or via `clear_pending`.
   `bible_version_confirmed` (server → operators only) announces a
   translation change; it never reaches display sockets, and
   `follow_along` (server → operators only, never display sockets) is a
   suggestion built from `/api/transcript`: coordinates plus the
   `display_id` it follows on from, no text. That endpoint's HTTP reply
   carries `follow_along_active`, which tells the audio pipeline to keep
   sending partial transcripts. Its Show button sends the
   ordinary `display_reference` action, so it is not a third door.
   With the operator's `follow_auto` switch on (`set_follow_auto`: server-held,
   persisted with the auto-approve mode, off by default, confirmed to every
   console as `follow_auto_confirmed`; refused, and reported off, while
   `BIBLELIVE_FOLLOW_ALONG` is false), a hint that starts at most
   `FOLLOW_AUTO_MAX_JUMP` (3) verses past the passage on screen is not sent as
   a hint: the server shows it through `display_manual_passage` with
   `source="follow"`, re-resolved from coordinates, and operators get its
   `display_confirmed`. It is taken only if it still follows on from the
   passage on screen, checked after the last await and before the call, and
   never while the display is cleared. Any other hint is sent as above. It is
   the operator's setting, like auto-approve, not a third door to
   `_send_display_reference`. The server cannot tell a partial transcript from
   a final one, so the auto-show can act on an utterance still being spoken.
   `AppState.bible_lookup` is the one live lookup — read it through
   `app_state`, never a reference captured at startup, or a switch will not
   reach you.
   Covered by `backend/tests/test_websocket_approval.py`,
   `backend/tests/test_manual_reference.py` and (follow-along)
   `backend/tests/test_follow_along.py` (including the auto-show class) and
   `backend/tests/test_follow_auto.py` (the switch).
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
3. **VAD frame discipline**: frames must be exactly the size the active
   VAD requires at 16 kHz — WebRTC VAD (the default): 10/20/30 ms. The
   optional Silero VAD (`--vad silero`) takes the same 30 ms frames and
   re-chunks them into its 512-sample windows, carrying leftover samples
   over, so the capture frame size does not change. The
   load-bearing part is that no audio is lost: `AudioCapture.get_audio_frame()`
   must return exactly that many bytes and must not drop carry-over bytes
   between calls (it keeps a `_frame_buffer`). Swapping the VAD (e.g. to
   one with a different chunk size) is allowed, but is a detection-accuracy
   change: benchmark it (see `testing.md`).
4. **No StrictMode in the frontend entry points** — it double-mounts in
   dev and would open two WebSockets. The shared `shared/renderApp.tsx`
   helper documents this; keep both `main.tsx` files using it.
5. **Frontend is served by the backend**: pages at `/display` and
   `/operator`, built assets under `/static/frontend/` (Vite `base` in
   `frontend/vite.config.ts`, which also proxies `/ws` to the backend in
   dev). Dev uses the same paths via a Vite middleware — don't
   "simplify" to `/operator.html` links.
6. **SQLite DB** at `bible_data/biblelive.sqlite` is opened with
   `check_same_thread=False` (cross-thread reads in FastAPI workers).
   Treat it as read-only: don't add writes on the request path.
   Import procedures live in `../../DATABASE_SETUP.md`.

## Known pitfalls / dead ends

- `core/verse_parser.py` and `core/verse_parser_lt.py` both exist: the
  `_lt` one is Lithuanian-language parsing. Don't merge them blindly.
- `bible_lookup.py` has no abbreviation-mapping dict — lookups go through
  the SQLite `books` table.
- Tests marked `audio` need the optional extras (faster-whisper,
  webrtcvad, installed by `make install-dev`); without them they fail with
  import errors rather than skip.
