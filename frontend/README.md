# BibleLive Frontend

Modern frontend for the BibleLive display/operator system, built with
**Vite + React + TypeScript**. It is a multi-page app with two entries:

| Page      | URL (backend)   | Purpose                                      |
|-----------|-----------------|----------------------------------------------|
| Display   | `/display`      | Audience screen showing approved references   |
| Operator  | `/operator`     | Control panel: approve/clear pending refs     |

## Layout

```
frontend/
├── display.html          # Vite entry → display page
├── operator.html         # Vite entry → operator page
├── src/
│   ├── display/          # Audience screen (App, entry, styles)
│   ├── operator/         # Operator panel (App, entry, styles)
│   └── shared/           # types, reference formatting, useWebSocket hook
└── legacy/               # The original inline-CSS/JS pages (reference only)
```

## Build output

`npm run build` emits the production bundle to **`../static/frontend/`**,
which the FastAPI backend serves under the `/static` endpoint. The pages
themselves are served at `/display` and `/operator` by `core/server/`,
with asset URLs prefixed by `/static/frontend/` (see `base` in `vite.config.ts`).
`static/frontend` is generated output — do not edit it by hand.

## Commands

```bash
npm install       # install dependencies (first time only)
npm run dev       # dev server at http://localhost:5173 (/display, /operator)
npm run build     # production build → ../static/frontend
npm run typecheck # strict TypeScript check (build itself does not type-check)
```

## Development workflow

1. Start the backend (it must be running for WebSockets to work):

   ```bash
   ./start.sh            # or: make dev (from the repo root)
   ```

2. In another terminal, from `frontend/`:

   ```bash
   npm run dev
   ```

3. Open http://localhost:5173/display and http://localhost:5173/operator.
   The dev server proxies `/ws/*` to the backend on port 8000.

## Production

Run `npm run build`, then start the backend normally. The backend serves
the built pages at `/display` and `/operator`; assets are served from
`/static/frontend/...`. If the build output is missing, those routes
return a 503 with build instructions.

## WebSocket protocol (unchanged from the original pages)

- Display client connects to `/ws/display` and receives:
  `{"type": "reference", "data": {book, chapter, verse_start, verse_end, confidence, inferred, text}}`
  and `{"type": "clear"}`.
- Operator client connects to `/ws/operator?token=xxx` (token required if auth enabled),
  receives `state_snapshot` on connect, `pending_update`, `approval_confirmed`, `display_confirmed`,
  `clear_confirmed`, `pending_cleared`, and sends `{"action": "approve", "reference_id": "uuid"}`,
  `{"action": "clear"}`, `{"action": "clear_pending"}`, plus the display/settings actions
  (`set_verse_limit`, `set_display_theme`, `set_auto_mode`).

### Manual scripture selection

The operator can also put a passage of their own on the display. Both halves use
HTTP (authenticated with the operator token when auth is enabled):

- `GET /api/books` — the canonical 66-book list (`name`, `chapters`, `order`,
  plus each book's `abbrev` and parser-accepted name `variants`), so the console
  carries no canon of its own and the search box can match `ge`, `ps`, or
  `canticles` without shipping a name table.
- `GET /api/lookup?book=John&chapter=3&verse_start=16&verse_end=17` — the verses
  and display-ready text, or 404/422 with the reason. Read-only.

Staging the result in Up Next is client-side and changes nothing on screen. Show
sends `{"action": "display_reference", "book": ..., "chapter": ..., "verse_start": ...,
"verse_end": ...}`: coordinates only, no reference ID and no text, because the
passage was never queued — the server validates it against its own canon and
Bible database, displays it through the same path an approval uses, and answers
with `display_confirmed`. A manual passage never enters the pending queue and
never carries a confidence.

### Display history and Show Again

The sidebar lists the passages that reached the display, newest first — the
server's `reference_history`, of which a snapshot carries the newest 20 while
`history_count` reports the total. Each entry has a stable server-assigned `id`,
and selecting one stages it in Up Next exactly like a found passage (client-side
only; LIVE does not move). Show sends the same coordinate-only
`{"action": "display_reference", ...}` request described above, so a repeat is
validated and resolved by the server like any other display.

Showing a passage again is a new display, so the server records a new history
entry for it (one per display, bounded the same way). The console holds a staged
entry as a snapshot taken when it was clicked, so a history update or eviction
cannot swap in a different passage, and it keeps the staging across a dropped
socket.

Both clients reconnect automatically with linear backoff (3s × attempt,
up to 10 attempts).
