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

### Launcher
- **New operator token** in Advanced → Network replaces the network-mode token,
  which locks out every phone or tablet it was shared with. It works while
  BibleLive is stopped and takes effect at the next Start.
- **Speech checks** in Advanced → Speech show whether the Lithuanian speech models
  are actually on this computer (a missing file used to fall back to Whisper
  silently), and a **Run GPU self-test** button checks the graphics card before a
  service.
- **Identify screens** beside the projector picker briefly shows a large number
  on every monitor, so the right one is picked for the audience.
- **Port check:** before Start the launcher warns when the server port is already
  taken and names the program using it (for example OBS or another BibleLive).
  If Start fails on it, **Change port** takes you straight to the port field.
- **Diagnostics** in the console collects the logs, recent service transcripts
  and settings into one ZIP for support. The access token and session audio are
  never included.
- The live wave now moves with the real microphone during a session, and stays
  flat when no sound arrives.
- The launcher reopens where it was left: window position and width, the
  Advanced tab and the console filters. Switching between Simple and Advanced no
  longer moves the window. A position on a screen that is no longer connected
  is ignored.
- Console: **Copy logs** copies the lines shown, and a "Paused — jump to latest"
  button appears when the log is scrolled up. A failed copy now says so instead
  of "Copied". The console buttons fit on one row in Lithuanian too.

### Operator console
- **Auto-clear timer** in Settings → Display clears a passage after 5–600 seconds
  on air. It starts Off. The console shows the selected duration beside Clear
  while an on-air passage is timed. Showing a passage, including through
  follow-along, starts a fresh timer. If a passage is restored after a server
  restart, it gets the full duration again.
- **Phones and tablets:** tapping a row in Pending, History or Plan reveals a
  Show button on that row. Find Scripture has separate Show and Add to Plan
  buttons and no longer covers Clear, Live and +1. A compact bar shows what is on
  the display when the preview scrolls out of view; tap it to jump back. Clear,
  Live and +1 have larger touch targets, and Logout moves into Settings on narrow
  screens. On touchscreen laptops and iPads with a trackpad, only a mouse
  double-click shows a row, so a finger double-tap no longer shows one by
  accident.

### Detection
- Fixes for misreads from the 2026-11-22 service: a speaker correcting a
  numbered book ("antras karalius, antrą karalių knygą, šeštas skyrius") now
  gives 2 Kings 6 instead of an auto-shown 2 Kings 2; a numbered book named
  without a number ("antra karalių") takes over from the previous book; Hebrews
  is recognised as "Laiškas žydams" / "Žydams 11 skyrius". New spelling fixes for
  Proverbs ("Patolių"), Luke ("Mūko") and "eilutė" misheard by the speech models.

### Install
- The Windows installer and the Mac app ship the Lithuanian Bible (`ltb`) again: a fresh
  install shows Lithuanian verse text without copying `bible_ltb.sqlite` in by hand. 0.2.1
  release builds left it out, so the server could not restore a saved `ltb` choice.

## 0.2.1 (2026-11-22)

A new launcher window on Windows and Mac, a calmer operator console with a
Live switch, and OBS settings kept by the server.

### Launcher
- A new launcher window with a Simple view (start/stop, microphone, projector,
  spoken language) and an Advanced view with Speech, Network and Launch
  settings beside a live console of the launcher, server and speech logs.
- The selected microphone shows a live level while idle, with a warning when
  it hears near-silence, so a dead mic is found before the service starts.
- QR codes: a button puts a code for the display page in a corner of the
  audience display (shown only in network mode); the operator's code, which
  carries the token, is under Advanced → Network and never on the projector.
- The Mac app opens the same launcher. ⌘Q stops the services like the close
  button, and launching no longer triggers a firewall prompt.

### Operator console
- **Live switch:** take the audience display off air (black) and back without
  losing the passage; the console's preview keeps showing it.
- Staging (Up Next) is gone. Plan is always in view under On display, with
  Pending and History beside it. Click or arrow keys highlight a row;
  double-click or Enter shows it. In Find Scripture, Enter shows and
  Ctrl+Enter adds to the plan.
- A live microphone meter in the speech badge in the header.
- The launcher's warm palette, with a dark header band.
- Clear history from the History tab's menu.
- Notices appear only when something fails; successful actions no longer pop
  up a message.
- Clearer Lithuanian wording throughout Operator and Settings.
- Follow-along Show and auto-show keep a three-verse window that rolls with
  the reading (10 → 10–11 → 10–12 → 11–13).
- Optional **+1 verse** button (Settings → This console): adds the next verse
  to the passage on display, with the same three-verse window.

### Display and OBS
- Smooth fades between passages, the idle picture and the OBS band.
- The OBS overlay's layout is set in Settings and kept by the server: OBS uses
  one fixed link, `/display?mode=obs`, and changes apply live. An old link
  that still carries layout options keeps them; paste the plain link to let
  Settings control them.

### Security
- The operator WebSocket no longer accepts its token as `?token=` in the URL,
  where web servers and proxies log it. Consoles since 0.1.0 already send it
  as the first message; a client that still uses the URL is closed with code
  1008 after 10 seconds. Scripts that open `/ws/operator` must send
  `{"action": "authenticate", "token": "..."}` first.

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
