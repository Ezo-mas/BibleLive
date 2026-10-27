# macOS (Apple Silicon) — spike results and plan

Updated 2026-10-27 · branch `spike/macos-arm64` · owner-facing summary of the
pre-pin spike work and what remains before an Apple Silicon release can ship.

**Phase 0 result: complete.** Run `40584729242` (2026-10-27, `macos-15` arm64)
reports `PIN STATUS: passed` — bundled Python/Tk, the source-built ffmpeg and the
speech stack all green on one runner, each with its evidence attached to the
`macos-spike-results` artifact.

**Scope guard.** This is the minimal-release path: a bundled, self-contained app tree
for Apple Silicon, without refactoring the server, the parser or the WebSocket
protocol. Detection-accuracy and database validation are untouched, and the Windows
CI database gate stays a separate, explicitly approved follow-up.

## Why spikes first

The macOS plan rests on three assumptions that no amount of local Linux testing can
settle: that the bundled Python (with Tk) can open a real GUI window and a real file
dialog on a hosted macOS runner, that an LGPL ffmpeg with working AVFoundation can be
produced for arm64, and that the pinned speech stack actually recognizes Lithuanian
speech on arm64. Each one is now a check in a runnable harness rather than a belief.

## The harness

| File | What it proves |
| --- | --- |
| `.github/workflows/macos-spikes.yml` | Runs all three spikes on `macos-15` (arm64), writes a `macos-spike-results` artifact and a job summary, and fails the run unless the verdict is `PIN STATUS: passed` |
| `scripts/macos/spike_tk.py` | A mapped Tk window and a **real modal file dialog** in a child process; PASS requires the child's window-state marker to report `viewable` *and* the dialog still waiting at the timeout |
| `scripts/macos/spike_speech.py` | Provider descriptor, explicit `backend="cpu"` load with a runtime backend assertion, silence load/run, language-matched recognition through the real parser, `TIMING {json}` lines |
| `scripts/macos/build-ffmpeg.sh` | Builds the bundled ffmpeg from pinned source and verifies it: avfoundation device, audio encode/decode round-trip, no `--enable-gpl`, system-only linkage |
| `backend/tests/fixtures/lt-john-3-16.wav` | The Lithuanian recognition fixture (+ checksum + provenance README) |

Run it with `gh workflow run macos-spikes.yml` (or the Actions UI — the workflow also
runs on a push touching the workflow or `scripts/macos/**` while this branch is being
tested). One run downloads roughly 1.5 GB and takes 3–6 minutes on the runner;
GitHub bills macOS runners at 10×.

**Status semantics.** Each required check writes a `.status` file; the Report step
reads them and exits non-zero unless every one is `PASS`, so a failed step, a missing
artifact or a garbled report can never produce a misleading "passed". `blocked` (a
missing prerequisite, e.g. no Lithuanian voice and no fixture) and `failed` are red
on purpose. Nothing in the harness presents a single synthesized utterance as
accuracy evidence; that remains the detection benchmark's job.

## What was tried, run by run

| Run | Result | What it established |
| --- | --- | --- |
| 40580023339 | workflow rejected at startup | `runner.*` is not available in job-level `env` — the whole file was invalid |
| 40580450623 | failed | Tk **passed on arm64**; runners carry 177 `say` voices with **no Lithuanian**; the first prebuilt candidate has no `avfoundation`; the spike dir was hidden, so `upload-artifact` silently skipped it |
| 40581719122 | failed | ffmpeg built from pinned source with AVFoundation enumerated; the Python step died on HTTP 403 because it resolved "latest" through the unauthenticated GitHub API |
| 40583163319 | failed | Python pinned and Tk passed; the built ffmpeg passed its audio round-trip; the speech spike ran kmynas on CPU and then crashed in my own code (`model is closed` — reading `model.backend` after the `with` block) |
| 40583841238 | **blocked** | everything ran: Tk PASS, ffmpeg PASS, speech PASS with the English Whisper smoke and the runtime CPU assertion; only Lithuanian was `BLOCKED` (no voice, no fixture yet) |
| 40584729242 | **passed** | the checked-in Lithuanian fixture closed the last gate: Tk PASS, ffmpeg PASS, speech PASS — kmynas on CPU transcribed it to `John 3:16` and Whisper did the same for English |

## Verified on the runner

Runner: macOS 15.7.9, arm64, Apple M2 Pro (Virtual), 5 CPUs, console user `runner`,
1 login session (recorded in `runner.txt` in the artifact).

- **Bundled Python + Tk.** `cpython-3.13.16+20261013` (aarch64 `install_only`)
  imports `sqlite3`, `ssl` and `tkinter`, opens a viewable root window, and opens a
  real modal file dialog that waits for input. This is the check the Python/Tk pin
  was waiting on.
- **ffmpeg.** Built from `ffmpeg-8.1.3.tar.xz` (sha256
  `7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3`; the FFmpeg
  release signature was verified by hand once, and the hash is pinned in the build
  script). The artifact is LGPL-only (`buildconf.txt` shows no `--enable-gpl`),
  static, links only `/System/Library/Frameworks` and `/usr/lib`, lists
  `avfoundation` as an input device, enumerates the runner's video and audio devices
  live, and passes a 16 kHz mono encode/decode round-trip (`smoke.txt`).
- **Speech.** The pinned set from `backend/constraints.txt` installs on arm64; the
  native descriptor reports `['metal', 'cpu']`; an explicit `backend="cpu"` load
  reports `CPU` at runtime (`parakeet: using cpu backend (strict)`) — the v1 CPU
  policy holds on arm64. On the runner kmynas read the Lithuanian fixture as
  `'Jono evangelija trečią skyriaus šešioliktąjį būtį.'` and the parser turned it into
  `John 3:16`; Whisper `small` int8 read the English fixture as `'Today we read from
  the Gospel of John, chapter 3, verse 16.'` and did the same. Both engines are
  therefore exercised end to end, not just loaded.
- **Nothing is pinned to a moving target.** Python, the ffmpeg source and the wheel
  set are all pinned with hashes; every run re-verifies them, and the checked-in
  speech fixture's checksum is verified before it is used.

### Observed timings (single runs — a starting point, not a threshold)

| Engine | Input | Cold load | Warm run (per trial) |
| --- | --- | --- | --- |
| kmynas-v4-Q8_0 (CPU) | 1.0 s silence | 0.71 s | 0.19 s |
| kmynas-v4-Q8_0 (CPU) | 4.18 s Lithuanian fixture | 0.36 s | 0.46–0.49 s |
| whisper `small` int8 (CPU) | 4.21 s English fixture | 9.11 s | 1.6–1.9 s |

The acceptance test must not reuse these numbers as a limit. Several warm *and*
cold trials on a documented runner (hardware, OS, date) set the threshold, and the
figure is recorded next to the test so a slow runner cannot silently redefine what
counts as acceptable.

## Decisions already taken

- **ffmpeg: build from source, don't trust a prebuilt.** The arm64 prebuilts tested
  either lack the `avfoundation` input device (`serversideup/ffmpeg-lgpl-builds`:
  `--disable-autodetect` without an explicit `--enable-avfoundation`) or are GPL
  builds (`martin-riedl.de` bundles x264/x265), which would break LGPL parity with
  the Windows pin. The official tarball plus a documented configure line gives a
  reproducible, LGPL-only, AVFoundation-enabled artifact.
- **transcribe.cpp: CPU only for v1.** The spike asserts the *reported* backend at
  runtime, not just the requested one. `TRANSCRIBE_BACKENDS=cpu` stays supplemental
  until the wrapper demonstrably supports it; Metal is a later evaluation.
- **Tk pinning.** The GUI prerequisite is now demonstrated on arm64, but the pin
  stays provisional until the launcher's own root window and file dialogs pass on
  macOS — that is Phase 1 work, not something this harness can claim.
- **Speech fixture.** GitHub's macOS runners have no Lithuanian voice, so the gate
  runs from a checked-in, checksummed fixture whose source and license are
  documented beside it. A real speaker recording is preferable and should replace it
  (file, checksum and README together) when one is legally available.
- **Database.** A validated Lithuanian database remains a hard release requirement —
  no silent KJV-only production build.
- **Out of scope here.** The Windows CI database gate is a separate, explicitly
  approved follow-up; it is deliberately absent from this branch.

## Further plan

**Phase 0 — this branch (done).** Exit criteria were one run whose `report.md` shows
every check `PASS` with its evidence attached: run `40584729242` meets them (Tk,
ffmpeg and speech statuses all `PASS`; buildconf, devices, smoke, linkage, the
fixture hash and the timing lines all in the artifact).

**Phase 1 — minimal app tree.** Assemble one verified tree that both ZIP and DMG
package: bundled Python + Tk, the static ffmpeg built by `build-ffmpeg.sh`, the
speech models (download + verification), the launcher in start/stop form, and the
app's own GUI smoke (real root window, real file dialog). Exit criteria: the tree
runs from a clean macOS arm64 machine with no developer tooling installed, and the
GUI smoke passes inside the packaged app rather than in a bare interpreter.

**Phase 2 — acceptance test and pins.** Run the acceptance test on the packaged app
with the documented performance threshold (set from several warm and cold trials on a
recorded runner), then freeze the pins: Python/Tk, ffmpeg source hash, and the wheel
set from `backend/constraints.txt`. Exit criteria: a recorded pass with hardware, OS
and date noted, and no pin whose only evidence is a single measurement.

**Phase 3 — packaging and release.** One verified app tree for both ZIP and DMG,
with explicit signing-state enforcement (a signed app never ships inside an unsigned
image, and vice versa), install/uninstall notes, and the Lithuanian database verified
in the shipped artifact.

**Separate follow-ups (need their own approval).** The Windows CI database gate;
Metal-backend evaluation; notarization specifics and any code-signing/CI-token
changes (those trigger the project's security-review rule).

## Open questions

- Which validated Lithuanian database ships, and how its presence is verified in CI.
- Whether the launcher needs the Tk file dialog at all (passage import / projector
  selection) — the spike covers the capability either way.
- When to replace the synthetic Lithuanian fixture with a real recording.
