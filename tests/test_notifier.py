"""Tests for praxis/notifier.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.notifier import Notifier


# 1. Slack notify: urlopen is called with the webhook URL
def test_slack_notify_sent(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_SLACK_WEBHOOK_URL", "https://hooks.slack.com/test/webhook")
    monkeypatch.delenv("PRAXIS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_CHAT_ID", raising=False)

    notifier = Notifier(tmp_path)
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = b"ok"

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        notifier.notify("hello")

    mock_open.assert_called_once()
    req_arg = mock_open.call_args[0][0]
    assert "hooks.slack.com" in req_arg.full_url


# 2. Telegram notify: urlopen is called with api.telegram.org
def test_telegram_notify_sent(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_TELEGRAM_BOT_TOKEN", "testtoken123")
    monkeypatch.setenv("PRAXIS_TELEGRAM_CHAT_ID", "12345")
    monkeypatch.delenv("PRAXIS_SLACK_WEBHOOK_URL", raising=False)

    notifier = Notifier(tmp_path)
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = b"ok"

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        notifier.notify("hello")

    mock_open.assert_called_once()
    req_arg = mock_open.call_args[0][0]
    assert "api.telegram.org" in req_arg.full_url


# 3. No backends configured → no HTTP calls
def test_no_backends_no_call(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PRAXIS_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_CHAT_ID", raising=False)

    notifier = Notifier(tmp_path)
    with patch("urllib.request.urlopen") as mock_open:
        notifier.notify("hello")

    mock_open.assert_not_called()


# 4. urlopen raises → notify() does NOT raise; writes to notifier.log
def test_failure_silenced(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_SLACK_WEBHOOK_URL", "https://hooks.slack.com/fail")
    monkeypatch.delenv("PRAXIS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_CHAT_ID", raising=False)

    notifier = Notifier(tmp_path)
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        notifier.notify("hello")  # Must NOT raise

    log = notifier._log_path
    assert log.exists()
    content = log.read_text()
    assert "network down" in content


# 5. notify_task_complete formats message containing outcome
def test_notify_task_complete_formats(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PRAXIS_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_CHAT_ID", raising=False)

    notifier = Notifier(tmp_path)
    received = []

    def fake_notify(message: str, channel: str = "default") -> None:
        received.append(message)

    notifier.notify = fake_notify  # type: ignore[method-assign]
    notifier.notify_task_complete("prompt text", "success", "all good")

    assert len(received) == 1
    assert "success" in received[0]


# 6. notify_morning_handoff reads file content and calls notify
def test_notify_morning_handoff_reads_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PRAXIS_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_CHAT_ID", raising=False)

    handoff = tmp_path / "morning-handoff.md"
    handoff.write_text("Today's priorities: X, Y, Z")

    notifier = Notifier(tmp_path)
    received = []
    notifier.notify = lambda msg, channel="default": received.append(msg)  # type: ignore[method-assign]
    notifier.notify_morning_handoff(handoff)

    assert len(received) == 1
    assert "Today's priorities" in received[0]


# 7. notify_morning_handoff with missing file calls notify with "no file found"
def test_notify_morning_handoff_missing_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PRAXIS_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_CHAT_ID", raising=False)

    notifier = Notifier(tmp_path)
    received = []
    notifier.notify = lambda msg, channel="default": received.append(msg)  # type: ignore[method-assign]
    notifier.notify_morning_handoff(tmp_path / "nonexistent.md")

    assert len(received) == 1
    assert "no file found" in received[0]


# 8. Secrets not logged when Slack call fails
def test_secrets_not_logged(tmp_path: Path, monkeypatch):
    secret_url = "https://hooks.slack.com/services/SECRET/URL/TOKEN"
    monkeypatch.setenv("PRAXIS_SLACK_WEBHOOK_URL", secret_url)
    monkeypatch.delenv("PRAXIS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PRAXIS_TELEGRAM_CHAT_ID", raising=False)

    notifier = Notifier(tmp_path)
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        notifier.notify("sensitive message")

    log = notifier._log_path
    assert log.exists()
    log_content = log.read_text()
    assert secret_url not in log_content, "Slack webhook URL must NOT appear in notifier.log"
    assert "[REDACTED]" in log_content
