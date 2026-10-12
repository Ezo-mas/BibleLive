# Running the CPU benchmark on a Linux laptop

Goal: find out whether a modest laptop CPU can run the `small` speech-to-text
model fast enough for a live service, and whether it slows down as it heats up.
Written for Fedora; **the scripts have not yet been run on Fedora**, so expect
to fix small things on the first run and note what you changed.

## What you need

- The repo (a plain `git clone`; `benchmarks/`, the gold files and the manifest are tracked).
- One audio file, **`09-13.mp3`** (about 56 MB), which is deliberately not in git.
- Internet on the first run: Whisper model weights download from Hugging Face
  (`small` is a few hundred MB, `base` less). To run offline, copy
  `~/.cache/huggingface` from the desktop instead.

## 1. Get the code and the audio onto the laptop

```bash
git clone https://github.com/Ezo-mas/BibleLive.git && cd BibleLive
git switch feat/linux-bench-bootstrap      # until it is merged
mkdir -p bench_audio
# from the desktop (or use a USB stick):
scp user@desktop:/path/to/BibleLive/bench_audio/09-13.mp3 bench_audio/
```

The runner checks the file's sha256 against the manifest, so a damaged or
different copy stops with a clear error instead of giving wrong numbers.

## 2. Set up (once)

```bash
./scripts/setup-linux.sh
```

It installs `gcc`, `gcc-c++`, `ffmpeg-free` and a supported Python with `dnf`
(asks for sudo), makes `.venv`, installs the backend + audio extras, checks the
imports, and prints a hardware report. **Python 3.11 to 3.13 only**: Fedora 44's
default `python3` is probably 3.14, which has no proven wheels, so the script
installs and uses `python3.13` (or 3.12 / 3.11) explicitly.

If it prints a "Problems" line (no AVX2, no MP3 decoder, ...) read it before going on.

## 3. Prepare the machine

A benchmark on a battery-saving laptop measures the power settings, not the CPU.

- Plug in AC power.
- GNOME: Settings -> Power -> Power Mode -> **Performance** (or `powerprofilesctl set performance`).
- Close the browser and other heavy apps. Do not use the laptop during the run.
- Let it cool to idle temperature first.

## 4. Run

```bash
./scripts/bench-cpu.sh                 # small + base, first 15 minutes of 09-13
./scripts/bench-cpu.sh --models small  # just small
./scripts/bench-cpu.sh --full          # whole recording (~41 min per model)
```

Each model is followed by a 2-minute cool-down so the next one starts cold
(`--cooldown-s 0` to disable). Results land in `results/laptop-<host>-<date>/`.

## 5. Read the result

```
model    rec             win   RTF   p90   max  TP/FP/FN  verdict
small    09-13-live-lt    30  0.57  1.00  1.19    0/0/13  marginal: average RTF above 0.5, little headroom
base     09-13-live-lt    30  0.15  0.23  0.24    0/0/13  keeps up
clock: 3500 MHz early -> 3500 MHz late (+0%), peak 70 C
```

- **RTF** is seconds of compute per second of audio, averaged over the run. Below 1.0
  is faster than real time. For a live service aim for **0.5 or less**, which leaves
  room for OBS or a browser sharing the machine. Between 0.5 and 1.0 it keeps up on
  average but has little headroom.
- **p90 / max** are the slowest windows. Time per window follows how much is said (a
  pause can take 3 s, a dense passage 36 s for the same 30 s of audio), so a p90 above
  1.0 means dense speech runs slower than real time: a queue builds, then drains in
  the pauses. That shows up as the reference appearing late.
- **clock** is the CPU clock speed early versus late in the runs, sampled every 5 s.
  A drop of more than 10% is flagged as throttling. Per-window times cannot show this
  because they depend on the content, which is why the clock is sampled separately.
  (It is Linux-only: it reads `/sys/.../scaling_cur_freq`; if there is no cpufreq the
  line says there are too few samples.)
- **TP/FP/FN** are a sanity check only. The slice covers just a few of the 13 gold
  references and the rest count as misses. Non-overlapping windows can also cut a
  reference in half at a window boundary, so TP reads lower here than in the
  overlapped desktop benchmark. Compare TP with the desktop run of the *same*
  command on the same slice (same model, `--hop-s 30`), never with a full-file score.
- The RTF here uses non-overlapping windows, so every audio second is transcribed
  once, which is what production does. Production also skips silence, so it is at
  most this expensive.

### Desktop reference (Ryzen 7 5800X, 8 cores, quiet machine, same slice and command)

| model | RTF | p90 | max |
|---|---|---|---|
| small | 0.57 | 1.00 | 1.19 |
| base | 0.15 | 0.23 | 0.24 |

So even a fast desktop CPU runs small only marginally in real time with the current
decode settings. Expect a 2014-era laptop to be clearly slower; `base`, or cheaper
decoding (below), is the more realistic CPU option. Timings vary a lot between runs
(the same window took 36 s in one run and 18 s in another), so repeat before relying
on a single figure.

## What to send back

The whole `results/laptop-*/` folder: `summary.txt` (the table), `clock.csv`,
`hwinfo.txt` and `hwinfo.json` (CPU, RAM, AVX, power profile), and the per-model JSON files.

## If small is too slow on CPU

Speed, measured on one slow, dense 30 s window on the desktop CPU (single runs each,
so indicative only):

| decode setting | time | words |
|---|---|---|
| as benchmarked (beam 5, long prompt) | 18.4 s | 62 |
| no `initial_prompt` | 11.1 s | 64 |
| `beam_size=2` | 11.1 s | 61 |
| `beam_size=1` (greedy) | 9.1 s | 61 |
| no repeat guards | 29.8 s | 178 (decoder loops) |

Beam search and the long prompt are the expensive parts; the repeat guards are cheap
and must stay.

Accuracy, from the GPU benchmark over both recordings (25 gold references, windowed
mode, 2026-10-12). Beam width did not change how many references were found:

| model | beam 5 (current) | beam 2 | beam 1 (greedy) |
|---|---|---|---|
| small | 5 hits / 5 wrong | 5 / 3 | 5 / 5 |
| medium | 11 hits / 9 wrong | 11 / 11 | 11 / 5 |

Each variant swaps about one reference for another, and the wrong-extras count does not
move in one direction, so treat the differences as noise: there is no measured accuracy
cost to greedy decoding on this corpus, but 25 references cannot rule out a small one.
The prompt length has not been tested for accuracy (removing only its example sentence
made small worse). The CPU speedup of beam 1 is from a single window; re-measure it
before relying on it.

## Known gaps

- `SpeechToText` does not set CTranslate2's `cpu_threads`. Measured on the desktop:
  the default behaves like 4 threads and 8 or 16 threads are no faster (16 is slower),
  so thread count is not the lever.
- Linux live capture is fixed to `ffmpeg -f pulse -i default`: no device or channel
  selection. This does not affect the benchmark, only a live-mic test later.
