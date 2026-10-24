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
make lint / format    # ruff check / ruff format (line-length 100, target py313)
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
hotwords, beam size, model or compute type, the `--stt` speech models and
their spelling maps, VAD choice or tuning, utterance segmentation, transcript
post-processing, parser patterns — needs a
before/after run on the same corpus and settings. Before proposing one,
check "Tried and not adopted" in `benchmarks/BASELINE_NOTES.md` (rolling
windows, longer caps, vocal isolation, denoise/gate already measured).

Run it with the `detection-benchmark` skill
(`.claude/skills/detection-benchmark/SKILL.md`): it has the exact offline
GGUF command for the shipped engines (`--stt kmynas` and
`--stt kmynas+canary`) over every corpus service, which change needs a fresh
transcription, and how to report the result. Don't hand-build a smaller run
in its place.

- The canonical corpus is 14 Lithuanian services, 164 gold references
  (results dated before 2026-10-19 used the first 13, 158 gold):
  the runs listed in `benchmarks/corpus/lt-sermons.txt` (manifest, record,
  and the `--t-range` of the sermon part where a recording has one), with
  `--mode offline --context-ttl-s 600`. Results so far are in
  `benchmarks/BASELINE_NOTES.md`. There is no English gold corpus yet: say
  so when an English-only change is unmeasured instead of implying it was.
- `--stt kmynas|canary|kmynas+canary` (with `--kmynas-model` /
  `--canary-model` .gguf paths and `--device cpu|cuda`) runs the Lithuanian
  GGUF engines through the same code as live; it needs the `gguf` extra and
  `--mode offline` or `rt` (windowed mode reads Whisper segments). The
  spelling maps are applied when cached transcripts are parsed, so a map
  change replays from the cache in seconds.
- `--mode windowed` cuts fixed 30 s windows and never runs the VAD or the
  segmenter. VAD or segmentation changes need `--mode offline`, which
  replays frames through `audio/segmenter.py` exactly as the live pipeline
  does.
- Report `total_tp` / `total_fp` / `total_fn` and runtime (`rtf`) for both
  runs. Fewer false positives matters as much as more true positives — every
  FP is a wrong verse offered to the operator.
- Like the live pipeline, the benchmark drops references to verses that do
  not exist, using `--bible-db` (default `bible_data/biblelive.sqlite`). The
  DB is not in git, so from a worktree pass the main checkout's path; without
  it the run warns and over-counts false positives. Likewise
  `--verse-text-db` (default `bible_data/bible_ltb.sqlite`) fills a
  Lithuanian chapter-only reference with the verse read aloud, as live;
  without it the run warns and under-counts.
- Name outputs uniquely under `results/`; never overwrite an earlier run.
- Tuning knobs are flags shared with the live CLI (`--beam-size`,
  `--end-of-speech-s`, `--soft-utterance-s`, `--max-utterance-s`,
  `--pre-roll-s`, `--start-voiced-frames`, `--vad silero`, ...), so a
  sweep needs no code edit.
- The transcript cache (`results/.bench-cache`) is keyed on audio hash,
  model/device/compute type, beam size, prompt + hotwords, mode/window
  settings and (offline/rt) the segmentation settings — **not** on any code
  change. The cache holds transcripts, and the parser and spelling maps run
  again on every replay, so a parser-pattern or spelling-map change is
  measured from the cache in seconds. A code change upstream of the
  transcript (STT post-processing in `audio/stt.py`, VAD, segmentation)
  needs `--no-cache` (or a fresh `--cache-dir`) on the "after" run, or it
  silently replays the old transcripts.
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

Tests drive the pages' WebSockets with the shared fakes in
`frontend/src/test/operatorHarness.ts`: `createMockSocket(url?)`, then
`push(socket, message)` and `lastSent(socket)` on the socket the test holds,
and `jsonResponse(body, status)` for `fetch`. Each test file registers its own
socket (one, or a list). `src/test/setup.ts` stubs `fetch` and cleans up.

## Caveats

- **Skips are not passes.** Locally, tests skip when ffmpeg is not on
  PATH (`test_live_path.py`; CI fails instead), there is no display (the
  launcher window tests; CI has none, so they never run there), the KJV
  source is missing, or no Whisper tokenizer is cached. Off Windows, the
  launcher's 1366x768 window-height test and the Windows creation-flags test
  always skip (Windows-only behaviour and font metrics). Run with `-rs` and
  list every skip and its reason in the summary. Tests marked `audio` need
  the optional extras (`faster-whisper`, `webrtcvad`, installed by
  `make install-dev`); without them they fail with import errors rather
  than skip.
- Test markers: `integration`, `audio`, `slow` (pytest config in the root
  `pyproject.toml`, with `--strict-markers`). `slow` marks the three
  real-ffmpeg end-to-end tests in `test_live_path.py` (~6 s each); a quick
  local run can leave them out with `pytest backend/tests -m "not slow"`.
  That is not a full run: CI and the definition of done run everything.
- Every test has a 60 s limit (`pytest-timeout`, `timeout` in the root
  `pyproject.toml`): a hang fails that test with a stack dump instead of
  stalling the run. The `timeout` setting is unknown to pytest without the
  plugin (`--strict-config` errors), so re-run `make install-dev` after
  pulling this change.
- If a check fails with "command not found", run `make install-dev` once
  (permitted) and retry before reporting.
- A test that needs the audio pipeline object without its models or threads
  builds it with `make_processor(**overrides)` from
  `backend/tests/processor_factory.py`, not `BibleLiveAudioProcessor.__new__`
  plus hand-set attributes. A new attribute in `__init__` needs a default
  there too; `test_processor_factory.py` fails and names it otherwise.
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
`architecture.md` invariant 2): `backend/tests/test_websocket_approval.py`
and `backend/tests/test_manual_reference.py`. When editing that flow,
run them explicitly.
