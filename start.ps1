# BibleLive Startup Script (PowerShell)
# Starts both the server and audio pipeline with proper error handling

$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

# Python packages live under backend/; make them importable as core.* / audio.*
$backend = Join-Path $RepoRoot "backend"
$paths = @($env:PYTHONPATH -split ';' | Where-Object { $_ -and $_ -ne $backend })
$env:PYTHONPATH = (@($backend) + $paths) -join ';'

# Use the venv's Python if present, otherwise whatever "python" is on PATH
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "py" }
# Configuration
$ServerHost = if ($env:BIBLELIVE_HOST) { $env:BIBLELIVE_HOST } else { "0.0.0.0" }
$ServerPort = if ($env:BIBLELIVE_PORT) { $env:BIBLELIVE_PORT } else { "8000" }
$HealthCheckUrl = "http://127.0.0.1:$ServerPort/readyz"
$HealthCheckTimeout = 30
$HealthCheckInterval = 1

# Extra flags for the audio pipeline, e.g. $env:BIBLELIVE_AUDIO_ARGS = "--device cpu --model base"
$AudioArgs = @()
if ($env:BIBLELIVE_AUDIO_ARGS) { $AudioArgs = $env:BIBLELIVE_AUDIO_ARGS -split '\s+' }

$script:Server = $null

function Stop-Tree($proc) {
    # taskkill /T also kills child processes (uvicorn's reloader spawns one)
    if ($proc -and -not $proc.HasExited) {
        & taskkill.exe /PID $proc.Id /T /F *> $null
    }
}

# Check required files
if (-not (Test-Path "backend\core\server\app.py")) {
    Write-Host "Error: backend\core\server\app.py not found"
    Write-Host "Please run this script from the BibleLive project root directory"
    exit 1
}
if (-not (Test-Path "backend\audio\cli.py")) {
    Write-Host "Error: backend\audio\cli.py not found"
    exit 1
}

Write-Host "Starting BibleLive System..."
Write-Host "============================"
Write-Host ""

$exitCode = 0
try {
    # Start the server in the background (logs share this window)
    Write-Host "Starting BibleLive Server on ${ServerHost}:${ServerPort}..."
    $script:Server = Start-Process -FilePath $Python -ArgumentList "-m", "core.server" `
        -WorkingDirectory $RepoRoot -NoNewWindow -PassThru

    # Wait for server to be ready
    Write-Host "Waiting for server to be ready..."
    $elapsed = 0
    $ready = $false
    while ($elapsed -lt $HealthCheckTimeout) {
        try {
            $r = Invoke-WebRequest -Uri $HealthCheckUrl -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch { }

        if ($script:Server.HasExited) {
            Write-Host "Error: Server process died during startup"
            exit 1
        }
        Start-Sleep -Seconds $HealthCheckInterval
        $elapsed += $HealthCheckInterval
    }

    if (-not $ready) {
        Write-Host "Error: Server did not become ready within $HealthCheckTimeout seconds"
        exit 1
    }
    Write-Host "Server is ready!"
    Write-Host ""

    Write-Host "Starting Audio Pipeline..."
    Write-Host ""
    Write-Host "============================"
    Write-Host "BibleLive System Running"
    Write-Host "============================"
    Write-Host "  Server:   http://localhost:$ServerPort"
    Write-Host "  Display:  http://localhost:$ServerPort/display"
    Write-Host "  Operator: http://localhost:$ServerPort/operator"
    Write-Host "  API Docs: http://localhost:$ServerPort/docs"
    Write-Host ""
    Write-Host "Press Ctrl+C to stop"
    Write-Host ""

    # Audio pipeline runs in the foreground (main process)
    & $Python -m audio.cli @AudioArgs
    $exitCode = $LASTEXITCODE
}
finally {
    Write-Host ""
    Write-Host "Shutting down BibleLive System..."
    if ($script:Server -and -not $script:Server.HasExited) {
        Write-Host "  Stopping server (PID $($script:Server.Id))..."
        Stop-Tree $script:Server
    }
    Write-Host "Shutdown complete."
}
exit $exitCode