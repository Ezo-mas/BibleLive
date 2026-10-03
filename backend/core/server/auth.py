"""Authentication dependency for operator HTTP endpoints."""

import logging
import secrets
from typing import Optional

from fastapi import Header, HTTPException

from ..config import config

logger = logging.getLogger(__name__)


async def verify_operator_token(authorization: Optional[str] = Header(None)) -> None:
    """Verify operator authentication token.

    Raises:
        HTTPException: 401 if authentication is required but token is missing or invalid.
    """
    # If auth is not required, allow all requests
    if not config.REQUIRE_AUTH:
        return

    # If auth is required but no token configured, reject (misconfiguration)
    if not config.OPERATOR_TOKEN:
        logger.error("Authentication required but BIBLELIVE_OPERATOR_TOKEN not set")
        raise HTTPException(
            status_code=500,
            detail="Server misconfiguration: authentication enabled but token not set",
        )

    # Check for authorization header
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Parse Bearer token
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header format. Use: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]

    # Validate token using constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(token, config.OPERATOR_TOKEN):
        logger.warning(
            "Invalid operator token attempted",
            extra={
                "event": "auth_failure",
                "auth_method": "http_bearer",
                "remote_addr": "unknown",  # FastAPI doesn't easily expose this in dependencies
            },
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
