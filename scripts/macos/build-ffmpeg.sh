#!/usr/bin/env bash
# Build the bundled macOS ffmpeg: LGPL-only, static, with AVFoundation.
#
# Why build instead of downloading a prebuilt: the Windows build pins a BtbN
# LGPL zip, but for macOS arm64 the prebuilts we tested either lack the
# avfoundation input device (serversideup/ffmpeg-lgpl-builds: --disable-autodetect
# without an explicit --enable-avfoundation) or are GPL builds (martin-riedl.de
# bundles x264/x265). This script builds the official source with the configure
# line below and verifies the result before anything can pin it.
#
# Provenance: ffmpeg.org tarball, SHA256 pinned here and the FFmpeg release
# signature (key FCF9 86EA 15E6 E293 A564 4F10 B432 2F04 D676 58D8) verified once
# by hand. LGPL obligations: the source URL + this configure line are the
# "corresponding source" record; COPYING.LGPLv2.1 is copied next to the binary.
#
# Usage: scripts/macos/build-ffmpeg.sh <output-dir>
#
# Output: <output-dir>/ffmpeg (static, macOS >= 14), <output-dir>/COPYING.LGPLv2.1,
# and the evidence files <output-dir>/{buildconf.txt,devices.txt,linkage.txt}.
# Exits non-zero if the tarball hash, the device list or the linkage check fails.
set -euo pipefail

SOURCE_URL="https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz"
SOURCE_SHA256="7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3"
VERSION="8.1.3"
MACOS_MIN="14.0"

DEST="${1:?usage: build-ffmpeg.sh <output-dir>}"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/biblelive-ffmpeg.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

echo "==> source"
curl -fsSL -o "$WORK/ffmpeg-$VERSION.tar.xz" "$SOURCE_URL"
actual="$(shasum -a 256 "$WORK/ffmpeg-$VERSION.tar.xz" | awk '{print $1}')"
if [ "$actual" != "$SOURCE_SHA256" ]; then
  echo "SHA256 mismatch for ffmpeg-$VERSION.tar.xz: expected $SOURCE_SHA256, got $actual"
  exit 1
fi
echo "ffmpeg-$VERSION.tar.xz sha256=$actual"

echo "==> configure"
tar -xf "$WORK/ffmpeg-$VERSION.tar.xz" -C "$WORK"
cd "$WORK/ffmpeg-$VERSION"
export MACOSX_DEPLOYMENT_TARGET="$MACOS_MIN"
# LGPL-only (no --enable-gpl/--enable-nonfree/--enable-version3: no x264, x265 or
# libfdk_aac), self-contained (--disable-autodetect keeps Homebrew paths out) and
# AVFoundation enabled explicitly, because --disable-autodetect drops it otherwise.
./configure \
  --disable-gpl --disable-nonfree --disable-version3 \
  --disable-autodetect \
  --enable-static --disable-shared \
  --disable-doc --disable-debug \
  --disable-programs --enable-ffmpeg \
  --enable-avfoundation --enable-videotoolbox --enable-audiotoolbox \
  --cc=clang \
  --extra-cflags="-mmacosx-version-min=$MACOS_MIN" \
  --extra-ldflags="-mmacosx-version-min=$MACOS_MIN" \
  --pkg-config-flags=--static
make -j"$(sysctl -n hw.ncpu)" ffmpeg
cp ffmpeg "$DEST/ffmpeg"
cp COPYING.LGPLv2.1 "$DEST/COPYING.LGPLv2.1"

echo "==> evidence"
"$DEST/ffmpeg" -hide_banner -buildconf > "$DEST/buildconf.txt"
"$DEST/ffmpeg" -hide_banner -devices > "$DEST/devices.txt" 2>&1
if ! grep -qi avfoundation "$DEST/devices.txt"; then
  echo "AVFOUNDATION: FAIL - the built ffmpeg has no avfoundation input device"
  exit 1
fi
otool -L "$DEST/ffmpeg" | tail -n +2 | awk '{print $1}' | sort -u > "$DEST/linkage.txt"
non_system="$(grep -vE '^(/System/|/usr/lib/)' "$DEST/linkage.txt" || true)"
if [ -n "$non_system" ]; then
  echo "linkage check failed - non-system libraries:"
  echo "$non_system"
  exit 1
fi
echo "binary sha256: $(shasum -a 256 "$DEST/ffmpeg" | awk '{print $1}')"
echo "linkage: system frameworks and /usr/lib only"
echo "AVFOUNDATION: PASS"
