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

### No cap on utterance length: a stuck-open VAD feeds unbounded audio to Whisper
- **Where:** `backend/audio/pipeline.py` (`_vad_loop` utterance assembly;
  mirrored in `benchmarks/run_benchmark.py::run_single_recording`).
- **Problem:** An utterance closes only after >50 consecutive silent 30ms
  frames (~1.5s). If VAD never sees that much consecutive silence (steady
  noise floor, music, reverberation), `speech_frames` grows without bound and
  the whole accumulation goes to a single `transcribe_buffer` call.
- **Impact:** Unbounded memory growth in the audio buffer during a live
  service, one multi-minute STT call blocking the single transcription
  thread (all other utterances queue behind it), and Whisper returning only
  a fragment of a very long buffer — references in the dropped portion are
  silently lost.
- **Evidence:** Benchmark 2026-10-11 on a 50min live recording
  (`results/07-05-lt-norm-offline-cuda-small-gpu.json`): after loudnorm the
  VAD never closed, producing exactly 1 segment at t=3012.8s whose
  transcript covers only the start of the file.
- **Logged:** 2026-10-11

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

### Untracked DB snapshot files live in the repo working tree
- **Where:** `bible_data/biblelive.sqlite.bak-20261011-135608` and
  `bible_data/biblelive.sqlite.pre-repair-20261011-135608` (untracked).
- **Problem:** Two one-off SQLite snapshots (a backup and a pre-repair copy,
  from a 2026-10-11 DB repair) sit permanently in the working tree. They are
  not referenced by any code or config and will silently grow stale as the
  real `bible_data/biblelive.sqlite` is re-imported.
- **Impact:** Disk bloat in a directory operators may mistake for live data;
  a stale snapshot could be restored or shipped by mistake (e.g. a
  deployment copying the whole `bible_data/` directory), serving outdated
  verse text with no error.
- **Evidence:** `git status --short` shows `?? bible_data/` containing only
  these two files; no manifest, script, or code refers to them.
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
