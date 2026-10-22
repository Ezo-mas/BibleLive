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
- **User-visible text goes through `t()`** (`useI18n()`, or a `Translator`
  passed into a non-React helper). Add the key to `shared/messages.en.ts` and
  its Lithuanian value to `shared/messages.lt.ts`; tsc fails on a missing or
  extra key. Plurals are objects selected by `count`. Bible text, book names,
  and reference strings are not translated.
- **Generated bundle rules:** the prebuilt bundle `static/frontend/` is
  built by `npm run build` (into `../static/frontend`, invariant 5 in
  `architecture.md`) and served by the backend — don't hand-edit it, and
  don't rebuild it unless asked (rebuild diffs are big and generated;
  after `frontend/src` changes the UI only updates once rebuilt).

## Where to look (frontend tasks)

| Task | Files |
| --- | --- |
| Change operator UI | `frontend/src/operator/` (+ `operator.css`) |
| Change display UI / text fitting | `frontend/src/display/` (+ `display.css`, `fitText.ts`) |
| Stream overlay for OBS (`/display?mode=obs`) | `frontend/src/display/StreamOverlay.tsx`, `streamOverlayOptions.ts` (URL options, fit-or-drop-verses), `streamOverlay.css`; picked in `display/main.tsx` |
| Change shared WS hook / message types | `frontend/src/shared/useWebSocket.ts`, `types.ts` (mirror of `backend/core/server/models.py`) |
| Right-hand column tabs (Pending / Plan / History) | `frontend/src/operator/components/SidePanel.tsx`, `PendingQueue.tsx`, `PlanPanel.tsx`, `HistoryPanel.tsx` |
| History (one row per passage) / Show Again staging / double-click show | `frontend/src/operator/components/HistoryPanel.tsx`, `passageKey.ts`, `hooks/useOperatorState.ts` |
| Plan (passages lined up in advance; browser-local, `localStorage`) | `frontend/src/operator/hooks/usePlan.ts`, `components/PlanPanel.tsx`; added from Find Scripture (Shift+Enter / Plan button; Enter shows directly, Ctrl+Enter stages) |
| Manual scripture search (Find Scripture, incl. verse-start preview) | `frontend/src/operator/components/ScriptureSearch.tsx`, `hooks/useScriptureSearch.ts` |
| Spoken-language switch (Settings → Audio, header chip) | `frontend/src/operator/languageApi.ts`, `hooks/useSpokenLanguage.ts`, `components/LanguageSettings.tsx`, `components/LanguageChip.tsx` — plain HTTP to `/api/language`, not the WebSocket |
| Bible-version switch (Settings → Audio) | `frontend/src/operator/bibleVersionApi.ts`, `hooks/useBibleVersion.ts`, `components/BibleVersionSettings.tsx` — HTTP to `/api/bible-version`; changes made elsewhere arrive as `bible_version_confirmed` on the operator socket |
| Book names from the loaded translation (Lithuanian) | display via `formatReference`/`bookDisplayName` in `frontend/src/shared/reference.ts` (uses the server's `book_label`); search via `localizeBooks`/`bookLabel` in `operator/scriptureQuery.ts` (accent-folded matching, canonical `name` for every request) and `hooks/useScriptureSearch.ts` |
| Follow-along suggestion line (Reading now · Show) | `frontend/src/operator/components/FollowAlongHint.tsx`, `hooks/useOperatorState.ts` (`follow_along` message; Show sends `display_reference`) |
| Follow-along auto-show switch (Settings → Auto-approve) | `frontend/src/operator/components/FollowAutoControls.tsx`, `SettingsDialog.tsx`, `hooks/useOperatorState.ts` (`set_follow_auto`, `follow_auto_confirmed`); the auto-followed tag is in `LivePanel.tsx` and `ReferenceCard.tsx` |
| Interface language (strings, Settings → Display → Interface language) | `frontend/src/shared/messages.en.ts`, `messages.lt.ts`, `i18n.ts`, `i18nContext.ts` (`useI18n`), `I18nProvider.tsx` (wired in `shared/renderApp.tsx`) |
| Idle screen picture (shown while nothing is on screen; Settings → Display → Idle screen) | display: `display/App.tsx` (`.display-idle` in `display.css`); the local `IdleImage` type and `readIdleImage` in `shared/idleScreen.ts` stand in for the pending `display_settings` field; console: `operator/idleScreenApi.ts`, `hooks/useIdleScreen.ts`, `components/IdleScreenSettings.tsx` (+ `.css`). `StreamOverlay` never shows it |
| Change WS message protocol or approval flow | **stop — ask the user first** (Behavior rules) |

## Related docs

- `testing.md` — canonical verification commands and caveats.
- `architecture.md` — StrictMode, backend-served routes, generated bundle.
