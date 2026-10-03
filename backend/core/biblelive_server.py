"""Compatibility shim for the old biblelive_server module.

The server implementation has been split into the core.server package:

- core.server.models      - Pydantic models
- core.server.auth        - operator token verification
- core.server.connections - WebSocket connection management
- core.server.state       - AppState (pending queue, display, history)
- core.server.app         - create_app() factory and main() entry point

Import from core.server instead. This module exists only for backward
compatibility during migration.
"""

from .server import (  # noqa: F401
    ApprovalRequest,
    ApprovalResponse,
    AppState,
    BibleReference,
    ClearPendingResponse,
    ClearResponse,
    ConnectionManager,
    ErrorResponse,
    OperatorApproveAction,
    OperatorClearDisplayAction,
    OperatorClearPendingAction,
    ReferenceSubmitRequest,
    ReferenceSubmitResponse,
    StateResponse,
    create_app,
    create_bible_lookup,
    main,
    verify_operator_token,
)

if __name__ == "__main__":
    main()
