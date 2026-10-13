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
