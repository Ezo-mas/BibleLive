---
name: detection-benchmark
description: Measure a detection-accuracy change in BibleLive (parser patterns, spelling maps, the two-model agreement rule, STT post-processing, Whisper prompt/hotwords/beam/model, the --stt engines, VAD, utterance segmentation) with a before/after run on the Lithuanian benchmark corpus, and report TP/FN and false positives split by confidence. Use whenever the AGENTS.md detection-accuracy rule applies, before calling such a change done.
---

# Detection benchmark: before and after

AGENTS.md requires this for detection-accuracy changes; unit tests are not
enough. Flag details: `docs/agent-guides/testing.md` ("Detection-accuracy
benchmark") and `benchmarks/README.md`. Before proposing an experiment, read
"Tried and not adopted" in `benchmarks/BASELINE_NOTES.md`.

## 1. Pick the run type

| The change touches | Run | Time |
|---|---|---|
| Parser patterns (`core/verse_parser*.py`), spelling maps (`audio/spelling_maps.py`), agreement (`audio/ensemble.py`) | Replay from the transcript cache (no `--no-cache`) | Seconds |
| STT post-processing (`audio/stt.py`), prompt/hotwords/beam/model, `--stt` models, VAD, segmentation | Fresh transcription: `--no-cache` on the "after" run | ~25–50 min GPU, ~45–75 min CPU per engine |

Prompt, hotwords, models and segmentation *settings* are in the cache key;
*code* is not. That's why upstream code changes need `--no-cache`.

- A fresh run loads the GPU or CPU for up to an hour. Tell the user the
  expected time before starting one, unless they asked for it. Run it in the
  background.
- At most two GPU jobs at once. For CPU GGUF runs, use at most two jobs with
  `--cpu-threads 6` each on the 8-core dev box.
- If `bench_audio/` or the GPU is unavailable, report "benchmark not run".
  Never substitute a smaller or synthetic run and call it a result.
- After a stopped or killed background run, check for leftover workers before
  starting new ones (`pgrep -af run_benchmark.py`; on Windows look for
  `python` processes whose command line contains `run_benchmark.py`). On
  Windows they keep running after their job is reaped.

## 2. Inputs

- **Corpus:** `benchmarks/corpus/lt-sermons.txt`, 14 services and 164 gold
  references. Each line is `<manifest> <record id> [START:END]`.
- **The main checkout supplies the untracked inputs:** `bench_audio/` (the
  user's recordings, read-only), `bible_data/biblelive.sqlite` and
  `bible_ltb.sqlite`, and `results/.bench-cache`. From a worktree, pass the
  main checkout's paths. Without the DBs the run warns and miscounts.
- **Engines:** measure the shipped tier(s) the change affects: `--stt kmynas`
  (CPU tier) and/or `--stt kmynas+canary` (GPU tier). The GGUF engines need
  `--kmynas-model` / `--canary-model` paths. On the Linux dev box these are
  `~/models/kmynas-v4/kmynas-v4/kmynas-v4-Q8_0.gguf` and
  `~/models/canary-gguf/canary-1b-v2-Q8_0.gguf`.
- **English** has no gold corpus. Say an English-only change is unmeasured.

## 3. Run before and after with identical settings

Run "before" on the base commit (for example a worktree at `origin/main`) and
"after" on the change, with the same flags. The cache key includes
`--model` and `--device` even for GGUF engines. The existing caches were made
with `--model large-v3-turbo` plus `--device cpu` (Kmynas tier) or
`--device cuda` (Kmynas+Canary tier). Keep those so "before" replays instead of
re-transcribing.

```bash
MAIN=/home/ezomas/BibleLive          # main checkout: audio, DBs, cache, results
TAG=<change>-before                  # then <change>-after from the changed checkout
STT=kmynas; DEVICE=cpu               # or STT=kmynas+canary; DEVICE=cuda
grep -v '^#' benchmarks/corpus/lt-sermons.txt | while read -r manifest record trange; do
  python benchmarks/run_benchmark.py --manifest benchmarks/corpus/$manifest \
    --records "$record" ${trange:+--t-range $trange} \
    --audio-dir $MAIN/bench_audio --mode offline --context-ttl-s 600 \
    --model large-v3-turbo --device $DEVICE --stt $STT \
    --kmynas-model ~/models/kmynas-v4/kmynas-v4/kmynas-v4-Q8_0.gguf \
    --canary-model ~/models/canary-gguf/canary-1b-v2-Q8_0.gguf \
    --cache-dir $MAIN/results/.bench-cache \
    --bible-db $MAIN/bible_data/biblelive.sqlite \
    --verse-text-db $MAIN/bible_data/bible_ltb.sqlite \
    --out $MAIN/results/review5span-$TAG-$record.json   # add --no-cache for upstream changes
done
```

Use the project venv's `python`; on Windows, use `.venv\Scripts\python.exe`
from Git Bash.

- Start "before" with one short recording (e.g. `10-18-live-lt`). It should
  finish in seconds with `"transcripts_cached": true`. If it starts decoding
  (minutes, `decoder:` lines), the settings don't match the cache key. Stop
  and fix the flags instead of waiting an hour.
- Never overwrite an earlier result file. Pick a new tag instead.
- `~/bl-bench-tools/benchstt.sh` (the user's own scripts, outside git) wraps
  this same loop with `PAR=2` parallelism, when present.

## 4. Compare and report

```bash
python benchmarks/compare_runs.py <change>-before <change>-after --results-dir $MAIN/results
```

The table shows TP / medium+high FP per recording, then one totals line per
tag: TP, FN, FP low, FP med+high, and runtime. The totals line also flags a
cached runtime (not a speed figure), failures, and missing recordings.

Report both runs:
- Totals and the recordings that changed.
- The engine(s) and the commits measured.
- Whether the runs were cached.
- The real-time factor (rtf) for fresh runs only.

Judge mainly by TP and **medium+high** FP. Medium and high references can be
auto-approved onto the screen; low ones always wait for the operator, so a few
more low FPs are not a deal breaker. Fewer wrong references matters as much
as more found ones. For an adopted change, add the result to
`benchmarks/BASELINE_NOTES.md`; for a rejected one, add it to "Tried and not
adopted" there.
