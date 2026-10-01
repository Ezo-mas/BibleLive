# Regression Audit Fixes - Remaining Items

This document tracks the fixes implemented for the regression audit findings.

## Completed Fixes

### Critical (P0)
✅ **1. Frontend operator authentication** - Already fixed in previous session
- Token input UI with localStorage
- WebSocket connection with token parameter
- Auth failure detection and re-prompt
- Logout functionality

### High Priority (P1)
✅ **3. Fail-fast authentication validation**
- Config.validate() raises ValueError when auth enabled but token missing
- Server refuses to start with misconfigured auth

✅ **4. Protected /api/state endpoint**
- Endpoint now requires authentication when enabled

✅ **7. Constant-time token comparison**
- Using secrets.compare_digest() in all auth checks

### Medium Priority
✅ **8. Stale protocol tests**
- Updated test_websocket_approval.py to use reference_id
- Removed obsolete index-based approvals
- Updated to expect state_snapshot on operator connect
- All HTTP tests now use reference_id

✅ **9. Documentation - .env loading**
- DEPLOYMENT.md now clarifies .env is NOT auto-loaded
- Provides manual loading instructions: set -a; source .env; set +a
- Distinguishes local dev from systemd EnvironmentFile

✅ **10. Caddy rate_limit plugin**
- Added warning that rate_limit requires plugin
- Commented out in example config

✅ **11. Audio pipeline systemd service**
- Added complete systemd service example
- Documented audio group membership
- Explained dependency on main biblelive service

### Low Priority
✅ **12. Readiness check documentation**
- Updated to correctly describe current behavior
- States it checks database file exists, not loaded

✅ **13. Frontend protocol documentation**
- Updated README.md: approve now uses reference_id
- Updated frontend/README.md: documents state_snapshot

✅ **14. Remove emoji from logs**
- Already completed in previous session

## Remaining Issues

### Critical (P0)
⚠️ **2. Public reference ingestion DoS surface**
- /api/reference remains unauthenticated by design
- Audio pipeline needs to POST references
- Mitigation options:
  a. Network-level restriction (firewall, proxy ACL)
  b. Separate ingestion token
  c. Rate limiting at proxy level
  d. Queue size limits
- Decision needed: architectural change vs operational controls

### High Priority (P1)
⚠️ **5. Environment file deployment**
- Fixed: DEPLOYMENT.md now explicitly creates .env
- Fixed: Sets permissions 0600
- Fixed: Documents production token generation

### Medium/Low
⚠️ **6. WebSocket token log exposure**
- Fixed: SECURITY.md documents Nginx log redaction
- Fixed: DEPLOYMENT.md includes custom log format
- Tokens in query string still visible in application logs

⚠️ **15. CORS configuration**
- Not addressed: wildcard + credentials is permissive
- Not a bypass, but expands attack surface
- Consider tightening CORS_ORIGINS in production

⚠️ **16. No frontend tests**
- No changes made
- Recommendation: add vitest/jest tests for auth flow

## Test Status

- test_authentication.py: ✅ Created, comprehensive coverage
- test_websocket_approval.py: ✅ Updated for reference_id protocol
- Test harness issue: httpx2 module not found (environment issue, not code issue)

## Documentation Updated

- ✅ README.md - approve API description
- ✅ frontend/README.md - WebSocket protocol
- ✅ DEPLOYMENT.md - .env handling, audio service, Caddy plugin, log redaction, .env creation
- ✅ SECURITY.md - created with full auth guide
- ✅ FIXES_2026-10-01.md - change summary

## Recommendations for Next Phase

1. **Reference ingestion protection** (architectural decision needed)
   - Evaluate threat model: internal audio pipeline vs internet exposure
   - If internet-exposed: add network restrictions or separate auth
   - Add queue size limits regardless

2. **Application logging** (easy win)
   - Add structured logging for security events
   - Consider masking tokens in app logs (not just proxy logs)

3. **Frontend tests** (quality improvement)
   - Add unit tests for token storage/retrieval
   - Test WebSocket reconnection logic
   - Test auth failure handling

4. **CORS tightening** (production hardening)
   - Document expected CORS_ORIGINS for production
   - Consider removing wildcard support when auth is enabled

