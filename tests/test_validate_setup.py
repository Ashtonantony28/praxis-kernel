"""Tests for scripts/validate_setup.py (TASK-I3TESTS)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Dynamic import (validate_setup is a script, not a package module)
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location(
    "validate_setup", _SCRIPTS_DIR / "validate_setup.py"
)
validate_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate_setup)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_http_ctx(status: int = 200, body: bytes = b'{"ok":true}'):
    """Return a context-manager mock whose __enter__ yields a response mock."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _error_urlopen(*args, **kwargs):
    import urllib.error
    raise urllib.error.URLError("connection refused")


# ---------------------------------------------------------------------------
# TestEmailCheck
# ---------------------------------------------------------------------------

class TestEmailCheck:
    def test_skip_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("PRAXIS_EMAIL_IMAP_HOST", raising=False)
        status, detail = validate_setup.check_email()
        assert status == "skip"

    def test_pass_on_successful_login(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "secret")

        mock_mail = MagicMock()
        mock_imap_cls = MagicMock(return_value=mock_mail)
        with patch("imaplib.IMAP4_SSL", mock_imap_cls):
            status, detail = validate_setup.check_email()

        assert status == "pass"
        assert "imap.example.com" in detail

    def test_fail_on_login_error(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "wrong")

        mock_mail = MagicMock()
        mock_mail.login.side_effect = Exception("auth failed")
        with patch("imaplib.IMAP4_SSL", MagicMock(return_value=mock_mail)):
            status, detail = validate_setup.check_email()

        assert status == "fail"
        assert "app password" in detail.lower() or "PRAXIS_EMAIL" in detail


# ---------------------------------------------------------------------------
# TestCalendarCheck
# ---------------------------------------------------------------------------

class TestCalendarCheck:
    def test_skip_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("PRAXIS_CALENDAR_URL", raising=False)
        status, _ = validate_setup.check_calendar()
        assert status == "skip"

    def test_pass_on_reachable_url(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics")
        with patch("urllib.request.urlopen", return_value=_ok_http_ctx()):
            status, detail = validate_setup.check_calendar()
        assert status == "pass"
        assert "reachable" in detail.lower()

    def test_fail_on_unreachable_url(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics")
        with patch("urllib.request.urlopen", side_effect=_error_urlopen):
            status, detail = validate_setup.check_calendar()
        assert status == "fail"
        assert "PRAXIS_CALENDAR_URL" in detail


# ---------------------------------------------------------------------------
# TestGitHubCheck
# ---------------------------------------------------------------------------

class TestGitHubCheck:
    def test_skip_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        status, _ = validate_setup.check_github()
        assert status == "skip"

    def test_pass_when_gh_auth_ok(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        completed = subprocess.CompletedProcess(args=["gh"], returncode=0, stdout=b"", stderr=b"")
        with patch("subprocess.run", return_value=completed):
            status, detail = validate_setup.check_github()
        assert status == "pass"
        assert "ok" in detail.lower()

    def test_fail_when_gh_auth_fails(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_bad")
        completed = subprocess.CompletedProcess(args=["gh"], returncode=1, stdout=b"", stderr=b"")
        with patch("subprocess.run", return_value=completed):
            status, detail = validate_setup.check_github()
        assert status == "fail"
        assert "gh auth login" in detail


# ---------------------------------------------------------------------------
# TestLinearCheck
# ---------------------------------------------------------------------------

class TestLinearCheck:
    def test_skip_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("PRAXIS_LINEAR_API_KEY", raising=False)
        status, _ = validate_setup.check_linear()
        assert status == "skip"

    def test_pass_on_valid_key(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_LINEAR_API_KEY", "lin_api_key")
        body = json.dumps({"data": {"viewer": {"id": "u1", "name": "Alice"}}}).encode()
        with patch("urllib.request.urlopen", return_value=_ok_http_ctx(body=body)):
            status, detail = validate_setup.check_linear()
        assert status == "pass"
        assert "Alice" in detail

    def test_fail_on_invalid_key(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_LINEAR_API_KEY", "bad_key")
        with patch("urllib.request.urlopen", side_effect=_error_urlopen):
            status, detail = validate_setup.check_linear()
        assert status == "fail"
        assert "PRAXIS_LINEAR_API_KEY" in detail


# ---------------------------------------------------------------------------
# TestNotionCheck
# ---------------------------------------------------------------------------

class TestNotionCheck:
    def test_skip_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("PRAXIS_NOTION_TOKEN", raising=False)
        status, _ = validate_setup.check_notion()
        assert status == "skip"

    def test_pass_on_valid_token(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_NOTION_TOKEN", "secret_notion")
        with patch("urllib.request.urlopen", return_value=_ok_http_ctx(status=200)):
            status, detail = validate_setup.check_notion()
        assert status == "pass"
        assert "valid" in detail.lower() or "token" in detail.lower()

    def test_fail_on_invalid_token(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_NOTION_TOKEN", "bad_token")
        with patch("urllib.request.urlopen", side_effect=_error_urlopen):
            status, detail = validate_setup.check_notion()
        assert status == "fail"
        assert "PRAXIS_NOTION_TOKEN" in detail


# ---------------------------------------------------------------------------
# TestSlackCheck
# ---------------------------------------------------------------------------

class TestSlackCheck:
    def test_skip_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("PRAXIS_SLACK_WEBHOOK_URL", raising=False)
        status, _ = validate_setup.check_slack()
        assert status == "skip"

    def test_pass_on_ok_response(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        with patch("urllib.request.urlopen", return_value=_ok_http_ctx(status=200)):
            status, detail = validate_setup.check_slack()
        assert status == "pass"
        assert "ok" in detail.lower()

    def test_fail_on_error_response(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_SLACK_WEBHOOK_URL", "https://hooks.slack.com/bad")
        with patch("urllib.request.urlopen", side_effect=_error_urlopen):
            status, detail = validate_setup.check_slack()
        assert status == "fail"
        assert "PRAXIS_SLACK_WEBHOOK_URL" in detail


# ---------------------------------------------------------------------------
# TestTelegramCheck
# ---------------------------------------------------------------------------

class TestTelegramCheck:
    def test_skip_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        status, _ = validate_setup.check_telegram()
        assert status == "skip"

    def test_pass_on_valid_token(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
        body = json.dumps({"result": {"username": "mybot"}}).encode()
        with patch("urllib.request.urlopen", return_value=_ok_http_ctx(body=body)):
            status, detail = validate_setup.check_telegram()
        assert status == "pass"
        assert "mybot" in detail

    def test_fail_on_invalid_token(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bad_token")
        with patch("urllib.request.urlopen", side_effect=_error_urlopen):
            status, detail = validate_setup.check_telegram()
        assert status == "fail"
        assert "TELEGRAM_BOT_TOKEN" in detail


# ---------------------------------------------------------------------------
# TestWhatsAppCheck
# ---------------------------------------------------------------------------

class TestWhatsAppCheck:
    def test_skip_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("PRAXIS_WHATSAPP_ALLOWED_NUMBERS", raising=False)
        status, _ = validate_setup.check_whatsapp()
        assert status == "skip"

    def test_pass_when_bridge_running(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_WHATSAPP_ALLOWED_NUMBERS", "+1234567890")
        monkeypatch.setenv("PRAXIS_WHATSAPP_BRIDGE_PORT", "3001")
        with patch("urllib.request.urlopen", return_value=_ok_http_ctx()):
            status, detail = validate_setup.check_whatsapp()
        assert status == "pass"
        assert "running" in detail.lower()

    def test_fail_when_bridge_not_running(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_WHATSAPP_ALLOWED_NUMBERS", "+1234567890")
        monkeypatch.setenv("PRAXIS_WHATSAPP_BRIDGE_PORT", "3001")
        with patch("urllib.request.urlopen", side_effect=_error_urlopen):
            status, detail = validate_setup.check_whatsapp()
        assert status == "fail"
        assert "bridge.js" in detail


# ---------------------------------------------------------------------------
# TestWebSearchCheck
# ---------------------------------------------------------------------------

class TestWebSearchCheck:
    def test_skip_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("PRAXIS_WEB_SEARCH_API_KEY", raising=False)
        status, _ = validate_setup.check_web_search()
        assert status == "skip"

    def test_pass_on_200_response(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_WEB_SEARCH_API_KEY", "brave_key")
        with patch("urllib.request.urlopen", return_value=_ok_http_ctx(status=200)):
            status, detail = validate_setup.check_web_search()
        assert status == "pass"
        assert "ok" in detail.lower()

    def test_fail_on_error(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_WEB_SEARCH_API_KEY", "bad_key")
        with patch("urllib.request.urlopen", side_effect=_error_urlopen):
            status, detail = validate_setup.check_web_search()
        assert status == "fail"
        assert "PRAXIS_WEB_SEARCH_API_KEY" in detail


# ---------------------------------------------------------------------------
# TestRunValidation
# ---------------------------------------------------------------------------

class TestRunValidation:
    # Canonical check names as they appear as keys in the results dict
    _CHECK_NAMES = [
        "Email", "Slack", "Linear", "Notion", "Telegram",
        "WhatsApp", "Web search", "Calendar", "GitHub",
    ]

    def _make_checks_list(self, result=("skip", "mocked")):
        """Build a replacement _CHECKS list where every fn returns `result`."""
        return [(name, MagicMock(return_value=result)) for name in self._CHECK_NAMES]

    def test_returns_dict_with_all_check_names(self, monkeypatch):
        """run_validation() returns a dict keyed by each check name."""
        fake_checks = self._make_checks_list()
        monkeypatch.setattr(validate_setup, "_CHECKS", fake_checks)
        results = validate_setup.run_validation()
        for name in self._CHECK_NAMES:
            assert name in results, f"Missing key: {name}"

    def test_counts_summary(self, monkeypatch, capsys):
        """run_validation() prints summary line with N checks: X passed, Y failed, Z skipped."""
        fake_checks = self._make_checks_list(("skip", "not configured"))
        monkeypatch.setattr(validate_setup, "_CHECKS", fake_checks)
        validate_setup.run_validation()
        captured = capsys.readouterr()
        assert "checks:" in captured.out
        assert "passed" in captured.out
        assert "failed" in captured.out
        assert "skipped" in captured.out

    def test_pass_fail_skip_counts_correct(self, monkeypatch, capsys):
        """Summary line counts match the actual distribution of results."""
        # 1 pass, 1 fail, 7 skipped — one per check name
        results_map = {
            "Email": ("pass", "ok"),
            "Slack": ("fail", "bad"),
        }
        fake_checks = [
            (name, MagicMock(return_value=results_map.get(name, ("skip", "n/a"))))
            for name in self._CHECK_NAMES
        ]
        monkeypatch.setattr(validate_setup, "_CHECKS", fake_checks)
        results = validate_setup.run_validation()
        captured = capsys.readouterr()

        passed = sum(1 for s, _ in results.values() if s == "pass")
        failed = sum(1 for s, _ in results.values() if s == "fail")
        skipped = sum(1 for s, _ in results.values() if s == "skip")
        assert passed == 1
        assert failed == 1
        assert skipped == 7

        assert "1 passed" in captured.out
        assert "1 failed" in captured.out
        assert "7 skipped" in captured.out


# ---------------------------------------------------------------------------
# TestDotenvAutoLoad
# ---------------------------------------------------------------------------

class TestDotenvAutoLoad:
    """Verify run_validation() auto-loads .env before running checks."""

    def test_dotenv_loaded_before_checks(self, monkeypatch, tmp_path):
        """Credentials in .env are visible to check functions during run_validation()."""
        import os

        # Write a .env with a fake Linear key
        env_file = tmp_path / ".env"
        env_file.write_text("PRAXIS_LINEAR_API_KEY=fake_lin_key_autoload_test\n")

        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.delenv("PRAXIS_LINEAR_API_KEY", raising=False)

        # Replace _CHECKS with a single check that records what it sees in env
        recorded_key = []

        def fake_linear():
            recorded_key.append(os.environ.get("PRAXIS_LINEAR_API_KEY", ""))
            return ("fail", "mocked network")

        monkeypatch.setattr(validate_setup, "_CHECKS", [("Linear", fake_linear)])

        results = validate_setup.run_validation()

        # Clean up the side-effect of auto-loading on os.environ
        os.environ.pop("PRAXIS_LINEAR_API_KEY", None)

        assert recorded_key == ["fake_lin_key_autoload_test"], (
            "check_linear did not see the key loaded from .env; got: " + repr(recorded_key)
        )
        assert results["Linear"][0] != "skip", "Linear check was skipped despite credential in .env"

    def test_dotenv_loaded_message_printed(self, monkeypatch, tmp_path, capsys):
        """run_validation() prints 'Loaded credentials from .env' when .env exists."""
        env_file = tmp_path / ".env"
        env_file.write_text("# empty test env\n")

        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(validate_setup, "_CHECKS", [])  # no actual checks

        validate_setup.run_validation()

        out = capsys.readouterr().out
        assert "Loaded credentials from .env" in out

    def test_no_dotenv_message_when_missing(self, monkeypatch, tmp_path, capsys):
        """run_validation() does NOT print the .env message when .env is absent."""
        # tmp_path has no .env file
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(validate_setup, "_CHECKS", [])

        validate_setup.run_validation()

        out = capsys.readouterr().out
        assert "Loaded credentials from .env" not in out
