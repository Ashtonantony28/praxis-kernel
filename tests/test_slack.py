"""Tests for praxis/integrations/slack.py and praxis/slack_listener.py."""
from __future__ import annotations

import json
import re
import signal
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.config import Config
from praxis.integrations.slack import (
    SLACK_WEBHOOK_DOMAIN,
    _check_domain,
    _extract_domain,
    execute_slack,
)
from praxis.queue import Task, TaskQueue
from praxis.slack_listener import SlackSocketListener
from praxis.__main__ import _parse_mode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        workspace_root=tmp_path,
        memory_root=tmp_path / ".praxis" / "memory",
        hook_path=tmp_path / ".claude" / "hooks" / "escalation-boundary.py",
        allowed_domains=frozenset(),
    )


@pytest.fixture
def config_with_slack(tmp_path: Path) -> Config:
    return Config(
        workspace_root=tmp_path,
        memory_root=tmp_path / ".praxis" / "memory",
        hook_path=tmp_path / ".claude" / "hooks" / "escalation-boundary.py",
        allowed_domains=frozenset({"hooks.slack.com"}),
    )


# ---------------------------------------------------------------------------
# TestSlackNotify
# ---------------------------------------------------------------------------


class TestSlackNotify:
    @patch("praxis.integrations.slack.urllib.request.urlopen")
    def test_notify_posts_to_webhook(
        self, mock_urlopen, config_with_slack, monkeypatch
    ):
        monkeypatch.setenv(
            "PRAXIS_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T1/B1/test"
        )
        mock_urlopen.return_value = MagicMock()
        result = execute_slack({"action": "notify", "message": "hello"}, config_with_slack)
        assert mock_urlopen.call_count == 1
        assert "sent successfully" in result

    def test_notify_domain_check_blocks_unlisted(self, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_SLACK_WEBHOOK_URL", "https://evil.example.com/hook"
        )
        result = execute_slack({"action": "notify", "message": "hi"}, config)
        assert "not in PRAXIS_ALLOWED_DOMAINS" in result

    def test_notify_missing_webhook_url(self, config_with_slack, monkeypatch):
        monkeypatch.delenv("PRAXIS_SLACK_WEBHOOK_URL", raising=False)
        result = execute_slack({"action": "notify", "message": "hi"}, config_with_slack)
        assert "PRAXIS_SLACK_WEBHOOK_URL not set" in result

    @patch(
        "praxis.integrations.slack.urllib.request.urlopen",
        side_effect=urllib.error.HTTPError(
            url="", code=500, msg="Internal Server Error", hdrs=None, fp=None
        ),
    )
    def test_notify_http_error_returns_string(
        self, mock_urlopen, config_with_slack, monkeypatch
    ):
        monkeypatch.setenv(
            "PRAXIS_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T1/B1/test"
        )
        result = execute_slack({"action": "notify", "message": "oops"}, config_with_slack)
        assert "HTTP 500" in result

    def test_notify_missing_message(self, config_with_slack):
        result = execute_slack({"action": "notify"}, config_with_slack)
        assert "'message' is required" in result


# ---------------------------------------------------------------------------
# TestSlackStageMessage
# ---------------------------------------------------------------------------


class TestSlackStageMessage:
    def test_stage_message_writes_json_file(self, config):
        execute_slack(
            {"action": "stage_message", "recipient": "@alice", "message": "hello"},
            config,
        )
        staging_dir = (
            config.workspace_root / ".praxis" / "staging" / "slack" / "messages"
        )
        assert len(list(staging_dir.glob("*.json"))) == 1

    def test_stage_message_file_content_correct(self, config):
        execute_slack(
            {
                "action": "stage_message",
                "recipient": "@alice",
                "message": "hello",
                "subject": "test subject",
            },
            config,
        )
        staging_dir = (
            config.workspace_root / ".praxis" / "staging" / "slack" / "messages"
        )
        files = list(staging_dir.glob("*.json"))
        assert len(files) == 1
        record = json.loads(files[0].read_text(encoding="utf-8"))
        assert record["recipient"] == "@alice"
        assert record["message"] == "hello"
        assert record["subject"] == "test subject"
        assert record["status"] == "staged"
        assert "id" in record
        assert "created_at" in record

    @patch("praxis.integrations.slack.urllib.request.urlopen")
    def test_stage_message_never_makes_http_call(self, mock_urlopen, config):
        execute_slack(
            {"action": "stage_message", "recipient": "@bob", "message": "test"},
            config,
        )
        assert mock_urlopen.call_count == 0

    def test_list_staged_returns_item(self, config):
        execute_slack(
            {"action": "stage_message", "recipient": "@alice", "message": "hi"},
            config,
        )
        result = execute_slack({"action": "list_staged"}, config)
        assert "@alice" in result

    def test_stage_message_creates_missing_staging_dir(self, config):
        execute_slack(
            {"action": "stage_message", "recipient": "@charlie", "message": "ping"},
            config,
        )
        assert (
            config.workspace_root / ".praxis" / "staging" / "slack" / "messages"
        ).exists()


# ---------------------------------------------------------------------------
# TestSlackApproval
# ---------------------------------------------------------------------------


class TestSlackApproval:
    @patch("praxis.integrations.slack.urllib.request.urlopen")
    def test_post_approval_creates_file(
        self, mock_urlopen, config_with_slack, monkeypatch
    ):
        monkeypatch.setenv(
            "PRAXIS_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T1/B1/test"
        )
        mock_urlopen.return_value = MagicMock()
        execute_slack(
            {
                "action": "post_approval_request",
                "title": "Deploy?",
                "description": "Run make deploy",
            },
            config_with_slack,
        )
        approvals_dir = (
            config_with_slack.workspace_root
            / ".praxis"
            / "staging"
            / "slack"
            / "approvals"
        )
        assert len(list(approvals_dir.glob("*.json"))) == 1

    @patch("praxis.integrations.slack.urllib.request.urlopen")
    def test_post_approval_calls_notify(
        self, mock_urlopen, config_with_slack, monkeypatch
    ):
        monkeypatch.setenv(
            "PRAXIS_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T1/B1/test"
        )
        mock_urlopen.return_value = MagicMock()
        execute_slack(
            {
                "action": "post_approval_request",
                "title": "T",
                "description": "D",
            },
            config_with_slack,
        )
        assert mock_urlopen.call_count == 1

    @patch("praxis.integrations.slack.urllib.request.urlopen")
    def test_get_approval_returns_pending(
        self, mock_urlopen, config_with_slack, monkeypatch
    ):
        monkeypatch.setenv(
            "PRAXIS_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T1/B1/test"
        )
        mock_urlopen.return_value = MagicMock()
        execute_slack(
            {
                "action": "post_approval_request",
                "title": "T",
                "description": "D",
                "approval_id": "test-123",
            },
            config_with_slack,
        )
        result = execute_slack(
            {"action": "get_approval", "approval_id": "test-123"}, config_with_slack
        )
        assert "pending" in result

    def test_get_approval_missing_returns_error(self, config):
        result = execute_slack(
            {"action": "get_approval", "approval_id": "no-such-id"}, config
        )
        assert "not found" in result

    def test_list_approvals_filters_by_status(self, config):
        approvals_dir = (
            config.workspace_root / ".praxis" / "staging" / "slack" / "approvals"
        )
        approvals_dir.mkdir(parents=True, exist_ok=True)
        for aid, status in [("a1", "pending"), ("a2", "approved")]:
            (approvals_dir / f"{aid}.json").write_text(
                json.dumps(
                    {
                        "id": aid,
                        "created_at": "2026-05-27T00:00:00",
                        "title": f"T-{aid}",
                        "description": "D",
                        "status": status,
                        "responded_at": None,
                        "note": None,
                    }
                ),
                encoding="utf-8",
            )
        result = execute_slack(
            {"action": "list_approvals", "status": "approved"}, config
        )
        assert "a2" in result
        assert "a1" not in result

    @patch("praxis.integrations.slack.urllib.request.urlopen")
    def test_approval_round_trip(self, mock_urlopen, config_with_slack, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T1/B1/test"
        )
        mock_urlopen.return_value = MagicMock()
        execute_slack(
            {
                "action": "post_approval_request",
                "title": "Deploy",
                "description": "Deploy to prod",
                "approval_id": "round-trip-test",
            },
            config_with_slack,
        )
        approval_path = (
            config_with_slack.workspace_root
            / ".praxis"
            / "staging"
            / "slack"
            / "approvals"
            / "round-trip-test.json"
        )
        record = json.loads(approval_path.read_text(encoding="utf-8"))
        assert record["status"] == "pending"

        # Manually update to approved
        record["status"] = "approved"
        approval_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

        result = execute_slack(
            {"action": "get_approval", "approval_id": "round-trip-test"},
            config_with_slack,
        )
        assert "approved" in result


# ---------------------------------------------------------------------------
# TestSlackListener
# ---------------------------------------------------------------------------


class TestSlackListener:
    @pytest.fixture
    def listener(self, tmp_path: Path) -> SlackSocketListener:
        queue = MagicMock(spec=TaskQueue)
        return SlackSocketListener(
            bot_token="xoxb-test",
            app_token="xapp-test",
            workspace_root=tmp_path,
            queue=queue,
        )

    def test_dm_message_enqueues_task(self, listener):
        listener._handle_message(
            {"text": "run tests", "user": "U123", "channel_type": "im"}
        )
        assert listener._queue.append.call_count == 1
        task = listener._queue.append.call_args[0][0]
        assert task.prompt == "run tests"

    @patch("praxis.slack_listener.urllib.request.urlopen")
    def test_slash_command_enqueues_task(self, mock_urlopen, listener):
        mock_urlopen.return_value = MagicMock()
        listener._handle_slash_command(
            {
                "command": "/praxis",
                "text": "check git status",
                "user_id": "U456",
                "response_url": "",
            }
        )
        assert listener._queue.append.call_count == 1
        task = listener._queue.append.call_args[0][0]
        assert "/praxis" in task.prompt
        assert "check git status" in task.prompt

    def test_block_action_updates_approval_file(self, listener, tmp_path):
        approvals_dir = (
            tmp_path / ".praxis" / "staging" / "slack" / "approvals"
        )
        approvals_dir.mkdir(parents=True, exist_ok=True)
        approval_file = approvals_dir / "my-approval.json"
        approval_file.write_text(
            json.dumps(
                {
                    "id": "my-approval",
                    "created_at": "2026-05-27T00:00:00",
                    "title": "Test",
                    "description": "Do thing",
                    "status": "pending",
                    "responded_at": None,
                    "note": None,
                }
            ),
            encoding="utf-8",
        )
        listener._handle_block_action(
            {
                "actions": [
                    {"action_id": "approval_my-approval", "value": "approve"}
                ],
                "user": {"id": "U789"},
            }
        )
        record = json.loads(approval_file.read_text(encoding="utf-8"))
        assert record["status"] == "approved"
        assert record["responded_at"] is not None

    def test_sigterm_sets_running_false(self, listener):
        listener._running = True
        listener._handle_sigterm(signal.SIGTERM, None)
        assert listener._running is False

    def test_missing_app_token_raises_on_start(self, tmp_path):
        queue = MagicMock(spec=TaskQueue)
        listener = SlackSocketListener(
            bot_token="xoxb-test",
            app_token="",
            workspace_root=tmp_path,
            queue=queue,
        )
        with pytest.raises(RuntimeError):
            listener.start()

    def test_empty_dm_not_enqueued(self, listener):
        listener._handle_message({"text": "   ", "user": "U123", "channel_type": "im"})
        assert listener._queue.append.call_count == 0


# ---------------------------------------------------------------------------
# TestSlackMain
# ---------------------------------------------------------------------------


class TestSlackMain:
    def test_slack_listen_parse_mode(self):
        assert _parse_mode(["--slack-listen"]) == "slack"

    def test_parse_mode_interactive_unchanged(self):
        assert _parse_mode(["hello world"]) == "interactive"
        assert _parse_mode([]) == "interactive"

    def test_slack_listen_missing_bot_token_exits(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PRAXIS_SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("PRAXIS_SLACK_APP_TOKEN", raising=False)
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(sys, "argv", ["praxis", "--slack-listen"])
        from praxis.__main__ import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert "PRAXIS_SLACK_BOT_TOKEN" in str(exc_info.value)

    def test_slack_listen_missing_app_token_exits(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PRAXIS_SLACK_BOT_TOKEN", "xoxb-fake-token")
        monkeypatch.delenv("PRAXIS_SLACK_APP_TOKEN", raising=False)
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(sys, "argv", ["praxis", "--slack-listen"])
        from praxis.__main__ import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert "PRAXIS_SLACK_APP_TOKEN" in str(exc_info.value)
