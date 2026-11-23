# Backlog

Open features, verification checks, maintenance, and experiments. Confirmed
defects are tracked in [`TECH_DEBT.md`](TECH_DEBT.md).

## Follow-ups

### macOS: second screen, and the network prompt naming Python
From the first Mac test (M1, 2026-11-17, PR #94). The display opening full
screen on an extended display is untested (no USB-C adapter on hand); check it
with the projector set to "Extended display", including a screen placed left of
or above the main one (negative coordinates). The network prompt names
`python3.13`, not BibleLive (the server is the bundled Python, and the firewall
asks per binary); the launcher and `docs/macos-install.md` now explain it.
Running the server under a BibleLive-named binary would remove it. Also
unchecked: whether macOS asks for the microphone again after a re-downloaded
update.

## Launcher follow-ups

- **Live wave from real levels.** The launcher's live-state wave (`--wave-live`)
  is a canned animation. Feed it the pipeline's real input levels, as the
  console's meter does since [PR #116](https://github.com/Ezo-mas/BibleLive/pull/116).
- **Identify screens.** A button by Projector that briefly shows a large number
  on every monitor, so the operator picks the right one.
- **Speech checks in Advanced.** Show whether the Lithuanian GGUF models are
  actually present (today a missing file silently falls back to Whisper), and a
  "Run GPU self-test" button reusing `--gpu-self-test`.
- **Diagnostics bundle.** One click zips the logs and settings, with the
  operator token removed, for support.
- **Regenerate the operator token.** Revokes access for devices it was shared
  with. Needs a security review.
- **Port check before Start.** When the port is taken, name the program using it.
- **Console quick actions.** A one-click Copy logs button beside Log files, and a
  "Paused — jump to latest" indicator when the operator scrolls the console up
  (auto-scroll already stops then, but nothing says so).
- **Remember the window between launches.** Save the window's position and size
  per view, the Advanced tab (Speech / Network / Launch) and the console filters
  (source, minimum level) in `launcher.json`, beside the Simple/Advanced choice that
  is already saved, so a repeat Sunday setup opens as it was left.
- **Launcher on the shared tokens.** [PR #115](https://github.com/Ezo-mas/BibleLive/pull/115)
  moved the console to the launcher's warm palette, but
  `frontend/src/launcher/launcher.css` still holds its own copy of the colours as
  raw hex values. Point it at `frontend/src/styles/tokens.css` so there is one set
  of tokens.

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
