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


# ---------------------------------------------------------------------------
# Approvals — staging file helpers
# ---------------------------------------------------------------------------

def _scan_staging_items(root: Path) -> list[dict]:
    """Return all pending/staged approval items across .praxis/staging/.

    Covers:
    - external_actions.jsonl  (status == "pending")
    - wiki_updates.jsonl      (status == "pending")
    - slack/messages/*.json   (status not in ("approved", "rejected"))
    - events/*.ics            (no sidecar or sidecar status != approved/rejected)
    - drafts/*.eml            (no sidecar or sidecar status != approved/rejected)
    """
    import json as _json

    staging = root / ".praxis" / "staging"
    items: list[dict] = []

    if not staging.exists():
        return items

    # 1. external_actions.jsonl
    actions_file = staging / "external_actions.jsonl"
    if actions_file.exists():
        for line in actions_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                if entry.get("status") == "pending":
                    item = dict(entry)
                    item["_source_file"] = str(actions_file.relative_to(root))
                    item["_item_type"] = "external_action"
                    item.setdefault("summary", f"[{entry.get('provider','?')}] {entry.get('action','?')}")
                    items.append(item)
            except _json.JSONDecodeError:
                pass

    # 2. wiki_updates.jsonl
    wiki_file = staging / "wiki_updates.jsonl"
    if wiki_file.exists():
        for line in wiki_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                if entry.get("status") == "pending":
                    item = dict(entry)
                    item["_source_file"] = str(wiki_file.relative_to(root))
                    item["_item_type"] = "wiki_update"
                    item.setdefault("summary", f"wiki: {entry.get('page_slug','?')} ({entry.get('linear_issue_id','?')})")
                    items.append(item)
            except _json.JSONDecodeError:
                pass

    # 3. slack/messages/*.json
    slack_msgs_dir = staging / "slack" / "messages"
    if slack_msgs_dir.exists():
        for msg_file in sorted(slack_msgs_dir.glob("*.json")):
            try:
                entry = _json.loads(msg_file.read_text(encoding="utf-8"))
                status = entry.get("status", "staged")
                if status not in ("approved", "rejected"):
                    item = dict(entry)
                    item.setdefault("id", msg_file.stem)
                    item["_source_file"] = str(msg_file.relative_to(root))
                    item["_item_type"] = "slack_message"
                    item.setdefault("summary", f"slack: {entry.get('recipient','?')}: {str(entry.get('message',''))[:60]}")
                    items.append(item)
            except Exception:
                pass

    # 4. events/*.ics — use sidecar .json for status
    events_dir = staging / "events"
    if events_dir.exists():
        for ics_file in sorted(events_dir.glob("*.ics")):
            sidecar = ics_file.with_suffix(".json")
            status = "pending"
            sidecar_data: dict = {}
            if sidecar.exists():
                try:
                    sidecar_data = _json.loads(sidecar.read_text(encoding="utf-8"))
                    status = sidecar_data.get("status", "pending")
                except Exception:
                    pass
            if status not in ("approved", "rejected"):
                item: dict = {
                    "id": ics_file.stem,
                    "status": status,
                    "_source_file": str(ics_file.relative_to(root)),
                    "_item_type": "calendar_event",
                    "summary": f"calendar: {ics_file.stem}",
                }
                item.update(sidecar_data)
                item["id"] = ics_file.stem
                item["_source_file"] = str(ics_file.relative_to(root))
                item["_item_type"] = "calendar_event"
                items.append(item)

    # 5. drafts/*.eml — use sidecar .json for status
    drafts_dir = staging / "drafts"
    if drafts_dir.exists():
        for eml_file in sorted(drafts_dir.glob("*.eml")):
            sidecar = eml_file.with_suffix(".json")
            status = "pending"
            sidecar_data = {}
            if sidecar.exists():
                try:
                    sidecar_data = _json.loads(sidecar.read_text(encoding="utf-8"))
                    status = sidecar_data.get("status", "pending")
                except Exception:
                    pass
            if status not in ("approved", "rejected"):
                item = {
                    "id": eml_file.stem,
                    "status": status,
                    "_source_file": str(eml_file.relative_to(root)),
                    "_item_type": "email_draft",
                    "summary": f"email: {eml_file.stem}",
                }
                item.update(sidecar_data)
                item["id"] = eml_file.stem
                item["_source_file"] = str(eml_file.relative_to(root))
                item["_item_type"] = "email_draft"
                items.append(item)

    return items


def _apply_approval_action(root: Path, approval_id: str, action: str) -> dict | None:
    """Find a staging item by id, set its status, execute if approving an external action.

    Returns the updated item dict, or None if the item was not found.
    ``action`` must be "approve" or "reject".
    """
    import json as _json

    new_status = "approved" if action == "approve" else "rejected"
    staging = root / ".praxis" / "staging"

    # 1. Search external_actions.jsonl
    actions_file = staging / "external_actions.jsonl"
    if actions_file.exists():
        entries: list[dict] = []
        found_entry: dict | None = None
        for line in actions_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = _json.loads(line)
                entries.append(e)
                if e.get("id") == approval_id:
                    found_entry = e
            except _json.JSONDecodeError:
                pass
        if found_entry is not None:
            found_entry["status"] = new_status
            if action == "approve":
                try:
                    from praxis.__main__ import _execute_approved_action
                    result = _execute_approved_action(found_entry)
                    found_entry["executed_result"] = result
                except Exception as exc:
                    found_entry["executed_result"] = f"error: {exc}"
            # Rewrite file
            with actions_file.open("w", encoding="utf-8") as f:
                for e in entries:
                    f.write(_json.dumps(e) + "\n")
            return dict(found_entry)

    # 2. Search wiki_updates.jsonl
    wiki_file = staging / "wiki_updates.jsonl"
    if wiki_file.exists():
        entries = []
        found_entry = None
        for line in wiki_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = _json.loads(line)
                entries.append(e)
                if e.get("id") == approval_id:
                    found_entry = e
            except _json.JSONDecodeError:
                pass
        if found_entry is not None:
            found_entry["status"] = new_status
            with wiki_file.open("w", encoding="utf-8") as f:
                for e in entries:
                    f.write(_json.dumps(e) + "\n")
            return dict(found_entry)

    # 3. slack/messages/{approval_id}.json
    slack_file = staging / "slack" / "messages" / f"{approval_id}.json"
    if slack_file.exists():
        try:
            entry = _json.loads(slack_file.read_text(encoding="utf-8"))
            entry["status"] = new_status
            slack_file.write_text(_json.dumps(entry, indent=2), encoding="utf-8")
            return dict(entry)
        except Exception:
            pass

    # 4. events/{approval_id}.ics — update sidecar
    ics_file = staging / "events" / f"{approval_id}.ics"
    if ics_file.exists():
        sidecar = ics_file.with_suffix(".json")
        sidecar_data: dict = {}
        if sidecar.exists():
            try:
                sidecar_data = _json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                pass
        sidecar_data["id"] = approval_id
        sidecar_data["status"] = new_status
        sidecar.write_text(_json.dumps(sidecar_data, indent=2), encoding="utf-8")
        return {"id": approval_id, "status": new_status, "_item_type": "calendar_event"}

    # 5. drafts/{approval_id}.eml — update sidecar
    eml_file = staging / "drafts" / f"{approval_id}.eml"
    if eml_file.exists():
        sidecar = eml_file.with_suffix(".json")
        sidecar_data = {}
        if sidecar.exists():
            try:
                sidecar_data = _json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                pass
        sidecar_data["id"] = approval_id
        sidecar_data["status"] = new_status
        sidecar.write_text(_json.dumps(sidecar_data, indent=2), encoding="utf-8")
        return {"id": approval_id, "status": new_status, "_item_type": "email_draft"}

    return None


# ---------------------------------------------------------------------------
# Approvals route handlers
# ---------------------------------------------------------------------------

async def get_approvals(request: Request) -> Response:
    """GET /api/approvals — list all pending staged external actions.

    Returns::

        {
          "items": [
            {
              "id": "...",
              "type": "external_action | wiki_update | slack_message | ...",
              "status": "pending",
              "summary": "human-readable description",
              ...original fields...
            },
            ...
          ]
        }
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    root = _workspace_root()
    items = _scan_staging_items(root)
    return JSONResponse({"items": items})


async def post_approval_action(request: Request) -> Response:
    """POST /api/approvals/{approval_id}/approve  or  /reject.

    Finds the item by id across all staging files, sets its status, and (for
    external_actions) executes the approved action.

    Returns::

        {
          "id": "...",
          "status": "approved" | "rejected",
          ...updated fields...
        }
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    approval_id = request.path_params.get("approval_id", "")
    action = request.path_params.get("action", "")

    if action not in ("approve", "reject"):
        return JSONResponse(
            {"error": "Bad Request", "detail": "Action must be 'approve' or 'reject'"},
            status_code=400,
        )

    root = _workspace_root()
    result = _apply_approval_action(root, approval_id, action)

    if result is None:
        return JSONResponse(
            {"error": "Not Found", "detail": f"Approval '{approval_id}' not found"},
            status_code=404,
        )

    return JSONResponse(result)


async def post_approvals_bulk(request: Request) -> Response:
    """POST /api/approvals/bulk — approve or reject multiple items at once.

    Request body::

        {
          "ids": ["id1", "id2", ...],
          "action": "approve" | "reject"
        }

    Returns::

        {
          "results": [
            {"id": "id1", "status": "approved"},
            {"id": "id2", "status": "not_found"},
            ...
          ]
        }
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

    ids = body.get("ids", [])
    action = body.get("action", "")

    if not isinstance(ids, list) or action not in ("approve", "reject"):
        return JSONResponse(
            {
                "error": "Bad Request",
                "detail": "Expected {\"ids\": [...], \"action\": \"approve\"|\"reject\"}",
            },
            status_code=400,
        )

    root = _workspace_root()
    results = []
    for approval_id in ids:
        item_result = _apply_approval_action(root, str(approval_id), action)
        if item_result is None:
            results.append({"id": approval_id, "status": "not_found"})
        else:
            results.append({"id": approval_id, "status": item_result.get("status", "unknown")})

    return JSONResponse({"results": results})


# ---------------------------------------------------------------------------
# Schedule — CronScheduler CRUD helpers
# ---------------------------------------------------------------------------

def _get_scheduler(root: Path):
    """Return a loaded CronScheduler for .praxis/schedule/tasks.json."""
    from praxis.queue import TaskQueue
    from praxis.scheduler import CronScheduler

    queue = TaskQueue(root / ".praxis" / "queue")
    schedule_file = root / ".praxis" / "schedule" / "tasks.json"
    log_file = root / ".praxis" / "schedule" / "dispatch.log"
    scheduler = CronScheduler(queue=queue, schedule_file=schedule_file, log_file=log_file)
    scheduler.load()
    return scheduler


# ---------------------------------------------------------------------------
# Schedule route handlers
# ---------------------------------------------------------------------------

async def get_schedule(request: Request) -> Response:
    """GET /api/schedule — list all scheduled tasks.

    Returns::

        {
          "tasks": [
            {
              "id": "...",
              "name": "...",
              "prompt": "...",
              "schedule": "*/5 * * * *",
              "enabled": true,
              "last_run": "...|null",
              "next_run": "...|null",
              "created_at": "..."
            },
            ...
          ]
        }
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    root = _workspace_root()
    scheduler = _get_scheduler(root)
    tasks = [t.to_dict() for t in scheduler.list_tasks()]
    return JSONResponse({"tasks": tasks})


async def post_schedule(request: Request) -> Response:
    """POST /api/schedule — add a new scheduled task.

    Request body (JSON)::

        {
          "name": "string (required)",
          "prompt": "string (required)",
          "schedule": "cron expression (required)"
        }

    Returns::

        {"task": {...ScheduledTask dict...}}

    Returns 400 if required fields missing or cron expression invalid.
    Returns 503 if croniter is not installed.
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

    name = body.get("name", "")
    prompt = body.get("prompt", "")
    schedule = body.get("schedule", "")

    if not name or not isinstance(name, str):
        return JSONResponse(
            {"error": "Bad Request", "detail": "'name' field is required and must be a non-empty string"},
            status_code=400,
        )
    if not prompt or not isinstance(prompt, str):
        return JSONResponse(
            {"error": "Bad Request", "detail": "'prompt' field is required and must be a non-empty string"},
            status_code=400,
        )
    if not schedule or not isinstance(schedule, str):
        return JSONResponse(
            {"error": "Bad Request", "detail": "'schedule' field is required and must be a non-empty string"},
            status_code=400,
        )

    root = _workspace_root()
    scheduler = _get_scheduler(root)

    try:
        task = scheduler.add_task(name=name, schedule=schedule, prompt=prompt)
    except ImportError as exc:
        return JSONResponse(
            {"error": "Service Unavailable", "detail": str(exc)},
            status_code=503,
        )
    except ValueError as exc:
        return JSONResponse(
            {"error": "Bad Request", "detail": f"Invalid cron expression: {exc}"},
            status_code=400,
        )

    scheduler.save()
    return JSONResponse({"task": task.to_dict()}, status_code=201)


async def put_schedule_task(request: Request) -> Response:
    """PUT /api/schedule/{task_id} — update fields of a scheduled task.

    Updatable fields: name, prompt, schedule, enabled.

    Returns::

        {"task": {...updated ScheduledTask dict...}}

    Returns 404 if task not found.
    Returns 400 if cron expression is invalid.
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    task_id = request.path_params.get("task_id", "")

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Bad Request", "detail": "Request body must be valid JSON"},
            status_code=400,
        )

    root = _workspace_root()
    scheduler = _get_scheduler(root)

    # Find the task
    task = next((t for t in scheduler.list_tasks() if t.id == task_id), None)
    if task is None:
        return JSONResponse(
            {"error": "Not Found", "detail": f"Scheduled task '{task_id}' not found"},
            status_code=404,
        )

    # Apply updates
    if "name" in body and isinstance(body["name"], str) and body["name"]:
        task.name = body["name"]
    if "prompt" in body and isinstance(body["prompt"], str) and body["prompt"]:
        task.prompt = body["prompt"]
    if "enabled" in body and isinstance(body["enabled"], bool):
        task.enabled = body["enabled"]
    if "schedule" in body and isinstance(body["schedule"], str) and body["schedule"]:
        from praxis.scheduler import _compute_next_run
        try:
            task.schedule = body["schedule"]
            task.next_run = _compute_next_run(body["schedule"])
        except ImportError as exc:
            return JSONResponse(
                {"error": "Service Unavailable", "detail": str(exc)},
                status_code=503,
            )
        except ValueError as exc:
            return JSONResponse(
                {"error": "Bad Request", "detail": f"Invalid cron expression: {exc}"},
                status_code=400,
            )

    scheduler.save()
    return JSONResponse({"task": task.to_dict()})


async def delete_schedule_task(request: Request) -> Response:
    """DELETE /api/schedule/{task_id} — remove a scheduled task.

    Returns 204 No Content on success.
    Returns 404 if not found.
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    task_id = request.path_params.get("task_id", "")

    root = _workspace_root()
    scheduler = _get_scheduler(root)

    try:
        scheduler.remove_task(task_id)
    except KeyError:
        return JSONResponse(
            {"error": "Not Found", "detail": f"Scheduled task '{task_id}' not found"},
            status_code=404,
        )

    scheduler.save()
    return Response(status_code=204)


async def post_schedule_enable(request: Request) -> Response:
    """POST /api/schedule/{task_id}/enable — enable a scheduled task.

    Returns::

        {"task": {...updated ScheduledTask dict...}}

    Returns 404 if not found.
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    task_id = request.path_params.get("task_id", "")

    root = _workspace_root()
    scheduler = _get_scheduler(root)

    try:
        scheduler.enable_task(task_id)
    except KeyError:
        return JSONResponse(
            {"error": "Not Found", "detail": f"Scheduled task '{task_id}' not found"},
            status_code=404,
        )

    scheduler.save()
    task = next((t for t in scheduler.list_tasks() if t.id == task_id), None)
    return JSONResponse({"task": task.to_dict() if task else {}})


async def post_schedule_disable(request: Request) -> Response:
    """POST /api/schedule/{task_id}/disable — disable a scheduled task.

    Returns::

        {"task": {...updated ScheduledTask dict...}}

    Returns 404 if not found.
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    task_id = request.path_params.get("task_id", "")

    root = _workspace_root()
    scheduler = _get_scheduler(root)

    try:
        scheduler.disable_task(task_id)
    except KeyError:
        return JSONResponse(
            {"error": "Not Found", "detail": f"Scheduled task '{task_id}' not found"},
            status_code=404,
        )

    scheduler.save()
    task = next((t for t in scheduler.list_tasks() if t.id == task_id), None)
    return JSONResponse({"task": task.to_dict() if task else {}})


async def post_schedule_run_now(request: Request) -> Response:
    """POST /api/schedule/{task_id}/run-now — enqueue task immediately.

    Bypasses the cron schedule and enqueues the task's prompt directly.

    Returns::

        {"task_id": "...queued task id..."}

    Returns 404 if scheduled task not found.
    """
    auth_err = _check_token(request)
    if auth_err is not None:
        return auth_err

    task_id = request.path_params.get("task_id", "")

    root = _workspace_root()
    scheduler = _get_scheduler(root)

    scheduled_task = next((t for t in scheduler.list_tasks() if t.id == task_id), None)
    if scheduled_task is None:
        return JSONResponse(
            {"error": "Not Found", "detail": f"Scheduled task '{task_id}' not found"},
            status_code=404,
        )

    from praxis.queue import Task, TaskQueue

    queue = TaskQueue(root / ".praxis" / "queue")
    queued = Task.create(prompt=scheduled_task.prompt, priority=5)
    queue.ensure_dirs()
    queue.append(queued)

    # Emit event — fire-and-forget.
    try:
        from praxis.event_bus import TASK_QUEUED, get_event_bus
        get_event_bus().publish_sync(TASK_QUEUED, {"task_id": queued.id, "source": "run_now", "schedule_id": task_id})
    except Exception:
        pass

    return JSONResponse({"task_id": queued.id}, status_code=201)
