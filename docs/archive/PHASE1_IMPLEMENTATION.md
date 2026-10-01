# Phase 1 Implementation: Reference IDs and State Snapshots

## Changes Made

### Backend (biblelive_server.py)

#### 1. Enhanced Data Models
- **BibleReference**: Now includes stable `id` (UUID) and `created_at` (ISO timestamp) fields
- **WebSocket message schemas**: Added typed Pydantic models for all WebSocket messages:
  - `WSOperatorStateSnapshot`: Full state sent on operator connection
  - `WSOperatorPendingUpdate`: Incremental pending queue updates
  - `WSOperatorApprovalConfirmed`, `WSOperatorClearConfirmed`, `WSOperatorPendingCleared`
  - `WSOperatorConnectionStatus`: Connection count updates
  - `WSOperatorError`: Error responses for invalid actions
  - `WSDisplayReference`, `WSDisplayClear`: Display client messages

#### 2. Approval Protocol Change
- **Old**: Approve by array index (`{"action": "approve", "index": 0}`)
- **New**: Approve by stable reference ID (`{"action": "approve", "reference_id": "uuid"}`)
- **Benefits**: Eliminates race conditions when multiple operators interact with the same queue

#### 3. WebSocket State Synchronization
- **Operator connections**: Now receive full `state_snapshot` immediately on connect
  - Includes: pending queue, history (last 20), current display, connection counts
  - Operators see complete state even after page reload or reconnection
- **Display connections**: Receive current display state on connect
  - Shows current reference if one is displayed
  - Shows clear message if nothing is displayed

#### 4. History Management
- History now bounded at 50 items maximum (prevents unbounded memory growth)
- Items inserted at front (newest first)
- API returns last 20 items for efficiency

#### 5. API Endpoints Updated
- `POST /api/reference`: Returns `reference_id` in response
- `POST /api/approve`: Now requires `reference_id` instead of `index`
  - Returns 404 if reference not found
  - Returns 422 if `reference_id` missing
- `GET /api/state`: Now includes full `pending` and `history` arrays (not just counts)

### Frontend Changes

#### 1. Type Definitions (types.ts)
- `BibleReference`: Added `id` and `created_at` fields
- `OperatorMessage`: Added `state_snapshot` and `error` message types
- `ApproveAction`: Changed from `index: number` to `reference_id: string`
- `AppStateSnapshot`: Now includes `pending` and `history` arrays

#### 2. Operator Component (operator/App.tsx)
- Added handler for `state_snapshot` messages (sets all state from server)
- Added handler for `error` messages (logs to console)
- Removed `/api/state` HTTP fetch on connection (no longer needed)
- Changed approval to use `reference.id` instead of array index
- Changed React keys from index-based to `reference.id` (improves reconciliation)
- History now bounded at 50 items in frontend (matches backend)

## Migration Notes

### Breaking Changes
1. **Operator WebSocket Protocol**: The `approve` action now requires `reference_id` instead of `index`
2. **API Response Format**: `/api/state` now returns full arrays, not just counts
3. **Data Model**: All `BibleReference` objects now have `id` and `created_at` fields

### Backwards Compatibility
- HTTP API endpoints maintain similar structure (just different field names)
- Display WebSocket protocol unchanged (still receives `reference` and `clear` messages)
- Database schema unchanged (references not persisted)

### Testing
Run the test suite to verify the changes:
```bash
cd /home/ezomas/BibleLive
python -m pytest tests/test_reference_id_protocol.py -v
```

Frontend type checking:
```bash
cd frontend
npm run typecheck
npm run build
```

### Deployment
1. Build the frontend: `cd frontend && npm run build`
2. Restart the server: The backend changes are live when you restart
3. All connected clients will automatically reconnect and receive the new protocol

## Benefits Delivered

### For Operators
- **No more stale state**: Reconnecting operators see the complete current state
- **No more race conditions**: References identified by stable ID, not shifting array position
- **Better error feedback**: Server responds with specific error messages

### For Displays
- **Immediate sync**: Display clients show current reference immediately on connection
- **Reliable after reconnect**: No blank screen when reconnecting mid-service

### For Developers
- **Type-safe protocol**: All WebSocket messages now have Pydantic schemas
- **Better debugging**: State snapshot makes it easy to see what's happening
- **Memory safety**: History bounded at 50 items prevents memory leaks
- **Clearer API**: Explicit error codes (404, 422) instead of generic 400

## Next Steps
See REFACTORING_PLAN.md for remaining priorities:
- Authentication for operator controls
- Configuration management
- Database connection pooling
- Frontend design system and theming
