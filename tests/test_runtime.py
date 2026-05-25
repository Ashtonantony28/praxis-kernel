"""Tests for ClaudeCodeRuntime auth resolution (Phase B)."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from praxis.runtime.claude_code import ClaudeCodeRuntime


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure auth env vars don't leak between tests."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def mock_anthropic():
    """Inject a fake anthropic module into sys.modules."""
    mod = MagicMock()
    mod.Anthropic.return_value = MagicMock()
    with patch.dict(sys.modules, {"anthropic": mod}):
        yield mod


def test_from_env_oauth_token(monkeypatch, mock_anthropic):
    """OAuth token is used when present."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok-123")
    runtime = ClaudeCodeRuntime.from_env()
    assert runtime.auth_method == "oauth"
    mock_anthropic.Anthropic.assert_called_once_with(api_key="oauth-tok-123")


def test_from_env_api_key(monkeypatch, mock_anthropic):
    """API key is used when OAuth token is absent."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
    runtime = ClaudeCodeRuntime.from_env()
    assert runtime.auth_method == "api_key"
    mock_anthropic.Anthropic.assert_called_once_with()


def test_from_env_oauth_priority(monkeypatch, mock_anthropic):
    """OAuth wins when both are set."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
    runtime = ClaudeCodeRuntime.from_env()
    assert runtime.auth_method == "oauth"
    mock_anthropic.Anthropic.assert_called_once_with(api_key="oauth-tok-123")


def test_from_env_oauth_scrubs_api_key(monkeypatch, mock_anthropic):
    """When OAuth is active, ANTHROPIC_API_KEY is removed from os.environ."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
    ClaudeCodeRuntime.from_env()
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_from_env_neither_exits(mock_anthropic):
    """SystemExit when no auth is configured."""
    with pytest.raises(SystemExit) as exc_info:
        ClaudeCodeRuntime.from_env()
    assert "no auth configured" in str(exc_info.value)
