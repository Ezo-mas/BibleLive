# Changelog

What changed for the people running a service, one section per installer
release. Newest first.

**Releasing.** Set `version` in `backend/pyproject.toml`, move the
"Unreleased" notes under a heading for that version and date, merge, then
push a tag `v<version>` on that commit. The tag runs
`.github/workflows/windows-installer.yml`, which builds the installer, stamps
it with the version and commit (`backend/core/build-id.txt`, shown in the
launcher and written to every service log) and attaches it to a GitHub release.

## Unreleased

Everything up to now; no installer has been tagged yet. Highlights from the
improvement plan (`docs/IMPROVEMENT_PLAN.md`, M1 to M7):

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
