# Backlog

Features and checks planned for later, with rough size and the risks known so
far. Defects go in [`TECH_DEBT.md`](TECH_DEBT.md); this file is for things not
built yet.

## Follow-ups

### Two-model speech on a 4 GB GPU with OBS running
Kmynas + Canary has not been run with real audio on a 4 GB card next to OBS. The
2026-10-18 attempt on a GTX 1650 Ti received digital silence, so the ~2.2 GB of
VRAM it showed was the models at rest; while transcribing they used ~3.15 GB on
the development PC. Test with OBS streaming and live sound: watch dedicated and
shared GPU memory in Task Manager and the transcription times in the session
log. If shared memory climbs or transcription lags, use the CPU tier on that PC.

### macOS: second screen, and the network prompt's wording
From the first Mac test (M1, 2026-11-17, PR #94). The display opening full
screen on an extended display is untested (no USB-C adapter on hand); check it
with the projector set to "Extended display", including a screen placed left of
or above the main one (negative coordinates). Network mode works, but macOS's
"accept incoming connections" prompt names Python, not BibleLive: the server is
the bundled `python3.13`, and the application firewall asks per binary. Explain
it in the launcher's network wording and in `docs/macos-install.md` (or run the
server under a BibleLive-named binary). Also unchecked: whether macOS asks for
the microphone again after a re-downloaded update.

## Planned console and display features (owner notes, 2026-11-17)

To start after the improvement plan (M7), apart from what is blocked waiting
for new service recordings. Each changes how the operator works, so each gets a
plan and review before it is built.

### Remove staging; Plan always in view
Staging (Up Next) has gone practically unused at real services: Plan already
covers "what comes next", and the Live switch below covers "check it before the
audience sees it". Remove staging and redesign the console so Plan is always
visible, with the queue (pending) and History beside it. Rework every flow and
shortcut that stages today: arrow-key selection then Show, Ctrl+Enter in Find
Scripture, double-click restore, Show on a history row, and the Up Next card.
Large: layout (designer + `DESIGN.md`), `useStaging`/reducer, many App tests.
Mock the layout first and get it approved before building.

### Live / not-live switch
A console button. While not live the audience display shows nothing at all: a
blank screen, no idle picture or logo, as if disconnected. Going live shows the
current passage again. The state is the server's, so a reconnecting display
obeys it. This is a protocol change (new action and confirmation, a field in the
snapshot and `display_settings`): show the message shapes to the owner before
building. Open: whether auto-approve keeps showing references while not live.
Pairs with removing staging; build them as one milestone.

### Follow-along adds verses (rolling window)
Built (2026-11-18), not yet seen live. As the speaker reads on, Show and
auto-show grow the passage to three verses, then roll (10, 10-11, 10-12,
11-13 ...), whatever the verse limit; a hint now names only the verse being
read. At a service, check that the text size stays steady and that the next
verse is still offered promptly after a rolled passage goes up.
Optional follow-up: a console "expand" button that adds one verse per press,
sharing the same window rule.

### Animations
Subtle motion where it helps: the logo, the OBS overlay's show/hide, display
passage swaps. Respect `prefers-reduced-motion`; nothing moves while text is
being read.

### Launcher redesign: simple and advanced views
Built (PR #111, merged 2026-11-20): the launcher window
becomes a pywebview page (React, `frontend/src/launcher/`) with Simple and
Advanced views, an always-on microphone meter before the session, live server
and audio output in the Advanced console, a no-signal warning and a status strip.
The Tk window stays as a fallback. Follow-ups, each its own change:
- **QR codes split by audience.** The audience QR shown on `/display` on demand
  (from the launcher); the operator QR only under Advanced → Network.
- **macOS on the new window.** Add the `launcher` extra to the Mac app
  (regenerate `scripts/macos/requirements-arm64.lock` on the Mac for pyobjc) and
  test on the M1; until then the Mac keeps the Tk window. Includes the wording
  pass for the macOS network prompt above.
- **Input meter in the operator console.** A small live level next to Speech
  status. Needs a new server message (protocol change: show the shapes first).
- **Identify screens.** A button by Projector that briefly shows a large number
  on every monitor, so the operator picks the right one.
- **Speech checks in Advanced.** Show whether the Lithuanian GGUF models are
  actually present (today a missing file silently falls back to Whisper), and a
  "Run GPU self-test" button reusing `--gpu-self-test`.
- **Diagnostics bundle.** One click zips the logs and settings, with the
  operator token removed, for support.
- **Regenerate the operator token.** Revokes access for devices it was shared
  with. Needs a security review.
- **Port check before Start.** When the port is taken, name the program using it.
- **Console quick actions.** A one-click Copy logs button beside Log files, and a
  "Paused — jump to latest" indicator when the operator scrolls the console up
  (auto-scroll already stops then, but nothing says so).
- **Remember the window between launches.** Save the window's position and size
  per view, the Advanced tab (Speech / Network / Launch) and the console filters
  (source, minimum level) in `launcher.json`, beside the Simple/Advanced choice that
  is already saved, so a repeat Sunday setup opens as it was left.
- **One palette for launcher and pages (undecided).** The launcher uses its own
  warm palette (`frontend/src/launcher/launcher.css`), not the console's
  `DESIGN.md` tokens. The owner likes the launcher's better: decide whether to
  move the operator/display pages to it (update `DESIGN.md` and the token CSS)
  or bring the launcher onto the existing tokens. Either way, one set of tokens.

## Maintenance

### npm 10.9 cannot install vitest 4.1.11 or later
Size: small. The frontend dev tools moved to vite 6 and vitest 5 to clear
`npm audit` (2026-10-24). npm 10.9.9 crashes installing vitest 4.1.11 and later
(`Cannot read properties of null (reading 'edgesOut')` in its peer loader,
reproduced in an empty project); 5.0.5 installs. `package.json` allows
`^5.0.5`, so only `package-lock.json` holds it at 5.0.5: before an
`npm update` that moves vitest, check that it installs, or upgrade npm first.

### Test suite audit
Size: small, what is left. Not urgent.

First pass done 2026-10-21 on the Fedora box (Python 3.13): the two-socket
WebSocket hang is fixed (`TECH_DEBT.md`, resolved), every CI job has
`timeout-minutes`, the control-server and language-proxy tests no longer wait
0.5 s per server stop, the three real-ffmpeg `test_live_path.py` tests are
marked `slow` (`-m "not slow"` for a quick local run; CI runs everything), and
the Linux-only launcher window failures are fixed (the tests saved settings to
the Windows `%APPDATA%` layout; the 1366x768 height test is now Windows-only,
because Linux Tk's default fonts make the same window ~790 px tall). Full suite
there: 59 s to 38 s, 20 s without `slow`. No test was clearly meaningless, so
none was removed. Every test now has a 60 s limit (`pytest-timeout`), so a
future hang fails one test with a stack dump instead of a whole CI job. The CI
Python matrix is settled: one version, 3.13. Left:

- Windows-only slowness, not measurable on Linux (all under 0.2 s there):
  `test_db_path_resolution.py` (3–4.6 s each on Windows on 2026-10-20; the
  tests import a tiny 66-book canon in-process, no subprocess, so likely
  SQLite or antivirus file cost) and `test_launcher_process.py` (1.5–2.5 s).
  Profile them on Windows with `pytest --durations=15` (the Windows 3.13 test
  run is a good moment).
- `test_capture_recovery.py::test_vad_loop_resumes_after_ffmpeg_dies` takes
  ~2.2 s on Windows (0.5 s on Linux), spent in thread joins, not in the frame
  wait (now patched to 0.05 s). The transcript sender's 0.2 s queue poll is
  done: a constant (`QUEUE_POLL_S`) patched to 0.01 s in tests, so a sender
  stop costs ~0.02 s instead of ~0.2 s (2026-11-18).

## Experiments

### More recordings for the parser
Every new service recording goes into the corpus (`benchmarks/corpus/`): the
held-out set first, as M5 describes. Needs services recorded and copied into
`bench_audio/`.

### Fewer low-confidence false positives in two-model mode
Two-model mode (Kmynas + Canary) has 15 low-confidence false positives on the
fitted corpus against 3 for Kmynas alone. A replay on 2026-11-17 found where they
come from: a reference only Canary found gives 6 correct and 9 wrong ones, one
only Kmynas found gives 20 correct and 6 wrong. Dropping Canary-only references
takes the tier back to Kmynas alone (167 TP). Dropping only single-model
chapter-only references ("Psalms 25", no verse) removes 3 wrong ones and no
correct one, on the same corpus it was found on, so it needs held-out services
before it is adopted. Any change here is in `audio/ensemble.py`, which only the
two-model tier runs, so the Kmynas CPU tier cannot change.

