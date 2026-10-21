# Backlog

Features and checks planned for later, with rough size and the risks known so
far. Defects go in [`TECH_DEBT.md`](TECH_DEBT.md); this file is for things not
built yet.

## Follow-ups

### Translate the stream overlay's "+N more verses"
The OBS overlay (`/display?mode=obs`, `frontend/src/display/StreamOverlay.tsx`)
writes its note in English only. Use the i18n layer: `t('stage.moreVerses', { count })`.
Small.

### Two-model speech on a 4 GB GPU with OBS running
Kmynas + Canary has not been run with real audio on a 4 GB card next to OBS. The
2026-10-18 attempt on a GTX 1650 Ti received digital silence, so the ~2.2 GB of
VRAM it showed was the models at rest; while transcribing they used ~3.15 GB on
the development PC. Test with OBS streaming and live sound: watch dedicated and
shared GPU memory in Task Manager and the transcription times in the session
log. If shared memory climbs or transcription lags, use the CPU tier on that PC.

### Edge pop-ups on a signed-in profile
The projector window's Edge flags (no sync, no extensions, no implicit sign-in)
were checked only on a fresh Windows profile. Check once on a PC whose Edge is
signed in with extensions installed.

## Features

### A second PC on the same Wi-Fi opens the display or operator page
Size: medium, about 2–3 days.

Status: in progress on branch `feat/lan-access` (launcher option and token, the
launcher's Windows Firewall button for Private networks, QR codes for both pages
(**Show QR codes** in the launcher, using `segno`), SECURITY.md and DEPLOYMENT.md).
Still to do: a phone on the church Wi-Fi opens a page from the PC (see Risk below).

The server can already listen on the network (`BIBLELIVE_HOST=0.0.0.0`) and has
operator tokens; the launcher deliberately binds `127.0.0.1`
(`backend/launcher/process.py`). Needed:

- A launcher option "allow other devices on this network"; with it on, operator
  access requires a token.
- Show the address to open (e.g. `http://192.168.1.23:8000/display`), ideally
  with a QR code.
- A Windows Firewall rule (installer or launcher).
- Remote display tabs stay view-only; remote operator tabs need the token.
- Update `SECURITY.md` and `DEPLOYMENT.md`.

Risk: many church Wi-Fi networks isolate devices from each other. Check that a
phone can open a page served by the laptop before building this.

### Follow-along without the one-utterance lag
Size: large, about 1–2 weeks with tuning. Plan it properly first.

The follow-along hint appears only after the verse has been read, because a
transcript exists only when an utterance ends (1.5 s of silence, or the 8–12 s
cut). Options:

- **Partial transcripts** every 2–3 s while the speaker reads: a second
  transcription stream (roughly twice the CPU work; a 6th-gen i7 laptop ran at
  0.21× real time on one stream), segmenter changes, follow-along matching tuned
  for partial text, and live testing.
- **Offer the next verse** as soon as the current one is matched: cheap, helps
  straight reading only.

## Maintenance

### Test suite audit
Size: medium, about 1–2 days. Not urgent.

Go through `backend/tests` (~1200 tests, ~1.5–2 min locally) and `frontend/src`
tests:

- Remove tests that assert nothing meaningful (duplicates, tests of mocks,
  tests that only restate the implementation).
- Speed: the count is not the problem (~1:1 test-to-code lines). On 2026-10-20
  the 15 slowest tests took ~53 of the suite's 91 s; the other ~1150 ran in ~40 s.
  Start there (`pytest --durations=15`):
  - `test_language_proxy.py::test_a_pipeline_that_is_not_running_is_a_503`
    (6 s, likely a real timeout — shorten it in the test).
  - `test_db_path_resolution.py`: four tests at 3–4.6 s (subprocess runs).
  - `test_control_server.py`, `test_launcher_process.py`,
    `test_audio_language_switch.py`, `test_capture_recovery.py`: 1.5–2.5 s each
    (port/process waits).
  - `test_live_path.py`: three tests at ~6 s with real ffmpeg. Keep them — they
    are the end-to-end check of the approval flow — but mark them so a quick
    local run can skip them.
- Fix the two-socket WebSocket hang first (see `TECH_DEBT.md`, "WebSocket tests
  with two sockets open can hang forever") and add a CI job timeout.
- Linux only: on the Fedora dev box (2026-10-21, Python 3.13, with a
  display) three `test_launcher_network.py` window tests fail and one errors
  at teardown, alone or in the full run (43 passed, 3 failed, 1 error); they
  pass on Windows. They assume Windows: the firewall only applies on win32
  (`launcher/network.py` `firewall_applies`), and the settings/height checks
  likely depend on it or on Linux font metrics:
  `test_window_shows_addresses_token_and_operator_fragment` (`network_token`
  is `None`), `test_window_checks_the_firewall_only_when_network_mode_is_on`
  (firewall never checked; plus the teardown error), and
  `test_window_fits_a_1366x768_screen_with_the_network_and_firewall_rows`
  (793 px requested vs. 690 px allowed). Patch the platform in the tests or
  mark them Windows-only. CI never sees this: it has no display, so the
  window tests skip there.
- Decide the CI Python matrix: today 3.11/3.12/3.13 while the installer ships
  3.11 and dev uses 3.12. Either test only what ships, or move everything to 3.13
  in its own PR (all pinned packages have Windows cp313 wheels, checked
  2026-10-20) with an installer build and a test service.

## Experiments

### Hybrid WebRTC + Silero VAD
One last test before closing the Silero question (Silero alone lost to WebRTC,
see `benchmarks/BASELINE_NOTES.md`): WebRTC starts an utterance and Silero ends
it, or a frame is voiced only when both agree. Add as `--vad hybrid`, keep 30 ms
frames, one benchmark run per variant. Adopt only for about +5 correct
references with no more medium/high false positives.
