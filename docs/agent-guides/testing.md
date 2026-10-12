# Testing guide — canonical verification commands and caveats

One source of truth for how to verify a change. The *required* checks for
"done" are scoped in the root `AGENTS.md` ("Definition of done") by area;
this guide is the full command reference and the caveats around them.

## Backend (from repo root; Makefile exports PYTHONPATH=backend)

```bash
make install-dev      # pip install -e "./backend[dev,audio]" — permitted, once
make test             # pytest backend/tests -v
make check            # ruff lint + format-check + tests in one go
make typecheck        # mypy core audio (separate from check)
make lint / format    # ruff check / ruff format (line-length 100, target py311)
make dev              # uvicorn --factory core.server.app:create_app --reload, port 8000
```

Python tests need `backend/` on the import path — the Makefile and root
`pyproject.toml` (`pythonpath = ["backend", "."]`) handle it; bare
`python -m ...` invocations need `export PYTHONPATH="$PWD/backend"`.

On Windows there is no `make`: use `.venv\Scripts\python.exe -m ...` with
`$env:PYTHONPATH = "$PWD/backend"`. The `check` + `typecheck` equivalents:

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pytest backend/tests -q
.venv/Scripts/python.exe -m mypy backend/core backend/audio --install-types --non-interactive
```

## Detection-accuracy benchmark

Unit tests prove the code does what it says, not that it detects more
references. Any change that can move detection quality — Whisper prompt,
hotwords, beam size, model or compute type, VAD choice or tuning, utterance
segmentation, transcript post-processing, parser patterns — needs a
before/after run on the same corpus and settings:

```bash
python benchmarks/run_benchmark.py \
    --manifest benchmarks/corpus/manifest-07-05-lt.json \
    --audio-dir bench_audio --mode windowed --model small --device cuda \
    --out results/<change>-before.json      # then -after.json with the change
```

- The canonical corpus is two Lithuanian services, 25 gold references.
  There is no English gold corpus yet: say so when an English-only change
  is unmeasured instead of implying it was.
- `--mode windowed` cuts fixed 30 s windows and never runs the VAD or the
  segmenter. VAD or segmentation changes need `--mode offline`, which
  replays frames through `audio/segmenter.py` exactly as the live pipeline
  does.
- Report `total_tp` / `total_fp` / `total_fn` and runtime (`rtf`) for both
  runs. Fewer false positives matters as much as more true positives — every
  FP is a wrong verse offered to the operator.
- Name outputs uniquely under `results/`; never overwrite an earlier run.
- Tuning knobs are flags shared with the live CLI (`--beam-size`,
  `--end-of-speech-s`, `--soft-utterance-s`, `--max-utterance-s`, ...), so a
  sweep needs no code edit.
- The transcript cache (`results/.bench-cache`) is keyed on audio hash,
  model/device/compute type, beam size, prompt + hotwords, mode/window
  settings and (offline/rt) the segmentation settings — **not** on any code
  change (STT post-processing, parser, VAD). For those, pass `--no-cache`
  (or a fresh `--cache-dir`) to the "after" run, or it silently replays the
  old transcripts.
- `bench_audio/` is the user's recordings: read-only. If it or CUDA is
  unavailable, report the benchmark as not run — don't substitute a
  smaller or synthetic run and call it a result.
- `benchmarks/README.md` covers modes, the matching rule, and gold format.

## Frontend (from `frontend/`)

```bash
npm install           # or make frontend-install from the repo root
npm test -- --run     # vitest (plain `npm test` stays in watch mode)
npm run lint          # eslint, --max-warnings 0
npm run typecheck     # tsc --noEmit
npm run screenshots   # visual regression (needs the stack running)
```

## Caveats

- **Audio-test skips are not passes.** Tests marked `audio` need the
  optional extras (`faster-whisper`, `webrtcvad`) and skip gracefully
  without them. If a summary says a suite is green while audio tests
  skipped, count what was actually covered and say so plainly.
- Test markers: `integration`, `audio` (pytest config in the root
  `pyproject.toml`).
- If a check fails with "command not found", run `make install-dev` once
  (permitted) and retry before reporting.
- Tests for transcript post-processing (`audio/stt.py` corrections, parser
  inputs) must use realistic Whisper output: already-formatted `John 3:16`,
  multi-digit chapters and verses (`Psalms 119:105`), merged digits
  (`John 316`). A correction regex once rewrote `John 3:16` to `John 3:1-6`
  while its single-digit tests stayed green.
- The language-control server tests bind real local ports; on Windows a
  test logging from the control server failed once in five full runs
  (2026-10-12) and did not reproduce. Rerun once before blaming an
  unrelated change, and report the flake either way.
- A check failure is reported, never suppressed. Don't skip tests,
  weaken assertions, or silence errors to make verification pass; fix
  the cause instead.

## Invariant coverage pointers

Two suites directly cover the load-bearing approval invariants (see
`architecture.md` invariant 3): `backend/tests/test_websocket_approval.py`
and `backend/tests/test_manual_reference.py`. When editing that flow,
run them explicitly.
