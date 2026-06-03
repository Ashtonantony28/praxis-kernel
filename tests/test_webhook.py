"""Tests for hooks_engine.make_webhook_routes — webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app():
    """Build a minimal Starlette app with webhook routes."""
    from starlette.applications import Starlette
    from praxis.hooks_engine import make_webhook_routes

    routes = make_webhook_routes()
    return Starlette(routes=routes)


def _hmac_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Core tests
# ---------------------------------------------------------------------------

class TestWebhookValidJson:
    """test_webhook_valid_json_enqueues_task and related happy-path cases."""

    def test_valid_json_enqueues_task(self, tmp_path: Path):
        """POST /webhooks/github with valid JSON enqueues a Task and returns 201."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        payload = {"action": "push", "ref": "refs/heads/main"}
        body = json.dumps(payload).encode()

        appended_tasks = []

        def fake_append(task):
            appended_tasks.append(task)

        fake_queue = MagicMock()
        fake_queue.append.side_effect = fake_append

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with patch("praxis.queue.TaskQueue", return_value=fake_queue):
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={"Content-Type": "application/json"},
                )

        assert response.status_code == 201
        assert len(appended_tasks) == 1
        task = appended_tasks[0]
        assert "github" in task.prompt
        assert "push" in task.prompt or "action" in task.prompt

    def test_valid_json_response_contains_task_id(self, tmp_path: Path):
        """201 response body contains a task_id field."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        payload = {"event": "test"}
        body = json.dumps(payload).encode()

        fake_queue = MagicMock()

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with patch("praxis.queue.TaskQueue", return_value=fake_queue):
                response = client.post(
                    "/webhooks/slack",
                    content=body,
                    headers={"Content-Type": "application/json"},
                )

        assert response.status_code == 201
        data = response.json()
        assert "task_id" in data

    def test_prompt_truncated_to_500_chars(self, tmp_path: Path):
        """Prompt uses at most 500 chars of the JSON body."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        # Create a large JSON payload
        payload = {"data": "x" * 1000}
        body = json.dumps(payload).encode()

        appended_tasks = []

        def fake_append(task):
            appended_tasks.append(task)

        fake_queue = MagicMock()
        fake_queue.append.side_effect = fake_append

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with patch("praxis.queue.TaskQueue", return_value=fake_queue):
                response = client.post(
                    "/webhooks/generic",
                    content=body,
                    headers={"Content-Type": "application/json"},
                )

        assert response.status_code == 201
        task = appended_tasks[0]
        # Prompt format: "Webhook from {source}: {json_str[:500]}"
        # The json_str part should be truncated at 500
        prefix = "Webhook from generic: "
        json_part = task.prompt[len(prefix):]
        assert len(json_part) <= 500

    def test_source_name_appears_in_prompt(self, tmp_path: Path):
        """The source from the URL path appears in the enqueued task prompt."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        body = json.dumps({"x": 1}).encode()
        appended_tasks = []

        fake_queue = MagicMock()
        fake_queue.append.side_effect = lambda t: appended_tasks.append(t)

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with patch("praxis.queue.TaskQueue", return_value=fake_queue):
                client.post(
                    "/webhooks/linear",
                    content=body,
                    headers={"Content-Type": "application/json"},
                )

        assert len(appended_tasks) == 1
        assert "linear" in appended_tasks[0].prompt

    def test_body_too_large_returns_413(self, tmp_path: Path):
        """Payload exceeding 64 KB returns HTTP 413."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        body = b"x" * (64 * 1024 + 1)

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 413

    def test_invalid_json_returns_400(self, tmp_path: Path):
        """Non-JSON body returns HTTP 400."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        body = b"this is not json"

        fake_queue = MagicMock()

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            with patch("praxis.queue.TaskQueue", return_value=fake_queue):
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={"Content-Type": "text/plain"},
                )

        assert response.status_code == 400

    def test_missing_workspace_root_returns_500(self):
        """Missing PRAXIS_WORKSPACE_ROOT returns HTTP 500."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        body = json.dumps({"ok": True}).encode()

        # Ensure env var is not set
        env = {k: v for k, v in os.environ.items() if k != "PRAXIS_WORKSPACE_ROOT"}
        with patch.dict(os.environ, env, clear=True):
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 500


class TestWebhookHmacValidation:
    """test_webhook_invalid_hmac_returns_401 and related HMAC tests."""

    def test_invalid_hmac_returns_401(self, tmp_path: Path):
        """POST with wrong HMAC signature returns HTTP 401."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        body = json.dumps({"action": "push"}).encode()
        wrong_sig = "sha256=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

        fake_queue = MagicMock()

        with patch.dict(
            os.environ,
            {
                "PRAXIS_WORKSPACE_ROOT": str(tmp_path),
                "PRAXIS_WEBHOOK_SECRET_GITHUB": "correct-secret",
            },
        ):
            with patch("praxis.queue.TaskQueue", return_value=fake_queue):
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Hub-Signature-256": wrong_sig,
                    },
                )

        assert response.status_code == 401
        assert fake_queue.append.call_count == 0

    def test_missing_hmac_header_returns_401(self, tmp_path: Path):
        """POST without signature header when secret is configured returns 401."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        body = json.dumps({"action": "push"}).encode()

        fake_queue = MagicMock()

        with patch.dict(
            os.environ,
            {
                "PRAXIS_WORKSPACE_ROOT": str(tmp_path),
                "PRAXIS_WEBHOOK_SECRET_GITHUB": "mysecret",
            },
        ):
            with patch("praxis.queue.TaskQueue", return_value=fake_queue):
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={"Content-Type": "application/json"},
                    # No X-Hub-Signature-256 header
                )

        assert response.status_code == 401

    def test_valid_hmac_enqueues_task(self, tmp_path: Path):
        """POST with correct HMAC signature succeeds and enqueues a task."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        secret = "my-webhook-secret"
        payload = {"action": "opened", "number": 42}
        body = json.dumps(payload).encode()
        sig = _hmac_sig(secret, body)

        appended_tasks = []
        fake_queue = MagicMock()
        fake_queue.append.side_effect = lambda t: appended_tasks.append(t)

        with patch.dict(
            os.environ,
            {
                "PRAXIS_WORKSPACE_ROOT": str(tmp_path),
                "PRAXIS_WEBHOOK_SECRET_GITHUB": secret,
            },
        ):
            with patch("praxis.queue.TaskQueue", return_value=fake_queue):
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Hub-Signature-256": sig,
                    },
                )

        assert response.status_code == 201
        assert len(appended_tasks) == 1

    def test_no_secret_configured_skips_hmac(self, tmp_path: Path):
        """When no secret env var is set, HMAC check is skipped entirely."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        body = json.dumps({"data": "anything"}).encode()
        fake_queue = MagicMock()

        # No PRAXIS_WEBHOOK_SECRET_GITHUB set
        env = {k: v for k, v in os.environ.items() if k != "PRAXIS_WEBHOOK_SECRET_GITHUB"}
        env["PRAXIS_WORKSPACE_ROOT"] = str(tmp_path)

        with patch.dict(os.environ, env, clear=True):
            with patch("praxis.queue.TaskQueue", return_value=fake_queue):
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={"Content-Type": "application/json"},
                )

        assert response.status_code == 201

    def test_hmac_secret_keyed_by_source_uppercase(self, tmp_path: Path):
        """Secret lookup uses uppercased source name."""
        from starlette.testclient import TestClient

        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)

        secret = "stripe-secret"
        body = json.dumps({"type": "payment_intent.created"}).encode()
        sig = _hmac_sig(secret, body)

        appended_tasks = []
        fake_queue = MagicMock()
        fake_queue.append.side_effect = lambda t: appended_tasks.append(t)

        # Source is "stripe" → env var is PRAXIS_WEBHOOK_SECRET_STRIPE
        with patch.dict(
            os.environ,
            {
                "PRAXIS_WORKSPACE_ROOT": str(tmp_path),
                "PRAXIS_WEBHOOK_SECRET_STRIPE": secret,
            },
        ):
            with patch("praxis.queue.TaskQueue", return_value=fake_queue):
                response = client.post(
                    "/webhooks/stripe",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Hub-Signature-256": sig,
                    },
                )

        assert response.status_code == 201
        assert len(appended_tasks) == 1


class TestMakeWebhookRoutesList:
    """Tests for the make_webhook_routes() return value."""

    def test_returns_list(self):
        """make_webhook_routes() returns a list."""
        from praxis.hooks_engine import make_webhook_routes

        result = make_webhook_routes()
        assert isinstance(result, list)

    def test_returns_one_route(self):
        """make_webhook_routes() returns exactly one Route."""
        from praxis.hooks_engine import make_webhook_routes

        result = make_webhook_routes()
        assert len(result) == 1

    def test_route_path_is_webhooks(self):
        """The route path is /webhooks/{source}."""
        from praxis.hooks_engine import make_webhook_routes

        result = make_webhook_routes()
        route = result[0]
        assert "/webhooks" in str(route.path)
