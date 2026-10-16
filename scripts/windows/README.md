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

# Windows installer

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\build-installer.ps1 `
    [-SkipPortableBuild] [-Iscc <ISCC.exe>] [-BibleDb <path>] [-SkipFrontendBuild]
```

Builds `dist\BibleLive-Setup-<version>.exe` from `build\BibleLive\` (runs `build-portable.ps1 -NoZip`
first unless `-SkipPortableBuild`). Needs **Inno Setup 6.5.0 or newer** (`ExecAndLogOutput` 6.3,
`ExecAndCaptureOutput` 6.4, the vendored Lithuanian translation 6.5); ISCC is searched in `-Iscc`,
`$env:ISCC`, then `Program Files (x86)`, `Program Files` and `%LOCALAPPDATA%\Programs`
`\Inno Setup 6`. Compression is `lzma2/ultra64` (~750 MB RAM per compression thread, 2 threads), so
compiling ~2.5 GB takes a while; use `lzma2/max` in `installer\BibleLive.iss` on a small machine.
The result (~1.4 GB) is far below Inno's 4.2 GB single-file limit, so no disk spanning.

What the installer does:

- Per-user (no admin), default `%LOCALAPPDATA%\Programs\BibleLive`; English or Lithuanian.
  Same `AppId` upgrades in place (running app closed; `python\`, `app\backend`, `app\static`,
  `app\ffmpeg`, `licenses\` are wiped before copying).
- Checks `nvidia-smi` (best GPU if several): refuses to install without an NVIDIA GPU, driver 525.60+,
  compute capability 7.0+ (GTX 1660 / RTX 2060 or newer) and 4 GB VRAM; under 6 GB it asks whether to
  continue (OBS streaming may stutter; silent installs just log it). A driver too old to report
  `compute_cap` falls back to the name + driver check (the self-test below still catches problems).
  `/SKIPGPUCHECK` bypasses. `BibleLive-console.cmd --check-gpu` applies the same limits (exit 3).
- After copying files, runs `python -m launcher --download-model` (live progress) and
  `--gpu-self-test`; on failure offers Retry / Continue anyway. `/SKIPMODEL` skips both. Rerun later:
  `BibleLive-console.cmd --download-model` / `--gpu-self-test`.
- Start Menu: BibleLive, BibleLive (console), Logs folder, Uninstall; optional desktop icon.
- Uninstall removes the install folder (ends processes running from it; a browser window is not
  touched) and asks whether to delete settings, logs (`%APPDATA%\BibleLive`) and the model
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
