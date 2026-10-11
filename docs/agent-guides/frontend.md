# Frontend guide — React conventions, UI layout, and task mapping

Commands for verification live in `testing.md` (the canonical list);
architecture invariants (StrictMode, served routes, generated bundle)
live in `architecture.md` — read it when a task touches those. This
guide covers React conventions, the UI file layout, WebSocket hooks,
and the generated-bundle rules.

## Repo layout (frontend)

```
frontend/
  src/operator/    Operator panel (React)
  src/display/     Audience display page (React)
  src/shared/      Code shared by both pages (types, hooks, renderApp)
  src/components/  Shared UI components
  src/hooks/       Shared React hooks (useTheme)
  src/styles/      tokens.css + global.css (page CSS lives beside each App)
  scripts/screenshot.mjs  Visual regression snapshots
```

## Conventions

- TypeScript: ESM, React 18. Prettier: no semicolons, single quotes,
  width 100 (`frontend/.prettierrc.json`). ESLint runs with
  `--max-warnings 0`.
- Don't add dependencies without asking; the current set is minimal on
  purpose (React + Vite).
- **Generated bundle rules:** the prebuilt bundle `static/frontend/` is
  built by `npm run build` (into `../static/frontend`, invariant 6 in
  `architecture.md`) and served by the backend — don't hand-edit it, and
  don't rebuild it unless asked (rebuild diffs are big and generated;
  after `frontend/src` changes the UI only updates once rebuilt).

## Where to look (frontend tasks)

| Task | Files |
| --- | --- |
| Change operator UI | `frontend/src/operator/` (+ `operator.css`) |
| Change display UI / text fitting | `frontend/src/display/` (+ `display.css`, `fitText.ts`) |
| Change shared WS hook / message types | `frontend/src/shared/useWebSocket.ts`, `types.ts` (mirror of `backend/core/server/models.py`) |
| History / Show Again staging UI | `frontend/src/operator/components/HistoryPanel.tsx`, `frontend/src/operator/hooks/useOperatorState.ts` |
| Manual scripture search (Find Scripture) | `frontend/src/operator/components/ScriptureSearch.tsx` |
| Change WS message protocol or approval flow | **stop — ask the user first** (Behavior rules) |

## Related docs

- `testing.md` — canonical verification commands and caveats.
- `architecture.md` — StrictMode, backend-served routes, generated bundle.
