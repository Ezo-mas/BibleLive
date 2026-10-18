<#
.SYNOPSIS
  Builds the Windows installer dist\BibleLive-Setup-<version>.exe with Inno Setup 6.

.DESCRIPTION
  Windows PowerShell 5.1 compatible. Runs build-portable.ps1 first (unless -SkipPortableBuild) so
  build\BibleLive exists, then compiles scripts\windows\installer\BibleLive.iss with ISCC.exe.
  Requires Inno Setup 6.5.0 or newer (https://jrsoftware.org/isdl.php). The version comes from
  backend\pyproject.toml.

.PARAMETER SkipPortableBuild  Reuse the existing build\BibleLive folder.
.PARAMETER Iscc               Path to ISCC.exe. Otherwise: $env:ISCC, then the usual Inno Setup 6 folders.
.PARAMETER BibleDb            Passed on to build-portable.ps1.
.PARAMETER ExtraBibleDb       Passed on to build-portable.ps1 (default there: bible_data\bible_ltb.sqlite
                              when it exists).
.PARAMETER ReuseDependencies  Passed on to build-portable.ps1: keep the bundled Python and packages when
                              their inputs did not change (app-only changes rebuild in under a minute).
.PARAMETER Fast               Quick compression (lzma2/fast) for test installers: about a minute instead
                              of five, but a larger exe. Leave it off for installers you hand out.
.PARAMETER SkipFrontendBuild  Passed on to build-portable.ps1.
#>
[CmdletBinding()]
param(
    [switch]$SkipPortableBuild,
    [string]$Iscc,
    [string]$BibleDb,
    [string[]]$ExtraBibleDb = @(),
    [switch]$ReuseDependencies,
    [switch]$Fast,
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = 'Stop'

$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Build = Join-Path $Repo 'build\BibleLive'
$Dist = Join-Path $Repo 'dist'
$Script = Join-Path $PSScriptRoot 'installer\BibleLive.iss'

$m = Select-String -Path (Join-Path $Repo 'backend\pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $m) { throw 'Cannot read the version from backend\pyproject.toml' }
$Version = $m.Matches[0].Groups[1].Value
Write-Host "BibleLive version $Version"

# ---- ISCC.exe ---------------------------------------------------------------------------
$candidates = @()
if ($Iscc) { $candidates += $Iscc }
if ($env:ISCC) { $candidates += $env:ISCC }
if (${env:ProgramFiles(x86)}) { $candidates += (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe') }
if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe') }
if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe') }
$IsccPath = $candidates | Where-Object { $_ -and (Test-Path -PathType Leaf $_) } | Select-Object -First 1
if (-not $IsccPath) {
    throw ("ISCC.exe (Inno Setup compiler) not found. Install Inno Setup 6.5.0 or newer from " +
        "https://jrsoftware.org/isdl.php, or pass -Iscc <path to ISCC.exe> or set the ISCC environment variable. " +
        "Searched: " + ($candidates -join '; '))
}
Write-Host "Using $IsccPath"

# ---- portable folder --------------------------------------------------------------------
if ($SkipPortableBuild) {
    if (-not (Test-Path (Join-Path $Build 'python\python.exe'))) {
        throw "-SkipPortableBuild given but $Build is missing or incomplete; run build-portable.ps1 first."
    }
    Write-Host "Reusing $Build"
} else {
    $portableArgs = @{ NoZip = $true }
    if ($BibleDb) { $portableArgs.BibleDb = $BibleDb }
    if ($PSBoundParameters.ContainsKey('ExtraBibleDb')) { $portableArgs.ExtraBibleDb = $ExtraBibleDb }
    if ($ReuseDependencies) { $portableArgs.ReuseDependencies = $true }
    if ($SkipFrontendBuild) { $portableArgs.SkipFrontendBuild = $true }
    & (Join-Path $PSScriptRoot 'build-portable.ps1') @portableArgs
}
foreach ($f in 'python\python.exe', 'python\pythonw.exe', 'app\backend\launcher\__main__.py',
               'app\bible_data\biblelive.sqlite', 'app\ffmpeg\ffmpeg.exe', 'app\biblelive.ico',
               'BibleLive.cmd', 'BibleLive-console.cmd') {
    if (-not (Test-Path (Join-Path $Build $f))) { throw "Portable folder is incomplete: $Build\$f missing" }
}

# ---- compile ----------------------------------------------------------------------------
New-Item -ItemType Directory -Force $Dist | Out-Null
$numeric = ($Version -replace '[^0-9.].*$', '')
if ($numeric -notmatch '^\d+(\.\d+){0,3}$') { $numeric = '0.0.0' }
$isccArgs = @(
    "/DAppVersion=$Version",
    "/DVersionNumeric=$numeric",
    "/DBuildDir=$Build",
    "/DOutputDir=$Dist"
)
if ($Fast) {
    $isccArgs += '/DCompression=lzma2/fast', '/DOutputSuffix=-fast'
    Write-Host "Compiling $Script (lzma2/fast: quick test build, larger exe)"
} else {
    Write-Host "Compiling $Script (lzma2/ultra64 on ~2.5 GB: this takes a while)"
}
$isccArgs += $Script
& $IsccPath @isccArgs
if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

$exe = Join-Path $Dist ("BibleLive-Setup-$Version" + $(if ($Fast) { '-fast' } else { '' }) + '.exe')
if (-not (Test-Path $exe)) { throw "Expected installer not found: $exe" }
Write-Host ("Built {0} ({1} MB)" -f $exe, [math]::Round((Get-Item $exe).Length / 1MB, 1)) -ForegroundColor Green
