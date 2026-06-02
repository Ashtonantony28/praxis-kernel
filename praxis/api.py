"""REST API helpers for Praxis web UI.

This module provides shared auth utilities used by all /api/* route handlers.
The token is read from PRAXIS_UI_TOKEN env var; if unset, auth is disabled
(safe when binding to 127.0.0.1 only).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

try:
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    if TYPE_CHECKING:
        from starlette.websockets import WebSocket
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "[praxis] REST API requires additional dependencies.\n"
        "  Install with: pip install praxis[mcp]\n"
        f"  Missing: {exc}"
    ) from exc


def _check_token(request: Request) -> Response | None:
    """Check Bearer token for HTTP requests.

    Returns None if auth passes (token not configured, or token matches).
    Returns a 401 JSONResponse if the token is configured and does not match.
    """
    token = os.environ.get("PRAXIS_UI_TOKEN", "")
    if not token:
        # Auth disabled — no token configured.
        return None
    auth_header = request.headers.get("Authorization", "")
    if auth_header == f"Bearer {token}":
        return None
    return JSONResponse(
        {"error": "Unauthorized", "detail": "Valid Bearer token required"},
        status_code=401,
    )


def _check_token_ws(websocket: "WebSocket") -> bool:
    """Check Bearer token for WebSocket handshake.

    Returns True if auth passes (token not configured, or token matches).
    Returns False if the token is configured and does not match.

    Accepts the token via:
    - Query param: ?token=<value>
    - Authorization header: Bearer <value>
    """
    token = os.environ.get("PRAXIS_UI_TOKEN", "")
    if not token:
        return True
    # Check query param first, then Authorization header.
    query_token = websocket.query_params.get("token", "")
    if query_token == token:
        return True
    auth_header = websocket.headers.get("Authorization", "")
    if auth_header == f"Bearer {token}":
        return True
    return False
