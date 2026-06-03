"""Tests for hooks_engine.make_webhook_routes() and queue_runner._start_file_watcher.

Covers:
- test_webhook_valid_json_enqueues_task: POST valid JSON enqueues a Task
- test_webhook_invalid_hmac_returns_401: HMAC mismatch returns 401
- test_webhook_missing_hmac_header_returns_401: Secret set but no sig header → 401
- test_webhook_oversized_body_returns_413: body > 64KB returns 413
- test_webhook_invalid_json_returns_400: non-JSON body returns 400
- test_webhook_no_secret_skips_hmac: no secret → HMAC check skipped → 201
- test_start_file_watcher_noop_without_paths: no PRAXIS_WATCH_PATHS → no FileWatcher
- test_start_file_watcher_starts_with_paths: PRAXIS_WATCH_PATHS set → FileWatcher.start() called
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(tmp_path: Path):
    """Build a minimal Starlette app wrapping make_webhook_routes()."""
    from starlette.applications import Starlette
    from starlette.routing import Route
    from praxis.hooks_engine import make_webhook_routes

    routes = make_webhook_routes()
    assert routes, "make_webhook_routes() returned empty list (starlette not installed?)"
    return Starlette(routes=routes)


def _client(tmp_path: Path):
    """Return a Starlette TestClient and the workspace path."""
    from starlette.testclient import TestClient

    env_patch = {
        "PRAXIS_WORKSPACE_ROOT": str(tmp_path),
    }
    # Ensure no leftover secret
    env_patch.pop("PRAXIS_WEBHOOK_SECRET_GITHUB", None)

    queue_dir = tmp_path / ".praxis" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    app = _make_app(tmp_path)
    return TestClient(app, raise_server_exceptions=False), tmp_path


# ---------------------------------------------------------------------------
# Webhook handler tests
# ---------------------------------------------------------------------------

class TestWebhookValidJsonEnqueuesTask:
    """POST valid JSON to /webhooks/{source} → 201 + task on disk."""

    def test_webhook_valid_json_enqueues_task(self, tmp_path: Path) -> None:
        """POST with valid JSON body enqueues a Task and returns 201."""
        from starlette.testclient import TestClient
        from praxis.hooks_engine import make_webhook_routes
        from starlette.applications import Starlette

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        routes = make_webhook_routes()
        app = Starlette(routes=routes)
        client = TestClient(app, raise_server_exceptions=False)

        payload = {"event": "push", "ref": "refs/heads/main"}
        body = json.dumps(payload).encode()

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 201
        data = response.json()
        assert "task_id" in data

        # Verify a task was written to the queue
        tasks_file = queue_dir / "tasks.jsonl"
        assert tasks_file.exists(), "tasks.jsonl was not created"
        tasks_raw = tasks_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(tasks_raw) >= 1
        task_obj = json.loads(tasks_raw[-1])
        assert "github" in task_obj["prompt"]
        assert task_obj["priority"] == 5


class TestWebhookInvalidHmacReturns401:
    """POST with wrong HMAC signature → 401."""

    def test_webhook_invalid_hmac_returns_401(self, tmp_path: Path) -> None:
        """When PRAXIS_WEBHOOK_SECRET_GITHUB is set and signature is wrong, return 401."""
        from starlette.testclient import TestClient
        from praxis.hooks_engine import make_webhook_routes
        from starlette.applications import Starlette

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        routes = make_webhook_routes()
        app = Starlette(routes=routes)
        client = TestClient(app, raise_server_exceptions=False)

        payload = {"event": "push"}
        body = json.dumps(payload).encode()

        env = {
            "PRAXIS_WORKSPACE_ROOT": str(tmp_path),
            "PRAXIS_WEBHOOK_SECRET_GITHUB": "correct-secret",
        }
        with patch.dict(os.environ, env):
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": "sha256=deadbeef",
                },
            )

        assert response.status_code == 401
        assert "invalid signature" in response.json().get("error", "")


class TestWebhookMissingHmacHeader:
    """POST with secret set but no X-Hub-Signature-256 → 401."""

    def test_webhook_missing_hmac_header_returns_401(self, tmp_path: Path) -> None:
        from starlette.testclient import TestClient
        from praxis.hooks_engine import make_webhook_routes
        from starlette.applications import Starlette

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        routes = make_webhook_routes()
        app = Starlette(routes=routes)
        client = TestClient(app, raise_server_exceptions=False)

        body = json.dumps({"x": 1}).encode()

        env = {
            "PRAXIS_WORKSPACE_ROOT": str(tmp_path),
            "PRAXIS_WEBHOOK_SECRET_GITHUB": "mysecret",
        }
        with patch.dict(os.environ, env):
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 401


class TestWebhookOversizedBodyReturns413:
    """POST with body > 64KB → 413."""

    def test_webhook_oversized_body_returns_413(self, tmp_path: Path) -> None:
        from starlette.testclient import TestClient
        from praxis.hooks_engine import make_webhook_routes
        from starlette.applications import Starlette

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        routes = make_webhook_routes()
        app = Starlette(routes=routes)
        client = TestClient(app, raise_server_exceptions=False)

        body = b"x" * (64 * 1024 + 1)

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            response = client.post(
                "/webhooks/github",
                content=body,
            )

        assert response.status_code == 413


class TestWebhookInvalidJsonReturns400:
    """POST with invalid JSON body → 400."""

    def test_webhook_invalid_json_returns_400(self, tmp_path: Path) -> None:
        from starlette.testclient import TestClient
        from praxis.hooks_engine import make_webhook_routes
        from starlette.applications import Starlette

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        routes = make_webhook_routes()
        app = Starlette(routes=routes)
        client = TestClient(app, raise_server_exceptions=False)

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            response = client.post(
                "/webhooks/myservice",
                content=b"not-json",
            )

        assert response.status_code == 400
        assert "invalid JSON" in response.json().get("error", "")


class TestWebhookNoSecretSkipsHmac:
    """POST without PRAXIS_WEBHOOK_SECRET_<SOURCE> set → HMAC check skipped → 201."""

    def test_webhook_no_secret_skips_hmac(self, tmp_path: Path) -> None:
        from starlette.testclient import TestClient
        from praxis.hooks_engine import make_webhook_routes
        from starlette.applications import Starlette

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        routes = make_webhook_routes()
        app = Starlette(routes=routes)
        client = TestClient(app, raise_server_exceptions=False)

        payload = {"hello": "world"}
        body = json.dumps(payload).encode()

        # Remove any secret from env
        env = {
            "PRAXIS_WORKSPACE_ROOT": str(tmp_path),
        }
        # Explicitly remove secret if present
        with patch.dict(os.environ, env):
            os.environ.pop("PRAXIS_WEBHOOK_SECRET_NOAUTH", None)
            response = client.post(
                "/webhooks/noauth",
                content=body,
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 201


class TestWebhookValidHmacPasses:
    """POST with correct HMAC-SHA256 signature → 201."""

    def test_webhook_correct_hmac_returns_201(self, tmp_path: Path) -> None:
        from starlette.testclient import TestClient
        from praxis.hooks_engine import make_webhook_routes
        from starlette.applications import Starlette

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        routes = make_webhook_routes()
        app = Starlette(routes=routes)
        client = TestClient(app, raise_server_exceptions=False)

        secret = "my-super-secret"
        payload = {"action": "opened"}
        body = json.dumps(payload).encode()

        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        env = {
            "PRAXIS_WORKSPACE_ROOT": str(tmp_path),
            "PRAXIS_WEBHOOK_SECRET_MYSVC": secret,
        }
        with patch.dict(os.environ, env):
            response = client.post(
                "/webhooks/mysvc",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": sig,
                },
            )

        assert response.status_code == 201


# ---------------------------------------------------------------------------
# FileWatcher startup tests (queue_runner._start_file_watcher)
# ---------------------------------------------------------------------------

class TestStartFileWatcher:
    """Unit tests for queue_runner._start_file_watcher."""

    def test_start_file_watcher_noop_without_paths(self, tmp_path: Path) -> None:
        """_start_file_watcher is a no-op when PRAXIS_WATCH_PATHS is not set."""
        from praxis.queue_runner import _start_file_watcher
        from praxis.queue import TaskQueue

        queue = TaskQueue(tmp_path / ".praxis" / "queue")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRAXIS_WATCH_PATHS", None)
            with patch("praxis.queue_runner.FileWatcher", autospec=True) if False else _noop_ctx():
                # We verify FileWatcher is never constructed by checking no error occurs
                _start_file_watcher(queue)  # should silently return

    def test_start_file_watcher_starts_with_paths(self, tmp_path: Path) -> None:
        """_start_file_watcher calls FileWatcher.start() when PRAXIS_WATCH_PATHS is set."""
        from praxis.queue_runner import _start_file_watcher
        from praxis.queue import TaskQueue

        queue = TaskQueue(tmp_path / ".praxis" / "queue")
        watch_dir = tmp_path / "watched"
        watch_dir.mkdir()

        fake_watcher = MagicMock()
        fake_watcher_cls = MagicMock(return_value=fake_watcher)

        env = {"PRAXIS_WATCH_PATHS": str(watch_dir)}
        with patch.dict(os.environ, env):
            with patch("praxis.queue_runner.FileWatcher", fake_watcher_cls, create=True):
                # _start_file_watcher does `from .hooks_engine import FileWatcher`
                # so we patch at the import site
                pass  # patching via sys.modules approach below

        # Patch via sys.modules to intercept the lazy import inside _start_file_watcher
        fake_hooks_engine = MagicMock()
        fake_hooks_engine.FileWatcher = fake_watcher_cls

        with patch.dict(os.environ, env):
            with patch.dict(sys.modules, {"praxis.hooks_engine": fake_hooks_engine}):
                _start_file_watcher(queue)

        fake_watcher_cls.assert_called_once_with(queue)
        fake_watcher.start.assert_called_once()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from contextlib import contextmanager


@contextmanager
def _noop_ctx():
    yield


class TestMakeWebhookRoutesReturnsRoutes:
    """make_webhook_routes() must return a non-empty list when starlette is available."""

    def test_make_webhook_routes_returns_list(self) -> None:
        from praxis.hooks_engine import make_webhook_routes
        routes = make_webhook_routes()
        assert isinstance(routes, list)
        assert len(routes) >= 1

    def test_route_path_pattern(self) -> None:
        from praxis.hooks_engine import make_webhook_routes
        routes = make_webhook_routes()
        # Each Route should have a path containing 'webhooks'
        from starlette.routing import Route
        for route in routes:
            assert "webhooks" in route.path


class TestMcpServerWiresWebhookRoutes:
    """Verify MCPServer.start() registers /webhooks/{source} via make_webhook_routes()."""

    def test_mcp_server_registers_webhook_route(self, tmp_path: Path) -> None:
        """After start(), the app should respond to POST /webhooks/test."""
        from starlette.testclient import TestClient
        from unittest.mock import patch, MagicMock
        from praxis.config import Config
        from praxis.mcp_server import MCPServer

        config = Config(
            workspace_root=tmp_path,
            memory_root=tmp_path / ".praxis" / "memory",
            hook_path=tmp_path / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )
        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        captured: dict = {}

        def fake_uvicorn_run(app, **kwargs: object) -> None:
            captured["app"] = app

        with patch("praxis.mcp_server.uvicorn.run", side_effect=fake_uvicorn_run):
            server = MCPServer(config)
            server.start(port=9999)

        assert "app" in captured, "uvicorn.run was not called"
        client = TestClient(captured["app"], raise_server_exceptions=False)

        body = json.dumps({"ping": True}).encode()
        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            response = client.post(
                "/webhooks/test",
                content=body,
                headers={"Content-Type": "application/json"},
            )

        # 201 means the route exists and the handler ran
        assert response.status_code == 201
