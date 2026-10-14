# Minimum requirements

- **GPU**: NVIDIA GTX 1660 / RTX 2060 or newer (compute capability 7.0+; CTranslate2 float16 needs it).
  6 GB VRAM recommended, 4 GB is the absolute minimum. GTX 10 series and older are not supported.
- **Driver**: NVIDIA 525.60 or newer.
- **RAM**: 16 GB recommended. **CPU**: 4 cores / 8 threads. **OS**: Windows 10 / 11 x64.
- **Streaming with OBS on the same PC**: use the NVENC encoder, not x264 (x264 competes with
  BibleLive for the CPU). The original GTX 1650 (TU117) has the older Volta-generation NVENC with
  lower stream quality; the GTX 1650 Super, GTX 1660 family and RTX 20xx have the Turing NVENC.
  With under 6 GB of VRAM, 1080p30 streaming next to the speech model may stutter.

# Windows portable build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\build-portable.ps1 `
    [-SkipFrontendBuild] [-BibleDb <path>] [-SkipCuda] [-NoZip]
```

Produces `build\BibleLive\` and `dist\BibleLive-portable-<version>.zip` (version from
`backend\pyproject.toml`). Needs Windows 10+ (`tar.exe`), Node/npm (unless
`-SkipFrontendBuild`) and internet access for the first run.

- **Pinned inputs**: `deps.json` (URL + SHA256) for python-build-standalone CPython 3.11 and an
  LGPL FFmpeg build. Downloads are cached in `build\cache\` and verified; a mismatch fails the build.
  To update a pin, change the URL and the SHA256 together.
- **Python packages**: only third-party dependencies (`core` + `audio` + `cuda` extras, derived
  from `backend\pyproject.toml`) are installed, held to `backend\constraints.txt`. BibleLive's own
  code is copied to `app\backend` and put on `sys.path` by `site-packages\biblelive.pth`
  (installing it would break `core.config`'s project-root lookup).
- **Layout**: `python\`, `app\{pyproject.toml,backend,static\frontend,bible_data,data,ffmpeg}`,
  `licenses\`, `BibleLive.cmd` (GUI), `BibleLive-console.cmd` (CLI).
- **Bible DB**: `-BibleDb` (default `bible_data\biblelive.sqlite`); only that file is copied.
- **Smoke test** (fails the build): bundled Python with an empty temp `APPDATA`/`LOCALAPPDATA`:
  `launcher --dry-run`, imports, `ffmpeg -version` + dshow, and `core.server` serving
  `/readyz`, `/display`, `/operator`.
- `-SkipCuda` leaves out the NVIDIA wheels (quick test builds; no GPU transcription).
- The Whisper model is not bundled: `BibleLive-console.cmd --download-model`, then
  `--gpu-self-test` (installer steps). Models live in `%LOCALAPPDATA%\BibleLive\models`.
