<#
.SYNOPSIS
  Builds the portable BibleLive folder (build\BibleLive) and zip (dist\BibleLive-portable-<version>.zip).

.DESCRIPTION
  Windows PowerShell 5.1 compatible. Downloads (into build\cache, SHA256-verified against
  scripts\windows\deps.json): python-build-standalone CPython 3.11 and an LGPL FFmpeg build.
  Installs only third-party dependencies into the bundled Python (pinned by backend\constraints.txt);
  the biblelive source stays in app\backend and is put on sys.path by a .pth file.

.PARAMETER SkipFrontendBuild  Reuse an existing static\frontend bundle (no npm ci / build).
.PARAMETER BibleDb            Bible SQLite file to ship. Default: <repo>\bible_data\biblelive.sqlite.
.PARAMETER ExtraBibleDb       More translation files to ship beside it, keeping their names.
                              Default: <repo>\bible_data\bible_ltb.sqlite when it exists (the
                              Lithuanian text the server switches to for Lithuanian, and that the
                              pipeline reads verses from); a warning otherwise. -ExtraBibleDb @()
                              ships none.
.PARAMETER SkipCuda           Omit the cuda extra (quick test builds; GPU transcription will not work).
.PARAMETER ReuseDependencies  Keep build\BibleLive\python (the bundled Python and its packages) from the
                              last build when nothing it depends on changed (Python archive, extras,
                              backend\constraints.txt, backend\pyproject.toml); only the app, ffmpeg and
                              licenses are rebuilt. Falls back to a full build when anything differs.
.PARAMETER NoZip              Do not create the zip.
#>
[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [string]$BibleDb,
    [string[]]$ExtraBibleDb = @(),
    [switch]$SkipCuda,
    [switch]$ReuseDependencies,
    [switch]$NoZip
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.IO.Compression.FileSystem

$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Build = Join-Path $Repo 'build'
$Cache = Join-Path $Build 'cache'
$Out = Join-Path $Build 'BibleLive'
$Dist = Join-Path $Repo 'dist'
$Deps = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'deps.json') | ConvertFrom-Json
if (-not $BibleDb) { $BibleDb = Join-Path $Repo 'bible_data\biblelive.sqlite' }
if (-not $PSBoundParameters.ContainsKey('ExtraBibleDb')) {
    # Lithuanian services need it (display text and the verse read aloud); without it the
    # display shows the English text. Not in git, so a CI build may not have it.
    $ltb = Join-Path $Repo 'bible_data\bible_ltb.sqlite'
    if (Test-Path -PathType Leaf $ltb) {
        $ExtraBibleDb = @($ltb)
    } else {
        Write-Warning ("$ltb not found: this build has no Lithuanian Bible text, so Lithuanian " +
            'services will show the English text. Pass -ExtraBibleDb <path to bible_ltb.sqlite>.')
    }
}

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }

# Run a native command; fail the build on a non-zero exit code. stderr is not redirected,
# so PowerShell 5.1 does not turn it into error records.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
}

function Get-Verified {
    param($Dep)
    New-Item -ItemType Directory -Force $Cache | Out-Null
    $path = Join-Path $Cache $Dep.file
    if (Test-Path $path) {
        $have = (Get-FileHash -Algorithm SHA256 $path).Hash.ToLower()
        if ($have -ne $Dep.sha256.ToLower()) {
            Write-Host "Cached $($Dep.file) has the wrong SHA256; downloading again."
            Remove-Item -Force $path
        }
    }
    if (-not (Test-Path $path)) {
        Write-Host "Downloading $($Dep.url)"
        $tmp = "$path.part"
        Invoke-WebRequest -UseBasicParsing -Uri $Dep.url -OutFile $tmp
        Move-Item -Force $tmp $path
    }
    $have = (Get-FileHash -Algorithm SHA256 $path).Hash.ToLower()
    if ($have -ne $Dep.sha256.ToLower()) {
        throw "SHA256 mismatch for $($Dep.file): expected $($Dep.sha256), got $have"
    }
    Write-Host "OK  $($Dep.file)  sha256=$have"
    return $path
}

function Get-FolderSizeMB($path) {
    $sum = (Get-ChildItem -Recurse -Force -File $path | Measure-Object -Property Length -Sum).Sum
    return [math]::Round($sum / 1MB, 1)
}

# ---- version ----------------------------------------------------------------------------
$pyproject = Join-Path $Repo 'backend\pyproject.toml'
$m = Select-String -Path $pyproject -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $m) { throw "Cannot read the version from $pyproject" }
$Version = $m.Matches[0].Groups[1].Value
Write-Host "BibleLive version $Version"

# ---- inputs -----------------------------------------------------------------------------
Step 'Checking inputs'
if (-not (Test-Path -PathType Leaf $BibleDb)) {
    throw "Bible database not found: $BibleDb (pass -BibleDb <path to biblelive.sqlite>)"
}
if ((Get-Item $BibleDb).Length -le 0) { throw "Bible database is empty: $BibleDb" }
foreach ($db in $ExtraBibleDb) {
    if (-not (Test-Path -PathType Leaf $db)) { throw "Extra Bible database not found: $db" }
    if ((Get-Item $db).Length -le 0) { throw "Extra Bible database is empty: $db" }
}
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
if (-not (Test-Path $tar)) { throw 'tar.exe (Windows 10 1803+) is required to unpack the Python archive.' }
if (-not $SkipFrontendBuild -and -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'npm is required to build the frontend (or pass -SkipFrontendBuild with an existing static\frontend).'
}

$pyArchive = Get-Verified $Deps.python
$ffArchive = Get-Verified $Deps.ffmpeg

# ---- frontend ---------------------------------------------------------------------------
$FrontendDist = Join-Path $Repo 'static\frontend'
if ($SkipFrontendBuild) {
    Step 'Skipping frontend build (reusing static\frontend)'
} else {
    Step 'Building the frontend (npm ci + npm run build)'
    Push-Location (Join-Path $Repo 'frontend')
    try {
        Invoke-Native 'npm.cmd' @('ci', '--no-audit', '--no-fund')
        Invoke-Native 'npm.cmd' @('run', 'build')
    } finally { Pop-Location }
}
foreach ($f in 'display.html', 'operator.html') {
    if (-not (Test-Path (Join-Path $FrontendDist $f))) {
        throw "Frontend bundle is incomplete: $FrontendDist\$f missing"
    }
}

# ---- fresh output folder ----------------------------------------------------------------
# gguf (transcribe.cpp: Kmynas / Canary on the processor, or Vulkan on any GPU) is always bundled;
# cuda adds the NVIDIA runtime DLLs for Whisper.
$extras = 'audio,gguf'
if (-not $SkipCuda) { $extras = 'audio,gguf,cuda' }
# What the bundled python\ folder is built from: if none of it changed, -ReuseDependencies keeps it.
$fingerprintText = @(
    $Deps.python.sha256, $extras,
    (Get-FileHash -Algorithm SHA256 (Join-Path $Repo 'backend\constraints.txt')).Hash,
    (Get-FileHash -Algorithm SHA256 $pyproject).Hash
) -join '|'
$fingerprintFile = Join-Path $Out 'python\.biblelive-deps'
$reuse = $false
if ($ReuseDependencies) {
    if ((Test-Path (Join-Path $Out 'python\python.exe')) -and (Test-Path $fingerprintFile) -and
        ((Get-Content -Raw $fingerprintFile).Trim() -eq $fingerprintText)) {
        $reuse = $true
    } else {
        Write-Host 'Dependencies changed (or no previous build): doing a full build.' -ForegroundColor Yellow
    }
}
if (-not $Out.StartsWith($Build, [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to delete outside build\' }
if ($reuse) {
    Step "Refreshing $Out (reusing the bundled Python and its packages)"
    Get-ChildItem -Force $Out | Where-Object { $_.Name -ne 'python' } | Remove-Item -Recurse -Force
} else {
    Step "Creating $Out"
    if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
    New-Item -ItemType Directory -Force $Out | Out-Null
}
$Py = Join-Path $Out 'python\python.exe'

if (-not $reuse) {
# ---- Python -----------------------------------------------------------------------------
Step 'Unpacking Python'
$unpack = Join-Path $Cache 'python-unpack'
if (Test-Path $unpack) { Remove-Item -Recurse -Force $unpack }
New-Item -ItemType Directory -Force $unpack | Out-Null
Invoke-Native $tar @('-xzf', $pyArchive, '-C', $unpack)
Move-Item (Join-Path $unpack 'python') (Join-Path $Out 'python')
Remove-Item -Recurse -Force $unpack
Invoke-Native $Py @('-c', "import tkinter, sqlite3, ssl; print('python OK, tk', tkinter.TkVersion)")

# ---- third-party dependencies -----------------------------------------------------------
Step 'Installing third-party dependencies (pinned by backend\constraints.txt)'
$genPy = Join-Path $Cache 'gen_requirements.py'
Set-Content -Encoding ASCII $genPy @'
import sys
import tomllib

with open(sys.argv[1], "rb") as fh:
    project = tomllib.load(fh)["project"]
reqs = list(project["dependencies"])
for extra in sys.argv[2].split(","):
    reqs += project["optional-dependencies"][extra]
print("\n".join(reqs))
'@
$reqFile = Join-Path $Cache 'requirements-bundle.txt'
& $Py $genPy $pyproject $extras | Set-Content -Encoding ASCII $reqFile
if ($LASTEXITCODE -ne 0) { throw 'Could not derive the requirement list from pyproject.toml' }
Write-Host (Get-Content $reqFile | Out-String)
Invoke-Native $Py @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-warn-script-location',
    '--no-cache-dir', '-r', $reqFile, '-c', (Join-Path $Repo 'backend\constraints.txt'))
Set-Content -Encoding ASCII $fingerprintFile $fingerprintText
} # end of: not reusing the bundled Python

# ---- app --------------------------------------------------------------------------------
Step 'Copying the application'
$App = Join-Path $Out 'app'
New-Item -ItemType Directory -Force $App | Out-Null
# pyproject.toml marker: core.config finds PROJECT_ROOT = app\ because it sits two levels above backend\core.
Copy-Item (Join-Path $Repo 'pyproject.toml') (Join-Path $App 'pyproject.toml')
# Window icon for the launcher GUI (launcher.gui looks for app\biblelive.ico).
Copy-Item (Join-Path $PSScriptRoot 'installer\biblelive.ico') (Join-Path $App 'biblelive.ico')
foreach ($pkg in 'core', 'audio', 'launcher') {
    $src = Join-Path $Repo "backend\$pkg"
    $dst = Join-Path $App "backend\$pkg"
    & robocopy.exe $src $dst /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ tests '*.egg-info' /XF '*.pyc' '*.pyo' | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $pkg (exit $LASTEXITCODE)" }
}
$global:LASTEXITCODE = 0
New-Item -ItemType Directory -Force (Join-Path $App 'static') | Out-Null
& robocopy.exe $FrontendDist (Join-Path $App 'static\frontend') /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed for the frontend bundle (exit $LASTEXITCODE)" }
$global:LASTEXITCODE = 0
New-Item -ItemType Directory -Force (Join-Path $App 'bible_data') | Out-Null
Copy-Item $BibleDb (Join-Path $App 'bible_data\biblelive.sqlite')
foreach ($db in $ExtraBibleDb) {
    Copy-Item $db (Join-Path $App ('bible_data\' + (Split-Path -Leaf $db)))
}
# The server needs no files from data\ at runtime (verses come from the SQLite DB; the launcher
# keeps server settings under %APPDATA%). The folder is only the default base for relative paths.
New-Item -ItemType Directory -Force (Join-Path $App 'data') | Out-Null

# Put app\backend on sys.path. Relative .pth entries resolve against the site-packages dir.
$site = Join-Path $Out 'python\Lib\site-packages'
Set-Content -Encoding ASCII (Join-Path $site 'biblelive.pth') '..\..\..\app\backend'

# ---- ffmpeg -----------------------------------------------------------------------------
Step 'Extracting ffmpeg (LGPL build)'
$ffDir = Join-Path $App 'ffmpeg'
$lic = Join-Path $Out 'licenses'
New-Item -ItemType Directory -Force $ffDir, $lic | Out-Null
$zip = [IO.Compression.ZipFile]::OpenRead($ffArchive)
try {
    $inner = $Deps.ffmpeg.innerDir
    foreach ($pair in @(@("$inner/bin/ffmpeg.exe", (Join-Path $ffDir 'ffmpeg.exe')),
                        @("$inner/LICENSE.txt", (Join-Path $lic 'FFmpeg-LICENSE.txt')))) {
        $entry = $zip.GetEntry($pair[0])
        if (-not $entry) { throw "Entry $($pair[0]) not found in $($Deps.ffmpeg.file)" }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $pair[1], $true)
    }
} finally { $zip.Dispose() }

# ---- licenses ---------------------------------------------------------------------------
Step 'Collecting licenses'
Copy-Item (Join-Path $Repo 'LICENSE') (Join-Path $lic 'BibleLive-LICENSE.txt')
Copy-Item (Join-Path $Out 'python\LICENSE.txt') (Join-Path $lic 'Python-LICENSE.txt')
Set-Content -Encoding UTF8 (Join-Path $lic 'FFmpeg-SOURCE.txt') @"
This package includes an unmodified FFmpeg binary (LGPL build, no GPL/nonfree components).
FFmpeg is licensed under the GNU Lesser General Public License; see FFmpeg-LICENSE.txt.

Binary build : $($Deps.ffmpeg.url)
Build recipe : $($Deps.ffmpeg.buildScriptsUrl)
Source code  : $($Deps.ffmpeg.sourceUrl)  (tag n8.1.3, https://git.ffmpeg.org/ffmpeg.git)
"@
Set-Content -Encoding UTF8 (Join-Path $lic 'THIRD-PARTY.txt') @'
BibleLive bundles third-party software; each package keeps its own license:
 * Python packages: see the *.dist-info\licenses and LICENSE files under python\Lib\site-packages.
 * NVIDIA cuBLAS / cuDNN / NVRTC runtime DLLs (python\Lib\site-packages\nvidia): NVIDIA's license terms
   (see the license files inside each nvidia-* package directory).
 * Python itself: Python-LICENSE.txt. FFmpeg: FFmpeg-LICENSE.txt, FFmpeg-SOURCE.txt.
 * transcribe.cpp (transcribe-cpp, transcribe-cpp-native): see their *.dist-info\licenses files.
 * Speech models are downloaded separately on first setup, not bundled: Whisper large-v3-turbo
   (MIT), Kmynas (Ezomas/kmynas-parakeet-lt-v4-gguf) and Canary-1B-v2 (handy-computer/canary-1b-v2-gguf).
   Read each model card for its license terms.
'@

# ---- launchers --------------------------------------------------------------------------
Step 'Writing launchers'
Set-Content -Encoding ASCII (Join-Path $Out 'BibleLive.cmd') @'
@echo off
rem Starts the BibleLive launcher window (no console).
start "" /D "%~dp0app" "%~dp0python\pythonw.exe" -m launcher %*
'@
Set-Content -Encoding ASCII (Join-Path $Out 'BibleLive-console.cmd') @'
@echo off
rem Command-line launcher (e.g. --dry-run, --list-mics, --check-gpu, --download-model, --gpu-self-test).
pushd "%~dp0app"
"%~dp0python\python.exe" -m launcher %*
set "BL_RC=%ERRORLEVEL%"
popd
exit /b %BL_RC%
'@

# ---- smoke test -------------------------------------------------------------------------
Step 'Smoke test (bundled Python, empty temp APPDATA)'
$tmp = Join-Path $env:TEMP ("biblelive-smoke-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$null = New-Item -ItemType Directory -Force (Join-Path $tmp 'Roaming'), (Join-Path $tmp 'Local')
$saved = @{}
foreach ($k in 'APPDATA', 'LOCALAPPDATA', 'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV',
               'BIBLELIVE_PORT', 'BIBLELIVE_HOST', 'BIBLELIVE_SETTINGS_PATH') {
    $saved[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
}
$server = $null
try {
    $env:APPDATA = Join-Path $tmp 'Roaming'
    $env:LOCALAPPDATA = Join-Path $tmp 'Local'
    foreach ($k in 'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV') { Remove-Item "Env:\$k" -ErrorAction SilentlyContinue }

    Push-Location $App
    try {
        Write-Host '- launcher --dry-run'
        Invoke-Native $Py @('-m', 'launcher', '--dry-run')
        Write-Host '- imports'
        Invoke-Native $Py @('-c', "import faster_whisper, ctranslate2, webrtcvad, transcribe_cpp, tkinter, core.config as c; print('imports OK, PROJECT_ROOT =', c.PROJECT_ROOT)")
        Write-Host '- ffmpeg'
        Invoke-Native (Join-Path $ffDir 'ffmpeg.exe') @('-hide_banner', '-version')
    } finally { Pop-Location }

    $ffExe = Join-Path $ffDir 'ffmpeg.exe'
    $devices = & cmd.exe /c "`"$ffExe`" -hide_banner -devices 2>&1" | Out-String
    $global:LASTEXITCODE = 0
    if ($devices -notmatch 'dshow') { throw 'The bundled ffmpeg has no dshow (DirectShow) input support.' }
    Write-Host '- ffmpeg supports dshow'

    Write-Host '- console launcher via cmd'
    Invoke-Native (Join-Path $env:SystemRoot 'System32\cmd.exe') @('/c', (Join-Path $Out 'BibleLive-console.cmd'), '--dry-run')

    Write-Host '- server'
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop()
    $env:BIBLELIVE_PORT = "$port"
    $env:BIBLELIVE_HOST = '127.0.0.1'
    $env:BIBLELIVE_SETTINGS_PATH = Join-Path $tmp 'server_settings.json'
    $server = Start-Process -FilePath $Py -ArgumentList @('-m', 'core.server') -WorkingDirectory $App `
        -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $tmp 'server.out.log') -RedirectStandardError (Join-Path $tmp 'server.err.log')
    $base = "http://127.0.0.1:$port"
    $ready = $false
    for ($i = 0; $i -lt 90; $i++) {
        if ($server.HasExited) { break }
        try {
            if ((Invoke-WebRequest -UseBasicParsing -Uri "$base/readyz" -TimeoutSec 2).StatusCode -eq 200) { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) {
        Get-Content (Join-Path $tmp 'server.err.log') -Tail 30 -ErrorAction SilentlyContinue | Write-Host
        throw "Server did not become ready on port $port"
    }
    foreach ($page in '/display', '/operator') {
        $code = (Invoke-WebRequest -UseBasicParsing -Uri "$base$page" -TimeoutSec 5).StatusCode
        if ($code -ne 200) { throw "GET $page returned $code" }
        Write-Host "  GET $page -> $code"
    }
} finally {
    if ($server -and -not $server.HasExited) {
        & taskkill.exe /PID $server.Id /T /F | Out-Null
    }
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
    Start-Sleep -Milliseconds 500
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
Write-Host 'Smoke test passed.' -ForegroundColor Green

# ---- cleanup ----------------------------------------------------------------------------
Step 'Cleaning'
Get-ChildItem -Recurse -Force -Directory -Include '__pycache__' $App | Remove-Item -Recurse -Force
Get-ChildItem -Recurse -Force -Directory -Include 'tests', '*.egg-info' $App | Remove-Item -Recurse -Force
Get-ChildItem -Recurse -Force -File -Include '*.pyc', '*.pyo' $App | Remove-Item -Force
$pipCache = Join-Path $Out 'python\pip-cache'
if (Test-Path $pipCache) { Remove-Item -Recurse -Force $pipCache }

# ---- zip --------------------------------------------------------------------------------
$folderMB = Get-FolderSizeMB $Out
if ($NoZip) {
    Write-Host "`nBuilt $Out ($folderMB MB). Zip skipped (-NoZip)."
} else {
    Step 'Creating the zip'
    New-Item -ItemType Directory -Force $Dist | Out-Null
    $zipPath = Join-Path $Dist "BibleLive-portable-$Version.zip"
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    # tar.exe (bsdtar) writes zip64 with forward-slash entry names; Compress-Archive in 5.1 is limited to 2 GB.
    Invoke-Native $tar @('-a', '-c', '-f', $zipPath, '-C', $Build, 'BibleLive')
    $zipMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "`nBuilt $Out ($folderMB MB)"
    Write-Host "Zip   $zipPath ($zipMB MB)"
}
