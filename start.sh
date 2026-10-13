#!/bin/bash
# BibleLive Startup Script
# Starts both the server and audio pipeline with proper error handling

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# Python packages live under backend/; make them importable as core.* / audio.*
export PYTHONPATH="${REPO_ROOT}/backend${PYTHONPATH:+:${PYTHONPATH}}"

# Configuration
SERVER_HOST="${BIBLELIVE_HOST:-0.0.0.0}"
SERVER_PORT="${BIBLELIVE_PORT:-8000}"
HEALTH_CHECK_URL="http://localhost:${SERVER_PORT}/readyz"
HEALTH_CHECK_TIMEOUT=30
HEALTH_CHECK_INTERVAL=1

# PID tracking
SERVER_PID=""
AUDIO_PID=""

# Cleanup function
cleanup() {
    echo ""
    echo "Shutting down BibleLive System..."
    
    if [ -n "$AUDIO_PID" ] && kill -0 "$AUDIO_PID" 2>/dev/null; then
        echo "  Stopping audio pipeline (PID $AUDIO_PID)..."
        kill "$AUDIO_PID" 2>/dev/null || true
        wait "$AUDIO_PID" 2>/dev/null || true
    fi
    
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "  Stopping server (PID $SERVER_PID)..."
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    
    echo "Shutdown complete."
}

# Set up trap for cleanup on exit, interrupt, or termination
trap cleanup EXIT INT TERM

# Check if required files exist
if [ ! -f "backend/core/server/app.py" ]; then
    echo "Error: backend/core/server/app.py not found"
    echo "Please run this script from the BibleLive project root directory"
    exit 1
fi

if [ ! -f "backend/audio/cli.py" ]; then
    echo "Error: backend/audio/cli.py not found"
    exit 1
fi

# Frontend bundle: static/frontend/ is generated and gitignored, so a pull never
# updates it, and /readyz only checks the files exist. A missing or stale bundle
# only warns: /display and /operator answer 503 with build instructions until
# the bundle exists, so the server still starts.
if [ ! -f "static/frontend/display.html" ]; then
    echo "WARNING: static/frontend/display.html not found."
    echo "The pages will answer 503 with build instructions until it exists."
    echo "Build it: cd frontend && npm install && npm run build"
fi
if [ -d "frontend/src" ] && [ -n "$(find frontend/src -newer static/frontend/display.html -print -quit 2>/dev/null)" ]; then
    echo "WARNING: frontend/src is newer than static/frontend/display.html."
    echo "The served bundle may be stale. Rebuild: cd frontend && npm run build"
fi

echo "Starting BibleLive System..."
echo "============================"
echo ""

# Start the server in background
echo "Starting BibleLive Server on ${SERVER_HOST}:${SERVER_PORT}..."
python3 -m core.server &
SERVER_PID=$!

# Wait for server to be ready
echo "Waiting for server to be ready..."
ELAPSED=0
while [ $ELAPSED -lt $HEALTH_CHECK_TIMEOUT ]; do
    if curl -sf "$HEALTH_CHECK_URL" > /dev/null 2>&1; then
        echo "✓ Server is ready!"
        break
    fi
    sleep $HEALTH_CHECK_INTERVAL
    ELAPSED=$((ELAPSED + HEALTH_CHECK_INTERVAL))
    
    # Check if server process is still running
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "Error: Server process died during startup"
        exit 1
    fi
done

if [ $ELAPSED -ge $HEALTH_CHECK_TIMEOUT ]; then
    echo "Error: Server did not become ready within ${HEALTH_CHECK_TIMEOUT} seconds"
    exit 1
fi

echo ""

# Start audio pipeline
echo "Starting Audio Pipeline..."
python3 -m audio.cli &
AUDIO_PID=$!

echo ""
echo "============================"
echo "BibleLive System Running"
echo "============================"
echo "  Server:   http://localhost:${SERVER_PORT}"
echo "  Display:  http://localhost:${SERVER_PORT}/display"
echo "  Operator: http://localhost:${SERVER_PORT}/operator"
echo "  API Docs: http://localhost:${SERVER_PORT}/docs"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Wait for audio pipeline (main process)
wait "$AUDIO_PID"
