---
name: session-replay
description: Replay a recorded BibleLive service (a session log .jsonl from the launcher's logs/sessions) through the current parser and diff it against the references sent live. Use when investigating a wrong or missed reference from a real service, and before and after changing the parser, spelling maps or agreement rule, to see the effect on real services in seconds.
---

# Replay a live service through the parser

Session logs hold every utterance of a service: each speech model's
transcript and the references sent live. Next to each `.jsonl` there is
usually a `.wav`, the whole service as 16 kHz mono. An utterance's
`start_s`/`end_s` are positions in that WAV.

## Where the logs are

| Machine | Path |
|---|---|
| Windows (launcher) | `%APPDATA%\BibleLive\logs\sessions` |
| Elsewhere (launcher) | `~/.biblelive/logs/sessions` |
| Linux dev box (copies) | `~/Documents/biblelive-transfer/app-logs/sessions` |

A service run with `--session-log-dir DIR` logs to that `DIR` instead.

## Replay

```bash
python benchmarks/session_replay.py <session.jsonl> [--diff-only] \
  --bible-db $MAIN/bible_data/biblelive.sqlite \
  --verse-text-db $MAIN/bible_data/bible_ltb.sqlite
```

Use the project venv's Python. The DB flags are needed only from a worktree
(`MAIN` = the main checkout; `bible_data/` is not in git).

It uses the pipeline's own code: per-model spelling maps, the two-model
agreement rule, duplicate collapse, low-confidence marking, and context reset
on a language switch. Output:

- One line per utterance with references:
  - `same` when the current code finds what was sent live.
  - `DIFF ... | live: ...` when it doesn't.
- Lines for references flushed by a language switch or still held at the end.
  These are never in the log, so there is nothing to compare them with.
- A summary: `N utterance(s) with references; M differ from what was sent live`.

On unchanged code, a DIFF usually means the parser changed after the service.
Check `git log --since <service date> -- backend/core/verse_parser*.py`. The
other possible cause: context expiry is measured on recording time, where live
uses the wall clock.

## Before and after a parser change

1. Replay each relevant session on the base code. Save the output.
2. Apply the change and replay again.
3. Diff the two outputs. Report every utterance that changed, and say for
   each whether it's a fix or a new wrong reference. Also run the
   `detection-benchmark` skill: a few services are a smoke test, not a
   measurement.

## Investigating one reference

- Find the utterance by its `time` or `start_s` in the `.jsonl`. Read every
  model's transcript there, and the utterance before it (context and held
  chapter-only references carry over).
- To hear what was said, cut the clip from the WAV:
  `ffmpeg -ss <start_s> -to <end_s> -i <session>.wav clip.wav`.
- The log can't replay what never reached it: `utterance_dropped` events,
  and the speech recognition itself. A wrong transcript is a speech-model
  problem, not a parser one.

A logged service can become a benchmark recording: annotate gold references
against the WAV (format in `benchmarks/README.md`). Recording 14
(`10-18-live-lt`) was added that way.
