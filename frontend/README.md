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
  receives `state_snapshot` on connect, `pending_update`, `approval_confirmed`, `clear_confirmed`,
  `pending_cleared`, and sends `{"action": "approve", "reference_id": "uuid"}`,
  `{"action": "clear"}`, `{"action": "clear_pending"}`.

Both clients reconnect automatically with linear backoff (3s × attempt,
up to 10 attempts).
