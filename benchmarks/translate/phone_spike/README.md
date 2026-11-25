# P0.5 phone audio spike

Does translated audio keep playing on a phone in a pocket? Three players, two
codecs, one iPhone and one Android phone, on mobile data through a Cloudflare
quick tunnel. Brief: `docs/translate/PHASE0.md`, P0.5.

| Player | What it is |
| --- | --- |
| `element` | one `<audio>` element, `src` switched to the next clip when one ends |
| `mse` | one `<audio>` element fed by MediaSource (ManagedMediaSource on iPhone), clips appended in sequence |
| `webaudio` | Web Audio: each clip decoded and scheduled on an AudioContext |

Codecs: MP3 and AAC (64 kbit/s mono), encoded with the bundled ffmpeg. The
MediaSource player uses AAC in fragmented MP4.

## Run it

```
# once: encode the clips (project venv)
python benchmarks/translate/phone_spike/phone_spike_clips.py --ffmpeg build/BibleLive/app/ffmpeg/ffmpeg.exe
# server (prints the key) and tunnel (prints the https address)
python -u benchmarks/translate/phone_spike/phone_spike_server.py --run-name phones-1
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8093
# afterwards
python benchmarks/translate/phone_spike/phone_spike_summary.py phones-1
```

The phone opens `https://<name>.trycloudflare.com/?k=<key>`. Every page event
goes to `results/translate/phone_spike/logs/<run>/events.jsonl`, so the log
shows what played while the screen was off; the sheet is for what only a person
can tell (heard it, resumed, sounded right).

## Test on each phone

Turn Wi-Fi off first (mobile data), volume up. Use the same name for the log
each time ("iphone" or "android"). Reload the page between runs.

**Stage 1, quick screen (about 3 min per run, 6 runs):** for each player and
codec: start, listen 30 s with the screen on, lock the screen for 2 minutes,
unlock. Note whether it played on while locked.

**Stage 2, full test (the best one or two from stage 1):**

1. Headphones (wired or Bluetooth), screen locked, 10 minutes.
2. While playing: Wi-Fi on, wait 30 s, Wi-Fi off again. Does it carry on?
3. While playing: play something in another app for 20 s (YouTube, music),
   then come back. Does ours resume by itself, after a tap, or not at all?
4. iPhone only: silent switch on. Still audible?
