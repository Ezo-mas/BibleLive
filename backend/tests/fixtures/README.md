# Test fixtures

## `lt-john-3-16.wav`

A Lithuanian utterance of a Bible reference. The macOS spike
(`.github/workflows/macos-spikes.yml` → `scripts/macos/spike_speech.py`) feeds it to
the pinned recognizer and asserts that the transcript parses to `John 3:16`, so the
macOS CI gate proves *recognition*, not merely that the model loads and accepts
audio. No pytest uses this file today.

- Format: 16 kHz mono PCM16 WAV, 4.18 s, 133,686 bytes
- Spoken: "Jono evangelija trečias skyrius šešiolikta eilutė"
- Converter: **eSpeak NG 1.52.0**, Lithuanian voice (`-v lt`):

  ```sh
  espeak-ng -v lt -s 150 -w lt-john-3-16.wav "Jono evangelija trečias skyrius šešiolikta eilutė"
  ffmpeg -i lt-john-3-16.wav -ar 16000 -ac 1 -c:a pcm_s16le lt-john-3-16.wav.tmp && mv lt-john-3-16.wav.tmp lt-john-3-16.wav
  ```

- SHA256: `e1008ed037266a4ffc1118fbaf2696e139ab7190055ef4e13eff7bf66876a94e`
  (also in `lt-john-3-16.wav.sha256`, which the workflow verifies before use)
- Why synthetic: GitHub's macOS runners ship 177 `say` voices and none of them is
  Lithuanian (the workflow checks on every run), and the gate has to exercise the
  recognizer we ship rather than whatever voice a runner happens to have.
- License: eSpeak NG is GPLv3+, but this file contains no eSpeak NG code or data —
  it is a waveform rendered by the synthesizer, and is distributed with BibleLive
  (MIT) as a test asset. Replace it with a real speaker recording as soon as one is
  legally available, and update the checksum.
- Verified 2026-10-27 with `kmynas-v4-Q8_0.gguf` (transcribe-cpp 0.4.0, CPU
  backend): transcript `'Jono evangelija trečią skyriaus šešioliktąjį būtį.'`,
  parser output `John 3:16`.

A single synthesized utterance is a capability smoke test. Recognition accuracy —
false positives, misses, per-model behaviour — is the detection benchmark's job
(`docs/agent-guides/testing.md`), not this fixture's.
