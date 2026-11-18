#!/usr/bin/env bash
# Build build/BibleLive.app (Apple Silicon) and, unless --no-zip, dist/BibleLive-<version>-macos-arm64.zip
# and dist/BibleLive-<version>-macos-arm64.dmg, both made from the one tested app.
#
# The macOS counterpart of scripts/windows/build-portable.ps1: the pinned standalone Python (with
# Tk), the backend's pinned audio+gguf packages, the app, the LGPL ffmpeg from build-ffmpeg.sh, the
# English Bible built from data/kjv2.json, then a smoke test and an ad-hoc signature. Unsigned for
# distribution (docs/macos-arm64.md, owner decision Q10): users clear the quarantine once, as the
# install notes say (docs/macos-install.md).
#
# Usage: scripts/macos/build-app.sh [--skip-frontend-build] [--ffmpeg-dir DIR] [--bible-db FILE] [--no-zip]
#   --no-zip               build and test the app only (no zip, no disk image)
#   --skip-frontend-build  reuse static/frontend (no npm on the machine)
#   --ffmpeg-dir DIR       a folder from build-ffmpeg.sh (default: build it into build/cache/ffmpeg-macos)
#   --bible-db FILE        the English database (default: build it from data/kjv2.json)
#
# Needs the Xcode Command Line Tools (clang, codesign, otool) on the build machine only; the
# app needs nothing installed on the user's Mac. The Lithuanian Bible is never bundled (it is
# not ours to redistribute); see the install notes.
set -euo pipefail

SKIP_FRONTEND=0
FFMPEG_DIR=""
BIBLE_DB=""
NO_ZIP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-frontend-build) SKIP_FRONTEND=1 ;;
    --ffmpeg-dir) FFMPEG_DIR="$2"; shift ;;
    --bible-db) BIBLE_DB="$2"; shift ;;
    --no-zip) NO_ZIP=1 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || { echo "Build on an Apple Silicon Mac." >&2; exit 1; }

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD="$REPO/build"
CACHE="$BUILD/cache"
APP_BUNDLE="$BUILD/BibleLive.app"
CONTENTS="$APP_BUNDLE/Contents"
RES="$CONTENTS/Resources"
APP="$RES/app"
PY="$RES/python/bin/python3"
DIST="$REPO/dist"

# Same pin as the macOS spike (.github/workflows/macos-spikes.yml) and the Windows build's Python.
PY_ASSET="cpython-3.13.16+20261013-aarch64-apple-darwin-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.13.16%2B20261013-aarch64-apple-darwin-install_only.tar.gz"
PY_SHA256="ca0e6a70fb75a0984751bd75493e3de77680aef7fbd0fea9b903c299537b3423"
# The oldest macOS the app is built for; build-ffmpeg.sh uses the same.
MACOS_MIN="14.0"

step() { printf '\n==> %s\n' "$*"; }

VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$REPO/backend/pyproject.toml" | head -1)"
[ -n "$VERSION" ] || { echo "Cannot read the version from backend/pyproject.toml" >&2; exit 1; }
echo "BibleLive version $VERSION"

# ---- inputs -----------------------------------------------------------------------------
step "Checking inputs"
mkdir -p "$CACHE"
if [ ! -f "$CACHE/$PY_ASSET" ] || ! echo "$PY_SHA256  $CACHE/$PY_ASSET" | shasum -a 256 -c - >/dev/null 2>&1; then
  echo "Downloading $PY_URL"
  curl -fsSL -o "$CACHE/$PY_ASSET.part" "$PY_URL"
  mv "$CACHE/$PY_ASSET.part" "$CACHE/$PY_ASSET"
fi
echo "$PY_SHA256  $CACHE/$PY_ASSET" | shasum -a 256 -c -

if [ -z "$FFMPEG_DIR" ]; then
  FFMPEG_DIR="$CACHE/ffmpeg-macos"
  if [ ! -x "$FFMPEG_DIR/ffmpeg" ]; then
    step "Building ffmpeg (scripts/macos/build-ffmpeg.sh)"
    bash "$REPO/scripts/macos/build-ffmpeg.sh" "$FFMPEG_DIR"
  fi
fi
[ -x "$FFMPEG_DIR/ffmpeg" ] && [ -f "$FFMPEG_DIR/COPYING.LGPLv2.1" ] || { echo "No ffmpeg in $FFMPEG_DIR" >&2; exit 1; }

# ---- frontend ---------------------------------------------------------------------------
FRONTEND_DIST="$REPO/static/frontend"
if [ "$SKIP_FRONTEND" = 1 ]; then
  step "Skipping frontend build (reusing static/frontend)"
else
  step "Building the frontend (npm ci + npm run build)"
  (cd "$REPO/frontend" && npm ci --no-audit --no-fund && npm run build)
fi
for f in display.html operator.html; do
  [ -f "$FRONTEND_DIST/$f" ] || { echo "Frontend bundle is incomplete: $FRONTEND_DIST/$f missing" >&2; exit 1; }
done

# ---- fresh bundle -----------------------------------------------------------------------
step "Creating $APP_BUNDLE"
case "$APP_BUNDLE" in "$BUILD"/*) ;; *) echo "Refusing to delete outside build/" >&2; exit 1 ;; esac
rm -rf "$APP_BUNDLE"
mkdir -p "$CONTENTS/MacOS" "$RES"

# ---- Python -----------------------------------------------------------------------------
step "Unpacking Python"
tar -xzf "$CACHE/$PY_ASSET" -C "$RES"
"$PY" -c "import tkinter, sqlite3, ssl; print('python OK, tk', tkinter.TkVersion)"

# ---- third-party dependencies -----------------------------------------------------------
step "Installing third-party dependencies (scripts/macos/requirements-arm64.lock, hash-checked)"
# Every wheel by version and SHA256 (generated from backend/constraints.txt by
# lock_requirements.py; backend/tests/test_macos_lock.py keeps the two in step). pip refuses a
# wheel whose hash differs and any dependency the lock does not list. Wheels only: a source
# build would need a compiler and could pick up Homebrew or /usr/local libraries.
"$PY" -m pip install --disable-pip-version-check --no-warn-script-location --no-cache-dir   --only-binary=:all: --require-hashes -r "$REPO/scripts/macos/requirements-arm64.lock"
"$PY" -m pip check

# ---- app --------------------------------------------------------------------------------
step "Copying the application"
mkdir -p "$APP/backend" "$APP/static" "$APP/bible_data" "$APP/data" "$APP/ffmpeg"
# pyproject.toml marker: core.config finds PROJECT_ROOT = app/ (two levels above backend/core).
cp "$REPO/pyproject.toml" "$APP/pyproject.toml"
for pkg in core audio launcher; do
  rsync -a --exclude __pycache__ --exclude tests --exclude '*.egg-info' --exclude '*.pyc' \
    "$REPO/backend/$pkg/" "$APP/backend/$pkg/"
done
STAMP="$VERSION"
if command -v git >/dev/null && BUILD_ID="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null)"; then
  STAMP="$VERSION+$BUILD_ID"
  [ -z "$(git -C "$REPO" status --porcelain --untracked-files=normal 2>/dev/null)" ] || STAMP="$STAMP.dirty"
fi
printf '%s\n' "$STAMP" > "$APP/backend/core/build-id.txt"
rsync -a "$FRONTEND_DIST/" "$APP/static/frontend/"

# Put app/backend on sys.path; relative .pth entries resolve against site-packages
# (Resources/python/lib/python3.13/site-packages -> Resources/app/backend).
SITE="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
printf '%s\n' "../../../../app/backend" > "$SITE/biblelive.pth"

if [ -n "$BIBLE_DB" ]; then
  cp "$BIBLE_DB" "$APP/bible_data/biblelive.sqlite"
else
  step "Building the English Bible from data/kjv2.json"
  (cd "$APP" && "$PY" -m core.import_bible --translation kjv --source "$REPO/data/kjv2.json" \
    --output "$APP/bible_data/biblelive.sqlite")
fi
[ -s "$APP/bible_data/biblelive.sqlite" ] || { echo "No Bible database" >&2; exit 1; }

# ---- ffmpeg and licenses ----------------------------------------------------------------
step "Adding ffmpeg (LGPL build) and licenses"
cp "$FFMPEG_DIR/ffmpeg" "$APP/ffmpeg/ffmpeg"
LIC="$RES/licenses"
mkdir -p "$LIC"
cp "$FFMPEG_DIR/COPYING.LGPLv2.1" "$LIC/FFmpeg-LICENSE.txt"
cp "$FFMPEG_DIR/buildconf.txt" "$LIC/FFmpeg-buildconf.txt"
cp "$REPO/LICENSE" "$LIC/BibleLive-LICENSE.txt"
cp "$RES/python/lib/python3.13/LICENSE.txt" "$LIC/Python-LICENSE.txt" 2>/dev/null || true
cat > "$LIC/FFmpeg-SOURCE.txt" <<EOF
This app includes an FFmpeg binary built from unmodified source (LGPL, no GPL/nonfree components).
FFmpeg is licensed under the GNU Lesser General Public License; see FFmpeg-LICENSE.txt.

Source code  : https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz (tag n8.1.3, https://git.ffmpeg.org/ffmpeg.git)
Build recipe : scripts/macos/build-ffmpeg.sh in the BibleLive repository; the configure line is in
               FFmpeg-buildconf.txt
EOF
cat > "$LIC/THIRD-PARTY.txt" <<'EOF'
BibleLive bundles third-party software; each package keeps its own license:
 * Python packages: see the *.dist-info/licenses and LICENSE files under python/lib/python3.13/site-packages.
 * Python itself: Python-LICENSE.txt. FFmpeg: FFmpeg-LICENSE.txt, FFmpeg-SOURCE.txt.
 * Speech models are downloaded separately on first setup, not bundled: Whisper large-v3-turbo
   (MIT), Kmynas (Ezomas/kmynas-parakeet-lt-v4-gguf) and Canary-1B-v2 (handy-computer/canary-1b-v2-gguf).
   Read each model card for its license terms.
EOF

# ---- bundle metadata and launcher -------------------------------------------------------
step "Writing Info.plist and the launcher"
cat > "$CONTENTS/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>BibleLive</string>
  <key>CFBundleDisplayName</key><string>BibleLive</string>
  <key>CFBundleIdentifier</key><string>org.biblelive.launcher</string>
  <key>CFBundleExecutable</key><string>BibleLive</string>
  <key>CFBundleIconFile</key><string>biblelive</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$STAMP</string>
  <key>LSMinimumSystemVersion</key><string>$MACOS_MIN</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>BibleLive listens to the sermon to find the Bible references that are read out.</string>
</dict>
</plist>
EOF
plutil -lint "$CONTENTS/Info.plist"
# The icon (scripts/macos/make_icns.py draws it; the same artwork as the Windows icon).
cp "$REPO/scripts/macos/biblelive.icns" "$RES/biblelive.icns"
# The bundle's executable is a small compiled program embedding the bundled Python
# (scripts/macos/launcher.c): macOS attributes microphone access to the process that is the
# bundle's CFBundleExecutable and shows the prompt with the text above; the server and the
# audio pipeline (and its ffmpeg) are its children. A shell script that execs python3 would
# turn the process into the Python binary, which has no Info.plist.
clang -O2 -Wall -mmacosx-version-min="$MACOS_MIN" \
  -I "$RES/python/include/python3.13" -L "$RES/python/lib" -lpython3.13 \
  -Wl,-rpath,@executable_path/../Resources/python/lib \
  -o "$CONTENTS/MacOS/BibleLive" "$REPO/scripts/macos/launcher.c"

# ---- linkage ----------------------------------------------------------------------------
step "Checking linkage (no Homebrew, /usr/local or build paths)"
bad=0
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q 'Mach-O'; then
    # Only the indented library lines: otool's header lines name the file itself (once per
    # architecture in a universal binary), which is under the build folder.
    if otool -L "$f" | grep -E '^[[:space:]]' | grep -E '/opt/homebrew|/usr/local|/Users/|/private/|/tmp/' ; then
      echo "  ^ in $f"
      bad=1
    fi
  fi
done < <(find "$CONTENTS" -type f \( -perm -u+x -o -name '*.so' -o -name '*.dylib' \) -print0)
[ "$bad" = 0 ] || { echo "Linkage check failed" >&2; exit 1; }
echo "linkage OK"

# ---- bytecode and signature -------------------------------------------------------------
# Python must never write into the signed app (that breaks the signature and macOS may then
# ask for the microphone again): compile everything now, and the launcher sets
# PYTHONDONTWRITEBYTECODE for itself and its children. unchecked-hash: the files never
# change inside the bundle, so no timestamp check is needed.
step "Precompiling bytecode"
find "$CONTENTS" -name __pycache__ -type d -prune -exec rm -rf {} +
# -B: the compiling interpreter writes no timestamp .pyc of its own; -f: rewrite every file,
# since compileall's up-to-date check would otherwise keep a timestamp .pyc already there.
"$PY" -B -m compileall -f -q -j 0 --invalidation-mode unchecked-hash \
  -x '/(test|tests|idle_test|lib2to3)/' "$RES/python/lib/python3.13" "$APP/backend"

step "Ad-hoc signature"
# Not a Developer ID signature: every Mach-O on Apple Silicon must carry one, and editing the
# tree (pip, the .pth, the launcher) invalidates the ones it came with.
codesign --force --deep --sign - "$APP_BUNDLE"
codesign --verify --deep --strict "$APP_BUNDLE"
echo "signature OK"

# ---- smoke test -------------------------------------------------------------------------
step "Smoke test (the signed app, empty temporary home)"
export PYTHONDONTWRITEBYTECODE=1
TMPH="$(mktemp -d)"
SERVER_PID=""
cleanup() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; rm -rf "$TMPH"; }
trap cleanup EXIT
(
  cd "$APP"
  export HOME="$TMPH"
  unset PYTHONHOME PYTHONPATH VIRTUAL_ENV
  echo "- launcher --dry-run (through the app's executable)"
  "$CONTENTS/MacOS/BibleLive" --dry-run
  echo "- launcher window (through the app's executable; closes itself)"
  "$CONTENTS/MacOS/BibleLive" --smoke
  echo "- imports"
  "$PY" -c "import faster_whisper, ctranslate2, webrtcvad, transcribe_cpp, tkinter, core.config as c; print('imports OK, PROJECT_ROOT =', c.PROJECT_ROOT)"
  echo "- version stamp"
  "$PY" -c "from core.version import version_string; s = version_string(); print('version:', s); assert not s.startswith('unknown')"
  echo "- platform"
  "$PY" -c "from launcher import platform, settings; p = platform.current(); print(p.__name__, settings.app_data_dir()); assert p.__name__.endswith('macos')"
  echo "- ffmpeg"
  "$APP/ffmpeg/ffmpeg" -hide_banner -devices 2>&1 | grep -q avfoundation || { echo "ffmpeg has no avfoundation" >&2; exit 1; }
)
PORT="$("$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
(cd "$APP" && HOME="$TMPH" BIBLELIVE_PORT="$PORT" BIBLELIVE_HOST=127.0.0.1 \
  BIBLELIVE_SETTINGS_PATH="$TMPH/server_settings.json" \
  exec "$PY" -m core.server > "$TMPH/server.log" 2>&1) &
SERVER_PID=$!
ready=0
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$PORT/readyz" >/dev/null 2>&1; then ready=1; break; fi
  kill -0 "$SERVER_PID" 2>/dev/null || break
  sleep 0.5
done
[ "$ready" = 1 ] || { tail -30 "$TMPH/server.log"; echo "Server did not become ready" >&2; exit 1; }
for page in /display /operator; do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT$page")"
  echo "  GET $page -> $code"
  [ "$code" = 200 ] || exit 1
done
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
SERVER_PID=""
echo "- running it left the app unchanged"
codesign --verify --deep --strict "$APP_BUNDLE"
echo "Smoke test passed."

SIZE_MB="$(du -sm "$APP_BUNDLE" | cut -f1)"
if [ "$NO_ZIP" = 1 ]; then
  echo "Built $APP_BUNDLE (${SIZE_MB} MB). Zip skipped (--no-zip)."
  exit 0
fi
step "Creating the zip and the disk image"
mkdir -p "$DIST"
ZIP="$DIST/BibleLive-$VERSION-macos-arm64.zip"
DMG="$DIST/BibleLive-$VERSION-macos-arm64.dmg"
rm -f "$ZIP" "$DMG"
ditto -c -k --keepParent "$APP_BUNDLE" "$ZIP"
# The disk image: the app and a link to /Applications to drag it onto, in a laid-out window
# (background with an arrow, fixed icon positions, no toolbar or sidebar). Unsigned, like the
# app inside it (ad-hoc signature only): a signed app never ships in an unsigned image or the
# other way round (docs/macos-arm64.md, Phase 3). dmgbuild writes the window layout without
# Finder; it and its two dependencies are hash-pinned build tools, never shipped.
DMG_TOOLS="$BUILD/dmg-tools"
case "$DMG_TOOLS" in "$BUILD"/*) ;; *) echo "Refusing to delete outside build/" >&2; exit 1 ;; esac
rm -rf "$DMG_TOOLS"
mkdir -p "$DMG_TOOLS/bg"
"$PY" -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: --no-deps \
  --require-hashes --target "$DMG_TOOLS/lib" -r "$REPO/scripts/macos/requirements-dmg.lock"
swift "$REPO/scripts/macos/dmg-background.swift" "$DMG_TOOLS/bg/background.png" "$DMG_TOOLS/bg/background@2x.png"
PYTHONPATH="$DMG_TOOLS/lib" "$PY" -B -s -m dmgbuild -s "$REPO/scripts/macos/dmg-settings.py" \
  -D app="$APP_BUNDLE" -D background="$DMG_TOOLS/bg/background.png" \
  -D icon="$REPO/scripts/macos/biblelive.icns" "BibleLive" "$DMG"
rm -rf "$DMG_TOOLS"
echo "- the disk image holds the same, still valid app, and its window layout"
MNT="$(mktemp -d)"
hdiutil attach -quiet -nobrowse -readonly -mountpoint "$MNT" "$DMG"
dmg_ok=1
codesign --verify --deep --strict "$MNT/BibleLive.app" || dmg_ok=0
diff -rq "$APP_BUNDLE" "$MNT/BibleLive.app" >/dev/null || { echo "The disk image's app differs from $APP_BUNDLE" >&2; dmg_ok=0; }
[ "$(readlink "$MNT/Applications")" = /Applications ] || dmg_ok=0
[ -f "$MNT/.DS_Store" ] && [ -f "$MNT/.background.tiff" ] || { echo "The disk image has no window layout" >&2; dmg_ok=0; }
# Detach can fail with "Resource busy" for a moment after the checks; retry, then force.
for _ in 1 2 3; do hdiutil detach -quiet "$MNT" 2>/dev/null && break; sleep 2; done
[ ! -d "$MNT/BibleLive.app" ] || hdiutil detach -quiet -force "$MNT" || true
rmdir "$MNT" 2>/dev/null || true
[ "$dmg_ok" = 1 ] || { rm -f "$DMG"; echo "Disk image check failed" >&2; exit 1; }
echo "Built $ZIP ($(du -m "$ZIP" | cut -f1) MB) and $DMG ($(du -m "$DMG" | cut -f1) MB) from ${SIZE_MB} MB."
