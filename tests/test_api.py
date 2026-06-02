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
