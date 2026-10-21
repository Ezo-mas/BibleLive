---
paths:
  - "backend/core/server/**"
  - "backend/audio/**"
  - "frontend/src/*/main.tsx"
  - "frontend/src/shared/renderApp.tsx"
---

# Load-bearing code

Read `docs/agent-guides/architecture.md` before changing this code, not
after. Its invariants and known pitfalls are easy to break by accident:
server port 8000 by default, display only through `approve_pending` /
`display_manual_passage` (no third door), exact VAD frame sizes with no
dropped carry-over, no StrictMode (both entry points go through
`shared/renderApp.tsx`), SQLite read-only on the request path.

Ask before changing the WebSocket protocol (`core/server/models.py`,
`frontend/src/shared/types.ts`) or the operator-approval flow.
