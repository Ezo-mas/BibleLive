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