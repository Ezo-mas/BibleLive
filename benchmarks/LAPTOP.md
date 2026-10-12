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
model    rec             win   RTF  first   last  TP/FP/FN  verdict
small    09-13-live-lt    30  0.41   0.38   0.45   2/1/11   keeps up
```

- **RTF** is seconds of compute per second of audio. Below 1.0 is faster than real
  time. For a live service, aim for **the `last` column at 0.5 or less**, which leaves
  room for silence-free stretches and for OBS or a browser sharing the machine.
- **first vs last** are the first and last third of the run. A `last` well above
  `first` means the CPU is throttling as it heats up (the report flags a 15%+ rise).
  Judge the machine by `last`.
- **TP/FP/FN** are a sanity check only. The slice covers just a few of the 13 gold
  references and the rest count as misses. Non-overlapping windows can also cut a
  reference in half at a window boundary, so TP reads lower here than in the
  overlapped desktop benchmark. Compare TP with the desktop run of the *same*
  command on the same slice (same model, `--hop-s 30`), never with a full-file score.
- The RTF here uses non-overlapping windows, so every audio second is transcribed
  once, which is what production does. Production also skips silence, so it is at
  most this expensive.

## What to send back

The whole `results/laptop-*/` folder: `summary.txt` (the table), `hwinfo.txt`
and `hwinfo.json` (CPU, RAM, AVX, power profile), and the per-model JSON files.

## Known gaps

- `SpeechToText` does not set CTranslate2's `cpu_threads`; we believe the default
  is 4, which may under-use a CPU with more cores. Not yet measured.
- Linux live capture is fixed to `ffmpeg -f pulse -i default`: no device or channel
  selection. This does not affect the benchmark, only a live-mic test later.
