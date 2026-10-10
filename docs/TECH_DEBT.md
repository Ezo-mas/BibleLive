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

### The action row scrolls out of reach on a short window with a busy queue
- **Where:** `frontend/src/operator/operator.css` (`.operator-pending`,
  `.operator-actions`) with `.operator-main-column` and `.operator-main`.
- **Problem:** The pending section grows with its contents (`flex: 1 0 auto`) and the
  action row is its last row, so a long queue pushes "Approve and Show", "Clear
  Display", and "Clear Queue" to the bottom of the scrolling column; below 1280px the
  main area scrolls instead, so the column is never height-bounded and the buttons stay
  at the end of the list. Bounding the section (`min-height: 0`, letting the queue
  scroll internally) would pin the row, but below 1280px that means giving the stacked
  column a bounded height as well — a layout decision, not a bug fix.
- **Impact:** On a short window (a 600px-tall laptop, or a phone in landscape with the
  on-screen keyboard up) during a busy service, the primary action needs a long scroll
  before it can be clicked, which is the opposite of the layout's own stated intent that
  it stay reachable without scrolling.
- **Evidence:** Measured 2026-10-10 against the running console: at 1280x600 with 21
  queued references the action row's top edge sat at y=2666 inside a 517px-tall main
  area; at 390x844 it sat at y=2706 inside 673px. Nothing was clipped or overlapped —
  the row is simply far below the fold.
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