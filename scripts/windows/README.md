# Minimum requirements

- **Lithuanian speech on the processor (Kmynas)**: no graphics card needed. 4 cores or more
  (8th-gen Intel Core i5 / Ryzen 1st gen class). The speech model uses the physical cores minus one
  (at most 6), leaving a core for capture, the server and the display. Measured: Kmynas on 6 threads
  of a Ryzen 5800X at a real-time factor of 0.06 (0.09 on 3 threads), about 1.1 GB RAM. A 4-core PC
  is not measured yet; an older 4-core CPU should still keep up, with references a little later.
- **Lithuanian speech on an NVIDIA GPU (Kmynas + Canary, the most accurate)**: GTX 1660 / RTX 2060 or
  newer (compute capability 7.0+). 6 GB VRAM recommended; Kmynas and Canary together use about 3.2 GB (they run through the
  bundled Vulkan backend, so no CUDA toolkit is needed for them).
  GTX 10 series and older are not supported.
- **Driver**: NVIDIA 525.60 or newer for the NVIDIA options.
- **English (Whisper)**: a GPU runs `large-v3-turbo`; on a processor only `small` is practical.
- **RAM**: 16 GB recommended. **OS**: Windows 10 / 11 x64.
- **Streaming with OBS on the same PC**: use the NVENC encoder, not x264 (x264 competes with
  BibleLive for the CPU). The original GTX 1650 (TU117) has the older Volta-generation NVENC with
  lower stream quality; the GTX 1650 Super, GTX 1660 family and RTX 20xx have the Turing NVENC.
  With under 6 GB of VRAM, 1080p30 streaming next to the speech model may stutter.

# Windows portable build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\build-portable.ps1 `
    [-SkipFrontendBuild] [-BibleDb <path>] [-ExtraBibleDb <path>,...] [-SkipCuda]
    [-ReuseDependencies] [-NoZip]
```

Produces `build\BibleLive\` and `dist\BibleLive-portable-<version>.zip` (version from
`backend\pyproject.toml`). Needs Windows 10+ (`tar.exe`), Node/npm (unless
`-SkipFrontendBuild`) and internet access for the first run.

- **Pinned inputs**: `deps.json` (URL + SHA256) for python-build-standalone CPython 3.13 and an
  LGPL FFmpeg build. Downloads are cached in `build\cache\` and verified; a mismatch fails the build.
  To update a pin, change the URL and the SHA256 together.
- **Python packages**: only third-party dependencies (`core` + `audio` + `gguf` extras, plus `cuda`
  unless `-SkipCuda`; derived from `backend\pyproject.toml`) are installed, held to
  `backend\constraints.txt`. `gguf` is transcribe.cpp (Kmynas and Canary on the processor, or on any
  GPU through its bundled Vulkan backend). BibleLive's own code is copied to `app\backend` and put on
  `sys.path` by `site-packages\biblelive.pth` (installing it would break `core.config`'s project-root
  lookup).
- **Layout**: `python\`, `app\{pyproject.toml,backend,static\frontend,bible_data,data,ffmpeg}`,
  `licenses\`, `BibleLive.cmd` (GUI), `BibleLive-console.cmd` (CLI).
- **Bible DB**: `-BibleDb` (default `bible_data\biblelive.sqlite`) is copied as
  `biblelive.sqlite`; `-ExtraBibleDb` adds more translations beside it under their own names.
  By default `bible_data\bible_ltb.sqlite` is shipped when it exists (the build warns when it
  does not): Lithuanian services need it, since the server switches the display to that
  translation for Lithuanian and the pipeline picks the verse being read aloud from it. Without
  it the display shows the English text. `-ExtraBibleDb @()` ships no extra translation.
- **Smoke test** (fails the build): bundled Python with an empty temp `APPDATA`/`LOCALAPPDATA`:
  `launcher --dry-run`, imports (including `transcribe_cpp`), `ffmpeg -version` + dshow, and
  `core.server` serving `/readyz`, `/display`, `/operator`.
- `-SkipCuda` leaves out the NVIDIA CUDA wheels (quick test builds). Whisper cannot run on the GPU
  then; Kmynas and Canary still do, through Vulkan.
- `-ReuseDependencies` keeps `build\BibleLive\python` (the bundled Python and its packages, the
  slow part) from the previous build when its inputs are unchanged: the Python archive, the
  extras, `backend\constraints.txt` and `backend\pyproject.toml` (fingerprint in
  `python\.biblelive-deps`). The app, ffmpeg and licenses are rebuilt and the smoke test still
  runs. If any input changed it says so and does a full build.
- **Speech models are not bundled.** They are downloaded into `%LOCALAPPDATA%\BibleLive\models`
  (installer steps, or by hand):
  - processor: `BibleLive-console.cmd --download-model --speech-engine kmynas --device cpu --save --no-whisper`
  - NVIDIA GPU: `BibleLive-console.cmd --download-model --speech-engine kmynas+canary --device auto --save`

  `--download-model` fetches the Lithuanian models the settings need, then Whisper for English unless
  `--no-whisper` is given. `--gpu-self-test` loads them and transcribes 1 s of silence.
  If a Lithuanian model is missing, the launcher warns and uses Whisper for Lithuanian instead.

# Windows installer

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\build-installer.ps1 `
    [-SkipPortableBuild] [-Iscc <ISCC.exe>] [-BibleDb <path>] [-ExtraBibleDb <path>,...]
    [-SkipFrontendBuild] [-ReuseDependencies] [-Fast]
```

Builds `dist\BibleLive-Setup-<version>.exe` from `build\BibleLive\` (runs `build-portable.ps1 -NoZip`
first unless `-SkipPortableBuild`). Needs **Inno Setup 6.5.0 or newer** (`ExecAndLogOutput` 6.3,
`ExecAndCaptureOutput` 6.4, the vendored Lithuanian translation 6.5); ISCC is searched in `-Iscc`,
`$env:ISCC`, then `Program Files (x86)`, `Program Files` and `%LOCALAPPDATA%\Programs`
`\Inno Setup 6`. Compression is `lzma2/ultra64` (~750 MB RAM per compression thread, 2 threads), so
compiling ~2.5 GB takes a while; use `lzma2/max` in `installer\BibleLive.iss` on a small machine.
`-Fast` compiles with `lzma2/fast` into `BibleLive-Setup-<version>-fast.exe` (~1.4 GB instead of
~1 GB; the separate name keeps a test build from being handed out by mistake).

Build times on a Ryzen 7 5800X: full build ~12 min (~7 min bundle + ~5 min compression);
`-ReuseDependencies -Fast` after an app-only change ~1.5 min. Quick test loop:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\build-installer.ps1 -SkipFrontendBuild `
    -ReuseDependencies -Fast
```
The result (~1 GB) is far below Inno's 4.2 GB single-file limit, so no disk spanning. The speech
models are downloaded at install time, not packed into the exe.

What the installer does:

- Per-user (no admin), default `%LOCALAPPDATA%\Programs\BibleLive`; English or Lithuanian.
  Same `AppId` upgrades in place (running app closed; `python\`, `app\backend`, `app\static`,
  `app\ffmpeg`, `licenses\` are wiped before copying).
- **Speech page** (after the tasks page), three options:
  - *Lithuanian on CPU, Kmynas* (no graphics card needed): Kmynas on the processor, `--device cpu`,
    and `--model small` for the English model (only downloaded if chosen below).
  - *Lithuanian on NVIDIA GPU, Kmynas + Canary* (most accurate): both models on the GPU, Whisper
    `large-v3-turbo` for English (always downloaded).
  - *Whisper only (previous setup)*: the earlier single-model setup, `large-v3-turbo` on the GPU.

  The page is preselected: the NVIDIA option when a qualifying card is found, otherwise the CPU option.
  The choice is saved to `%APPDATA%\BibleLive\launcher.json` and can be changed under Advanced in the
  launcher.
- **English model** (CPU option only): a second page asks whether to also download Whisper `small`
  (about 0.5 GB, slow on a processor). The default is No.
- **NVIDIA checks run only for an NVIDIA option** (the second and third). `nvidia-smi` (best GPU if
  several): refuses to continue without an NVIDIA GPU, driver 525.60+, compute capability 7.0+
  (GTX 1660 / RTX 2060 or newer) and 4 GB VRAM; under 6 GB it asks whether to continue (OBS streaming
  may stutter; silent installs just log it). A driver too old to report `compute_cap` falls back to the
  name + driver check (the speech test below still catches problems). `/SKIPGPUCHECK` bypasses.
  `BibleLive-console.cmd --check-gpu` applies the same limits (exit 3). The CPU option needs no GPU.
- After copying files, runs `python -m launcher --download-model ... --save` (live progress; the
  arguments match the chosen option, and `--no-whisper` is passed when the English model was not
  chosen), then `--gpu-self-test`, which loads the chosen Lithuanian models (and Whisper if it is
  downloaded) and transcribes silence. On failure it offers Retry / Continue anyway. `/SKIPMODEL`
  skips the speech settings, downloads and test. Rerun later: `BibleLive-console.cmd --download-model`
  and `--gpu-self-test` (both read the saved settings). Setup asks for about 4 GB free on the install
  drive (one figure for every option); the speech models themselves go to `%LOCALAPPDATA%` (about
  0.7 GB for the processor option, up to about 3.5 GB with Whisper and both NVIDIA models).
- Silent install switches: `/SPEECH=cpu|gpu|whisper` picks the option (default by hardware),
  `/WITHENGLISH` with the CPU option also downloads Whisper `small`, `/SKIPGPUCHECK` (testing only),
  `/SKIPMODEL`.
- Start Menu: BibleLive, BibleLive (console), Logs folder, Uninstall; optional desktop icon.
- **Windows Firewall**: Setup adds no rule and asks for no administrator approval. The launcher
  adds the `BibleLive` rule (Private networks only) when you press *Allow through Windows
  Firewall* in its network panel, with one administrator prompt (see SECURITY.md). Uninstall
  removes that rule if it exists, with an administrator prompt; a declined prompt is logged and
  the uninstall carries on. A silent uninstall (`/SILENT` or `/VERYSILENT`) cannot show that
  prompt, so it leaves the rule and logs that. Remove it from an administrator command prompt
  with `netsh advfirewall firewall delete rule name=BibleLive`.
- Uninstall removes the install folder (ends processes running from it; a browser window is not
  touched) and asks whether to delete settings, logs (`%APPDATA%\BibleLive`) and the speech models
  (`%LOCALAPPDATA%\BibleLive`). Default No; silent uninstall keeps them unless `/DELETEUSERDATA`.
- `installer\Lithuanian.isl` is the official translation from jrsoftware/issrc (commit
  `4b7b877a12d90dc36fd30393035d6b8cbf70c5c7`, Files/Languages/Lithuanian.isl, unmodified, Inno Setup
  license); it is not in the 6.x releases. `installer\make_icon.py` regenerates `biblelive.ico`.

## CI

`.github/workflows/windows-installer.yml` (manual run or `v*` tag, `windows-latest`): builds
`bible_data\biblelive.sqlite` from the tracked `data\kjv2.json` (`core.import_bible`), runs
`build-portable.ps1`, then `build-installer.ps1 -SkipPortableBuild` (Inno Setup is preinstalled on
the runner image; the workflow installs it with Chocolatey if missing). The exe is uploaded as an
artifact and, on tags, attached to the release (GitHub allows 2 GiB per release asset; the exe is
~1.4 GB). Optional signing with `signtool` runs only when the secrets `WINDOWS_CERT_PFX` (base64 PFX)
and `WINDOWS_CERT_PASSWORD` exist; otherwise the exe is unsigned and SmartScreen will warn.
