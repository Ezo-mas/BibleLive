---
paths:
  - "backend/core/verse_parser*.py"
  - "backend/core/verse_text.py"
  - "backend/audio/spelling_maps.py"
  - "backend/audio/ensemble.py"
  - "backend/tests/test_verse_parser*.py"
  - "backend/tests/test_verse_text.py"
---

# Lithuanian parser work

Before changing parser patterns, spelling maps or the agreement rule, read
"Lithuanian parser: lessons from past fixes" in `docs/agent-guides/backend.md`.
The short version:

- Start from a real transcript: replay the service (`session-replay` skill) and
  turn the exact text into a test, with what must stay untouched next to it.
- Check every model's spelling (Kmynas: number words; Canary: digits in its own
  formats). A model's own mishearings go in its map in `audio/spelling_maps.py`.
- Book names are also people and words ("Jonas ir du" is not John 2). A wrong
  book is worse than a miss; prefer silence or chapter-only over a guess.
- Utterance cuts split citations and even numbers; held references
  (`hold_chapter_only`) exist for that. Don't release them early.
- This is a detection-accuracy change: run the `detection-benchmark` skill
  (cached replay, seconds). Judge by TP and medium+high FP.
