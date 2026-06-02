"""Tests for praxis/api.py — token auth helpers."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers to build fake Starlette Request / WebSocket objects
# ---------------------------------------------------------------------------

def _make_request(auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request with the given Authorization header."""
    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    return req


def _make_websocket(token_param: str = "", auth_header: str = "") -> MagicMock:
    """Return a mock Starlette WebSocket."""
    ws = MagicMock()
    ws.query_params = {"token": token_param} if token_param else {}
    ws.headers = {"Authorization": auth_header} if auth_header else {}
    return ws


# ---------------------------------------------------------------------------
# _check_token — HTTP requests
# ---------------------------------------------------------------------------

class TestCheckToken:
    def test_no_auth_when_token_not_set(self):
        """If PRAXIS_UI_TOKEN is not set, _check_token always returns None."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PRAXIS_UI_TOKEN", None)
            result = _check_token(_make_request())
        assert result is None

    def test_no_auth_when_token_empty_string(self):
        """If PRAXIS_UI_TOKEN is empty string, auth is disabled."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": ""}):
            result = _check_token(_make_request())
        assert result is None

    def test_401_when_token_wrong(self):
        """If token is configured and header is wrong, return 401."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token(_make_request("Bearer wrongtoken"))
        assert result is not None
        assert result.status_code == 401

    def test_401_when_no_auth_header(self):
        """If token is configured and no header is provided, return 401."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token(_make_request(""))
        assert result is not None
        assert result.status_code == 401

    def test_passes_when_correct_bearer(self):
        """Correct Bearer token returns None (auth passed)."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token(_make_request("Bearer secret123"))
        assert result is None

    def test_401_response_is_json(self):
        """401 response body is JSON with 'error' key."""
        from praxis.api import _check_token
        import json

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token(_make_request("Bearer bad"))
        assert result is not None
        body = json.loads(result.body)
        assert "error" in body

    def test_passes_exactly_right_token(self):
        """Token comparison is exact — no prefix/suffix accepted."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "abc"}):
            # 'abc' token but header has 'abcX'
            result = _check_token(_make_request("Bearer abcX"))
        assert result is not None
        assert result.status_code == 401


# ---------------------------------------------------------------------------
# _check_token_ws — WebSocket handshake
# ---------------------------------------------------------------------------

class TestCheckTokenWs:
    def test_no_auth_when_token_not_set(self):
        """If PRAXIS_UI_TOKEN is not set, _check_token_ws always returns True."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PRAXIS_UI_TOKEN", None)
            result = _check_token_ws(_make_websocket())
        assert result is True

    def test_returns_false_when_token_wrong(self):
        """Incorrect query param and no header → False."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token_ws(_make_websocket("badtoken"))
        assert result is False

    def test_returns_true_with_correct_query_param(self):
        """Correct token in ?token= query param → True."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token_ws(_make_websocket(token_param="secret123"))
        assert result is True

    def test_returns_true_with_correct_auth_header(self):
        """Correct Bearer in Authorization header → True."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token_ws(
                _make_websocket(auth_header="Bearer secret123")
            )
        assert result is True

    def test_returns_false_no_token_no_header(self):
        """Token configured but neither query param nor header provided → False."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token_ws(_make_websocket())
        assert result is False

    def test_query_param_takes_precedence_over_header(self):
        """If query param is correct, it is accepted even if header is wrong."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            ws = _make_websocket(token_param="secret123", auth_header="Bearer bad")
            result = _check_token_ws(ws)
        assert result is True


# ---------------------------------------------------------------------------
# Helpers for route handler tests
# ---------------------------------------------------------------------------

def _make_http_request(query_params: dict | None = None, auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request for handler tests."""
    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.query_params = query_params or {}
    return req


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_status_returns_json(self, tmp_path):
        """get_status() returns a JSONResponse with version, queue_stats, daemon_running."""
        import asyncio
        import json
        from praxis.api import get_status

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_http_request()
            response = asyncio.run(get_status(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert "version" in body
        assert "queue_stats" in body
        assert "daemon_running" in body
        assert isinstance(body["queue_stats"], dict)
        assert isinstance(body["daemon_running"], bool)

    def test_status_401_with_bad_token(self, tmp_path):
        """get_status() returns 401 when token is wrong."""
        import asyncio
        from praxis.api import get_status

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_http_request(auth_header="Bearer wrong")
            response = asyncio.run(get_status(req))

        assert response.status_code == 401

    def test_status_daemon_running_false_when_no_pid_file(self, tmp_path):
        """daemon_running is False when .praxis/praxis.pid does not exist."""
        import asyncio
        import json
        from praxis.api import get_status

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_http_request()
            response = asyncio.run(get_status(req))

        body = json.loads(response.body)
        assert body["daemon_running"] is False


# ---------------------------------------------------------------------------
# GET /api/queue
# ---------------------------------------------------------------------------

class TestGetQueue:
    def test_queue_list_empty(self, tmp_path):
        """get_queue() returns empty task list when no tasks.jsonl exists."""
        import asyncio
        import json
        from praxis.api import get_queue

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_http_request()
            response = asyncio.run(get_queue(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["tasks"] == []
        assert body["total"] == 0

    def test_queue_list_with_tasks(self, tmp_path):
        """get_queue() returns tasks with correct shape when tasks exist."""
        import asyncio
        import json
        from praxis.queue import Task, TaskQueue
        from praxis.api import get_queue

        # Populate queue with two tasks.
        queue_dir = tmp_path / ".praxis" / "queue"
        tq = TaskQueue(queue_dir)
        task1 = Task.create(prompt="do something useful here", priority=1)
        task2 = Task.create(prompt="another task with a longer prompt " * 5, priority=0)
        tq.append(task1)
        tq.append(task2)

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_http_request()
            response = asyncio.run(get_queue(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["total"] == 2
        assert len(body["tasks"]) == 2
        for t in body["tasks"]:
            assert "id" in t
            assert "prompt_preview" in t
            assert "status" in t
            assert "priority" in t
            assert "queued_at" in t
            assert len(t["prompt_preview"]) <= 100

    def test_queue_list_status_filter(self, tmp_path):
        """get_queue() filters tasks by ?status= parameter."""
        import asyncio
        import json
        from praxis.queue import Task, TaskQueue
        from praxis.api import get_queue

        queue_dir = tmp_path / ".praxis" / "queue"
        tq = TaskQueue(queue_dir)
        task1 = Task.create(prompt="pending task", priority=0)
        task2 = Task.create(prompt="done task", priority=0)
        tq.append(task1)
        tq.append(task2)
        tq.update_status(task2.id, "done")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_http_request(query_params={"status": "pending"})
            response = asyncio.run(get_queue(req))

        body = json.loads(response.body)
        assert body["total"] == 1
        assert body["tasks"][0]["status"] == "pending"

    def test_queue_list_pagination(self, tmp_path):
        """get_queue() respects limit and offset parameters."""
        import asyncio
        import json
        from praxis.queue import Task, TaskQueue
        from praxis.api import get_queue

        queue_dir = tmp_path / ".praxis" / "queue"
        tq = TaskQueue(queue_dir)
        for i in range(5):
            tq.append(Task.create(prompt=f"task {i}", priority=0))

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_http_request(query_params={"limit": "2", "offset": "1"})
            response = asyncio.run(get_queue(req))

        body = json.loads(response.body)
        assert body["total"] == 5
        assert len(body["tasks"]) == 2

    def test_queue_list_401_with_bad_token(self, tmp_path):
        """get_queue() returns 401 when token is wrong."""
        import asyncio
        from praxis.api import get_queue

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_http_request(auth_header="Bearer wrong")
            response = asyncio.run(get_queue(req))

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/queue
# ---------------------------------------------------------------------------

def _make_post_request(body: dict, auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request with an async json() method."""
    import asyncio

    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.query_params = {}

    async def _json():
        return body

    req.json = _json
    return req


def _make_invalid_json_request() -> MagicMock:
    """Return a mock Starlette Request whose json() raises an exception."""
    import asyncio

    req = MagicMock()
    req.headers = {}
    req.query_params = {}

    async def _json():
        raise ValueError("not valid json")

    req.json = _json
    return req


class TestPostQueue:
    def test_queue_add_creates_task(self, tmp_path):
        """post_queue() creates a task and returns {task_id}."""
        import asyncio
        import json
        from praxis.queue import TaskQueue
        from praxis.api import post_queue

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_post_request({"prompt": "do something useful"})
            response = asyncio.run(post_queue(req))

        assert response.status_code == 201
        body = json.loads(response.body)
        assert "task_id" in body

        # Verify the task was actually written to the queue.
        queue_dir = tmp_path / ".praxis" / "queue"
        tq = TaskQueue(queue_dir)
        tasks = tq._read_all()
        assert len(tasks) == 1
        assert tasks[0].id == body["task_id"]
        assert tasks[0].prompt == "do something useful"

    def test_queue_add_emits_event(self, tmp_path):
        """post_queue() emits TASK_QUEUED event on the event bus."""
        import asyncio
        from unittest.mock import MagicMock, patch

        from praxis.api import post_queue

        mock_bus = MagicMock()

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with patch("praxis.event_bus.get_event_bus", return_value=mock_bus):
                req = _make_post_request({"prompt": "emit test"})
                asyncio.run(post_queue(req))

        mock_bus.publish_sync.assert_called_once()
        event_name = mock_bus.publish_sync.call_args[0][0]
        from praxis.event_bus import TASK_QUEUED
        assert event_name == TASK_QUEUED

    def test_queue_add_uses_default_priority(self, tmp_path):
        """post_queue() defaults priority to 3 when not provided."""
        import asyncio
        import json
        from praxis.queue import TaskQueue
        from praxis.api import post_queue

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_post_request({"prompt": "check priority default"})
            asyncio.run(post_queue(req))

        tq = TaskQueue(tmp_path / ".praxis" / "queue")
        tasks = tq._read_all()
        assert tasks[0].priority == 3

    def test_queue_add_respects_custom_priority(self, tmp_path):
        """post_queue() uses the provided priority value."""
        import asyncio
        from praxis.queue import TaskQueue
        from praxis.api import post_queue

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_post_request({"prompt": "high priority task", "priority": 1})
            asyncio.run(post_queue(req))

        tq = TaskQueue(tmp_path / ".praxis" / "queue")
        tasks = tq._read_all()
        assert tasks[0].priority == 1

    def test_queue_add_400_missing_prompt(self, tmp_path):
        """post_queue() returns 400 when 'prompt' field is missing."""
        import asyncio
        from praxis.api import post_queue

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_post_request({"mode": "build"})
            response = asyncio.run(post_queue(req))

        assert response.status_code == 400

    def test_queue_add_400_invalid_json(self, tmp_path):
        """post_queue() returns 400 when request body is not valid JSON."""
        import asyncio
        from praxis.api import post_queue

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_invalid_json_request()
            response = asyncio.run(post_queue(req))

        assert response.status_code == 400

    def test_queue_add_401_bad_token(self, tmp_path):
        """post_queue() returns 401 when token is wrong."""
        import asyncio
        from praxis.api import post_queue

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_post_request({"prompt": "test"}, auth_header="Bearer wrong")
            response = asyncio.run(post_queue(req))

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/queue/{task_id}
# ---------------------------------------------------------------------------

def _make_path_request(path_params: dict, auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request with path_params."""
    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.query_params = {}
    req.path_params = path_params
    return req


class TestGetQueueTask:
    def test_queue_task_detail(self, tmp_path):
        """get_queue_task() returns full task dict for a known task_id."""
        import asyncio
        import json
        from praxis.queue import Task, TaskQueue
        from praxis.api import get_queue_task

        queue_dir = tmp_path / ".praxis" / "queue"
        tq = TaskQueue(queue_dir)
        task = Task.create(prompt="detail test", priority=2)
        tq.append(task)

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_path_request({"task_id": task.id})
            response = asyncio.run(get_queue_task(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["id"] == task.id
        assert body["prompt"] == "detail test"
        assert body["status"] == "pending"

    def test_queue_task_detail_404(self, tmp_path):
        """get_queue_task() returns 404 for an unknown task_id."""
        import asyncio
        from praxis.api import get_queue_task

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_path_request({"task_id": "nonexistent000"})
            response = asyncio.run(get_queue_task(req))

        assert response.status_code == 404

    def test_queue_task_detail_includes_result_file(self, tmp_path):
        """get_queue_task() includes result from results/{id}.txt if exists."""
        import asyncio
        import json
        from praxis.queue import Task, TaskQueue
        from praxis.api import get_queue_task

        queue_dir = tmp_path / ".praxis" / "queue"
        tq = TaskQueue(queue_dir)
        task = Task.create(prompt="result file test", priority=0)
        tq.append(task)
        tq.write_result(task.id, "the result content")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_path_request({"task_id": task.id})
            response = asyncio.run(get_queue_task(req))

        body = json.loads(response.body)
        assert body["result"] == "the result content"


# ---------------------------------------------------------------------------
# DELETE /api/queue/{task_id}
# ---------------------------------------------------------------------------

class TestDeleteQueueTask:
    def test_queue_cancel_pending(self, tmp_path):
        """delete_queue_task() cancels a pending task with 204."""
        import asyncio
        from praxis.queue import Task, TaskQueue
        from praxis.api import delete_queue_task

        queue_dir = tmp_path / ".praxis" / "queue"
        tq = TaskQueue(queue_dir)
        task = Task.create(prompt="cancel me", priority=0)
        tq.append(task)

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_path_request({"task_id": task.id})
            response = asyncio.run(delete_queue_task(req))

        assert response.status_code == 204

        # Verify status was updated to 'failed' with error='cancelled'.
        updated = tq._read_all()
        assert updated[0].status == "failed"
        assert updated[0].error == "cancelled"

    def test_queue_cancel_running_409(self, tmp_path):
        """delete_queue_task() returns 409 when task is running."""
        import asyncio
        from praxis.queue import Task, TaskQueue
        from praxis.api import delete_queue_task

        queue_dir = tmp_path / ".praxis" / "queue"
        tq = TaskQueue(queue_dir)
        task = Task.create(prompt="currently running", priority=0)
        tq.append(task)
        tq.update_status(task.id, "running")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_path_request({"task_id": task.id})
            response = asyncio.run(delete_queue_task(req))

        assert response.status_code == 409

    def test_queue_cancel_done_409(self, tmp_path):
        """delete_queue_task() returns 409 when task is already done."""
        import asyncio
        from praxis.queue import Task, TaskQueue
        from praxis.api import delete_queue_task

        queue_dir = tmp_path / ".praxis" / "queue"
        tq = TaskQueue(queue_dir)
        task = Task.create(prompt="already done", priority=0)
        tq.append(task)
        tq.update_status(task.id, "done")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_path_request({"task_id": task.id})
            response = asyncio.run(delete_queue_task(req))

        assert response.status_code == 409

    def test_queue_cancel_404_not_found(self, tmp_path):
        """delete_queue_task() returns 404 for an unknown task_id."""
        import asyncio
        from praxis.api import delete_queue_task

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_path_request({"task_id": "doesnotexist00"})
            response = asyncio.run(delete_queue_task(req))

        assert response.status_code == 404

    def test_queue_cancel_401_bad_token(self, tmp_path):
        """delete_queue_task() returns 401 when token is wrong."""
        import asyncio
        from praxis.api import delete_queue_task

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_path_request({"task_id": "anyid"}, auth_header="Bearer wrong")
            response = asyncio.run(delete_queue_task(req))

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/approvals
# ---------------------------------------------------------------------------

def _make_get_request(query_params: dict | None = None, auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request for GET endpoints."""
    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.query_params = query_params or {}
    req.path_params = {}
    return req


def _make_approval_post_request(
    path_params: dict, body: dict | None = None, auth_header: str = ""
) -> MagicMock:
    """Return a mock Starlette Request for POST approval endpoints."""
    import asyncio
    import json

    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.path_params = path_params
    req.query_params = {}
    if body is not None:
        raw = json.dumps(body).encode()
        async def _json():
            return json.loads(raw)
        req.json = _json
    return req


class TestGetApprovals:
    def test_approvals_list_empty(self, tmp_path):
        """get_approvals() returns empty list when staging dir does not exist."""
        import asyncio
        import json
        from praxis.api import get_approvals

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_get_request()
            response = asyncio.run(get_approvals(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body == {"items": []}

    def test_approvals_list_with_external_actions(self, tmp_path):
        """get_approvals() returns pending external actions from external_actions.jsonl."""
        import asyncio
        import json
        import uuid
        from praxis.api import get_approvals

        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        action_id = str(uuid.uuid4())
        entry = {
            "id": action_id,
            "provider": "notion",
            "action": "create_page",
            "params": {"title": "Test Page"},
            "queued_at": "2026-06-01T12:00:00+00:00",
            "status": "pending",
        }
        (staging / "external_actions.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_get_request()
            response = asyncio.run(get_approvals(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["id"] == action_id
        assert item["status"] == "pending"
        assert item["provider"] == "notion"

    def test_approvals_list_excludes_already_approved(self, tmp_path):
        """get_approvals() excludes entries with status != 'pending'."""
        import asyncio
        import json
        import uuid
        from praxis.api import get_approvals

        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        entries = [
            {"id": str(uuid.uuid4()), "provider": "notion", "action": "create_page",
             "params": {}, "queued_at": "2026-06-01T12:00:00+00:00", "status": "pending"},
            {"id": str(uuid.uuid4()), "provider": "linear", "action": "create_issue",
             "params": {}, "queued_at": "2026-06-01T11:00:00+00:00", "status": "approved"},
        ]
        text = "\n".join(json.dumps(e) for e in entries) + "\n"
        (staging / "external_actions.jsonl").write_text(text, encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_get_request()
            response = asyncio.run(get_approvals(req))

        body = json.loads(response.body)
        assert len(body["items"]) == 1
        assert body["items"][0]["status"] == "pending"

    def test_approvals_list_401_bad_token(self, tmp_path):
        """get_approvals() returns 401 when token is wrong."""
        import asyncio
        from praxis.api import get_approvals

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_get_request(auth_header="Bearer wrong")
            response = asyncio.run(get_approvals(req))

        assert response.status_code == 401

    def test_approvals_list_includes_slack_messages(self, tmp_path):
        """get_approvals() includes staged Slack messages."""
        import asyncio
        import json
        import uuid
        from praxis.api import get_approvals

        msg_id = str(uuid.uuid4())
        msg_dir = tmp_path / ".praxis" / "staging" / "slack" / "messages"
        msg_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "id": msg_id,
            "created_at": "2026-06-01T12:00:00",
            "recipient": "alice",
            "message": "Hello from staging",
            "status": "staged",
        }
        (msg_dir / f"{msg_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_get_request()
            response = asyncio.run(get_approvals(req))

        body = json.loads(response.body)
        ids = [it["id"] for it in body["items"]]
        assert msg_id in ids


# ---------------------------------------------------------------------------
# POST /api/approvals/{id}/approve  and  /reject
# ---------------------------------------------------------------------------

class TestPostApprovalAction:
    def test_approval_approve_sets_status(self, tmp_path):
        """post_approval_action() sets status to 'approved' for an external action."""
        import asyncio
        import json
        import uuid
        from unittest.mock import patch as _patch
        from praxis.api import post_approval_action

        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        action_id = str(uuid.uuid4())
        entry = {
            "id": action_id,
            "provider": "notion",
            "action": "create_page",
            "params": {"title": "Test Page"},
            "queued_at": "2026-06-01T12:00:00+00:00",
            "status": "pending",
        }
        (staging / "external_actions.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with _patch("praxis.__main__._execute_approved_action", return_value="ok: created"):
                req = _make_approval_post_request({"approval_id": action_id, "action": "approve"})
                response = asyncio.run(post_approval_action(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["status"] == "approved"
        assert body["id"] == action_id

        # Verify file was rewritten.
        updated = json.loads((staging / "external_actions.jsonl").read_text())
        assert updated["status"] == "approved"

    def test_approval_reject_sets_status(self, tmp_path):
        """post_approval_action() sets status to 'rejected' for an external action."""
        import asyncio
        import json
        import uuid
        from praxis.api import post_approval_action

        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        action_id = str(uuid.uuid4())
        entry = {
            "id": action_id,
            "provider": "linear",
            "action": "create_issue",
            "params": {"title": "Bug"},
            "queued_at": "2026-06-01T12:00:00+00:00",
            "status": "pending",
        }
        (staging / "external_actions.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_approval_post_request({"approval_id": action_id, "action": "reject"})
            response = asyncio.run(post_approval_action(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["status"] == "rejected"

    def test_approval_action_404_not_found(self, tmp_path):
        """post_approval_action() returns 404 when the approval_id does not exist."""
        import asyncio
        from praxis.api import post_approval_action

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_approval_post_request({"approval_id": "doesnotexist", "action": "approve"})
            response = asyncio.run(post_approval_action(req))

        assert response.status_code == 404

    def test_approval_approve_slack_message(self, tmp_path):
        """post_approval_action() approves a staged Slack message."""
        import asyncio
        import json
        import uuid
        from praxis.api import post_approval_action

        msg_id = str(uuid.uuid4())
        msg_dir = tmp_path / ".praxis" / "staging" / "slack" / "messages"
        msg_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "id": msg_id,
            "created_at": "2026-06-01T12:00:00",
            "recipient": "alice",
            "message": "Hello",
            "status": "staged",
        }
        (msg_dir / f"{msg_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_approval_post_request({"approval_id": msg_id, "action": "approve"})
            response = asyncio.run(post_approval_action(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["status"] == "approved"

        updated = json.loads((msg_dir / f"{msg_id}.json").read_text())
        assert updated["status"] == "approved"


# ---------------------------------------------------------------------------
# POST /api/approvals/bulk
# ---------------------------------------------------------------------------

class TestPostApprovalsBulk:
    def test_approvals_bulk_approve_all(self, tmp_path):
        """post_approvals_bulk() approves all specified items."""
        import asyncio
        import json
        import uuid
        from unittest.mock import patch as _patch
        from praxis.api import post_approvals_bulk

        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True, exist_ok=True)

        ids = [str(uuid.uuid4()) for _ in range(3)]
        lines = []
        for aid in ids:
            lines.append(json.dumps({
                "id": aid, "provider": "notion", "action": "create_page",
                "params": {}, "queued_at": "2026-06-01T12:00:00+00:00", "status": "pending",
            }))
        (staging / "external_actions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with _patch("praxis.__main__._execute_approved_action", return_value="ok"):
                req = _make_approval_post_request(
                    path_params={},
                    body={"ids": ids, "action": "approve"},
                )
                response = asyncio.run(post_approvals_bulk(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        results = body["results"]
        assert len(results) == 3
        for r in results:
            assert r["status"] == "approved"

    def test_approvals_bulk_reject_all(self, tmp_path):
        """post_approvals_bulk() rejects all specified items."""
        import asyncio
        import json
        import uuid
        from praxis.api import post_approvals_bulk

        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True, exist_ok=True)

        ids = [str(uuid.uuid4()) for _ in range(2)]
        lines = []
        for aid in ids:
            lines.append(json.dumps({
                "id": aid, "provider": "linear", "action": "create_issue",
                "params": {}, "queued_at": "2026-06-01T12:00:00+00:00", "status": "pending",
            }))
        (staging / "external_actions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_approval_post_request(
                path_params={},
                body={"ids": ids, "action": "reject"},
            )
            response = asyncio.run(post_approvals_bulk(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        results = body["results"]
        assert all(r["status"] == "rejected" for r in results)

    def test_approvals_bulk_mixed_found_not_found(self, tmp_path):
        """post_approvals_bulk() handles a mix of found and not-found ids."""
        import asyncio
        import json
        import uuid
        from praxis.api import post_approvals_bulk

        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True, exist_ok=True)

        real_id = str(uuid.uuid4())
        fake_id = "does-not-exist"
        (staging / "external_actions.jsonl").write_text(
            json.dumps({
                "id": real_id, "provider": "notion", "action": "create_page",
                "params": {}, "queued_at": "2026-06-01T12:00:00+00:00", "status": "pending",
            }) + "\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_approval_post_request(
                path_params={},
                body={"ids": [real_id, fake_id], "action": "reject"},
            )
            response = asyncio.run(post_approvals_bulk(req))

        body = json.loads(response.body)
        results = {r["id"]: r["status"] for r in body["results"]}
        assert results[real_id] == "rejected"
        assert results[fake_id] == "not_found"

    def test_approvals_bulk_400_bad_action(self, tmp_path):
        """post_approvals_bulk() returns 400 for an invalid action."""
        import asyncio
        from praxis.api import post_approvals_bulk

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_approval_post_request(
                path_params={},
                body={"ids": [], "action": "execute"},
            )
            response = asyncio.run(post_approvals_bulk(req))

        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Helpers for schedule tests
# ---------------------------------------------------------------------------

def _make_schedule_get_request(query_params: dict | None = None, auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request for GET schedule endpoints."""
    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.query_params = query_params or {}
    req.path_params = {}
    return req


def _make_schedule_post_request(
    path_params: dict | None = None,
    body: dict | None = None,
    auth_header: str = "",
) -> MagicMock:
    """Return a mock Starlette Request for POST/PUT/DELETE schedule endpoints."""
    import asyncio
    import json

    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.path_params = path_params or {}
    req.query_params = {}

    async def _json():
        return body or {}

    req.json = _json
    return req


def _make_schedule_path_request(path_params: dict, auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request with path_params (no body)."""
    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.path_params = path_params
    req.query_params = {}
    return req


# ---------------------------------------------------------------------------
# GET /api/schedule — test_schedule_list
# ---------------------------------------------------------------------------

class TestGetSchedule:
    def test_schedule_list_empty(self, tmp_path):
        """get_schedule() returns empty list when no tasks exist."""
        import asyncio
        import json
        from praxis.api import get_schedule

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_get_request()
            response = asyncio.run(get_schedule(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert "tasks" in body
        assert body["tasks"] == []

    def test_schedule_list_with_tasks(self, tmp_path):
        """get_schedule() returns tasks from .praxis/schedule/tasks.json."""
        import asyncio
        import json
        from praxis.api import get_schedule

        schedule_dir = tmp_path / ".praxis" / "schedule"
        schedule_dir.mkdir(parents=True, exist_ok=True)
        tasks_data = [
            {
                "id": "task-1",
                "name": "Morning report",
                "prompt": "Summarize overnight events",
                "schedule": "0 8 * * *",
                "enabled": True,
                "last_run": None,
                "next_run": "2026-06-03T08:00:00+00:00",
                "created_at": "2026-06-02T12:00:00+00:00",
            }
        ]
        (schedule_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_get_request()
            response = asyncio.run(get_schedule(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert len(body["tasks"]) == 1
        assert body["tasks"][0]["name"] == "Morning report"
        assert body["tasks"][0]["enabled"] is True

    def test_schedule_list_401_with_bad_token(self, tmp_path):
        """get_schedule() returns 401 when token is wrong."""
        import asyncio
        from praxis.api import get_schedule

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_get_request(auth_header="Bearer wrong")
            response = asyncio.run(get_schedule(req))

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/schedule — test_schedule_add
# ---------------------------------------------------------------------------

class TestPostSchedule:
    def test_schedule_add(self, tmp_path):
        """post_schedule() creates a new scheduled task and returns 201."""
        import asyncio
        import json
        from unittest.mock import patch as _patch
        from praxis.api import post_schedule

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with _patch("praxis.scheduler._compute_next_run", return_value="2026-06-03T09:00:00+00:00"):
                req = _make_schedule_post_request(body={
                    "name": "Daily summary",
                    "prompt": "Summarize the day",
                    "schedule": "0 9 * * *",
                })
                response = asyncio.run(post_schedule(req))

        assert response.status_code == 201
        body = json.loads(response.body)
        assert "task" in body
        assert body["task"]["name"] == "Daily summary"
        assert body["task"]["prompt"] == "Summarize the day"
        assert body["task"]["schedule"] == "0 9 * * *"
        assert "id" in body["task"]
        assert body["task"]["enabled"] is True

    def test_schedule_add_persists_to_file(self, tmp_path):
        """post_schedule() saves the task to .praxis/schedule/tasks.json."""
        import asyncio
        import json
        from unittest.mock import patch as _patch
        from praxis.api import post_schedule

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with _patch("praxis.scheduler._compute_next_run", return_value="2026-06-03T09:00:00+00:00"):
                req = _make_schedule_post_request(body={
                    "name": "Persist test",
                    "prompt": "Check persistence",
                    "schedule": "*/10 * * * *",
                })
                asyncio.run(post_schedule(req))

        tasks_file = tmp_path / ".praxis" / "schedule" / "tasks.json"
        assert tasks_file.exists()
        saved = json.loads(tasks_file.read_text())
        assert len(saved) == 1
        assert saved[0]["name"] == "Persist test"

    def test_schedule_add_missing_name(self, tmp_path):
        """post_schedule() returns 400 if name is missing."""
        import asyncio
        from praxis.api import post_schedule

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_post_request(body={
                "prompt": "Do something",
                "schedule": "0 * * * *",
            })
            response = asyncio.run(post_schedule(req))

        assert response.status_code == 400

    def test_schedule_add_missing_prompt(self, tmp_path):
        """post_schedule() returns 400 if prompt is missing."""
        import asyncio
        from praxis.api import post_schedule

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_post_request(body={
                "name": "Task name",
                "schedule": "0 * * * *",
            })
            response = asyncio.run(post_schedule(req))

        assert response.status_code == 400

    def test_schedule_add_invalid_cron(self, tmp_path):
        """post_schedule() returns 400 if cron expression is invalid."""
        import asyncio
        from unittest.mock import patch as _patch
        from praxis.api import post_schedule

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with _patch("praxis.scheduler._compute_next_run", side_effect=ValueError("bad cron")):
                req = _make_schedule_post_request(body={
                    "name": "Bad cron",
                    "prompt": "Do something",
                    "schedule": "not a cron",
                })
                response = asyncio.run(post_schedule(req))

        assert response.status_code == 400


# ---------------------------------------------------------------------------
# PUT /api/schedule/{task_id} — update
# ---------------------------------------------------------------------------

class TestPutScheduleTask:
    def test_schedule_update_name(self, tmp_path):
        """put_schedule_task() updates the task name."""
        import asyncio
        import json
        from praxis.api import put_schedule_task

        schedule_dir = tmp_path / ".praxis" / "schedule"
        schedule_dir.mkdir(parents=True, exist_ok=True)
        task_id = "abc-123"
        tasks_data = [{
            "id": task_id,
            "name": "Old name",
            "prompt": "Do something",
            "schedule": "0 9 * * *",
            "enabled": True,
            "last_run": None,
            "next_run": "2026-06-03T09:00:00+00:00",
            "created_at": "2026-06-02T12:00:00+00:00",
        }]
        (schedule_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_post_request(
                path_params={"task_id": task_id},
                body={"name": "New name"},
            )
            response = asyncio.run(put_schedule_task(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["task"]["name"] == "New name"

    def test_schedule_update_not_found(self, tmp_path):
        """put_schedule_task() returns 404 for unknown task_id."""
        import asyncio
        from praxis.api import put_schedule_task

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_post_request(
                path_params={"task_id": "does-not-exist"},
                body={"name": "Updated"},
            )
            response = asyncio.run(put_schedule_task(req))

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/schedule/{task_id}
# ---------------------------------------------------------------------------

class TestDeleteScheduleTask:
    def test_schedule_delete(self, tmp_path):
        """delete_schedule_task() removes the task and returns 204."""
        import asyncio
        import json
        from praxis.api import delete_schedule_task

        schedule_dir = tmp_path / ".praxis" / "schedule"
        schedule_dir.mkdir(parents=True, exist_ok=True)
        task_id = "del-task-1"
        tasks_data = [{
            "id": task_id,
            "name": "To be deleted",
            "prompt": "Delete me",
            "schedule": "0 * * * *",
            "enabled": True,
            "last_run": None,
            "next_run": "2026-06-03T00:00:00+00:00",
            "created_at": "2026-06-02T12:00:00+00:00",
        }]
        (schedule_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_path_request(path_params={"task_id": task_id})
            response = asyncio.run(delete_schedule_task(req))

        assert response.status_code == 204
        # Verify file was updated
        saved = json.loads((schedule_dir / "tasks.json").read_text())
        assert len(saved) == 0

    def test_schedule_delete_not_found(self, tmp_path):
        """delete_schedule_task() returns 404 for unknown task_id."""
        import asyncio
        from praxis.api import delete_schedule_task

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_path_request(path_params={"task_id": "no-such-task"})
            response = asyncio.run(delete_schedule_task(req))

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/schedule/{task_id}/enable + /disable — test_schedule_enable_disable
# ---------------------------------------------------------------------------

class TestScheduleEnableDisable:
    def _write_task(self, schedule_dir, task_id: str, enabled: bool):
        import json
        tasks_data = [{
            "id": task_id,
            "name": "Toggle test",
            "prompt": "Run something",
            "schedule": "0 10 * * *",
            "enabled": enabled,
            "last_run": None,
            "next_run": "2026-06-03T10:00:00+00:00",
            "created_at": "2026-06-02T12:00:00+00:00",
        }]
        (schedule_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

    def test_schedule_enable(self, tmp_path):
        """post_schedule_enable() enables a disabled task."""
        import asyncio
        import json
        from praxis.api import post_schedule_enable

        schedule_dir = tmp_path / ".praxis" / "schedule"
        schedule_dir.mkdir(parents=True, exist_ok=True)
        task_id = "en-task-1"
        self._write_task(schedule_dir, task_id, enabled=False)

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_path_request(path_params={"task_id": task_id})
            response = asyncio.run(post_schedule_enable(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["task"]["enabled"] is True

    def test_schedule_disable(self, tmp_path):
        """post_schedule_disable() disables an enabled task."""
        import asyncio
        import json
        from praxis.api import post_schedule_disable

        schedule_dir = tmp_path / ".praxis" / "schedule"
        schedule_dir.mkdir(parents=True, exist_ok=True)
        task_id = "dis-task-1"
        self._write_task(schedule_dir, task_id, enabled=True)

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_path_request(path_params={"task_id": task_id})
            response = asyncio.run(post_schedule_disable(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["task"]["enabled"] is False

    def test_schedule_enable_not_found(self, tmp_path):
        """post_schedule_enable() returns 404 for unknown task."""
        import asyncio
        from praxis.api import post_schedule_enable

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_path_request(path_params={"task_id": "ghost"})
            response = asyncio.run(post_schedule_enable(req))

        assert response.status_code == 404

    def test_schedule_disable_not_found(self, tmp_path):
        """post_schedule_disable() returns 404 for unknown task."""
        import asyncio
        from praxis.api import post_schedule_disable

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_path_request(path_params={"task_id": "ghost"})
            response = asyncio.run(post_schedule_disable(req))

        assert response.status_code == 404

    def test_schedule_enable_persists_to_file(self, tmp_path):
        """post_schedule_enable() saves updated enabled=True to disk."""
        import asyncio
        import json
        from praxis.api import post_schedule_enable

        schedule_dir = tmp_path / ".praxis" / "schedule"
        schedule_dir.mkdir(parents=True, exist_ok=True)
        task_id = "persist-en-1"
        self._write_task(schedule_dir, task_id, enabled=False)

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_path_request(path_params={"task_id": task_id})
            asyncio.run(post_schedule_enable(req))

        saved = json.loads((schedule_dir / "tasks.json").read_text())
        assert saved[0]["enabled"] is True


# ---------------------------------------------------------------------------
# POST /api/schedule/{task_id}/run-now — test_schedule_run_now
# ---------------------------------------------------------------------------

class TestScheduleRunNow:
    def test_schedule_run_now(self, tmp_path):
        """post_schedule_run_now() enqueues the task immediately and returns 201."""
        import asyncio
        import json
        from praxis.api import post_schedule_run_now

        schedule_dir = tmp_path / ".praxis" / "schedule"
        schedule_dir.mkdir(parents=True, exist_ok=True)
        task_id = "run-task-1"
        tasks_data = [{
            "id": task_id,
            "name": "Run now test",
            "prompt": "Do it now",
            "schedule": "0 * * * *",
            "enabled": True,
            "last_run": None,
            "next_run": "2026-06-03T00:00:00+00:00",
            "created_at": "2026-06-02T12:00:00+00:00",
        }]
        (schedule_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_path_request(path_params={"task_id": task_id})
            response = asyncio.run(post_schedule_run_now(req))

        assert response.status_code == 201
        body = json.loads(response.body)
        assert "task_id" in body
        assert isinstance(body["task_id"], str)

        # Verify the task was enqueued
        queue_file = tmp_path / ".praxis" / "queue" / "tasks.jsonl"
        assert queue_file.exists()
        lines = [l for l in queue_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        queued = json.loads(lines[0])
        assert queued["prompt"] == "Do it now"

    def test_schedule_run_now_not_found(self, tmp_path):
        """post_schedule_run_now() returns 404 for unknown task."""
        import asyncio
        from praxis.api import post_schedule_run_now

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_path_request(path_params={"task_id": "ghost-task"})
            response = asyncio.run(post_schedule_run_now(req))

        assert response.status_code == 404

    def test_schedule_run_now_401_with_bad_token(self, tmp_path):
        """post_schedule_run_now() returns 401 when token is wrong."""
        import asyncio
        from praxis.api import post_schedule_run_now

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "mytoken", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_schedule_path_request(path_params={"task_id": "any"}, auth_header="Bearer wrong")
            response = asyncio.run(post_schedule_run_now(req))

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Wiki helpers
# ---------------------------------------------------------------------------

def _make_wiki_request(query_params: dict | None = None, auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request for wiki GET endpoints."""
    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.query_params = query_params or {}
    req.path_params = {}
    return req


def _make_wiki_path_request(path_params: dict, auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request with path_params for wiki detail endpoint."""
    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.path_params = path_params
    req.query_params = {}
    return req


def _make_body_request(body: dict, auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request with an async body() method returning JSON."""
    import asyncio
    import json

    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    req.query_params = {}
    req.path_params = {}

    async def _body():
        return json.dumps(body).encode("utf-8")

    req.body = _body
    return req


# ---------------------------------------------------------------------------
# GET /api/wiki/search — TestWikiSearch
# ---------------------------------------------------------------------------

class TestWikiSearch:
    def test_wiki_search_matches_slug(self, tmp_path):
        """get_wiki_search() returns results when query matches wiki page slug.

        This is the required test_wiki_search_matches_slug from the feature spec.
        """
        import asyncio
        import json
        from praxis.api import get_wiki_search

        pages_dir = tmp_path / "wiki" / "pages"
        pages_dir.mkdir(parents=True)
        (pages_dir / "aiden-antony.md").write_text(
            "---\nentity: Aiden Antony\n---\n\nSoftware engineer.", encoding="utf-8"
        )
        (pages_dir / "praxis-kernel.md").write_text(
            "---\nentity: Praxis Kernel\n---\n\nAgentic OS.", encoding="utf-8"
        )

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request(query_params={"q": "aiden"})
            response = asyncio.run(get_wiki_search(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert "results" in body
        slugs = [r["slug"] for r in body["results"]]
        assert "aiden-antony" in slugs

    def test_wiki_search_empty_query(self, tmp_path):
        """get_wiki_search() returns empty results for blank query."""
        import asyncio
        import json
        from praxis.api import get_wiki_search

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request(query_params={"q": ""})
            response = asyncio.run(get_wiki_search(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["results"] == []

    def test_wiki_search_no_match(self, tmp_path):
        """get_wiki_search() returns empty results when query matches nothing."""
        import asyncio
        import json
        from praxis.api import get_wiki_search

        pages_dir = tmp_path / "wiki" / "pages"
        pages_dir.mkdir(parents=True)
        (pages_dir / "foo.md").write_text("---\nentity: Foo\n---\n\nContent.", encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request(query_params={"q": "zzznomatch"})
            response = asyncio.run(get_wiki_search(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["results"] == []

    def test_wiki_search_sorted_by_score(self, tmp_path):
        """get_wiki_search() returns results sorted descending by score."""
        import asyncio
        import json
        from praxis.api import get_wiki_search

        pages_dir = tmp_path / "wiki" / "pages"
        pages_dir.mkdir(parents=True)
        (pages_dir / "one-mention.md").write_text("praxis is great.", encoding="utf-8")
        (pages_dir / "many-mentions.md").write_text(
            "praxis praxis praxis praxis praxis.", encoding="utf-8"
        )

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request(query_params={"q": "praxis"})
            response = asyncio.run(get_wiki_search(req))

        body = json.loads(response.body)
        results = body["results"]
        assert len(results) == 2
        assert results[0]["score"] >= results[1]["score"]
        assert results[0]["slug"] == "many-mentions"

    def test_wiki_search_401_with_bad_token(self, tmp_path):
        """get_wiki_search() returns 401 when token is wrong."""
        import asyncio
        from praxis.api import get_wiki_search

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request(auth_header="Bearer wrong")
            response = asyncio.run(get_wiki_search(req))

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/wiki/pages and /api/wiki/pages/{slug} — TestWikiPages
# ---------------------------------------------------------------------------

class TestWikiPages:
    def _write_page(self, pages_dir, slug: str, entity: str, body: str) -> None:
        content = f'---\nentity: "{entity}"\nvalid_from: "2026-01-01"\n---\n\n{body}'
        (pages_dir / f"{slug}.md").write_text(content, encoding="utf-8")

    def test_wiki_pages_list_empty(self, tmp_path):
        """get_wiki_pages() returns empty list when no pages exist."""
        import asyncio
        import json
        from praxis.api import get_wiki_pages

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request()
            response = asyncio.run(get_wiki_pages(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert "pages" in body
        assert body["pages"] == []

    def test_wiki_pages_list_with_pages(self, tmp_path):
        """get_wiki_pages() returns list of pages with frontmatter."""
        import asyncio
        import json
        from praxis.api import get_wiki_pages

        pages_dir = tmp_path / "wiki" / "pages"
        pages_dir.mkdir(parents=True)
        self._write_page(pages_dir, "alice", "Alice", "Alice is a developer.")
        self._write_page(pages_dir, "bob", "Bob", "Bob is a designer.")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request()
            response = asyncio.run(get_wiki_pages(req))

        body = json.loads(response.body)
        slugs = [p["slug"] for p in body["pages"]]
        assert "alice" in slugs
        assert "bob" in slugs
        alice = next(p for p in body["pages"] if p["slug"] == "alice")
        assert alice["frontmatter"]["entity"] == "Alice"

    def test_wiki_page_detail_404(self, tmp_path):
        """get_wiki_page_detail() returns 404 for unknown slug.

        This is the required test_wiki_page_detail_404 from the feature spec.
        """
        import asyncio
        import json
        from praxis.api import get_wiki_page_detail

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_path_request(path_params={"slug": "does-not-exist"})
            response = asyncio.run(get_wiki_page_detail(req))

        assert response.status_code == 404
        body = json.loads(response.body)
        assert "error" in body

    def test_wiki_page_detail_returns_content(self, tmp_path):
        """get_wiki_page_detail() returns full content and frontmatter."""
        import asyncio
        import json
        from praxis.api import get_wiki_page_detail

        pages_dir = tmp_path / "wiki" / "pages"
        pages_dir.mkdir(parents=True)
        self._write_page(pages_dir, "charlie", "Charlie", "Charlie builds things.")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_path_request(path_params={"slug": "charlie"})
            response = asyncio.run(get_wiki_page_detail(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["slug"] == "charlie"
        assert "Charlie builds things." in body["content"]
        assert body["frontmatter"]["entity"] == "Charlie"


# ---------------------------------------------------------------------------
# GET /api/memory — TestGetMemory
# ---------------------------------------------------------------------------

class TestGetMemory:
    def test_memory_returns_entries(self, tmp_path):
        """get_memory() returns entries from conversation jsonl files."""
        import asyncio
        import json
        from praxis.api import get_memory

        conv_dir = tmp_path / ".praxis" / "memory" / "conversations"
        conv_dir.mkdir(parents=True)
        entries = [
            {"ts": f"2026-06-0{i}T00:00:00Z", "prompt": f"task {i}", "outcome": "success"}
            for i in range(1, 4)
        ]
        (conv_dir / "2026-06-01.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
        )

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request()
            response = asyncio.run(get_memory(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert "entries" in body
        assert len(body["entries"]) == 3

    def test_memory_no_directory(self, tmp_path):
        """get_memory() returns empty list when conversations dir does not exist."""
        import asyncio
        import json
        from praxis.api import get_memory

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request()
            response = asyncio.run(get_memory(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["entries"] == []

    def test_memory_capped_at_10(self, tmp_path):
        """get_memory() returns at most 10 entries."""
        import asyncio
        import json
        from praxis.api import get_memory

        conv_dir = tmp_path / ".praxis" / "memory" / "conversations"
        conv_dir.mkdir(parents=True)
        entries = [
            {"ts": f"2026-06-02T00:00:{i:02d}Z", "prompt": f"p{i}", "outcome": "success"}
            for i in range(20)
        ]
        (conv_dir / "2026-06-02.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
        )

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request()
            response = asyncio.run(get_memory(req))

        body = json.loads(response.body)
        assert len(body["entries"]) == 10


# ---------------------------------------------------------------------------
# GET/POST /api/integrations — TestIntegrations
# ---------------------------------------------------------------------------

class TestIntegrations:
    def test_integrations_uses_cache(self, tmp_path):
        """get_integrations() returns cached result on second call without re-running.

        This is the required test_integrations_uses_cache from the feature spec.
        """
        import asyncio
        import json
        import praxis.api as api_module
        from praxis.api import get_integrations

        # Prime the cache manually to avoid network calls.
        api_module._integrations_cache = {
            "data": [{"name": "Email", "status": "skip", "message": "not configured"}]
        }
        api_module._integrations_cache_ts = api_module._time.monotonic()

        call_count = {"n": 0}
        orig = api_module._run_validation

        def mock_validate(root):
            call_count["n"] += 1
            return orig(root)

        api_module._run_validation = mock_validate
        try:
            with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
                req = _make_wiki_request()
                response = asyncio.run(get_integrations(req))
        finally:
            api_module._run_validation = orig
            # Reset cache to avoid polluting other tests.
            api_module._integrations_cache = {}
            api_module._integrations_cache_ts = 0.0

        assert response.status_code == 200
        body = json.loads(response.body)
        assert "integrations" in body
        # _run_validation should NOT have been called because cache was warm.
        assert call_count["n"] == 0

    def test_integrations_returns_list(self, tmp_path):
        """get_integrations() returns a list of integration status objects."""
        import asyncio
        import json
        import praxis.api as api_module
        from praxis.api import get_integrations

        # Ensure cache is cold.
        api_module._integrations_cache = {}
        api_module._integrations_cache_ts = 0.0

        mock_data = [
            {"name": "Email", "status": "skip", "message": "not configured"},
            {"name": "Slack", "status": "skip", "message": "not configured"},
        ]

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with patch("praxis.api._run_validation", return_value=mock_data):
                req = _make_wiki_request()
                response = asyncio.run(get_integrations(req))

        # Reset cache.
        api_module._integrations_cache = {}
        api_module._integrations_cache_ts = 0.0

        assert response.status_code == 200
        body = json.loads(response.body)
        assert "integrations" in body
        assert len(body["integrations"]) == 2

    def test_post_integrations_validate_force_fresh(self, tmp_path):
        """post_integrations_validate() ignores cache and re-runs validation."""
        import asyncio
        import json
        import praxis.api as api_module
        from praxis.api import post_integrations_validate

        # Prime a warm cache.
        api_module._integrations_cache = {
            "data": [{"name": "Email", "status": "pass", "message": "ok"}]
        }
        api_module._integrations_cache_ts = api_module._time.monotonic()

        fresh_data = [{"name": "Email", "status": "fail", "message": "down"}]

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with patch("praxis.api._run_validation", return_value=fresh_data):
                req = _make_wiki_request()
                response = asyncio.run(post_integrations_validate(req))

        api_module._integrations_cache = {}
        api_module._integrations_cache_ts = 0.0

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["integrations"][0]["status"] == "fail"


# ---------------------------------------------------------------------------
# GET/PUT /api/soul and /api/heartbeat — TestSoulHeartbeat
# ---------------------------------------------------------------------------

class TestSoulHeartbeat:
    def test_get_soul_404_when_missing(self, tmp_path):
        """get_soul() returns 404 when SOUL.md does not exist."""
        import asyncio
        from praxis.api import get_soul

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request()
            response = asyncio.run(get_soul(req))

        assert response.status_code == 404

    def test_get_soul_returns_content(self, tmp_path):
        """get_soul() returns content of .praxis/SOUL.md."""
        import asyncio
        import json
        from praxis.api import get_soul

        praxis_dir = tmp_path / ".praxis"
        praxis_dir.mkdir(parents=True)
        (praxis_dir / "SOUL.md").write_text("# Soul\nI am Praxis.", encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request()
            response = asyncio.run(get_soul(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert "I am Praxis." in body["content"]

    def test_put_soul_writes_file(self, tmp_path):
        """put_soul() writes content to .praxis/SOUL.md."""
        import asyncio
        import json
        from praxis.api import put_soul

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_body_request({"content": "# New Soul\nPraxis is calm."})
            response = asyncio.run(put_soul(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["ok"] is True
        soul_path = tmp_path / ".praxis" / "SOUL.md"
        assert soul_path.exists()
        assert "Praxis is calm." in soul_path.read_text(encoding="utf-8")

    def test_put_soul_missing_content_field(self, tmp_path):
        """put_soul() returns 400 when 'content' field is missing."""
        import asyncio
        from praxis.api import put_soul

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_body_request({"not_content": "something"})
            response = asyncio.run(put_soul(req))

        assert response.status_code == 400

    def test_get_heartbeat_404_when_missing(self, tmp_path):
        """get_heartbeat() returns 404 when HEARTBEAT.md does not exist."""
        import asyncio
        from praxis.api import get_heartbeat

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request()
            response = asyncio.run(get_heartbeat(req))

        assert response.status_code == 404

    def test_get_heartbeat_returns_content(self, tmp_path):
        """get_heartbeat() returns content of .praxis/HEARTBEAT.md."""
        import asyncio
        import json
        from praxis.api import get_heartbeat

        praxis_dir = tmp_path / ".praxis"
        praxis_dir.mkdir(parents=True)
        (praxis_dir / "HEARTBEAT.md").write_text("# Heartbeat\nCheck email daily.", encoding="utf-8")

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_wiki_request()
            response = asyncio.run(get_heartbeat(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert "Check email daily." in body["content"]

    def test_put_heartbeat_writes_file(self, tmp_path):
        """put_heartbeat() writes content to .praxis/HEARTBEAT.md."""
        import asyncio
        import json
        from praxis.api import put_heartbeat

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_body_request({"content": "# Heartbeat\nRun at 8am."})
            response = asyncio.run(put_heartbeat(req))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["ok"] is True
        hb_path = tmp_path / ".praxis" / "HEARTBEAT.md"
        assert hb_path.exists()
        assert "Run at 8am." in hb_path.read_text(encoding="utf-8")

    def test_put_heartbeat_missing_content_field(self, tmp_path):
        """put_heartbeat() returns 400 when 'content' field is missing."""
        import asyncio
        from praxis.api import put_heartbeat

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            req = _make_body_request({"wrong": "data"})
            response = asyncio.run(put_heartbeat(req))

        assert response.status_code == 400


# ---------------------------------------------------------------------------
# ws_endpoint — WebSocket event streaming
# ---------------------------------------------------------------------------

class TestWsEndpoint:
    def test_ws_auth_rejected_without_token(self):
        """ws_endpoint closes with code 4403 when token auth fails."""
        import asyncio
        from unittest.mock import AsyncMock

        from praxis.api import ws_endpoint

        ws = MagicMock()
        ws.query_params = {}
        ws.headers = {}
        ws.close = AsyncMock()
        ws.accept = AsyncMock()

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            asyncio.run(ws_endpoint(ws))

        ws.close.assert_called_once_with(code=4403)
        ws.accept.assert_not_called()

    def test_ws_receives_events(self):
        """ws_endpoint subscribes to EventBus '*' and sends events as JSON."""
        import asyncio
        import json

        from starlette.websockets import WebSocketDisconnect

        from praxis.api import ws_endpoint
        from praxis.event_bus import EventBus

        ws = MagicMock()
        ws.query_params = {}
        ws.headers = {}
        ws.accept = AsyncMock()

        sent_texts: list[str] = []

        async def fake_send_text(text: str) -> None:
            sent_texts.append(text)
            raise WebSocketDisconnect()

        ws.send_text = fake_send_text

        bus = EventBus()

        async def run() -> None:
            # Start ws_endpoint concurrently, let it subscribe
            task = asyncio.ensure_future(ws_endpoint(ws))
            # Yield to let ws_endpoint reach bus.subscribe and queue.get
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            # Publish an event — wildcard subscriber receives the envelope
            await bus.publish("task.queued", {"task_id": "t-1"})
            await task

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": ""}):
            with patch("praxis.api.get_event_bus", return_value=bus):
                asyncio.run(run())

        assert len(sent_texts) == 1
        data = json.loads(sent_texts[0])
        assert data["type"] == "task.queued"
        assert data["data"]["task_id"] == "t-1"
