"""Tests for praxis/api.py — token auth helpers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


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
