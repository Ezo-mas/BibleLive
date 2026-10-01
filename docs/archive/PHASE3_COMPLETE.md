# Phase 3: Configuration and Startup Production-Safety

## Summary

Replaced module-level configuration with typed config class, added health check endpoints, and improved startup script with proper error handling and cleanup.

## Key Changes

### 1. Typed Configuration Module (`core/config.py`)

Created centralized configuration class:
- Loads all settings from environment variables
- Typed attributes with sensible defaults
- **RELOAD defaults to false** (was "true" - unsafe for production)
- Validation on startup with warnings for production concerns
- Configuration summary for logging

**Benefits:**
- Single source of truth for configuration
- Type safety (no runtime type errors)
- Clear documentation of all settings
- Production-safe defaults

### 2. Health Check Endpoints

Added two standard health check endpoints:

**GET /healthz**
- Basic liveness check
- Returns 200 if server process is running
- Use for: container health probes, monitoring

**GET /readyz**
- Readiness check with component validation
- Checks: database connection, frontend build
- Returns 200 if ready, 503 if not
- Use for: startup verification, load balancer health checks

### 3. Improved Startup Script (`start.sh`)

Replaced fixed sleep with health check polling:

**Added:**
- `set -euo pipefail` for strict error handling
- Trap for cleanup on EXIT/INT/TERM signals
- Health check polling with timeout (30s)
- Proper PID tracking for both processes
- Graceful shutdown of all child processes
- File existence validation before startup
- Clear status messages and URLs

**Benefits:**
- Server failures detected immediately
- Clean shutdown on Ctrl+C or error
- No orphaned processes
- Reliable startup verification
- Configuration via environment variables

### 4. Environment Configuration Template

Created `.env.example` documenting all settings:
- Server host/port
- Reload flag (with production warning)
- Log level
- Database path and readonly mode
- Bible data sources
- Audio pipeline settings

## Configuration Usage

### Development
```bash
# Enable auto-reload for development
export BIBLELIVE_RELOAD=true
export BIBLELIVE_LOG_LEVEL=DEBUG
./start.sh
```

### Production
```bash
# Use .env file or export variables
export BIBLELIVE_RELOAD=false
export BIBLELIVE_LOG_LEVEL=INFO
export BIBLELIVE_DB_READONLY=true
./start.sh
```

### Docker/Container
```yaml
environment:
  BIBLELIVE_HOST: 0.0.0.0
  BIBLELIVE_PORT: 8000
  BIBLELIVE_RELOAD: false
  BIBLELIVE_LOG_LEVEL: INFO
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/readyz"]
  interval: 10s
  timeout: 5s
  retries: 3
```

## Testing

### Health Checks
```bash
# Start server
python core/biblelive_server.py

# Test liveness
curl http://localhost:8000/healthz
# {"status": "healthy"}

# Test readiness
curl http://localhost:8000/readyz
# {"status": "ready", "checks": {"server": "ok", "database": "ok", "frontend": "ok"}}
```

### Startup Script
```bash
# Test with default config
./start.sh

# Test with custom port
BIBLELIVE_PORT=9000 ./start.sh

# Test cleanup (Ctrl+C should gracefully shut down both processes)
```

## Breaking Changes

None for normal usage. Changes:
- `BIBLELIVE_RELOAD` now defaults to `false` (was implicitly "true")
- This is safer - explicitly opt into dev mode

## Files Created/Modified

**New:**
- `core/config.py` - Typed configuration class
- `.env.example` - Environment variable documentation

**Modified:**
- `core/biblelive_server.py` - Use config module, add health checks
- `start.sh` - Complete rewrite with proper error handling

## Benefits Delivered

### For Development
- Clear configuration documentation
- Easy to enable/disable features
- Better error messages on startup
- Fast failure on misconfiguration

### For Production
- Safe defaults (reload=false)
- Health checks for orchestration
- Graceful shutdown
- No orphaned processes
- Environment-based configuration

### For Operations
- Standard health check endpoints
- Readiness verification before traffic
- Component-level health status
- Configuration summary in logs
- Proper cleanup on failure

## Next: Database/Import Lifecycle Cleanup

Make Bible imports explicit, add metadata, improve connection handling, separate concerns.
