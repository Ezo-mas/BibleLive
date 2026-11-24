# Backlog

Open features, verification checks, maintenance, and experiments. Confirmed
defects are tracked in [`TECH_DEBT.md`](TECH_DEBT.md).

## Follow-ups

### macOS: second screen, and the network prompt naming Python
From the first Mac test (M1, 2026-11-17, PR #94). The display opening full
screen on an extended display is untested (no USB-C adapter on hand); check it
with the projector set to "Extended display", including a screen placed left of
or above the main one (negative coordinates), and that the launcher reopens
where it was left on that screen (PR #137, only tried on the built-in one). The network prompt names
`python3.13`, not BibleLive (the server is the bundled Python, and the firewall
asks per binary); the launcher and `docs/macos-install.md` now explain it.
Running the server under a BibleLive-named binary would remove it. Also
unchecked: whether macOS asks for the microphone again after a re-downloaded
update.

## Maintenance

### npm 10.9 cannot install vitest 4.1.11 or later
Size: small. The frontend dev tools moved to vite 6 and vitest 5 to clear
`npm audit` (2026-10-24). npm 10.9.9 crashes installing vitest 4.1.11 and later
(`Cannot read properties of null (reading 'edgesOut')` in its peer loader,
reproduced in an empty project); 5.0.5 installs. `package.json` allows
`^5.0.5`, so only `package-lock.json` holds it at 5.0.5: before an
`npm update` that moves vitest, check that it installs, or upgrade npm first.

## Experiments

### More recordings for the parser
Every new service recording goes into the corpus (`benchmarks/corpus/`): the
held-out set first, as M5 describes. Needs services recorded and copied into
`bench_audio/`.

### Fewer low-confidence false positives in two-model mode
Two-model mode (Kmynas + Canary) has 15 low-confidence false positives on the
fitted corpus against 3 for Kmynas alone. A replay on 2026-11-17 found where they
come from: a reference only Canary found gives 6 correct and 9 wrong ones, one
only Kmynas found gives 20 correct and 6 wrong. Dropping Canary-only references
takes the tier back to Kmynas alone (167 TP). Dropping only single-model
chapter-only references ("Psalms 25", no verse) removes 3 wrong ones and no
correct one, on the same corpus it was found on, so it needs held-out services
before it is adopted. Any change here is in `audio/ensemble.py`, which only the
two-model tier runs, so the Kmynas CPU tier cannot change.


### Quoted verses with no reference spoken
Catch a famous verse quoted without its reference ("Nes Dievas taip pamilo
pasaulį ...") by matching the transcript against the LTB text, e.g. SQLite FTS5
or trigrams over `bible_ltb.sqlite`. Risks: preachers paraphrase or quote other
translations, STT garbles words, and many nearby verses share wording (533 pairs
within 3 verses; see the follow-along notes in `benchmarks/BASELINE_NOTES.md`).
So it needs a high bar (a long run of consecutive matching words) and would only
ever be a low-confidence suggestion in Pending, never auto-shown. The corpus gold
marks cited references only, so scoring it needs quotes annotated on a few
services first. First step, no pipeline code: search the cached transcripts of
the 15 corpus services against LTB offline, count the hits and judge a sample by
hand. Build it only if real quotes clearly outnumber the junk.
