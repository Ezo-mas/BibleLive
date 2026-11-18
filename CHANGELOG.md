# Changelog

What changed for the people running a service, one section per installer
release. Newest first.

**Releasing.** Set `version` in `backend/pyproject.toml`, move the
"Unreleased" notes under a heading for that version and date, merge, then
push a tag `v<version>` on that commit. The tag runs
`.github/workflows/windows-installer.yml`, which builds the installer, stamps
it with the version and commit (`backend/core/build-id.txt`, shown in the
launcher and written to every service log) and attaches it to a GitHub release,
and `.github/workflows/macos-app.yml`, which attaches the Mac disk image and zip
to the same release.

## Unreleased

Nothing yet.

## 0.2.0 (2026-11-18)

The first release with a Mac app, and checked speech models on both platforms.

### macOS (new)
- BibleLive for Macs with an Apple processor (M1 or newer, macOS 14 or later):
  a disk image and a zip on the release page. It is not signed by Apple, so
  macOS needs **Open Anyway** once per version; `docs/macos-install.md` walks
  through it, the model download, the projector and uninstalling.
- The launcher asks for the microphone when you press Start, before the
  browser opens, and says so if macOS does not allow it. Quitting with ⌘Q or
  from the Dock stops the services like the close button.
- Tested on an M1: Lithuanian detection runs well within real time on the
  processor, with the same results as the Windows CPU tier.

### Speech models (Windows and Mac)
- `--download-model` fetches each model at a fixed, tested version and checks
  every file's checksum; a damaged or changed file is deleted and the download
  reports an error. Downloads use plain HTTPS (Hugging Face's newer transfer
  stalled on some networks).
- Models downloaded before this release keep working. The launcher says when
  the Lithuanian model is "an older download, not the checked version": run
  the download again to replace it.
- Whisper now starts from the downloaded copy without contacting Hugging Face.

### Bibles
- Translations that do not come with the app, such as the Lithuanian Bible,
  go in a per-user folder that survives updates: `%APPDATA%\BibleLive\bible_data`
  on Windows, `~/Library/Application Support/BibleLive/bible_data` on a Mac.
  Copies already inside the app's own `bible_data` keep working.
- The Lithuanian text read out for a detected verse is now always the same
  translation the display shows.

## 0.1.0 (2026-11-17)

The first tagged installer. Highlights from the improvement plan
(`docs/IMPROVEMENT_PLAN.md`, M1 to M7):

### Operator console
- Remove one detection from the queue without clearing the rest, and choose
  whether clearing the display shows the idle picture or a blank screen.
- The queue's action row stays in view on smaller windows; the two-column
  layout now holds down to 1024 px wide.
- "Verses shown" lives only under the preview (the duplicate in Settings is
  gone). Plan rows follow a change of Bible translation, and a failed text
  lookup is retried once.
- The OBS overlay's "+N more verses" note is translated.

### Live service
- The audio capture is watched, so a hung ffmpeg is noticed and restarted, and
  the launcher supervises the server and the speech pipeline.
- Display settings and the passage on screen survive a server restart.
- A disconnected operator console reconnects on its own.
- Lithuanian detection: a dash between a spoken ordinal and a numbered book,
  and a chapter number cut in two by the 12-second utterance limit, are now
  read correctly.

### Builds
- Every installer build names its version and commit, in the launcher and in
  each service log.
- The launcher's Windows-specific code sits behind a platform layer, the
  first step towards a macOS build.
- Continuous integration also runs the backend tests on Windows.
