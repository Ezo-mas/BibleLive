#!/usr/bin/env bash
# Set up BibleLive's Python environment on Linux (written for Fedora; dnf only).
#
#   ./scripts/setup-linux.sh                # install system packages + venv + deps
#   ./scripts/setup-linux.sh --no-system    # skip dnf; just the venv and pip install
#   ./scripts/setup-linux.sh --python /usr/bin/python3.12
#
# Safe to re-run. System packages need sudo; everything else stays inside .venv.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# The audio dependencies (ctranslate2, faster-whisper, webrtcvad) are only proven
# on these interpreters (the CI matrix). A new distro default such as 3.14 may
# have no wheels yet, so never trust a bare `python3`.
SUPPORTED_MINORS=(13 12 11)

SYSTEM=1
PYTHON_BIN=""

usage() { sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --no-system) SYSTEM=0 ;;
        --python)
            [ $# -ge 2 ] || { echo "error: --python needs a path" >&2; exit 2; }
            PYTHON_BIN="$2"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown option '$1'" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# This script installs packages and rewrites .venv; never run it on another OS
# (on Windows the existing .venv/Scripts would be clobbered).
[ "$(uname -s)" = "Linux" ] || { echo "error: setup-linux.sh is for Linux only" >&2; exit 1; }

say() { printf '\n==> %s\n' "$*"; }

python_minor() { "$1" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null; }

is_supported_python() {
    local v
    v="$(python_minor "$1")" || return 1
    [ "$v" -ge 311 ] && [ "$v" -le 313 ]
}

find_python() {
    if [ -n "$PYTHON_BIN" ]; then
        is_supported_python "$PYTHON_BIN" || {
            echo "error: $PYTHON_BIN is not Python 3.11-3.13" >&2; return 1; }
        echo "$PYTHON_BIN"; return 0
    fi
    local m
    for m in "${SUPPORTED_MINORS[@]}"; do
        if command -v "python3.$m" >/dev/null 2>&1; then
            echo "python3.$m"; return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------- system packages
if [ "$SYSTEM" -eq 1 ]; then
    say "Checking system packages"
    PKGS=()
    command -v gcc >/dev/null 2>&1 || PKGS+=(gcc)
    command -v g++ >/dev/null 2>&1 || PKGS+=(gcc-c++)   # webrtcvad builds from source on Linux
    command -v ffmpeg >/dev/null 2>&1 || PKGS+=(ffmpeg-free)

    if [ "${#PKGS[@]}" -gt 0 ]; then
        echo "Needed: ${PKGS[*]}"
        if command -v dnf >/dev/null 2>&1; then
            sudo dnf install -y "${PKGS[@]}"
        else
            echo "dnf not found. Install these with your package manager, then re-run with --no-system:"
            echo "  ${PKGS[*]}"
            exit 1
        fi
    else
        echo "Base packages present."
    fi

    # Python: use a supported interpreter if one exists; otherwise install the
    # newest the distro offers (package names assumed python3.N / python3.N-devel).
    if ! PY="$(find_python)"; then
        command -v dnf >/dev/null 2>&1 || {
            echo "error: no Python 3.11-3.13 and no dnf; install one and re-run" >&2; exit 1; }
        for m in "${SUPPORTED_MINORS[@]}"; do
            echo "Trying python3.$m ..."
            if sudo dnf install -y "python3.$m" "python3.$m-devel"; then break; fi
        done
        PY="$(find_python)" || {
            echo "error: could not install a supported Python (3.11-3.13) with dnf." >&2; exit 1; }
    fi
    # Headers for the chosen interpreter (webrtcvad compiles against them).
    HDR_DIR="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["include"])')"
    if [ ! -f "$HDR_DIR/Python.h" ] && command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y "$(basename "$PY")-devel"
    fi
fi

# ---------------------------------------------------------------- python venv
say "Selecting Python"
if ! PY="$(find_python)"; then
    echo "error: no Python 3.11-3.13 found (this machine's python3 is $(python3 --version 2>&1))." >&2
    echo "On Fedora: sudo dnf install python3.13 python3.13-devel   (or pass --python PATH)" >&2
    exit 1
fi
echo "Using $PY ($("$PY" --version))"

if [ -x .venv/bin/python ] && ! is_supported_python .venv/bin/python; then
    echo "error: existing .venv uses an unsupported Python ($(.venv/bin/python --version))." >&2
    echo "Remove it (rm -rf .venv) and re-run." >&2
    exit 1
fi
[ -x .venv/bin/python ] || "$PY" -m venv .venv

say "Installing Python dependencies (backend + audio extras)"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install -e "./backend[dev,audio]" -c backend/constraints.txt

# ---------------------------------------------------------------- verify
say "Verifying imports"
.venv/bin/python - <<'PYEOF'
import importlib

missing = []
for name in ("ctranslate2", "faster_whisper", "webrtcvad", "numpy", "fastapi"):
    try:
        importlib.import_module(name)
    except Exception as exc:  # report every failure, not just the first
        missing.append(f"{name}: {exc}")
if missing:
    raise SystemExit("import check FAILED:\n  " + "\n  ".join(missing))
print("imports ok")
PYEOF

say "Hardware and environment report"
.venv/bin/python benchmarks/hwprobe.py

cat <<'EOF'

Setup finished. Before benchmarking:
  - plug in AC power and use a performance profile:  powerprofilesctl set performance
  - copy the benchmark audio into ./bench_audio  (see benchmarks/LAPTOP.md)
  - then run:  ./scripts/bench-cpu.sh
EOF
