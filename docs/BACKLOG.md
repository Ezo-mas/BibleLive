# Backlog

Open features, verification checks, maintenance, and experiments. Confirmed
defects are tracked in [`TECH_DEBT.md`](TECH_DEBT.md).

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

### Follow-along rolling window at a service
Check that the text size stays steady and that the next verse is offered
promptly after a rolled passage goes up. The three-verse window shipped in
[PR #103](https://github.com/Ezo-mas/BibleLive/pull/103).

## Planned console and display features

### Expand a follow-along passage
Optional console button that adds one verse per press, using the same
three-verse window as Show and auto-show.

### Idle logo animation
Add subtle motion to the idle logo and respect `prefers-reduced-motion`.

## Launcher follow-ups

- **QR codes split by audience.** The audience QR shown on `/display` on demand
  (from the launcher); the operator QR only under Advanced → Network.
- **macOS on the new window.** Add the `launcher` extra to the Mac app
  (regenerate `scripts/macos/requirements-arm64.lock` on the Mac for pyobjc) and
  test on the M1. The network prompt wording is tracked in the macOS follow-up
  above.
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

### Profile slow tests on Windows
Run `pytest --durations=15` on Windows to recheck these audit findings:

- `test_db_path_resolution.py` (3–4.6 s per test on Windows on 2026-10-20)
  and `test_launcher_process.py` (1.5–2.5 s). Both were under 0.2 s per test
  on Linux.
- `test_capture_recovery.py::test_vad_loop_resumes_after_ffmpeg_dies` takes
  ~2.2 s on Windows (0.5 s on Linux), mostly in thread joins. The frame wait
  is already patched to 0.05 s in this test.

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

