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

### macOS: second screen, and the network prompt naming Python
From the first Mac test (M1, 2026-11-17, PR #94). The display opening full
screen on an extended display is untested (no USB-C adapter on hand); check it
with the projector set to "Extended display", including a screen placed left of
or above the main one (negative coordinates). The network prompt names
`python3.13`, not BibleLive (the server is the bundled Python, and the firewall
asks per binary); the launcher and `docs/macos-install.md` now explain it.
Running the server under a BibleLive-named binary would remove it. Also
unchecked: whether macOS asks for the microphone again after a re-downloaded
update.

### Follow-along rolling window at a service
Check that the text size stays steady and that the next verse is offered
promptly after a rolled passage goes up. The three-verse window shipped in
[PR #103](https://github.com/Ezo-mas/BibleLive/pull/103).

## Planned console and display features

### Show a row on touch screens
[PR #115](https://github.com/Ezo-mas/BibleLive/pull/115) removed the rows' Show
buttons: a row is shown by double-click or Enter. A phone or tablet (e.g. via the
operator QR code) has no Enter and no hover hint, and double-tap is unreliable in
some mobile browsers (iOS Safari), so showing a row there is untested and may not
work. Agreed design, touch only (`@media (hover: none) and (pointer: coarse)`),
desktop unchanged: a tap highlights the row and reveals one compact gold Show button
on that row only, placed in space the row already has (the timestamp line in
Pending and History, the tools group in Plan) so nothing shifts. History rows take
the highlight on touch so the button has a row to appear on. Rows get
`touch-action: manipulation` (no double-tap zoom, so double-tap still works),
`user-select: none` and `-webkit-touch-callout: none` on touch. Rejected: a Show
button on every row (clutter) and a sticky bottom action bar (far from the row).
Check on a real phone; the browser pane only simulates mouse clicks.

## Launcher follow-ups

- **Live wave from real levels.** The launcher's live-state wave (`--wave-live`)
  is a canned animation. Feed it the pipeline's real input levels, as the
  console's meter does since [PR #116](https://github.com/Ezo-mas/BibleLive/pull/116).
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
- **Launcher on the shared tokens.** [PR #115](https://github.com/Ezo-mas/BibleLive/pull/115)
  moved the console to the launcher's warm palette, but
  `frontend/src/launcher/launcher.css` still holds its own copy of the colours as
  raw hex values. Point it at `frontend/src/styles/tokens.css` so there is one set
  of tokens.

## Maintenance

### npm 10.9 cannot install vitest 4.1.11 or later
Size: small. The frontend dev tools moved to vite 6 and vitest 5 to clear
`npm audit` (2026-10-24). npm 10.9.9 crashes installing vitest 4.1.11 and later
(`Cannot read properties of null (reading 'edgesOut')` in its peer loader,
reproduced in an empty project); 5.0.5 installs. `package.json` allows
`^5.0.5`, so only `package-lock.json` holds it at 5.0.5: before an
`npm update` that moves vitest, check that it installs, or upgrade npm first.

### Windows CI: pytest exits silently mid-run
The "Windows backend tests" job twice ended with exit code 1 and no traceback or
FAILED line, mid-test: on PR #114 (2026-11-21, after `test_working_references.py`
at 99%) and on PR #122 (2026-11-21, during
`test_translation_follow.py::test_retry_warning_is_logged_once_per_requested_id`
at 85%). Neither PR touched those tests, and a rerun passed both times; the full
suite passes locally on Windows. Cause unknown. A first lead: some tests
monkeypatch `audio.pipeline.time.monotonic`, which replaces `time.monotonic`
for every thread in the process. pytest's faulthandler printed no stack, so it
may not be a native crash: check the job's step timing and any `os._exit` in tests.

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

