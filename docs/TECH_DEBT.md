# Tech Debt
Serious, confirmed, deferred issues only. See `AGENTS.md` for logging rules.

Entry format:

### Short title
- **Where:** `path` (`function`)
- **Problem:** What is wrong
- **Impact:** What breaks, and when
- **Evidence:** How it was confirmed
- **Logged:** YYYY-MM-DD

## Open

### The action row is back at the end of the list in the stacked layout
- **Where:** `frontend/src/operator/operator.css` (`.operator-pending`,
  `.operator-main-column`) with the media queries at 1024px and 1280px.
- **Problem:** The pending section is bounded — the queue scrolls inside it and
"Approve and Show" stays at the section's foot — only while the working column is
height-bounded, which it is when the utility column sits beside it. Below 1280px
the column stacks and grows with its content, so the section cannot be bounded
without bounding the stacked column too — a layout decision, not a bug fix — and
the action row returns to the end of the queued list.
- **Impact:** On a tablet or phone window (or a phone in landscape with the
on-screen keyboard up) during a busy service, the primary action needs a long
scroll before it can be clicked, and scripture search sits below the whole queue.
Desktop and laptop widths — the console's main use — are unaffected.
- **Evidence:** Measured 2026-10-10 against the running console with 21 queued
references, one line of verse text each: after bounding the section, 1440x900 put
the action row at y=783 in an 844px main area and 1366x600 at y=483 in 544px
(both visible without scrolling). In the stacked layout the same queue put it at
y=2896 in the 544px main area at 1280x600, y=3028 in 644px at 1024x700, and
y=3045 in 787px at 390x844. Nothing was clipped or overlapped — the row is simply
below the fold.
- **Logged:** 2026-10-10

### Language control server port-conflict detection is best-effort on Windows
- **Where:** `backend/audio/control.py`
  (`LanguageControlServer._port_has_live_listener`) against `backend/tests/test_control_server.py`
  (the port-conflict tests).
- **Problem:** `_ControlHTTPServer` inherits `allow_reuse_address = 1` from
  `http.server`, which on Windows sets `SO_REUSEADDR` and lets a second socket bind the
  same address and port, so a conflicting listener never makes `bind()` fail there.
  `start()` therefore probes for a live listener first, but the probe is best-effort:
  a failed connection does not prove the port is free (timeout, filtering, saturated
  backlog), wildcard bind hosts such as `0.0.0.0` cannot be reliably probed as written,
  and the probe-bind race remains.
- **Impact:** A conflicting listener is detected in the normal loopback case (covered by
  `test_control_port_conflict_raises_on_start` and
  `test_control_port_held_by_foreign_socket_raises_on_start`, which pass), but an exotic
  conflict that defeats both the probe and `bind()` would still start a second server
  with undefined traffic ownership.
- **Evidence:** Pre-fix reproduction on Windows 2026-10-11: a second `start()` logged
  `listening` on the occupied port and the test failed with `DID NOT RAISE OSError`;
  a raw `ThreadingHTTPServer` double-bind confirmed `SO_REUSEADDR` as the cause.
  Post-fix full suite: `337 passed`.
- **Logged:** 2026-10-10

### Stale DB snapshot files sit beside the live database
- **Where:** `bible_data/*.sqlite.bak-*` and `bible_data/*.sqlite.pre-repair-*`
  (one-off snapshots from a 2026-10-11 DB repair).
- **Problem:** The snapshots sit permanently next to the live database with
  nothing aging or removing them, so they grow silently stale as the real
  `bible_data/biblelive.sqlite` is re-imported. Since 2026-10-12 the snapshot
  suffixes are gitignored (`bible_data/*.sqlite.*` in `.gitignore`), so they no
  longer show as untracked and cannot be committed by accident — the remaining
  risk is local only.
- **Impact:** A stale snapshot could be restored or shipped by mistake (e.g. a
  deployment copying the whole `bible_data/` directory), serving outdated
  verse text with no error.
- **Evidence:** `.gitignore:33-35` (added in `e5e032b`, 2026-10-12, after this
  entry was logged); a fresh worktree's `git status` is clean of `bible_data/`;
  no manifest, script, or code refers to the snapshots. Entry revised
  2026-10-13 (logged 2026-10-11).
- **Logged:** 2026-10-11

### Lithuanian 1 and 2 Timothy are missed when spelled "Timotiej-"
- **Where:** `backend/core/verse_parser_lt.py` (`BOOK_STEMS`, `BOOKS` entries for
  `1 Timothy` / `2 Timothy`).
- **Problem:** The stem table and book variants use `timotej`. The spelling
  "Timotėjui" folds to that stem and parses, but the spelling "Timotiejui"
  (also "Timotiejaus", "Timotiejų") does not start with it, so the book name
  never matches. Titus ("Titui") and Philemon ("Filemonui") are unaffected.
- **Impact:** A transcript that spells the name "Timotiej-" silently loses
  every 1 or 2 Timothy citation: no reference reaches the operator panel.
  Present on `main` too. Which spelling Whisper emits for a given speaker is
  not established.
- **Evidence:** `ReferenceParser(language="lt").parse(...)` returns `[]` for
  "1 Timotiejui 3 16", "2 Timotiejaus 4 skyrius 2 eilutė" and
  "Timotiejui 3 skyrius 16 eilutė", but parses "1 Timotėjui 3 16" (1 Timothy
  3:16) and "2 Timotėjaus 4 skyrius 2 eilutė" (2 Timothy 4:2). Found by an
  independent review of PR #7, reproduced 2026-10-12.
- **Logged:** 2026-10-12

### Approving a reference can display with no verse text
- **Where:** `backend/core/server/state.py` (`AppState.approve_pending` via
  `_resolve_verses`).
- **Problem:** When the range resolves to no verses, `_resolve_verses` logs a
  warning and returns with empty verses, and `approve_pending` still sets the
  reference as `current_display` and pushes it to display sockets instead of
  refusing it. Left unchanged on purpose: refusing there would change the
  operator-approval flow.
- **Impact:** The projector shows a reference label with no text. Practically
  unreachable today: `validate_detection` at ingest guarantees the book and
  chapter (or first verse) exist in the loaded text, so a queued reference
  resolves unless the database changed or unloaded between queueing and
  approval. Low priority.
- **Evidence:** Code read 2026-10-13: `state.py:384-386` returns empty while
  `:314` and `:322` display unconditionally. Flagged by the follow-up review
  of the import/ingest work; logged, not fixed.
- **Logged:** 2026-10-13

### Capture-restart health reporting flaps, and a hung ffmpeg is never restarted
- **Where:** `backend/audio/pipeline.py` (`_restart_capture`,
  `_note_capture_healthy`, `health_report`).
- **Problem:** (a) `_capture_down` is cleared as soon as the relaunched ffmpeg
  process starts (`pipeline.py:397`), before it delivers a frame. If the device
  is still gone, ffmpeg exits at once and the flag flips again, so the operator
  status flaps between ok and degraded while the restart backoff itself works
  correctly. (b) The stall detector (`AUDIO_STALL_S`, `health_report`) only
  reports an ffmpeg that hangs without exiting; nothing restarts capture in
  that case — `_restart_capture` runs solely on `audio_capture.failed`.
- **Impact:** (a) Cosmetic status flapping during an outage. (b) A stuck but
  living ffmpeg ends detection with no recovery until the process is restarted;
  the operator sees "stalled" but the pipeline never heals itself.
- **Evidence:** Code read 2026-10-13: `:397` clears the flag at launch,
  `:329-330` gates restart on `failed`, `:271-278` reports silence only;
  backoff behavior is covered by `test_capture_recovery.py`. Flagged by the
  follow-up review; logged, not fixed.
- **Logged:** 2026-10-13

### The audio pipeline keeps the startup Bible and ignores a version switch
- **Where:** `backend/audio/pipeline.py` (the `BibleLookup` it builds at startup,
  used to validate parsed references before they are sent).
- **Problem:** The pipeline is a separate process with its own database
  connection, so the runtime Bible-version switch (`/api/bible-version`, the
  language default) changes the server's text but not what the pipeline checks
  detections against. The pipeline keeps validating against the translation it
  started with.
- **Impact:** With another translation active, references that exist only there
  (LTB has 1 Samuel 24:23 and Ecclesiastes 4:17; KJV lacks both) are dropped in
  the pipeline and never reach the operator; the reverse is caught later by the
  server's own check at ingest. It only affects verse-level edges, but it
  contradicts the feature's implication that everything follows the switch.
- **Evidence:** Code read 2026-10-14 (reviewer finding): the lookup is created
  once at pipeline startup and never reopened; the server's switch lives in
  `AppState.bible_lookup` and is not visible to it. Logged, not fixed.
- **Logged:** 2026-10-14

### A damaged settings file is overwritten on the next save, dropping every other setting (resolved)
- **Where:** `backend/core/server/settings_store.py` (`SettingsStore._read` and every
  `save_*` method, which all read, change one key and write the whole object back).
- **Problem:** `_read()` returns an empty dict for a file it cannot parse (bad JSON, a
  non-object, unreadable), and logs only a warning. The next `save_*` call starts from
  that empty dict and writes just its own key, so the damaged file is replaced by a
  valid one that has lost everything else it held: auto-approve mode, follow-along
  auto-show, the Bible translation, and the idle-screen record and fit.
- **Impact:** After a hand edit or a corrupted write, the first operator change
  (for example flipping Auto-approve) silently resets the other persisted settings to
  their defaults. The idle-screen picture then goes blank even though its file is
  still on disk, and a chosen translation reverts to the configured default. Atomic
  writes make a truncated file unlikely, so this needs a damaged file to start with.
- **Evidence:** Reproduced 2026-10-22: saved `auto_mode: "high"` and `follow_auto: true`,
  appended a stray `}` to the file, then saved `auto_mode: "all"`. The file read back as
  `{"auto_mode": "all"}`; `follow_auto` was gone. The warning "Ignoring settings file ...
  Extra data" was the only trace.
- **Logged:** 2026-10-22
- **Resolved:** 2026-10-24 — a damaged file (unreadable, not JSON, or not an object) is copied
  beside it as `<stem>.damaged-<YYYYMMDD-HHMMSS>.json` (local time; a counter suffix if the name
  is taken) on the first read, and identical damaged bytes are copied only once. The live file is
  not touched until the next save, which then writes only its new key. If the copy cannot be
  made, the save returns False, logs an ERROR and leaves the file as it is. Covered by
  `backend/tests/test_settings_store.py`.

### WebSocket tests with two sockets open can hang forever (resolved)
- **Where:** `backend/tests/conftest.py` (`client` fixture returns `TestClient(app)`
  without `with`); every test that holds a display and an operator socket at once
  (`test_follow_along.py`, `test_websocket_approval.py`, `test_manual_reference.py`,
  `test_display_safety.py`, `test_auto_mode.py`, `test_history_replay.py`,
  `test_authentication.py`, `test_api_validation.py`, `test_bible_version_switch.py`).
- **Problem:** Without the context manager, Starlette's TestClient starts a separate
  blocking portal (its own event loop on its own thread) for each `websocket_connect()`.
  A server broadcast then runs on one socket's loop and pushes into the other socket's
  anyio memory stream, which is not thread-safe. If the test thread is already waiting
  in `receive_text()` on that socket, the wake-up can be lost; `receive_text()` has no
  timeout, so the test waits forever.
- **Impact:** CI occasionally hangs instead of failing. The CI job has no
  `timeout-minutes`, so a hang holds the job up to GitHub's 6-hour limit. Not
  version-specific: it depends on timing, so any matrix leg can hit it.
- **Evidence:** 2026-10-20, run 39485316932 (PR feat/lan-access): Python 3.13 leg
  stuck in `TestHints::test_only_operators_get_the_hint_never_a_display` for 18+ min
  while 3.11/3.12 finished in ~2 min; the same test passed on 3.13 on 2026-10-18 with
  identical server and test code. Cause read from starlette 1.7.0
  `testclient.py` (`_portal_factory`, `WebSocketTestSession._run`); not reproduced
  locally. Likely fix: `with TestClient(app) as c: yield c` in the fixture (one shared
  loop; also runs app lifespan, so re-run the full suite), plus `timeout-minutes: 15`
  on the CI test job.
- **Logged:** 2026-10-20
- **Resolved:** 2026-10-21 — reproduced locally (a display + operator socket pair hung within
  34–1176 theme round-trips); the `client` fixture now enters `with TestClient(app)` so the
  test shares one event loop (20,000 round-trips clean), the shared session app gets a no-op
  lifespan so per-test shutdown no longer closes its database, and every CI job has
  `timeout-minutes`.

