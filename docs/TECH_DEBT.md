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

### Incorrect book-to-text mappings in the local SQLite database
- **Where:** `bible_data/biblelive.sqlite` — local database, not a code function.
- **Problem:** Verse rows are attributed to the wrong books. Book row "Jonah" holds
  879 verses (John's count) and "Jude" holds 618 (Judges'), while "John" holds only
  48. The database has 31,100 rows — about the full KJV total — so the text is present
  but mapped to shifted book IDs.
- **Impact:** The app can display the wrong scripture under a requested book name, and
  references whose rows landed elsewhere (for example John 3:16) resolve to no text at
  all. Display, operator previews, and history replay all read this database.
- **Evidence:** Reported by a read-only inspection during the Phase 3 review
  (John/Jonah, Jude/Judges). Confirmed on 2026-10-10 with per-book verse counts and
  `BibleLookup.get_verse_range("John", 3, 16, 16)` returning `[]` in this database,
  while the same lookup returns the KJV text in a freshly imported one.
- **Logged:** 2026-10-09

### Bible database path is resolved against the process working directory
- **Where:** `backend/core/server/app.py` (`create_bible_lookup`) — `config.DB_PATH` is
  the relative path "bible_data/biblelive.sqlite" and is passed through unchanged,
  although `config.resolve_db_path()` resolves it against the project root.
- **Problem:** A relative `DB_PATH` is resolved by SQLite against the current working
  directory, so the server finds the database only when started from the repo root. The
  settings file uses the project-root resolver, so the two paths behave differently.
- **Impact:** Starting uvicorn from any other directory (for example `backend/`) fails
  at startup with `FileNotFoundError: Bible database not found`, or loads a different
  database if one happens to exist at the same relative path.
- **Evidence:** Starting the server from `backend/` on 2026-10-10 failed with
  `FileNotFoundError: Bible database not found at bible_data\biblelive.sqlite`; the
  identical command from the repo root started normally.
- **Logged:** 2026-10-10

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

### Language control server port-conflict test fails on Windows
- **Where:** `backend/tests/test_control_server.py`
  (`test_control_port_conflict_raises_on_start`) against `backend/audio/control.py`
  (`_ControlHTTPServer`).
- **Problem:** The test expects a second bind to an already-bound port to raise
  `OSError`. `_ControlHTTPServer` inherits `allow_reuse_address = 1` from
  `http.server`, which on Windows sets `SO_REUSEADDR` and lets a second socket bind the
  same address and port.
- **Impact:** `make check` (pytest) reports one failure on Windows while the rest of the
  suite passes, so a real regression can hide behind a known-failing test.
- **Evidence:** Reproduced on 2026-10-10: `pytest backend/tests -q` →
  `1 failed, 289 passed` with `DID NOT RAISE OSError`, and
  `_ControlHTTPServer.allow_reuse_address` prints `1`.
- **Logged:** 2026-10-10