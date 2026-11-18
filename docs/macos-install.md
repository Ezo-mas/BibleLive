# Installing BibleLive on a Mac (Apple Silicon)

For Macs with an Apple processor (M1 or newer) and macOS 14 or later. Intel Macs are not
supported.

The app is not signed by Apple, so macOS blocks it until you allow it once in System
Settings (step 3). That is expected for this release; nothing is wrong with the download.

## 1. Before you start

- **Google Chrome** (or Microsoft Edge). BibleLive opens the audience display as a full-screen
  Chrome window on the projector and closes it again when you stop. Without Chrome the display
  opens in your default browser (Safari on a new Mac), and you have to drag it to the
  projector and press ⌃⌘F yourself.
- An internet connection for step 4: the speech models (a download of a few gigabytes) are fetched once.

## 2. Install

Each release on GitHub has the same app in two forms; take either.

- **Disk image:** download `BibleLive-<version>-macos-arm64.dmg` and double-click it. A plain
  Finder window opens with two items, **BibleLive** and **Applications**; drag **BibleLive**
  onto **Applications**. Then eject the disk image
  (the ⏏ button next to "BibleLive" in the Finder sidebar) and delete the `.dmg`.
- **Zip:** download `BibleLive-<version>-macos-arm64.zip` and double-click it. You get
  `BibleLive.app`; drag it into **Applications**.

Do not start BibleLive from inside the disk image or from Downloads: run it from
Applications. To update, quit BibleLive, install the new version the same way (replace the
old app when Finder asks) and do step 3 again.

## 3. Allow it to open (once)

1. Open BibleLive from Applications. macOS says *"BibleLive" Not Opened* (Apple could not
   verify it) and does not open it. Close the message.
2. Open **System Settings → Privacy & Security** and scroll down to the **Security** section,
   which says BibleLive was blocked. Click **Open Anyway**, click **Open Anyway** again in the
   message that follows, and confirm with your password or Touch ID.
3. BibleLive opens. From now on it opens normally; repeat this only after installing a new
   version.

(Instead of steps 1–2 you can run `xattr -dr com.apple.quarantine /Applications/BibleLive.app`
in Terminal, which removes the "downloaded from the internet" mark.)

After a new version, macOS may ask for the microphone again. If it does not ask and nothing
is detected, reset the permission in Terminal and start again (it will ask once more):

```
tccutil reset Microphone org.biblelive.launcher
```

## 4. Download the speech models (once)

In Terminal, paste this line and press Return, then wait until it says it is done:

```
/Applications/BibleLive.app/Contents/MacOS/BibleLive --download-model
```

It downloads the Lithuanian model (Kmynas) and Whisper for English, and checks every file
against the version BibleLive was tested with (a damaged or changed file is deleted and the
download reports an error; run it again). Without it, Lithuanian services fall back to
Whisper, which finds fewer references; the launcher window says so. If the launcher says a
model is "an older download, not the checked version", run the line above again.

## 5. First start

1. Open BibleLive from Applications (or Launchpad).
2. Choose the language and the microphone in the launcher window and press **Start**.
3. The operator page opens in your browser and the display on the projector screen.
4. The first time, macOS then asks: *"BibleLive" would like to access the microphone.* It
   comes up after the browser page, so look for it over (or behind) the browser and click
   **Allow**; nothing is heard until you do. (It may instead ask when the launcher window
   opens, since the window lists the microphones.) If you clicked Don't Allow by mistake:
   System Settings → Privacy & Security → Microphone, turn **BibleLive** on, then stop and
   start again.

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

1. Quit BibleLive (⌘Q in the launcher window; that also stops the display and the
   listening).
2. Drag `BibleLive.app` from Applications to the Bin.
3. To remove the settings, logs, session recordings, models (several gigabytes) and Bibles
   too, delete `~/Library/Application Support/BibleLive` (Finder: Go → Go to Folder…).
4. To remove the microphone permission, run this in Terminal:

```
tccutil reset Microphone org.biblelive.launcher
```

If you allowed incoming network connections (network mode), macOS keeps that entry under
System Settings → Network → Firewall → Options; remove it there.
