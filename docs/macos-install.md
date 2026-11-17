# Installing BibleLive on a Mac (Apple Silicon)

For Macs with an Apple processor (M1 or newer) and macOS 14 or later. Intel Macs are not
supported.

The app is not signed by Apple, so macOS blocks it until you allow it once (step 3). That is
expected for this release; nothing is wrong with the download.

## 1. Before you start

- **Google Chrome** (or Microsoft Edge). BibleLive opens the audience display as a full-screen
  Chrome window on the projector and closes it again when you stop. Without Chrome the display
  opens in your default browser (Safari on a new Mac), and you have to drag it to the
  projector and press ⌃⌘F yourself.
- An internet connection for step 4: the speech models (a download of a few gigabytes) are fetched once.

## 2. Install

1. Download `BibleLive-<version>-macos-arm64.zip` and double-click it. You get `BibleLive.app`.
2. Drag `BibleLive.app` into **Applications**.

## 3. Allow it to open (once)

Open **Terminal** (Applications → Utilities) and paste this line, then press Return:

```
xattr -dr com.apple.quarantine /Applications/BibleLive.app
```

This removes the "downloaded from the internet" mark that makes macOS refuse an unsigned
app. You need to do it again only after installing a new version.

## 4. Download the speech models (once)

In Terminal, paste this line and press Return, then wait until it says it is done:

```
/Applications/BibleLive.app/Contents/MacOS/BibleLive --download-model
```

It downloads the Lithuanian model (Kmynas) and Whisper for English. Without
it, Lithuanian services fall back to Whisper, which finds fewer references; the launcher
window says so.

## 5. First start

1. Open BibleLive from Applications (or Launchpad).
2. Choose the language and the microphone in the launcher window and press **Start**.
3. macOS asks: *"BibleLive" would like to access the microphone.* Click **Allow**.
   If you clicked Don't Allow by mistake: System Settings → Privacy & Security → Microphone,
   turn **BibleLive** on, then stop and start again.
4. The operator page opens in your browser; the display opens on the projector screen.

## 6. Projector

Connect the projector as a **separate screen**, not as a mirror: System Settings → Displays
→ select the projector → **Use as: Extended display**. When the projector only mirrors your
screen, BibleLive reports that there is no projector, so the audience display never covers
the operator page.

## 7. The Lithuanian Bible

The Lithuanian Bible text is not part of the download. If you have `bible_ltb.sqlite`, copy it
into this folder (BibleLive creates it on the first start):

```
~/Library/Application Support/BibleLive/bible_data
```

In Finder: Go → Go to Folder…, paste the path above, and drop the file there. Then stop and
start BibleLive. Do not put it inside `BibleLive.app`; changing the app breaks it.

## Where things are

| What | Where |
|---|---|
| Settings, logs and session recordings | `~/Library/Application Support/BibleLive` (logs in `logs/`) |
| Speech models | `~/Library/Application Support/BibleLive/models` |
| Extra Bibles | `~/Library/Application Support/BibleLive/bible_data` |

## Uninstall

Drag `BibleLive.app` from Applications to the Bin. To remove the settings, logs, models and
Bibles too, delete `~/Library/Application Support/BibleLive`. To remove the microphone
permission: System Settings → Privacy & Security → Microphone.
