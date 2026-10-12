#!/usr/bin/env bash
# Run the standard CPU speed-and-sanity benchmark and print a summary table.
#
#   ./scripts/bench-cpu.sh                         # small + base, first 15 minutes of 09-13
#   ./scripts/bench-cpu.sh --models "small"        # one model
#   ./scripts/bench-cpu.sh --minutes 30            # longer slice
#   ./scripts/bench-cpu.sh --full                  # the whole recording (~41 min per model)
#   ./scripts/bench-cpu.sh --audio-dir /path/to/bench_audio
#
# Uses non-overlapping 30 s windows (hop = window) so RTF is per audio second
# actually transcribed, and records per-window times to detect throttling.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODELS="small base"
MINUTES=15
FULL=0
AUDIO_DIR="$REPO_ROOT/bench_audio"
COOLDOWN_S=120
RECORD="09-13-live-lt"
AUDIO_FILE="09-13.mp3"
MANIFEST="benchmarks/corpus/manifest-07-05-lt.json"

usage() { sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --models) MODELS="${2:?--models needs a quoted list}"; shift ;;
        --minutes) MINUTES="${2:?--minutes needs a number}"; shift ;;
        --full) FULL=1 ;;
        --audio-dir) AUDIO_DIR="${2:?--audio-dir needs a path}"; shift ;;
        --cooldown-s) COOLDOWN_S="${2:?--cooldown-s needs a number}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown option '$1'" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

case "$MINUTES" in ''|*[!0-9]*) echo "error: --minutes must be a whole number" >&2; exit 2 ;; esac
[ "$MINUTES" -gt 0 ] || { echo "error: --minutes must be > 0" >&2; exit 2; }

PY=".venv/bin/python"
[ -x "$PY" ] || { echo "error: no .venv; run ./scripts/setup-linux.sh first" >&2; exit 1; }
[ -f "$AUDIO_DIR/$AUDIO_FILE" ] || {
    echo "error: $AUDIO_DIR/$AUDIO_FILE not found. Copy it from the desktop (see benchmarks/LAPTOP.md)." >&2
    exit 1; }

export PYTHONPATH="$REPO_ROOT/backend"
OUT="results/laptop-$(uname -n)-$(date +%Y%m%d-%H%M)"
mkdir -p "$OUT"

echo "== Hardware"
"$PY" benchmarks/hwprobe.py --json "$OUT/hwinfo.json" | tee "$OUT/hwinfo.txt"

RANGE_ARGS=()
if [ "$FULL" -eq 0 ]; then
    RANGE_ARGS=(--t-range "0:$((MINUTES * 60))")
    echo
    echo "Slice: first $MINUTES min of $AUDIO_FILE (use --full for the whole file)"
fi

RESULT_FILES=()
FIRST=1
for MODEL in $MODELS; do
    if [ "$FIRST" -eq 0 ] && [ "$COOLDOWN_S" -gt 0 ]; then
        echo
        echo "== Cooling down ${COOLDOWN_S}s so the next model starts from a cold CPU"
        sleep "$COOLDOWN_S"
    fi
    FIRST=0
    echo
    echo "== Model: $MODEL (cpu, int8)"
    # --no-cache: a cache replay has no per-window timings, which is the point of this run.
    "$PY" benchmarks/run_benchmark.py \
        --manifest "$MANIFEST" --audio-dir "$AUDIO_DIR" \
        --mode windowed --window-s 30 --hop-s 30 \
        --records "$RECORD" --device cpu --compute-type int8 --model "$MODEL" \
        --no-cache "${RANGE_ARGS[@]}" \
        --out "$OUT/$MODEL.json" 2>&1 | tee -a "$OUT/run.log"
    RESULT_FILES+=("$OUT/$MODEL.json")
done

echo
echo "== Summary"
"$PY" benchmarks/cpu_report.py "${RESULT_FILES[@]}" | tee "$OUT/summary.txt"
cat <<EOF

Results saved in $OUT/
Read the table like this:
  RTF   seconds of compute per second of audio; the 'last' column is the one that matters.
  A model 'keeps up' when last-third RTF <= 0.5. TP/FP/FN are for the slice only;
  gold references outside it are counted as misses, so compare TP against the desktop.
EOF
