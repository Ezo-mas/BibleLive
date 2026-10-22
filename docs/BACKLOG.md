# Backlog

Features and checks planned for later, with rough size and the risks known so
far. Defects go in [`TECH_DEBT.md`](TECH_DEBT.md); this file is for things not
built yet.

## Follow-ups

### Idle screen picture: check it on a projector
The operator can upload a picture (Settings → Display → Idle screen), choose Logo or
Fill screen, and remove it; it is stored beside the settings file and survives a
restart (`backend/core/server/idle_screen.py`). The server sends its version and fit
to the displays as the optional `idle_image: {version, fit} | null` field of
`display_settings` (approved protocol change, in `models.py` and `types.ts`). The
Logo and Fill CSS (`frontend/src/display/display.css`) has not been seen in a real
browser or on a projector yet, and the OBS overlay must keep ignoring the picture.

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

### A test service on the Python 3.13 installer
Since 2026-10-21 everything runs on one Python version, 3.13: the installer's
bundled Python, CI and development (PR #53). Every pin has a Windows cp313 wheel
on PyPI, and the suite passes on Linux 3.13 and in CI. On Windows, a 3.13 venv
with the `gguf` extra installed everything from wheels and passed the full suite
(2026-10-21, a low-end Ryzen laptop without GPU: 1217 passed, 1 skipped for no
cached Whisper tokenizer, 1:59), and the "Windows installer" workflow built a
working installer with the bundled python-build-standalone 3.13 (run
39638424826). Still to do: one test service with it. Recreate the Windows dev venv with `py -3.13`. Delete this
entry once the test service went well.

### A phone on the church Wi-Fi opens a page
Network mode is built (PR #47, merged 2026-10-20): the launcher's "allow other
devices on this network" option with an operator token, the Windows Firewall
rule for Private networks, QR codes for both pages, and the `SECURITY.md` /
`DEPLOYMENT.md` sections. Not yet checked where it matters: many church Wi-Fi
networks isolate devices from each other. On the church network, open the
display and the operator page from a phone (scan the QR codes) and note
whether it works.

### Follow-along speed-up on a real service
Partial transcripts, the verse-start rule and the auto-show switch are built and
benchmarked (`benchmarks/BASELINE_NOTES.md`, "Follow-along: partial transcripts"):
on the 14 recordings a hint comes about 3 s after the verse begins instead of
9.5 s. Not yet seen live. At the next service, keep auto-show off at first and
check the session log: the "Partial transcripts" line of the metrics summary
(count, audio, time) and whether the "Reading now" line shows the verse while it
is still being read. Then try the switch for a passage that is read straight
through. English (Whisper) takes no partial transcripts and is unmeasured.

## Maintenance

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
Python matrix is settled: one version, 3.13 (see "A test service on the
Python 3.13 installer" above). Left:

- Windows-only slowness, not measurable on Linux (all under 0.2 s there):
  `test_db_path_resolution.py` (3–4.6 s each on Windows on 2026-10-20; the
  tests import a tiny 66-book canon in-process, no subprocess, so likely
  SQLite or antivirus file cost) and `test_launcher_process.py` (1.5–2.5 s).
  Profile them on Windows with `pytest --durations=15` (the Windows 3.13 test
  run is a good moment).
- Smaller fixed waits left in production code: the transcript sender's 0.2 s
  queue poll (`audio/transcript_sender.py`) costs ~0.2 s per stopping test,
  and `test_capture_recovery.py::test_vad_loop_resumes_after_ffmpeg_dies`
  takes 0.5 s. Shortening them needs a production change (a constant tests can
  patch); worth ~2 s.

## Experiments

### Hybrid WebRTC + Silero VAD
One last test before closing the Silero question (Silero alone lost to WebRTC,
see `benchmarks/BASELINE_NOTES.md`): WebRTC starts an utterance and Silero ends
it, or a frame is voiced only when both agree. Add as `--vad hybrid`, keep 30 ms
frames, one benchmark run per variant. Adopt only for about +5 correct
references with no more medium/high false positives.
