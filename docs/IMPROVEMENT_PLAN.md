# BibleLive engineering blueprint

Architecture assessment and implementation plan, written 2026-11-11 against
`main` at `3d261fb`. Line numbers refer to that commit. It is written for the
implementation lead: every milestone names the files, the change, the tests
and the conditions for calling it done.

Rules for agents stay in `AGENTS.md` and `docs/agent-guides/`; defects in
`TECH_DEBT.md`; unbuilt features in `BACKLOG.md`. This file is the plan and
its settled decisions.

## 1. How to use this document

Work the milestones in order. Each one lists what it depends on, and
section 7 says which can run side by side. The standing decisions in
section 5 apply to every milestone and should not be reopened during
implementation. Section 6 records the owner's decisions; anything in the
ask-first areas that it does not cover still needs asking.

**Basis of the assessment.** Read in full: `core/server/app.py`, `state.py`,
`connections.py`, `core/config.py`, `audio/pipeline.py`, `audio/sender.py`,
the capture read path, the launcher's run loop, `StreamingParser`,
`useWebSocket.ts`, the display page and the operator message handler, plus
every guide, `TECH_DEBT.md`, `BACKLOG.md`, the benchmark notes and the macOS
plan. Other modules were read in outline.

No tests or benchmarks were run for this document. Findings marked *read*
come from the code and have not been reproduced. Start each of those with a
failing test.

## 2. Current state

BibleLive is three processes with narrow connections between them. The
launcher starts the other two. The audio pipeline listens and posts what it
detects. The server holds the state, decides what reaches the projector, and
serves both pages.

| Audio pipeline (`audio/`) | Server (`core/server/`) | Clients and launcher |
| --- | --- | --- |
| Capture: ffmpeg to 30 ms frames with carry-over | Ingest: validates against canon and Bible database, discards client text | Operator console (React): approves, searches, stages, changes settings |
| VAD thread: WebRTC VAD, segmenter, 8 s soft and 12 s hard cut | `AppState`: pending queue, on-screen passage, history, settings, follow-along | Display page (React): presentation only, reconnects forever |
| Transcription thread: Kmynas, Canary or Whisper; two-model agreement | Display door: `_send_display_reference`, called from two places only | OBS overlay: `/display?mode=obs` |
| Parser: per-language `StreamingParser` with database validator | Storage: Bible SQLite read-only; settings in an atomic JSON file | Launcher: Tk window or CLI; starts server then pipeline, opens the projector window |
| Senders: references, transcripts, heartbeat; bounded and retried | WebSocket `/ws/operator`, `/ws/display`; pages on port 8000 | Windows installer and portable build; macOS in spike phase |

Figures at the time of writing:

- 1,576 backend tests collected; about 440 frontend test cases in 39 files.
- Kmynas + Canary finds 167 of 171 gold references on 14 services, with 1
  medium or high false positive (`benchmarks/BASELINE_NOTES.md`).
- In the first live service, 308 of 598 utterances hit the 12 s cut.

### What is good and must be kept

- **The process split.** A stuck speech model cannot block the display, and
  the server never touches the microphone. Keep three processes.
- **One door to the projector.** Verse text always comes from the server's
  database, the client sends coordinates only, and
  `test_websocket_approval.py`, `test_manual_reference.py`,
  `test_follow_along.py` and `test_display_safety.py` hold it.
- **Measured detection work.** The benchmark shares its code with the live
  path (`audio/detection.py`, `segmenter.py`, `partials.py`), a parity test
  keeps them equal, cached replays take seconds, and rejected ideas are
  written down.
- **Session logs and replay.** A real service can be replayed through the
  current parser.
- **A real end-to-end test.** `test_live_path.py` runs ffmpeg, VAD, parser,
  HTTP, uvicorn and real WebSockets with only the speech model faked.
- **Pipeline defensive design.** Bounded queues, capture restart with
  backoff, stale references dropped instead of shown late, silent-microphone
  detection, health issues with stable codes.
- **Settings storage.** Atomic writes and a copy of a damaged file before it
  is replaced.
- **Small dependency set, pinned.** `backend/constraints.txt`, one Python
  version, strict pytest warnings and a 60 s per-test limit.
- **The agent documentation.** `AGENTS.md`, the four guides,
  `TECH_DEBT.md` and `BACKLOG.md` are accurate and stay the place where
  rules live.

### Where it is weak

Three groups. Survival: several single failures end a service or leave it
degraded until someone restarts the program. Structure: four files carry most
of the behaviour and the wire protocol is typed on one side only. Evidence:
detection numbers are fitted to the recordings they are measured on, English
is unmeasured, and nothing records which build ran a service or what was
shown.

## 3. Findings

IDs are used throughout the milestones. Severity reflects the effect on a
live service.

### Reliability

| ID | Finding | Evidence | Severity | Fixed in |
| --- | --- | --- | --- | --- |
| R1 | The launcher ends the whole service when either child process exits. Nothing restarts the server or the pipeline. | `launcher/process.py:364-375`: the monitor loop returns on the first exit and `run()` then kills both trees | High | M2 |
| R2 | Server state lives in memory only. A restart blanks the projector and loses the queue and history. Verse limit and theme return to 3 and dark because they are not in the settings file. | `state.py:90-93`; `settings_store.py` saves auto mode, follow-auto, Bible version and idle screen only | High | M2 |
| R3 | A hung ffmpeg is never restarted. `get_audio_frame` waits forever, so the VAD thread also stops applying language switches. | `capture.py:302-310`, `pipeline.py:517-523`; `TECH_DEBT.md` | High | M2 |
| R4 | Capture status flaps between ok and degraded during an outage. | `pipeline.py:617` clears `_capture_down` when ffmpeg is relaunched, before a frame arrives; `TECH_DEBT.md` | Low | M2 |
| R5 | A worker thread ends on an unguarded exception. The heartbeat reports `thread_stopped` but detection stays off. Unguarded: `_select_parser`, `merge_agreement`, the VAD loop body. *Read.* | `pipeline.py:508-556`, `695-724`, `968-992` | Medium | M2 |
| R6 | The pipeline validates detections against the Bible it opened at startup and ignores a translation switch. | `pipeline.py:763`; `TECH_DEBT.md` | Medium | M2 |
| R7 | Approving a reference that resolves to no verses shows a label with no text. | `state.py:626-634`; `TECH_DEBT.md` | Low | M2 |
| R8 | One stalled display socket holds every broadcast, and the operator's confirmation behind it, for up to 5 s. The single-socket send used on connect has no timeout. *Read.* | `connections.py:66-72`, `87-92`; `state.py:634` then `653` | Medium | M3 |
| R9 | The operator console stops reconnecting after 10 attempts, about 165 s in total, and then needs a click. | `useWebSocket.ts:46`, `107-111` | Low | M2 |
| R10 | A page cannot tell that its connection died silently. | No liveness message in the protocol; clients act only on close events | Low | M3, conditional |
| R11 | Valid JSON that is not an object closes the operator socket with an "unexpected error" log. | `app.py:552`: `message.get` on a list raises outside the JSON handler | Low | M1 |

### Structure

| ID | Finding | Evidence | Fixed in |
| --- | --- | --- | --- |
| S1 | Server-to-client messages are dict literals. Sixteen message types are built by hand with no model, `types.ts` mirrors them by hand, and no test compares the two sides. | `grep '"type": "'` in `core/server/`; `models.py` has models for inbound actions only | M1 |
| S2 | `app.py` defines every route inside one 800-line factory. `AppState` holds six concerns: display and queue, settings, idle picture, Bible version, pipeline health, follow-along. | `app.py` 1,228 lines; `state.py` 858 lines | M3 |
| S3 | The "no third door" rule is held by behaviour tests and convention. Nothing fails if a new caller of `_send_display_reference` appears. | `state.py:179-192` | M1 |
| S4 | Message ordering depends on an unwritten rule: change state and start the send with no `await` in between. Two comments mention it. | `state.py:505-507`, `786-787` | M1, M3 |
| S5 | `BibleLiveAudioProcessor` owns lifecycle, health, the VAD loop, capture recovery, partials, parsers and detection. Tests need a factory and a drift test to build one. | `pipeline.py` 1,037 lines; `tests/processor_factory.py` | M4 |
| S6 | Lithuanian rules sit inside the generic `StreamingParser`, and `_parse_tail` is about 220 lines. | `verse_parser.py:1149-1168`, `1196-1215`, `670-888` | M4 |
| S7 | `useOperatorState` is one hook with more than 20 state variables, confirmed values held in refs, and the message handler as a closure. | `useOperatorState.ts` 1,065 lines | M6 |
| S8 | Blocking work runs on the event loop. `available_bible_versions()` opens every `*.sqlite` file in `bible_data/` on each call, and a translation switch opens and counts a database inline. | `bible_versions.py:54-73`; `state.py:453-489` | M3 |
| S9 | The launcher's projector, process-tree and monitor code is Windows-only. | `launcher/projector.py`, `process.py:222-238`; `docs/macos-arm64.md` | M7 |

### Evidence and process

| ID | Finding | Evidence | Fixed in |
| --- | --- | --- | --- |
| E1 | Detection rules were fitted and measured on the same 14 services. There is no held-out set. | `BASELINE_NOTES.md`, "Re-check on new recordings" | M5 |
| E2 | There is no English gold corpus. English also gets no partial transcripts. | `testing.md`; `BACKLOG.md` | M5 |
| E3 | CI runs on Linux only. The product ships on Windows, and the launcher window tests never run in CI. | `.github/workflows/ci.yml`; `testing.md` caveats | M7 |
| E4 | No release tags and no version stamp. A session log does not say which build produced it. | `git tag` is empty; `pipeline.py:310-320` header fields | M2 |
| E5 | The server keeps no record of what was shown and when. Operator approvals, which are verified labels, are lost. | No writer in `core/server/` | M2 |
| E6 | "Timotiej-" spellings of 1 and 2 Timothy are missed. | `TECH_DEBT.md` | M4 |
| E7 | mypy runs with `disallow_untyped_defs = false` everywhere. | root `pyproject.toml` | M7 |

## 4. Target architecture

The shape stays: the same three processes with clearer seams inside each
one, a protocol both sides are checked against, and a service that recovers
from one failure without a person.

**Launcher**

- Supervises both children after a successful start. A child that exits is
  restarted with backoff, and the window says so.
- Platform code sits behind one small interface (projector window, process
  tree, monitors, firewall, settings directory) with a Windows implementation
  that behaves exactly as today and a macOS one added by the macOS plan.

**Server**

- `create_app` only wires things together. Routes live in one module per
  area under `core/server/routes/`. The WebSocket endpoint and the operator
  action handlers have their own module.
- `AppState` remains the object tests and routes hold, but delegates to four
  collaborators: the display session (on-screen passage, queue, history, and
  the door), pipeline health, the idle screen, and the Bible version holder.
- Every message on the wire has a pydantic model and a checked-in example.
  The frontend types stay hand-written and are tested against the same
  examples.
- Each socket has its own outbound queue and writer task, so a slow client
  delays only itself.
- A small runtime-state file lets a restarted server put back what was on
  screen. A service log records what was shown.
- Nothing slow runs on the event loop.

**Audio pipeline**

- `BibleLiveAudioProcessor` keeps threads, queues and lifecycle. Capture
  recovery, health reporting and detection move into three classes that can
  be built and tested alone.
- The detection class is the one the benchmark uses too, so "live equals
  benchmark" becomes a fact of construction and the parity test shrinks.
- Capture heals itself from a hung ffmpeg, and a bad utterance cannot end a
  thread.
- The pipeline learns the active translation from the server and reopens its
  validator.

**Parser**

- Two parsers remain: English and Lithuanian are not merged.
  Language-specific stream rules move out of `StreamingParser` into a profile
  object each language supplies.
- Every structural change replays identically on the cached corpus and on
  recorded sessions.

**Operator console**

- Console state is a pure reducer over server messages and operator intents.
  The hook around it holds the socket and the side effects (notices, flashes,
  local storage).
- The existing App-level tests stay as they are and are the regression net.

**Evidence**

- A held-out set of services that no rule is ever tuned on, reported next to
  the fitted corpus.
- An English gold set, even a small one.
- Every session log, service log and health reply carries the build version.

## 5. Standing decisions

Settled for the whole plan.

| ID | Decision | Why it is fixed now |
| --- | --- | --- |
| D1 | Three processes. The pipeline talks to the server over HTTP only. The server reaches the pipeline only through the loopback control API. Where the pipeline needs server state, it arrives in the reply to a request the pipeline already makes (the pattern `follow_along_active` set). | Keeps the server free of audio dependencies and avoids a second channel. |
| D2 | `_send_display_reference` keeps exactly two callers: `approve_pending` and `display_manual_passage`. Restoring state at startup sets `current_display` before any socket exists and is not a caller. All verse text is re-resolved from coordinates. | Non-negotiable 2. M1 adds a test that enforces it structurally. |
| D3 | The wire format changes only by adding optional fields or new message types, each agreed with the owner. Refactors leave every existing message byte-identical. Every message type has a model and a golden example. | Old console tabs and the pipeline must keep working across an upgrade. |
| D4 | Bible SQLite files are read-only. All mutable state goes to JSON files beside the settings file, through the one atomic writer in `settings_store.py`. A write is never awaited on the path that puts a passage on screen. | Non-negotiable 6, and a disk stall must not delay the projector. |
| D5 | A detection change is benchmarked before and after on both tiers with the `detection-benchmark` skill. A refactor of detection code must replay with zero differences: same TP, FN and FP by confidence, an empty per-reference diff, and an empty `session-replay` diff on recorded services. | Separates "moved code" from "changed behaviour" beyond doubt. |
| D6 | No new runtime or development dependency. Nothing in this plan needs one. If a builder thinks one is needed, stop and ask. | Project rule; the set is small on purpose. |
| D7 | Pipeline threading keeps the one-writer-per-field rule. A new class states which thread owns it in its docstring. | The current code is correct because of this rule, not because of locks. |
| D8 | A new failure the operator should see becomes a health issue with a stable `code`, `params` and English `text`, plus strings in `messages.en.ts` and `messages.lt.ts`. Launcher events get an `error_code` and entries in `launcher/i18n.py`. | Both interfaces are bilingual and already work this way. |
| D9 | Move code and change behaviour in separate changes. A move-only change has no test edits except import paths. | Makes review of the load-bearing files possible. |
| D10 | Port 8000, no StrictMode, both entry points through `shared/renderApp.tsx`, pages at `/display` and `/operator`, exact VAD frame sizes with carry-over. The generated bundle is not rebuilt unless the owner asks. | Non-negotiables 1, 3, 4, 5. |

## 6. Owner decisions

Agreed by the owner on 2026-11-11. These cover the ask-first areas
(`AGENTS.md`: approval flow, WebSocket protocol, `bible_data/`) as far as
this plan needs them. Anything beyond what is written here still needs
asking.

| ID | Question | Decision | Used by |
| --- | --- | --- | --- |
| Q1 | After a server restart, should the passage that was on screen come back? | Yes, when the saved record is under 10 minutes old. Re-resolved from coordinates, never from saved text. The queue is not restored. | M2 |
| Q2 | Should approving a reference that resolves to no text be refused? | Yes. Drop it from the queue and send the operator an error. | M2 |
| Q3 | May the heartbeat reply carry the active translation id so the pipeline can follow a switch? | Yes. Reply changes from 204 to 200 with a small JSON body. An older pipeline ignores the body. | M2 |
| Q4 | May the server write a service log of what was shown (coordinates, source, time)? No transcript text. | Yes, off by default, turned on by the launcher beside the session logs. | M2 |
| Q5 | Per-socket outbound queues change one guarantee: "confirmed to operators after the display was sent" becomes "after it was queued for the display". | Accepted. The message order each client sees stays the same. | M3 |
| Q6 | Add a periodic server liveness message so pages can detect a dead connection? | Approved, but build it only if a stale display is seen in a real service. | M3 |
| Q7 | Build the backlog's protocol features (remove one pending row, clear to blank) once the contract exists? | Yes, in M3. Show the owner each new message shape before it is built. | M3 |
| Q8 | Move the database snapshots out of the folder the server scans? | Yes, to `bible_data/backups/`. | M7 |
| Q9 | Add a Windows CI job? | Yes. The repository is public, so CI minutes are not a constraint: run it on every backend change. | M7 |
| Q10 | macOS: signed and notarized, or unsigned with instructions? | Unsigned with instructions for now. | M7 |

## 7. Roadmap at a glance

| ID | Milestone | Priority | Depends on | Can run beside |
| --- | --- | --- | --- | --- |
| M0 | Baselines | Essential | Nothing | Nothing |
| M1 | Safety net: protocol contract and invariant guards | Essential | M0 | Nothing |
| M2 | Service survivability | Essential | M1 | Its pipeline, launcher and server parts beside each other |
| M3 | Server core structure | Worthwhile | M2 | M4, M6 |
| M4 | Pipeline and parser structure | Worthwhile | M2 | M3, M6 |
| M5 | Detection evidence base | Worthwhile | M4, and new recordings | M3, M6 |
| M6 | Operator console structure | Worthwhile | M1 | M3, M4, M5 |
| M7 | Platform and release | Mixed | M2 | Anything not touching `launcher/` |

Why this order. M1 comes first because every later milestone edits
`state.py`, `app.py` or the message handler, and the contract is what tells a
refactor from a protocol change. M2 follows because it is the only milestone
that changes what happens in a service when something breaks. Structure work
(M3, M4, M6) comes after M2 so the behaviour it moves is already the
behaviour wanted. M5 needs the detection class from M4 and recordings that do
not exist yet.

## 8. Milestones

### M0 — Baselines (essential)

**Objective.** Record the numbers every later milestone is compared against.

**Changes.**

- Run `make check`, `make typecheck` and the four frontend checks on `main`.
  Record pass counts, every skip with its reason, and run time.
- Run the `detection-benchmark` skill for `kmynas` and `kmynas+canary` from
  the cache. Save the result files under `results/` with unique names.
  Expected from the notes: 161 TP, 10 FN, 3 low and 4 medium or high FP for
  Kmynas; 167 TP (61 high, 78 medium, 28 low), 4 FN, 13 low and 1 medium or
  high FP for both models.
- Run `session-replay` on the recorded services available on this machine
  and keep the output as the replay baseline.

**Depends on.** Nothing.

**Outcome.** A dated baseline.

**Tests and review.** None beyond running the checks. If the benchmark cannot
run (no `bench_audio/`, no cache, no models), say so and stop: M4 and M5
cannot be verified without it.

**Done when.** Baseline files exist and the numbers match the notes, or the
difference is explained.

### M1 — Safety net: protocol contract and invariant guards (essential)

**Objective.** Make the wire protocol and the display door checkable by
tests, without changing a byte on the wire.

**Addresses.** S1, S3, S4, R11.

**Changes.**

1. **Outbound message models.** In `core/server/models.py`, add one model per
   server-to-client message: `state_snapshot`, `pending_update`,
   `approval_confirmed`, `display_confirmed`, `clear_confirmed`,
   `pending_cleared`, `connection_status`, `display_settings_confirmed`,
   `auto_mode_confirmed`, `follow_auto_confirmed`, `bible_version_confirmed`,
   `follow_along`, `error` for operators; `reference`, `clear`,
   `display_settings` for displays. Two unions: `OperatorServerMessage` and
   `DisplayServerMessage`.
2. **Build messages from the models.** Replace each dict literal in
   `state.py` and `app.py` (`_ws_error` included) with the model's
   `model_dump()`. Key order and values must not change.
3. **Golden examples.** A backend test writes one example of every message
   and every operator action to `backend/tests/fixtures/protocol/` and fails
   when the checked-in file differs. A frontend test loads the same files:
   every operator example goes through the console's message handler without
   reaching an unknown-type branch, every display example through the
   display's handler. The backend test also feeds every action example to the
   server's `OperatorAction` adapter.
4. **Exhaustiveness.** Give the `switch` in the operator handler and the
   display handler a `never` default so a new union member fails `tsc`.
5. **Door guard.** A test parses `core/server/` with `ast` and asserts
   `_send_display_reference` is called only inside `approve_pending` and
   `display_manual_passage`, and `display_manual_passage` only from the
   `display_reference` handler and `offer_follow_along`.
6. **Ordering rule written down.** Add the "state change and send start share
   one synchronous step" rule to `architecture.md`, and a test that issues an
   approval and a clear concurrently against a slow fake display socket and
   asserts the display's final message matches `current_display`.
7. **R11.** In the WebSocket loop, answer a non-object JSON message with the
   existing "Missing 'action' field" error and keep the socket open.

**Depends on.** M0.

**Outcome.** A protocol change shows up as a fixture diff in review. A third
door fails a test. Later refactors of `state.py` have a net.

**Tests and review.**

- The full backend and frontend suites pass with no test edited except new
  ones. That is the proof the wire is unchanged.
- `biblelive-reviewer` on the diff, with the architecture guide read first.
- This edits `models.py`. It adds types and no wire change; state that in the
  summary.

**Done when.** Every message type on the wire has a model and a fixture, both
sides test against the fixtures, the door guard and ordering test pass, and
the README's WebSocket section agrees with the fixtures.

**Must not break.** Field order and optional-field behaviour in every message
(an older console tab reads them). The explicit "Unknown action" error. The
order of messages operators see after an approval: confirmation, then queue.

### M2 — Service survivability (essential)

**Objective.** One failure during a service is recovered without a person,
the operator is told, and afterwards there is a record of what happened.

**Addresses.** R1 to R7, R9, E4, E5. Uses decisions Q1 to Q4.

**Changes: pipeline.**

1. **Hung capture (R3).** `AudioCapture.get_audio_frame` returns `None` after
   a bounded wait (about 0.5 s) instead of looping while the queue is empty.
   `_frame_buffer` is kept across that return, so no carry-over is lost. In
   `_vad_loop`, when no frame has arrived for a new config value
   `capture_stall_restart_s` (default 10, above `AUDIO_STALL_S`), treat the
   stream as failed and call `_restart_capture()`. The language-switch check
   at the top of the loop now runs during a stall.
2. **Flapping (R4).** Clear `_capture_down` when the first frame arrives
   after a restart, in the frame path, not when ffmpeg is launched.
3. **Thread guards (R5).** Wrap the body of `_process_utterance` so an
   unexpected exception drops that utterance, counts it, and after three in a
   row raises a health issue with code `utterance_failed`. Give the VAD loop
   body the same guard with a short sleep on error so it cannot spin. Keep
   the existing `thread_stopped` report.
4. **Follow the translation (R6, Q3).** `POST /api/pipeline/heartbeat`
   replies 200 with `{"bible_version": id}`. `HeartbeatSender` hands the id
   to the processor; the transcription thread, between utterances, resolves
   it with `discover_versions` on its database folder, reopens its
   thread-local `BibleLookup` and rebuilds the validator inside the existing
   parsers without resetting their context. A missing file keeps the old
   lookup and raises a health issue.
5. **Version stamp (E4).** Add `core/version.py`: the package version plus an
   optional build id from a file the installer build writes. Put it in the
   session log header, the heartbeat, and `/healthz`.

**Changes: launcher.**

1. **Supervision (R1).** In `Launcher._run`, once the state is "running", a
   child that exits is restarted: pipeline with delays of 1, 2, 4 up to 30 s;
   server the same, followed by the readiness wait. Three exits inside 60 s
   end supervision with today's error. A failure before "running" stays fatal
   as now, so a bad setting is not hidden by a restart loop.
2. New event kinds and error codes (`audio_restarting`, `server_restarting`,
   `restart_gave_up`) through `viewmodel.py` and `i18n.py` in both languages.
3. On POSIX, start children in their own session and stop them with
   `killpg`, so a restarted pipeline cannot leave an ffmpeg holding the
   microphone. Windows keeps `taskkill /T /F`.

**Changes: server.**

1. **Persist display settings (R2).** Add `verse_limit` and `theme` to
   `SettingsStore` through its existing `_update` path; load them in
   `AppState.__init__`.
2. **Restore the screen (R2, Q1).** New `core/server/runtime_state.py`: a
   store beside the settings file holding the on-screen passage as
   coordinates, source and time, and the last 20 history entries as
   coordinates. It is written from `_record_history` and `clear_display` by a
   non-blocking, coalescing background write. In `create_app`, after the
   Bible version is restored and only when the lookup was not injected, a
   record younger than `RESTORE_DISPLAY_MAX_AGE_S` (default 600) is
   re-resolved with `resolve_passage` and set as `current_display`. A record
   that no longer resolves is dropped with a warning.
3. **No-text approval (R7, Q2).** In `approve_pending`, when the reference
   resolves to no verses, remove it from the queue, do not touch the display,
   and return a distinct result that `approve_and_notify` turns into an
   operator error and a `pending_update`.
4. **Service log (E5, Q4).** A dedicated logger with a `QueueHandler` and a
   JSON-lines file handler, enabled by `BIBLELIVE_SERVICE_LOG_DIR`. Events:
   shown (coordinates, source, confidence, inferred, whether auto-approved),
   cleared, queue cleared or overflowed, translation switched, pipeline
   status changes. The launcher sets the directory next to `logs/sessions`.
   No transcript text, in line with the promise in `/api/transcript`.

**Changes: console.**

1. **Reconnect (R9).** The operator page retries without limit like the
   display, still stopping on close code 1008. The backoff stays capped at
   30 s and the manual reconnect button stays.
2. Strings for the new health issue codes.

**Depends on.** M1.

**Outcome.** Killing the pipeline, killing the server, unplugging the
microphone or freezing ffmpeg mid-service each ends in a working service
within about a minute, with a visible notice and without a click.

**Tests and review.**

- `test_capture_recovery.py`: a fake ffmpeg whose stdout never yields leads
  to a restart; the health report goes degraded once and ok once; a frame
  split across the bounded wait is delivered whole.
- `test_live_path.py`: add a case where ffmpeg hangs, then a second launch
  delivers a detectable utterance.
- `test_launcher_process.py`: injected `popen` fakes for exit-then-restart,
  restart during startup stays fatal, three fast exits give up, stop during
  backoff returns promptly.
- New server tests: restore within and beyond the age limit, restore with a
  translation that lacks the verse, restored state reaches a newly connected
  display, the runtime file damaged or missing, settings round trip for verse
  limit and theme.
- `test_websocket_approval.py`: the no-text refusal and its message order.
- Pipeline: translation follow test with two small databases (the LTB-only
  verse 1 Samuel 24:23 case from `TECH_DEBT.md`).
- **Benchmark:** capture and segmentation code is touched. Run both tiers
  with `--no-cache` on at least three services and from the cache on all 14;
  results must equal M0.
- **Security review:** required. The heartbeat reply, the launcher's process
  handling and a new file under `core/server/` are on the `SECURITY.md` list.
- Manual check on Windows before a service: end `ffmpeg.exe`, then the
  pipeline's Python, then the server's, from Task Manager, and watch the
  window and both pages.

**Done when.** All of the above pass, the four `TECH_DEBT.md` entries this
closes are marked resolved with dates, and the manual Windows check is
recorded as done or explicitly not done.

**Must not break.**

- VAD frame size and carry-over (non-negotiable 3).
- Stale references are still dropped after 30 s, so a restarted server never
  shows an old detection.
- The restore path never sends anything by itself and never restores the
  queue.
- The restart backoff and its reset after `capture_healthy_s`.
- Launcher stop stays prompt and idempotent, and a stop during a restart must
  not leave a child running.
- GPU memory: a restarted pipeline loads its models again, so the old process
  must be gone first.

### M3 — Server core structure (worthwhile)

**Objective.** Split the two large server files along their real seams, take
slow work off the event loop, and stop one slow socket from delaying the
others.

**Addresses.** S2, S4, S8, R8, conditionally R10; backlog protocol items.
Uses decisions Q5 to Q7.

**Changes.**

1. **Routes (move only).** `core/server/routes/` with `health.py`,
   `pages.py`, `pipeline.py` (reference, transcript, heartbeat, status),
   `operator_http.py` (approve, clear, clear-pending, state), `scripture.py`
   (books, lookup), `language.py`, `bible_version.py`, `idle_screen.py`. Each
   exposes `build_router(app_state)`. `core/server/ws.py` holds the endpoint,
   authentication and the action table. `create_app` keeps startup, CORS,
   static mount and wiring.
2. **AppState collaborators (move only).** `PipelineHealth` (report, status),
   `IdleScreenService` (lock, picture, fit), `BibleVersions` (the live
   lookup, discovery, switch), `DisplaySession` (on-screen passage, queue,
   history, approve, manual display, clear, and the door). `AppState` keeps
   its public attributes and methods and forwards to them, so tests and
   `app.state.app_state` users do not change. `bible_lookup` stays readable
   through `AppState` only.
3. **Per-socket writers (Q5).** In `connections.py`, each connection gets a
   bounded `asyncio.Queue` and one writer task. `send_to_display` and
   `send_to_operator` enqueue and return. A full queue or a send that exceeds
   `SEND_TIMEOUT` closes and prunes that socket. The single-socket send used
   on connect goes through the same queue, which also gives it a timeout.
4. **Event loop.** Cache `discover_versions` results keyed by the folder's
   file names and modification times. Run the open-and-count in
   `switch_bible_version` through `asyncio.to_thread`, keeping the swap and
   the queue clear in one synchronous step afterwards.
5. **Protocol additions (Q6, Q7), each on its own:** `discard_pending`,
   clear-to-blank, and liveness only if a stale display has been seen. Each
   adds a model, a fixture, a `types.ts` entry and tests; show the owner the
   message shape first. Also the two small HTTP items from the backlog: CORS
   methods for PUT and DELETE, and requiring `application/octet-stream` on
   the idle-screen upload.

**Depends on.** M2, so the moved code already includes the restore and
service-log hooks.

**Outcome.** `app.py` under about 250 lines, `state.py` a façade, a stalled
projector costs the operator nothing, and a new endpoint has an obvious home.

**Tests and review.**

- Before the route move, add a test that snapshots every route's path,
  methods and whether it carries the token dependency. It must be identical
  after the move and must match the public and protected lists in
  `SECURITY.md`.
- Move-only steps: full suite green with only import edits in tests; M1
  fixtures unchanged.
- Writers: a test with one display socket that never reads shows an approval
  confirmed to the operator in well under a second, the slow socket pruned,
  and per-socket order preserved over 1,000 mixed messages.
- **Security review:** required (routes, `connections.py`, CORS).
- `biblelive-reviewer` after each step, not once at the end.

**Done when.** Route snapshot identical, fixtures identical apart from agreed
additions, the door guard passes against the new module layout, and the
guides' file tables point at the new files.

**Must not break.**

- The two-caller door and the checks in `offer_follow_along` that run with no
  `await` before the display call.
- The queue clear in the same synchronous step as a translation swap.
- Operator authentication: accept, then first message, then close 1008 on
  refusal; nothing sent before the token is checked.
- The two-socket test fixture (`with TestClient(app)`) that fixed the old
  hang.

### M4 — Pipeline and parser structure (worthwhile)

**Objective.** Make the pipeline's parts buildable alone, make live and
benchmark share one detection class, and pull language rules out of the
generic stream parser, all with identical detection output.

**Addresses.** S5, S6, E6.

**Changes.**

1. **`audio/detector.py`: `UtteranceDetector`.** Owns the lookup, validator,
   per-language parsers and alternates, parser selection with the
   language-switch flush, the two-model merge and the low-confidence marking.
   One method takes a `Transcription`, a language and a time and returns the
   references. No threads, no senders. Transcription-thread owned.
2. **`audio/capture_supervisor.py`.** Restart with backoff, the stall
   watchdog from M2, the healthy-stream reset and the microphone level
   tracking. VAD-thread owned.
3. **`audio/health.py`.** Builds the heartbeat report from the supervisor,
   the detector, the sender and the thread states.
4. `BibleLiveAudioProcessor` keeps `start`, `stop`, the two loops, the queues
   and the partial hand-over. `tests/processor_factory.py` shrinks with it.
5. **Benchmark.** `benchmarks/run_benchmark.py` builds an `UtteranceDetector`
   instead of its own `make_parsers` wiring. `test_detection_parity.py` then
   asserts both use the class, and moves to `make_processor` as the backlog
   asks.
6. **Language profile.** A small protocol in `core/verse_parser.py` with
   three hooks: adjust the first tokens of a new segment given the carry,
   trim the carry, and decide whether a trailing verse is cut off.
   `verse_parser_lt.py` supplies the Lithuanian one from the code now at
   `verse_parser.py:1149-1168` and `1196-1215`; English supplies a no-op.
   `StreamingParser` loses its `language == "lt"` branches.
7. **`_parse_tail`.** Split into named steps only where each step replays
   identically. Stop at the first step that does not and leave the rest.
8. **Timothy (E6), separate change.** Add the "timotiej" stem. This is a
   detection change: tests from real spellings, the untouched cases beside
   them, and the benchmark before and after.

**Depends on.** M2 (the watchdog and guards move with the code). M0's
benchmark and replay baselines.

**Outcome.** `pipeline.py` near 450 lines, one detection class under both
live and benchmark, a generic stream parser with no language names in it.

**Tests and review.**

- After every step: cached replay on 14 services for both tiers with zero
  difference from M0 (D5), and an empty `session-replay` diff.
- `test_verse_parser.py`, `test_verse_parser_lt.py`,
  `test_audio_language_switch.py`, `test_audio_partial_pipeline.py`,
  `test_pipeline_heartbeat.py` and `test_live_path.py` pass with import edits
  only.
- Timothy: report TP, FN and FP by confidence, before and after, both tiers.
- Read the Lithuanian parser lessons in `backend.md` before touching the
  parser.

**Done when.** Zero-difference replays are attached for each structural step,
the Timothy numbers are reported, and its `TECH_DEBT.md` entry is resolved.

**Must not break.**

- Held chapter-only references and the cut-off verse hold: never released
  early.
- Context expiry on the wall clock, per book.
- An utterance is parsed in the language it was captured in; a language
  switch flushes held references.
- The shutdown flush of held references before the sender stops.
- The two parsers stay separate implementations.

### M5 — Detection evidence base (worthwhile)

**Objective.** Know how well detection works on services it was not tuned on,
and in English, before tuning it further.

**Addresses.** E1, E2.

**Changes.**

1. **Held-out list.** Add `benchmarks/corpus/lt-heldout.txt` in the format of
   `lt-sermons.txt`. Services recorded from now on go there first. The
   benchmark and the skill report the fitted and held-out sets separately. A
   rule is never adjusted to fix a held-out miss without first moving that
   service to the fitted list and saying so.
2. **Gold candidates from the service log.** A script in `benchmarks/` that
   reads a service log from M2 and the matching session log and proposes gold
   lines (reference, time span) for a person to listen to and confirm.
   Operator approvals are the cheapest labels available.
3. **Work through "Re-check on new recordings"** in `BASELINE_NOTES.md` on
   the first three held-out services and record each outcome there.
4. **English.** A first English gold set from whatever English or interpreted
   recordings the owner can annotate. Until it exists, every English change
   is reported as unmeasured.
5. **Experiments, in this order, each adopted or written into "Tried and not
   adopted":** hybrid WebRTC and Silero VAD (backlog; adopt for about 5 more
   correct references and no more medium or high false positives); early
   detection from partial transcripts on the GGUF engines, aimed at the 5 s
   median report delay; the 4 GB GPU check with OBS from the backlog.

**Depends on.** M4 for the shared detection class. M2 for the service log.
New recordings and the owner's listening time, which no agent can supply.

**Outcome.** Two numbers for each tier, fitted and held-out, and a first
English number.

**Tests and review.** Unit tests for the list handling and the candidate
script. Benchmark runs follow the skill. Gold is never written by an agent
without a person confirming it, and the notes say how each entry was timed.

**Done when.** At least three held-out services are scored, each re-check
item has an outcome, and each experiment has a result on record.

**Must not break.** `bench_audio/` is read-only. Earlier result files are
never overwritten. The scoring rules (repeat credit, the gold rule for verses
announced while reading on) stay as documented.

### M6 — Operator console structure (worthwhile)

**Objective.** Turn the console's state into something that can be tested
without rendering, and clear the small console items in the backlog.

**Addresses.** S7; backlog console items; the stacked-layout entry in
`TECH_DEBT.md`.

**Changes.**

1. **Reducer.** `operator/state/operatorReducer.ts`: a pure function over
   server messages and operator intents. It holds the queue, history,
   on-screen passage, staged item, the confirmed and optimistic settings
   (today's `confirmedRef`), the follow-along hint and the in-flight flag. It
   returns the next state and a list of effects (notice, flash, store a
   setting).
2. **Hook.** `useOperatorState` keeps its returned shape. It runs the
   reducer, owns the socket, and performs the effects. Staging and the
   double-click gesture move to `useStaging`; notices and flashes to
   `useNotices`.
3. **Backlog items.** `usePlanSnippets` refetches on
   `bible_version_confirmed` and retries a failed lookup once; the OBS
   overlay's "more verses" note goes through `t()`; remove the duplicate
   "Verses shown" setting if the owner agrees.
4. **Stacked layout.** Give the layout decision in `TECH_DEBT.md` to
   `biblelive-designer` with `DESIGN.md`: below 1280 px, keep the action row
   visible while the queue scrolls.
5. **CSS (move only).** Split `operator.css` into one file per panel,
   imported in the same order.

**Depends on.** M1 for the fixtures and the exhaustive switch. Independent of
M3 and M4.

**Outcome.** A hook under about 300 lines and reducer tests that run in
milliseconds.

**Tests and review.**

- All existing `App.*.test.tsx` files pass unmodified. That is the acceptance
  test for the refactor.
- New reducer tests fed from the M1 fixtures, including: a snapshot arriving
  while something is staged, an error reverting each optimistic setting, a
  followed passage not clearing the operator's own request.
- `npm run lint && npm run typecheck && npm run format:check && npm test -- --run`.
- Layout: Playwright screenshots at 1440x900, 1280x720, 1024x700 and 390x844
  with 21 queued references, the sizes measured in the tech-debt entry.

**Done when.** The unmodified App tests pass, the hook's public shape is
unchanged, and the layout entry is resolved or the owner has declined the
change.

**Must not break.**

- No StrictMode; both entry points through `renderApp.tsx`.
- The token goes in the first socket message, never the URL. Token handling
  is not touched; if it must be, a security review is required.
- Enter, Ctrl+Enter and Shift+Enter in Find Scripture, and the other
  shortcuts.
- All text through `t()` in both languages; colours from tokens only.
- `static/frontend/` is not rebuilt.

### M7 — Platform and release (mixed)

**Objective.** Make the launcher portable, test what ships where it ships,
and make builds identifiable.

**Addresses.** S9, E3, E7, and the stale-snapshot entry in `TECH_DEBT.md`.
Uses decisions Q8 to Q10.

**Changes.**

1. **Launcher platform layer (worthwhile).** `launcher/platform/` with
   `windows.py` holding today's code unchanged and a small interface: find a
   browser and open the projector window, stop a process tree, list monitors,
   firewall rule, settings and log folders. `projector.py`, `monitors.py`,
   `network.py` and `process.py` call the interface.
2. **Windows CI (worthwhile, Q9).** One job on `windows-latest` running the
   backend suite on every backend change, with the same path filter as
   `ci.yml`. On Linux, run the Tk tests under `xvfb` so the launcher window
   tests stop being skipped.
3. **Releases (worthwhile).** Tag each installer build, keep a short
   `CHANGELOG.md`, and have the installer workflow write the build id that
   `core/version.py` reads.
4. **macOS (optional, Q10).** Follow Phases 1 to 3 in `docs/macos-arm64.md`
   as written, on the unsigned-with-instructions path: the user removes
   quarantine once, and the install notes are tested on a clean Mac. Record
   the decision in that document's "Decision before Phase 1". This plan adds
   only the platform layer above as its first step.
5. **Database snapshots (optional, Q8).** Move `*.sqlite.bak-*` and similar
   into `bible_data/backups/`, which the version scan does not read.
6. **Types (optional).** Turn on `disallow_untyped_defs` for `core.server.*`,
   then `audio.*`, one package per change.
7. **Retire `?token=` (optional).** One release after the first-message
   handshake shipped, remove the query-string path from
   `_authenticate_operator`. Security review required.

**Depends on.** M2 for the supervisor and the POSIX process-group handling.

**Outcome.** The Windows launcher behaves as before from behind an interface,
a Windows regression is caught before a Sunday, and every log names its
build.

**Tests and review.**

- `test_launcher_*.py` pass with import edits only for the platform move;
  `--dry-run` output is byte-identical before and after on Windows.
- The backlog's open Windows checks (microphone list, GPU start, 3.13
  installer service) are run on Windows after the move and recorded.
- Security review for anything in `network.py`, the bind address, tokens or
  CI secrets.
- macOS exit criteria are those in its own document, on a real Apple Silicon
  Mac.

**Done when.** Each item is done or declined on record. The platform layer
and release stamping are the two that should not be skipped.

**Must not break.** The projector never opens on the operator's screen. Stop
closes every process on the projector's private browser profile. Network
mode's firewall rule stays limited to Private networks. Port 8000.

## 9. Prioritization

| Tier | Items | Reason |
| --- | --- | --- |
| Essential | M0. M1 in full. From M2: hung-capture watchdog, thread guards, launcher supervision, persisted display settings, screen restore, operator reconnect, version stamp. | Each one removes a way a live service ends or degrades with nobody able to fix it from the console, or is the net that makes the rest safe to do. |
| Worthwhile | Rest of M2 (translation follow, no-text refusal, service log, flap fix). M3 routes, collaborators and per-socket writers. M4. M5. M6 reducer and layout. From M7: platform layer, Windows CI, releases. | Lowers the cost and risk of every later change, or replaces a guess with a measurement. |
| Optional | Liveness message. Version-discovery cache. CSS split. mypy tightening. Snapshot move. Removing `?token=`. `_parse_tail` split beyond the first clean steps. macOS release. | Real but small, or dependent on something outside engineering. |

If time allows only one milestone after the safety net, do M2. If a service
is days away, do the first three pipeline items of M2 and the launcher
supervision, and leave the server-side items for after it.

## 10. Verification and working rules

| Change touches | Required before "done" |
| --- | --- |
| Any backend file | `make check` and `make typecheck`. Report each skipped test by name and reason. |
| Any frontend file | From `frontend/`: `npm run lint && npm run typecheck && npm run format:check && npm test -- --run` |
| Capture, VAD, segmentation, speech models, spelling maps, agreement rule, parser | `detection-benchmark` skill, both tiers, before and after. A refactor must show zero difference. Upstream-of-transcript changes need `--no-cache` on the "after" run. |
| Parser or spelling maps, with a real service in mind | `session-replay` skill before and after. |
| Auth, tokens, routes, bind address, CORS, launcher network mode, control server, log redaction | `/security-review` in the main session, or state plainly that it is still needed. |
| `core/server/`, `audio/`, frontend entry points | Read `docs/agent-guides/architecture.md` first. `biblelive-reviewer` afterwards. |
| WebSocket message shapes, approval flow, `bible_data/` | Section 6 records what is agreed. Anything beyond it: stop and ask the owner. |

Working rules for the implementation lead:

- Never commit or push. Leave changes in the working tree and hand over a
  proposed commit split.
- Give each builder its own worktree. Two builders never edit the same file
  at once: M3 owns `core/server/`, M4 owns `audio/` and the parsers, M6 owns
  `frontend/src/operator/`.
- The benchmark from a worktree needs the main checkout's `bible_data/` paths
  passed in; the databases are not in git.
- Do not rebuild `static/frontend/`. Say in the summary that the UI changes
  need a build to be seen.
- A confirmed, out-of-scope defect goes into `docs/TECH_DEBT.md` in its
  format. A feature idea goes into `docs/BACKLOG.md`. Neither is fixed in
  passing.
- When a milestone closes an entry in either file, mark it resolved with the
  date and how it is covered.
- Update the guides' file tables in the same change that moves a file.

## 11. Not recommended

- **Merging the pipeline into the server process.** It would put model
  loading and GPU faults in the process that drives the projector.
- **Merging the English and Lithuanian parsers.** Listed as a known dead end
  in the architecture guide.
- **Generating `types.ts` from the Python models.** It needs a new dependency
  and a build step. Golden fixtures give the same protection.
- **A database for runtime state.** Two small JSON files written atomically
  are enough, and the read-only rule for SQLite stays simple.
- **A global lock around `AppState`.** With sends that can wait 5 s, it would
  make the stall in R8 worse. Per-socket writers remove the need.
- **Re-running rejected detection ideas.** Rolling windows, longer utterance
  caps, vocal isolation, denoise and gating, 1.0 s and 2.0 s end-of-speech,
  and a second large-v3 pass are measured and recorded in
  `BASELINE_NOTES.md`.
- **More parser tuning before M5.** Recall on the fitted corpus is 167 of
  171. The open question is how much of that holds on new services, not the
  last four references.
- **A state library or a router in the console.** A reducer in plain React
  covers the need.
