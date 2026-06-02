"""REST API helpers for Praxis web UI.

This module provides shared auth utilities and route handlers used by all
/api/* routes. The token is read from PRAXIS_UI_TOKEN env var; if unset,
auth is disabled (safe when binding to 127.0.0.1 only).
"""

from __future__ import annotations

import os
from pathlib import Path
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

# Package version — matches pyproject.toml
_VERSION = "0.1.0"


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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _workspace_root() -> Path:
    """Resolve the workspace root the same way Config.from_env() does."""
    ws = os.environ.get("PRAXIS_WORKSPACE_ROOT")
    return Path(ws).resolve() if ws else Path.cwd().resolve()


def _is_daemon_running(workspace_root: Path) -> bool:
    """Return True if .praxis/praxis.pid exists and the PID is alive."""
    import signal

    pid_file = workspace_root / ".praxis" / "praxis.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        # os.kill(pid, 0) raises OSError if the process does not exist.
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def get_status(request: Request) -> Response:
    """GET /api/status — system overview.

    Returns::

        {
          "version": "0.1.0",
          "queue_stats": {"pending": N, "running": N, "done": N, "failed": N},
          "daemon_running": bool
        }
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    from praxis.queue import TaskQueue

    root = _workspace_root()
    queue = TaskQueue(root / ".praxis" / "queue")
    queue_stats = queue.stats()
    daemon_running = _is_daemon_running(root)

    return JSONResponse(
        {
            "version": _VERSION,
            "queue_stats": queue_stats,
            "daemon_running": daemon_running,
        }
    )


async def get_queue(request: Request) -> Response:
    """GET /api/queue — paginated task list.

    Query params:
        status  (str, optional)  — filter by task status
        limit   (int, default 50) — max tasks to return
        offset  (int, default 0)  — skip first N tasks

    Returns::

        {
          "tasks": [
            {
              "id": "...",
              "prompt_preview": "first 100 chars...",
              "status": "pending",
              "priority": 0,
              "queued_at": "2026-01-01T00:00:00+00:00"
            },
            ...
          ],
          "total": int
        }
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    from praxis.queue import TaskQueue

    root = _workspace_root()
    queue = TaskQueue(root / ".praxis" / "queue")
    all_tasks = queue._read_all()

    # Apply optional status filter.
    status_filter = request.query_params.get("status", "")
    if status_filter:
        all_tasks = [t for t in all_tasks if t.status == status_filter]

    total = len(all_tasks)

    # Apply pagination.
    try:
        limit = int(request.query_params.get("limit", 50))
    except (ValueError, TypeError):
        limit = 50
    try:
        offset = int(request.query_params.get("offset", 0))
    except (ValueError, TypeError):
        offset = 0

    page = all_tasks[offset : offset + limit]

    tasks_out = [
        {
            "id": t.id,
            "prompt_preview": t.prompt[:100],
            "status": t.status,
            "priority": t.priority,
            "queued_at": t.created_at,
        }
        for t in page
    ]

    return JSONResponse({"tasks": tasks_out, "total": total})
