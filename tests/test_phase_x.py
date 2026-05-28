"""Tests for Phase X — Playwright, Notion, Linear, and --approve command."""

from __future__ import annotations

import json
import os
import uuid
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from praxis.config import Config
from praxis.runtime.cost import CostCircuitBreaker


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_config(workspace: Path, *, allowed_domains=frozenset()) -> Config:
    return Config(
        workspace_root=workspace,
        memory_root=workspace / ".praxis" / "memory",
        hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
        allowed_domains=frozenset(allowed_domains),
    )


def _write_staging(staging_file: Path, entries: list[dict]) -> None:
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    with staging_file.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _make_pending_entry(
    provider: str = "notion",
    action: str = "create_page",
    params: dict | None = None,
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "provider": provider,
        "action": action,
        "params": params or {"title": "Test Page"},
        "queued_at": "2026-05-27T10:00:00+00:00",
        "status": "pending",
    }


# ── Playwright ────────────────────────────────────────────────────────────────


class TestPlaywright:
    """Tests for praxis/integrations/playwright.py"""

    def test_fetch_domain_not_in_allowlist(self, tmp_path: Path):
        """fetch with empty allowed_domains returns domain error."""
        from praxis.integrations.playwright import _execute_playwright

        config = _make_config(tmp_path, allowed_domains=frozenset())
        result = _execute_playwright(
            {"action": "fetch", "url": "https://example.com"}, config
        )
        assert "PRAXIS_ALLOWED_DOMAINS" in result

    def test_fetch_domain_in_allowlist(self, tmp_path: Path):
        """fetch with allowed domain calls _run_playwright_script and returns content."""
        from praxis.integrations.playwright import _execute_playwright

        config = _make_config(tmp_path, allowed_domains=frozenset(["example.com"]))
        with patch(
            "praxis.integrations.playwright._run_playwright_script",
            return_value="page text content",
        ):
            result = _execute_playwright(
                {"action": "fetch", "url": "https://example.com"}, config
            )
        assert "page text" in result

    def test_fetch_truncates_at_max_chars(self, tmp_path: Path):
        """fetch truncates output at max_chars and appends truncation notice."""
        from praxis.integrations.playwright import _execute_playwright

        config = _make_config(tmp_path, allowed_domains=frozenset(["example.com"]))
        long_output = "x" * 5000
        with patch(
            "praxis.integrations.playwright._run_playwright_script",
            return_value=long_output,
        ):
            result = _execute_playwright(
                {
                    "action": "fetch",
                    "url": "https://example.com",
                    "max_chars": 100,
                },
                config,
            )
        assert "truncated" in result
        assert len(result) < 5000

    def test_fetch_playwright_import_error(self, tmp_path: Path):
        """fetch with PLAYWRIGHT_IMPORT_ERROR output tells user to pip install."""
        from praxis.integrations.playwright import _execute_playwright

        config = _make_config(tmp_path, allowed_domains=frozenset(["example.com"]))
        with patch(
            "praxis.integrations.playwright._run_playwright_script",
            return_value="PLAYWRIGHT_IMPORT_ERROR",
        ):
            result = _execute_playwright(
                {"action": "fetch", "url": "https://example.com"}, config
            )
        assert "pip install praxis[playwright]" in result

    def test_screenshot_path_outside_workspace_rejected(self, tmp_path: Path):
        """screenshot with output_path outside workspace returns error."""
        from praxis.integrations.playwright import _execute_playwright

        config = _make_config(tmp_path, allowed_domains=frozenset(["example.com"]))
        result = _execute_playwright(
            {
                "action": "screenshot",
                "url": "https://example.com",
                "output_path": "../outside.png",
            },
            config,
        )
        assert "outside WORKSPACE_ROOT" in result

    def test_screenshot_subprocess_env_strips_auth(self, tmp_path: Path):
        """_subprocess_env does not include auth tokens."""
        from praxis.integrations.playwright import _subprocess_env

        sensitive = {
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-secret",
            "PRAXIS_NOTION_TOKEN": "notion-secret",
            "PRAXIS_LINEAR_API_KEY": "linear-secret",
            "PRAXIS_SLACK_WEBHOOK_URL": "https://hooks.slack.com/secret",
            "PRAXIS_EMAIL_PASSWORD": "email-pass",
            "GITHUB_TOKEN": "ghp_secret",
        }
        with patch.dict(os.environ, sensitive, clear=False):
            env = _subprocess_env({"_PRAXIS_PW_URL": "http://x"})

        assert "ANTHROPIC_API_KEY" not in env
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "PRAXIS_NOTION_TOKEN" not in env
        assert "PRAXIS_LINEAR_API_KEY" not in env
        assert "PRAXIS_SLACK_WEBHOOK_URL" not in env
        assert "PRAXIS_EMAIL_PASSWORD" not in env
        assert "GITHUB_TOKEN" not in env
        # The extra env var we passed should still be present
        assert env.get("_PRAXIS_PW_URL") == "http://x"

    def test_retry_succeeds_on_second_attempt(self, tmp_path: Path):
        """_run_playwright_script retries on transient error and returns success."""
        from praxis.integrations.playwright import _run_playwright_script

        call_count = 0

        def fake_once(script, env):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "PLAYWRIGHT_ERROR:transient"
            return "actual content"

        with patch("praxis.integrations.playwright._run_playwright_script_once", side_effect=fake_once):
            with patch("praxis.integrations.playwright.time.sleep") as mock_sleep:
                result = _run_playwright_script("script", {})

        assert result == "actual content"
        mock_sleep.assert_called_once_with(1)

    def test_retry_exhausted_returns_last_error(self, tmp_path: Path):
        """_run_playwright_script returns last PLAYWRIGHT_ERROR after all retries exhausted."""
        from praxis.integrations.playwright import _run_playwright_script

        with patch(
            "praxis.integrations.playwright._run_playwright_script_once",
            return_value="PLAYWRIGHT_ERROR:some error",
        ):
            with patch("praxis.integrations.playwright.time.sleep"):
                result = _run_playwright_script("script", {}, max_retries=3)

        assert result.startswith("PLAYWRIGHT_ERROR:")

    def test_import_error_does_not_retry(self, tmp_path: Path):
        """_run_playwright_script does not retry on PLAYWRIGHT_IMPORT_ERROR."""
        from praxis.integrations.playwright import _run_playwright_script

        with patch(
            "praxis.integrations.playwright._run_playwright_script_once",
            return_value="PLAYWRIGHT_IMPORT_ERROR",
        ):
            with patch("praxis.integrations.playwright.time.sleep") as mock_sleep:
                result = _run_playwright_script("script", {})

        mock_sleep.assert_not_called()
        assert result == "PLAYWRIGHT_IMPORT_ERROR"

    def test_clean_error_message_no_traceback(self, tmp_path: Path):
        """_clean_playwright_error strips Traceback lines and detects timed out."""
        from praxis.integrations.playwright import _clean_playwright_error

        raw = 'Traceback (most recent call last):\n  File "x.py", line 1\nTimeoutError: timed out'
        result = _clean_playwright_error(raw)

        assert "Traceback" not in result
        assert 'File "' not in result
        assert "timed out" in result.lower()


# ── Notion ────────────────────────────────────────────────────────────────────


class TestNotion:
    """Tests for praxis/integrations/notion.py"""

    def test_search_missing_token(self, tmp_path: Path):
        """search without PRAXIS_NOTION_TOKEN returns clear error."""
        from praxis.integrations.notion import _execute_notion

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.notion.com"]))
        with patch.dict(os.environ, {}, clear=False):
            # Remove token if set
            env_backup = os.environ.pop("PRAXIS_NOTION_TOKEN", None)
            try:
                result = _execute_notion(
                    {"action": "search", "query": "test"}, config
                )
            finally:
                if env_backup is not None:
                    os.environ["PRAXIS_NOTION_TOKEN"] = env_backup
        assert "PRAXIS_NOTION_TOKEN" in result

    def test_search_domain_not_allowed(self, tmp_path: Path):
        """search with empty allowed_domains returns domain error."""
        from praxis.integrations.notion import _execute_notion

        config = _make_config(tmp_path, allowed_domains=frozenset())
        with patch.dict(os.environ, {"PRAXIS_NOTION_TOKEN": "test-token"}):
            result = _execute_notion(
                {"action": "search", "query": "test"}, config
            )
        assert "PRAXIS_ALLOWED_DOMAINS" in result

    def test_search_calls_api(self, tmp_path: Path):
        """search with token and allowed domain calls urlopen."""
        from praxis.integrations.notion import _execute_notion

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.notion.com"]))
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"results": []}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, {"PRAXIS_NOTION_TOKEN": "test-token"}):
            with patch("praxis.integrations.notion.urlopen", return_value=mock_response):
                result = _execute_notion(
                    {"action": "search", "query": "test"}, config
                )
        assert "results" in result

    def test_create_page_stages_to_jsonl(self, tmp_path: Path):
        """create_page writes a staging entry and does not call the API."""
        from praxis.integrations.notion import _execute_notion

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.notion.com"]))
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"

        with patch.dict(os.environ, {"PRAXIS_NOTION_TOKEN": "test-token"}):
            result = _execute_notion(
                {
                    "action": "create_page",
                    "parent_id": "parent-123",
                    "title": "My Page",
                    "content": "Some content",
                },
                config,
            )

        assert staging_file.exists()
        entries = [json.loads(line) for line in staging_file.read_text().splitlines() if line.strip()]
        assert len(entries) == 1
        assert entries[0]["provider"] == "notion"
        assert entries[0]["action"] == "create_page"
        assert entries[0]["status"] == "pending"

    def test_update_page_stages_to_jsonl(self, tmp_path: Path):
        """update_page writes a staging entry."""
        from praxis.integrations.notion import _execute_notion

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.notion.com"]))
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"

        with patch.dict(os.environ, {"PRAXIS_NOTION_TOKEN": "test-token"}):
            _execute_notion(
                {
                    "action": "update_page",
                    "page_id": "page-123",
                    "properties": {"Title": {"title": []}},
                },
                config,
            )

        assert staging_file.exists()
        entries = [json.loads(line) for line in staging_file.read_text().splitlines() if line.strip()]
        assert any(e["action"] == "update_page" for e in entries)

    def test_append_block_stages_to_jsonl(self, tmp_path: Path):
        """append_block writes a staging entry."""
        from praxis.integrations.notion import _execute_notion

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.notion.com"]))
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"

        with patch.dict(os.environ, {"PRAXIS_NOTION_TOKEN": "test-token"}):
            _execute_notion(
                {
                    "action": "append_block",
                    "block_id": "block-123",
                    "content": "Hello world",
                },
                config,
            )

        assert staging_file.exists()
        entries = [json.loads(line) for line in staging_file.read_text().splitlines() if line.strip()]
        assert any(e["action"] == "append_block" for e in entries)

    def test_staging_never_calls_api(self, tmp_path: Path):
        """create_page never calls urlopen."""
        from praxis.integrations.notion import _execute_notion

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.notion.com"]))

        with patch.dict(os.environ, {"PRAXIS_NOTION_TOKEN": "test-token"}):
            with patch("praxis.integrations.notion.urlopen") as mock_urlopen:
                _execute_notion(
                    {"action": "create_page", "title": "Test"},
                    config,
                )
                mock_urlopen.assert_not_called()

    def test_staged_entry_format(self, tmp_path: Path):
        """Staged entry has id, provider, action, params, queued_at, status=pending."""
        from praxis.integrations.notion import _execute_notion

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.notion.com"]))
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"

        with patch.dict(os.environ, {"PRAXIS_NOTION_TOKEN": "test-token"}):
            _execute_notion(
                {"action": "create_page", "title": "Format Test"},
                config,
            )

        entry = json.loads(staging_file.read_text().strip())
        # Verify UUID format
        parsed_id = uuid.UUID(entry["id"])
        assert str(parsed_id) == entry["id"]
        assert entry["provider"] == "notion"
        assert entry["action"] == "create_page"
        assert isinstance(entry["params"], dict)
        assert "queued_at" in entry
        assert entry["status"] == "pending"

    def test_unknown_action(self, tmp_path: Path):
        """Unknown action returns descriptive error."""
        from praxis.integrations.notion import _execute_notion

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.notion.com"]))
        with patch.dict(os.environ, {"PRAXIS_NOTION_TOKEN": "test-token"}):
            result = _execute_notion({"action": "delete_page"}, config)
        assert "unknown action" in result


# ── Linear ────────────────────────────────────────────────────────────────────


class TestLinear:
    """Tests for praxis/integrations/linear.py"""

    def test_list_issues_missing_api_key(self, tmp_path: Path):
        """list_issues without PRAXIS_LINEAR_API_KEY returns clear error."""
        from praxis.integrations.linear import _execute_linear

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.linear.app"]))
        env_backup = os.environ.pop("PRAXIS_LINEAR_API_KEY", None)
        try:
            result = _execute_linear({"action": "list_issues"}, config)
        finally:
            if env_backup is not None:
                os.environ["PRAXIS_LINEAR_API_KEY"] = env_backup
        assert "PRAXIS_LINEAR_API_KEY" in result

    def test_list_issues_domain_not_allowed(self, tmp_path: Path):
        """list_issues with empty allowed_domains returns domain error."""
        from praxis.integrations.linear import _execute_linear

        config = _make_config(tmp_path, allowed_domains=frozenset())
        with patch.dict(os.environ, {"PRAXIS_LINEAR_API_KEY": "lin_api_test"}):
            result = _execute_linear({"action": "list_issues"}, config)
        assert "PRAXIS_ALLOWED_DOMAINS" in result

    def test_list_issues_calls_api(self, tmp_path: Path):
        """list_issues with key and allowed domain calls urlopen."""
        from praxis.integrations.linear import _execute_linear

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.linear.app"]))
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"data": {"issues": {"nodes": []}}}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, {"PRAXIS_LINEAR_API_KEY": "lin_api_test"}):
            with patch("praxis.integrations.linear.urlopen", return_value=mock_response):
                result = _execute_linear({"action": "list_issues"}, config)
        assert "data" in result or "issues" in result

    def test_create_issue_stages_to_jsonl(self, tmp_path: Path):
        """create_issue writes a staging entry."""
        from praxis.integrations.linear import _execute_linear

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.linear.app"]))
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"

        with patch.dict(os.environ, {"PRAXIS_LINEAR_API_KEY": "lin_api_test"}):
            _execute_linear(
                {
                    "action": "create_issue",
                    "title": "Bug report",
                    "team_id": "team-123",
                },
                config,
            )

        assert staging_file.exists()
        entries = [json.loads(line) for line in staging_file.read_text().splitlines() if line.strip()]
        assert len(entries) == 1
        assert entries[0]["provider"] == "linear"
        assert entries[0]["action"] == "create_issue"
        assert entries[0]["status"] == "pending"

    def test_update_issue_stages_to_jsonl(self, tmp_path: Path):
        """update_issue writes a staging entry."""
        from praxis.integrations.linear import _execute_linear

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.linear.app"]))
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"

        with patch.dict(os.environ, {"PRAXIS_LINEAR_API_KEY": "lin_api_test"}):
            _execute_linear(
                {"action": "update_issue", "issue_id": "issue-456"},
                config,
            )

        assert staging_file.exists()
        entries = [json.loads(line) for line in staging_file.read_text().splitlines() if line.strip()]
        assert any(e["action"] == "update_issue" for e in entries)

    def test_add_comment_stages_to_jsonl(self, tmp_path: Path):
        """add_comment writes a staging entry."""
        from praxis.integrations.linear import _execute_linear

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.linear.app"]))
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"

        with patch.dict(os.environ, {"PRAXIS_LINEAR_API_KEY": "lin_api_test"}):
            _execute_linear(
                {"action": "add_comment", "issue_id": "issue-789", "body": "Good catch!"},
                config,
            )

        assert staging_file.exists()
        entries = [json.loads(line) for line in staging_file.read_text().splitlines() if line.strip()]
        assert any(e["action"] == "add_comment" for e in entries)

    def test_write_actions_never_call_api(self, tmp_path: Path):
        """create_issue never calls urlopen."""
        from praxis.integrations.linear import _execute_linear

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.linear.app"]))

        with patch.dict(os.environ, {"PRAXIS_LINEAR_API_KEY": "lin_api_test"}):
            with patch("praxis.integrations.linear.urlopen") as mock_urlopen:
                _execute_linear(
                    {"action": "create_issue", "title": "Test", "team_id": "team-1"},
                    config,
                )
                mock_urlopen.assert_not_called()

    def test_staging_appends_not_overwrites(self, tmp_path: Path):
        """Two create_issue calls produce two lines in the staging file."""
        from praxis.integrations.linear import _execute_linear

        config = _make_config(tmp_path, allowed_domains=frozenset(["api.linear.app"]))
        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"

        with patch.dict(os.environ, {"PRAXIS_LINEAR_API_KEY": "lin_api_test"}):
            _execute_linear(
                {"action": "create_issue", "title": "First", "team_id": "team-1"},
                config,
            )
            _execute_linear(
                {"action": "create_issue", "title": "Second", "team_id": "team-1"},
                config,
            )

        lines = [l for l in staging_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 2


# ── Approve command ───────────────────────────────────────────────────────────


class TestApproveCommand:
    """Tests for _run_approve, _execute_approved_action in praxis/__main__.py"""

    def test_no_staging_file(self, tmp_path: Path, capsys):
        """Missing staging file prints 'No pending actions'."""
        from praxis.__main__ import _run_approve

        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        _run_approve(staging_file)

        captured = capsys.readouterr()
        assert "No pending actions" in captured.out

    def test_empty_staging_file(self, tmp_path: Path, capsys):
        """Empty staging file prints 'No pending actions'."""
        from praxis.__main__ import _run_approve

        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        staging_file.parent.mkdir(parents=True, exist_ok=True)
        staging_file.write_text("")

        _run_approve(staging_file)

        captured = capsys.readouterr()
        assert "No pending actions" in captured.out

    def test_all_rejected_status_updated(self, tmp_path: Path):
        """Input 'n' marks the entry as rejected in the file."""
        from praxis.__main__ import _run_approve

        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        entry = _make_pending_entry()
        _write_staging(staging_file, [entry])

        with patch("builtins.input", side_effect=["n"]):
            _run_approve(staging_file)

        updated = [json.loads(l) for l in staging_file.read_text().splitlines() if l.strip()]
        assert updated[0]["status"] == "rejected"

    def test_all_skipped_left_pending(self, tmp_path: Path):
        """Input 's' leaves the entry as pending."""
        from praxis.__main__ import _run_approve

        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        entry = _make_pending_entry()
        _write_staging(staging_file, [entry])

        with patch("builtins.input", side_effect=["s"]):
            _run_approve(staging_file)

        updated = [json.loads(l) for l in staging_file.read_text().splitlines() if l.strip()]
        assert updated[0]["status"] == "pending"

    def test_approve_notion_action_updates_status(self, tmp_path: Path):
        """Input 'y' on a notion action marks it approved."""
        from praxis.__main__ import _run_approve

        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        entry = _make_pending_entry(provider="notion", action="create_page")
        _write_staging(staging_file, [entry])

        with patch("builtins.input", side_effect=["y"]):
            with patch("praxis.__main__._notion_execute", return_value="ok") as mock_exec:
                with patch.dict(
                    os.environ,
                    {
                        "PRAXIS_NOTION_TOKEN": "test-token",
                        "PRAXIS_ALLOWED_DOMAINS": "api.notion.com",
                    },
                ):
                    _run_approve(staging_file)

        updated = [json.loads(l) for l in staging_file.read_text().splitlines() if l.strip()]
        assert updated[0]["status"] == "approved"

    def test_approve_linear_action_updates_status(self, tmp_path: Path):
        """Input 'y' on a linear action marks it approved."""
        from praxis.__main__ import _run_approve

        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        entry = _make_pending_entry(provider="linear", action="create_issue")
        _write_staging(staging_file, [entry])

        with patch("builtins.input", side_effect=["y"]):
            with patch("praxis.__main__._linear_execute", return_value="ok"):
                with patch.dict(
                    os.environ,
                    {
                        "PRAXIS_LINEAR_API_KEY": "lin_api_test",
                        "PRAXIS_ALLOWED_DOMAINS": "api.linear.app",
                    },
                ):
                    _run_approve(staging_file)

        updated = [json.loads(l) for l in staging_file.read_text().splitlines() if l.strip()]
        assert updated[0]["status"] == "approved"

    def test_non_pending_entries_not_displayed(self, tmp_path: Path, capsys):
        """Only pending entries are listed in the prompt."""
        from praxis.__main__ import _run_approve

        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        approved_entry = {**_make_pending_entry(), "status": "approved"}
        pending_entry = _make_pending_entry(action="update_page")
        _write_staging(staging_file, [approved_entry, pending_entry])

        # Only 1 pending entry means only 1 input() call needed
        with patch("builtins.input", side_effect=["n"]):
            _run_approve(staging_file)

        captured = capsys.readouterr()
        # Should show "1 total" not "2 total"
        assert "1 total" in captured.out

    def test_approve_notion_missing_token(self, tmp_path: Path, capsys):
        """Approve with missing PRAXIS_NOTION_TOKEN produces token error result."""
        from praxis.__main__ import _run_approve

        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        entry = _make_pending_entry(provider="notion", action="create_page")
        _write_staging(staging_file, [entry])

        env_backup = os.environ.pop("PRAXIS_NOTION_TOKEN", None)
        try:
            with patch("builtins.input", side_effect=["y"]):
                with patch.dict(
                    os.environ,
                    {"PRAXIS_ALLOWED_DOMAINS": "api.notion.com"},
                    clear=False,
                ):
                    _run_approve(staging_file)
        finally:
            if env_backup is not None:
                os.environ["PRAXIS_NOTION_TOKEN"] = env_backup

        captured = capsys.readouterr()
        assert "PRAXIS_NOTION_TOKEN" in captured.out

    def test_approve_domain_not_allowed(self, tmp_path: Path, capsys):
        """Approve with empty PRAXIS_ALLOWED_DOMAINS returns domain error."""
        from praxis.__main__ import _run_approve

        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        entry = _make_pending_entry(provider="notion", action="create_page")
        _write_staging(staging_file, [entry])

        with patch("builtins.input", side_effect=["y"]):
            with patch.dict(
                os.environ,
                {
                    "PRAXIS_NOTION_TOKEN": "test-token",
                    "PRAXIS_ALLOWED_DOMAINS": "",
                },
                clear=False,
            ):
                _run_approve(staging_file)

        captured = capsys.readouterr()
        assert "PRAXIS_ALLOWED_DOMAINS" in captured.out

    def test_approve_reads_all_entries_and_rewrites(self, tmp_path: Path):
        """Both approved and rejected statuses are preserved after rewrite."""
        from praxis.__main__ import _run_approve

        staging_file = tmp_path / ".praxis" / "staging" / "external_actions.jsonl"
        already_approved = {**_make_pending_entry(action="update_page"), "status": "approved"}
        pending = _make_pending_entry(action="create_page")
        _write_staging(staging_file, [already_approved, pending])

        with patch("builtins.input", side_effect=["n"]):
            _run_approve(staging_file)

        updated = [json.loads(l) for l in staging_file.read_text().splitlines() if l.strip()]
        assert len(updated) == 2
        statuses = {e["action"]: e["status"] for e in updated}
        assert statuses["update_page"] == "approved"
        assert statuses["create_page"] == "rejected"


# ── Cost env ──────────────────────────────────────────────────────────────────


class TestCostEnv:
    """Tests for CostCircuitBreaker env var propagation and playwright env stripping."""

    def test_praxis_max_session_cost_read(self):
        """PRAXIS_MAX_SESSION_COST=5.0 is read by CostCircuitBreaker.from_env()."""
        with patch.dict(os.environ, {"PRAXIS_MAX_SESSION_COST": "5.0"}):
            breaker = CostCircuitBreaker.from_env()
        assert breaker.max_cost == 5.0

    def test_playwright_env_strips_notion_token(self):
        """_subprocess_env does not include PRAXIS_NOTION_TOKEN."""
        from praxis.integrations.playwright import _subprocess_env

        with patch.dict(os.environ, {"PRAXIS_NOTION_TOKEN": "secret-notion-token"}):
            env = _subprocess_env()
        assert "PRAXIS_NOTION_TOKEN" not in env

    def test_playwright_env_strips_linear_key(self):
        """_subprocess_env does not include PRAXIS_LINEAR_API_KEY."""
        from praxis.integrations.playwright import _subprocess_env

        with patch.dict(os.environ, {"PRAXIS_LINEAR_API_KEY": "lin_api_secret"}):
            env = _subprocess_env()
        assert "PRAXIS_LINEAR_API_KEY" not in env

    def test_playwright_env_strips_oauth_token(self):
        """_subprocess_env does not include CLAUDE_CODE_OAUTH_TOKEN."""
        from praxis.integrations.playwright import _subprocess_env

        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-secret"}):
            env = _subprocess_env()
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
