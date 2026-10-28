# macOS (Apple Silicon) — spike results and plan

Updated 2026-10-28 · branch `spike/macos-arm64` · owner-facing summary of the
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
| `scripts/macos/spike_tk.py` | A mapped Tk window, then a modal file dialog in a child process; PASS requires the child's root window to report `viewable` *and* the child to still be blocked at the timeout (the dialog itself is not inspected — see "Verified on the runner") |
| `scripts/macos/spike_speech.py` | Provider descriptor, explicit `backend="cpu"` load with a runtime backend assertion, silence load/run, language-matched recognition through the real parser, `TIMING {json}` lines; both model repos pinned to a commit |
| `scripts/macos/build-ffmpeg.sh` | Builds the bundled ffmpeg from pinned source and verifies it: avfoundation device, audio encode/decode round-trip, no `--enable-gpl`, system-only linkage |
| `backend/tests/fixtures/lt-john-3-16.wav` | The Lithuanian recognition fixture (+ checksum + provenance README) |

Run it with `gh workflow run macos-spikes.yml` (or the Actions UI) once the file is on
`main` — `workflow_dispatch` only sees the default branch. Until then, and afterwards
on any `spike/**` branch, a push touching the workflow or `scripts/macos/**` runs it;
a push to `main` does not. One run downloads roughly 1.5 GB and takes 3–6 minutes on
the runner; GitHub bills macOS runners at 10×. The uploaded artifact holds the evidence
files and the built ffmpeg, not the extracted Python (reproducible from its pinned
URL + hash).

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
  imports `sqlite3`, `ssl` and `tkinter` and opens a viewable root window; a child
  that then calls `askopenfilename` is still blocked after 15 s. That is strong
  circumstantial evidence of a waiting dialog, not proof that one was drawn (a hang
  would look the same) — acceptable, because the launcher does not use Tk file dialogs
  at all (no `filedialog` anywhere under `backend/`). The root-window result is the
  one the Python/Tk pin depends on.
- **ffmpeg.** Built from `ffmpeg-8.1.3.tar.xz` (sha256
  `7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3`; the FFmpeg
  release signature was verified by hand once, and the hash is pinned in the build
  script). The artifact is LGPL-only (`buildconf.txt` shows no `--enable-gpl`),
  static, links only `/System/Library/Frameworks` and `/usr/lib`, lists
  `avfoundation` as an input device, enumerates the runner's audio devices live
  (`Null Audio Device`, `Apple Virtual Sound Device` — the workflow now fails if none
  is listed), and passes a 16 kHz mono encode/decode round-trip (`smoke.txt`).
  **Not proven: capturing audio from a device.** Hosted runners cannot grant the
  microphone permission, so a live capture is Phase 1 work on a real Mac.
- **Speech.** The speech subset of `backend/constraints.txt` (`transcribe-cpp`,
  `numpy`, `faster-whisper`, `huggingface_hub` and their dependencies) installs on
  arm64 — not the whole `backend[audio,gguf]` set: `webrtcvad` is not installed by the
  harness and has no macOS wheel (see Phase 1). The
  native descriptor reports `['metal', 'cpu']`; an explicit `backend="cpu"` load
  reports `CPU` at runtime (`parakeet: using cpu backend (strict)`) — the v1 CPU
  policy holds on arm64. On the runner kmynas read the Lithuanian fixture as
  `'Jono evangelija trečią skyriaus šešioliktąjį būtį.'` and the parser turned it into
  `John 3:16`; Whisper `small` int8 read the English fixture as `'Today we read from
  the Gospel of John, chapter 3, verse 16.'` and did the same. Both engines are
  therefore exercised end to end, not just loaded. Kmynas alone is also what ships:
  `kmynas+canary` is the launcher's NVIDIA-GPU choice, so "auto" on a Mac resolves to
  `kmynas` (to be confirmed in the packaged launcher).
- **Pinning.** Python and the ffmpeg source are pinned by URL + SHA256 and re-verified
  on every run, as is the checked-in fixture. The wheels are pinned by **version only**
  (`constraints.txt` has no hashes), and the two model repos by commit
  (`Ezomas/kmynas-parakeet-lt-v4-gguf@b4c73adf`, `Systran/faster-whisper-small@536b0662`
  — the commits current when run `40584729242` passed; they were unpinned in that run).
  A shipped tree needs wheel and model-file hashes, not just versions (Phase 2).

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

Revised 2026-10-28 after review. The original phases assumed the remaining work was
packaging; the review found three things that decide whether a Mac release is usable
at all and that no hosted runner can test — microphone permission, Gatekeeper, and the
launcher's Windows-only code — so they now come first.

**Phase 0 — this branch (done).** Exit criteria were one run whose `report.md` shows
every check `PASS` with its evidence attached: run `40584729242` meets them (Tk,
ffmpeg and speech statuses all `PASS`; buildconf, devices, smoke, linkage, the
fixture hash and the timing lines all in the artifact). Hardening after review (model
revisions pinned, fixture preferred over a `say` voice, audio-device assertion,
spike-branch-only push trigger, smaller artifact) needs one more green run before
merge.

**Decision before Phase 1 — distribution and signing.** On macOS 15 an unsigned,
un-notarized app downloaded from the internet is quarantined, the Control-click →
Open bypass is gone (only System Settings → Privacy & Security → "Open Anyway"), and
every nested binary in a quarantined tree (ffmpeg, the Python dylibs, wheel `.so`
files) can be blocked separately. Microphone access is also granted per signed
identity: an ad-hoc-signed app may be asked again after every update. So signing is
not a late add-on — it shapes the tree (hardened runtime needs entitlements such as
`com.apple.security.device.audio-input` and, for pip-installed extensions,
`com.apple.security.cs.disable-library-validation`). Choose one, in writing:

- *Developer ID + notarization* (Apple Developer Program, CI signing secrets — a
  security-review change under `SECURITY.md`), or
- *unsigned, documented*: the user removes quarantine once (`xattr -dr
  com.apple.quarantine BibleLive.app`) — acceptable only for a small, assisted
  rollout, and the install notes must be tested on a clean Mac.

**Phase 1 — minimal app tree, proven on a real Mac.** Assemble one tree (a `.app`
bundle) with bundled Python + Tk, the static ffmpeg from `build-ffmpeg.sh`, the full
`backend[audio,gguf]` set, the speech-model download + hash check, and the launcher.
Known work:

- *VAD wheel.* `webrtcvad==2.0.10` is source-only on PyPI, so it would need a
  compiler on the user's Mac. `webrtcvad-wheels==2.0.14.post1` (already pinned for
  Windows) ships `cp313 macosx_11_0_arm64`; extend its marker to `darwin` in
  `backend/pyproject.toml` and `constraints.txt`, and prove the VAD frame sizes are
  unchanged (`backend/tests` VAD tests on the macOS runner).
- *Microphone permission.* `Info.plist` must carry `NSMicrophoneUsageDescription`, and
  the process that the user sees asked for permission must be the one that opens the
  device (TCC attributes capture to the "responsible" app: launched from Terminal it
  is Terminal; from the bundle it is the bundle). Without it capture fails or records
  silence with no error — the worst possible failure for a live service.
- *Launcher Windows-only paths.* `projector.py` finds Edge/Chrome only by Windows
  install paths and `.exe` names and its leftover-profile cleanup is Windows-only, so
  on a Mac the display opens in the default browser with no full-screen projector
  placement; `process.kill_tree` only `terminate()`s on non-Windows, so verify that
  stopping the launcher also stops the pipeline's ffmpeg child (an orphaned ffmpeg
  keeps the microphone open). Monitor enumeration needs the same check.
- *Runner-checkable:* `otool -L` over every Mach-O in the tree (no `/opt/homebrew`,
  `/usr/local` or build paths), the launcher's own Tk root window in the bundle, the
  full backend test suite with the bundled interpreter.

Exit criteria (on a **real Apple Silicon Mac** with no Xcode/Homebrew — a hosted runner
is neither clean nor allowed the microphone): install from the downloaded artifact
following the written notes; first launch shows the microphone prompt naming
BibleLive; a 30 s capture of real speech through the full pipeline reaches the
operator page as a pending reference; display opens on the second screen; stop leaves
no `ffmpeg`/Python process behind.

**Phase 2 — acceptance test and pins.** Acceptance test on the packaged app with a
documented performance threshold (several warm and cold trials on a recorded machine;
for a live service the number that matters is kmynas real-time factor on a long
utterance and end-to-end latency, not the 4 s fixture), then freeze: Python/Tk,
ffmpeg source hash, wheels **with hashes** (`pip install --require-hashes` for the
shipped tree), model files by commit + SHA256. Exit criteria: a recorded pass with
hardware, OS and date noted, and no pin whose only evidence is a single measurement.

**Phase 3 — packaging and release.** ZIP and DMG built from the one verified tree,
signing state enforced as decided above (a signed app never ships inside an unsigned
image, and vice versa), install/uninstall notes tested on a clean Mac, and the
Lithuanian database verified in the shipped artifact.

**Separate follow-ups (need their own approval).** The Windows CI database gate; Metal
backend evaluation; Intel Macs (out of scope: arm64 only); any code-signing/CI-token
change (security review).

## Open questions

- Which validated Lithuanian database ships, and how its presence is verified in CI.
- Signed + notarized or unsigned-with-instructions (see "Decision before Phase 1").
- Who provides the clean Apple Silicon Mac for the Phase 1 exit test.
- When to replace the synthetic Lithuanian fixture with a real recording.
