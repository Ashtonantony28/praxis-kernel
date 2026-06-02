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


async def post_queue(request: Request) -> Response:
    """POST /api/queue — add a new task to the queue.

    Request body (JSON)::

        {
          "prompt": "string (required)",
          "priority": 3,
          "mode": "build"
        }

    Returns::

        {"task_id": "..."}
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Bad Request", "detail": "Request body must be valid JSON"},
            status_code=400,
        )

    prompt = body.get("prompt", "")
    if not prompt or not isinstance(prompt, str):
        return JSONResponse(
            {"error": "Bad Request", "detail": "'prompt' field is required and must be a non-empty string"},
            status_code=400,
        )

    try:
        priority = int(body.get("priority", 3))
    except (ValueError, TypeError):
        priority = 3

    from praxis.queue import Task, TaskQueue

    root = _workspace_root()
    queue = TaskQueue(root / ".praxis" / "queue")
    task = Task.create(prompt=prompt, priority=priority)
    queue.append(task)

    # Emit TASK_QUEUED event — fire-and-forget; never fail the request.
    try:
        from praxis.event_bus import TASK_QUEUED, get_event_bus
        get_event_bus().publish_sync(TASK_QUEUED, {"task_id": task.id, "priority": priority})
    except Exception:
        pass

    return JSONResponse({"task_id": task.id}, status_code=201)


async def get_queue_task(request: Request) -> Response:
    """GET /api/queue/{task_id} — full task detail including result.

    Returns::

        {
          "id": "...",
          "prompt": "...",
          "status": "...",
          "priority": 0,
          "created_at": "...",
          "started_at": "...|null",
          "completed_at": "...|null",
          "result": "...|null",
          "error": "...|null",
          "stages": [...|null]
        }

    If a result file exists at results/{id}.txt, its content is returned as
    ``result`` even when the Task.result field is None.
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    task_id = request.path_params.get("task_id", "")

    from praxis.queue import TaskQueue

    root = _workspace_root()
    queue = TaskQueue(root / ".praxis" / "queue")
    all_tasks = queue._read_all()

    task = next((t for t in all_tasks if t.id == task_id), None)
    if task is None:
        return JSONResponse(
            {"error": "Not Found", "detail": f"Task '{task_id}' not found"},
            status_code=404,
        )

    task_dict = task.to_dict()

    # Supplement result from results file if not already set.
    if task_dict.get("result") is None:
        result_file = queue.results_dir / f"{task_id}.txt"
        if result_file.exists():
            task_dict["result"] = result_file.read_text()

    return JSONResponse(task_dict)


async def delete_queue_task(request: Request) -> Response:
    """DELETE /api/queue/{task_id} — cancel a pending task.

    Only pending tasks can be cancelled.

    Returns:
        204 No Content on success.
        404 if task not found.
        409 Conflict if task is not in 'pending' status.
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    task_id = request.path_params.get("task_id", "")

    from praxis.queue import TaskQueue

    root = _workspace_root()
    queue = TaskQueue(root / ".praxis" / "queue")
    all_tasks = queue._read_all()

    task = next((t for t in all_tasks if t.id == task_id), None)
    if task is None:
        return JSONResponse(
            {"error": "Not Found", "detail": f"Task '{task_id}' not found"},
            status_code=404,
        )

    if task.status != "pending":
        return JSONResponse(
            {
                "error": "Conflict",
                "detail": f"Task '{task_id}' cannot be cancelled — status is '{task.status}' (only 'pending' tasks can be cancelled)",
            },
            status_code=409,
        )

    queue.update_status(task_id, "failed", error="cancelled")
    return Response(status_code=204)
