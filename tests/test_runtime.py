"""Tests for ClaudeCodeRuntime auth resolution (Phase B)."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

from praxis.runtime.claude_code import (
    ClaudeCodeRuntime,
    MAX_CONTEXT_MESSAGES,
    CONTEXT_KEEP_RECENT,
)
from tests.conftest import FakeTextBlock, FakeToolUseBlock


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
    # Real exception classes so except clauses work
    mod.AuthenticationError = type("AuthenticationError", (Exception,), {})
    mod.APIConnectionError = type("APIConnectionError", (Exception,), {})
    mod.RateLimitError = type("RateLimitError", (Exception,), {})
    mod.APIStatusError = type("APIStatusError", (Exception,), {})
    with patch.dict(sys.modules, {"anthropic": mod}):
        yield mod


def test_from_env_oauth_token(monkeypatch, mock_anthropic):
    """OAuth token is used when present."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok-123")
    runtime = ClaudeCodeRuntime.from_env()
    assert runtime.auth_method == "oauth"
    mock_anthropic.Anthropic.assert_called_once_with(auth_token="oauth-tok-123")


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
    mock_anthropic.Anthropic.assert_called_once_with(auth_token="oauth-tok-123")


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


def test_from_env_no_anthropic():
    """from_env exits cleanly when anthropic package is missing."""
    with patch.dict(sys.modules, {"anthropic": None}):
        with pytest.raises(SystemExit) as exc_info:
            ClaudeCodeRuntime.from_env()
        assert "anthropic" in str(exc_info.value)


def test_run_loop_auth_error(monkeypatch, mock_anthropic):
    """Authentication errors produce a clean SystemExit."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-bad")
    runtime = ClaudeCodeRuntime.from_env()

    runtime.client.messages.create.side_effect = mock_anthropic.AuthenticationError()

    with pytest.raises(SystemExit) as exc_info:
        runtime.run_loop(
            model="claude-sonnet-4-6",
            system="sys",
            user_message="hi",
            tool_schemas=[],
            tool_executor=lambda n, a: "",
        )
    assert "authentication failed" in str(exc_info.value)


def test_run_loop_connection_error(monkeypatch, mock_anthropic):
    """Connection errors produce a clean SystemExit."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
    runtime = ClaudeCodeRuntime.from_env()

    runtime.client.messages.create.side_effect = mock_anthropic.APIConnectionError()

    with pytest.raises(SystemExit) as exc_info:
        runtime.run_loop(
            model="claude-sonnet-4-6",
            system="sys",
            user_message="hi",
            tool_schemas=[],
            tool_executor=lambda n, a: "",
        )
    assert "cannot reach Anthropic API" in str(exc_info.value)


def test_run_loop_rate_limit_retries_then_exits(monkeypatch, mock_anthropic):
    """Rate limit errors retry 3 times with backoff, then SystemExit."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
    runtime = ClaudeCodeRuntime.from_env()

    runtime.client.messages.create.side_effect = mock_anthropic.RateLimitError()

    sleep_calls: list[float] = []
    with patch("praxis.runtime.claude_code.time.sleep", side_effect=lambda d: sleep_calls.append(d)):
        with pytest.raises(SystemExit) as exc_info:
            runtime.run_loop(
                model="claude-sonnet-4-6",
                system="sys",
                user_message="hi",
                tool_schemas=[],
                tool_executor=lambda n, a: "",
            )
    assert "rate limited" in str(exc_info.value)
    assert "3 retries" in str(exc_info.value)
    # 4 total attempts: initial + 3 retries
    assert runtime.client.messages.create.call_count == 4
    # Backoff delays: 5s, 10s, 20s
    assert sleep_calls == [5, 10, 20]


def test_run_loop_rate_limit_retry_then_succeed(monkeypatch, mock_anthropic):
    """Rate limit on first call, success on second — returns result."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
    runtime = ClaudeCodeRuntime.from_env()

    # First call: 429, second call: success
    success_response = MagicMock()
    success_response.stop_reason = "end_turn"
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "hello after retry"
    success_response.content = [text_block]

    runtime.client.messages.create.side_effect = [
        mock_anthropic.RateLimitError(),
        success_response,
    ]

    with patch("praxis.runtime.claude_code.time.sleep"):
        result = runtime.run_loop(
            model="claude-sonnet-4-6",
            system="sys",
            user_message="hi",
            tool_schemas=[],
            tool_executor=lambda n, a: "",
        )
    assert result == "hello after retry"
    assert runtime.client.messages.create.call_count == 2


# ---------- context window management ----------


def _build_long_conversation(runtime, n_exchanges):
    """Build a messages list with n_exchanges assistant/user pairs."""
    messages = [{"role": "user", "content": "initial prompt"}]
    for i in range(n_exchanges):
        tool_block = FakeToolUseBlock(
            id=f"call_{i}", name=f"Tool{i}", input={"arg": f"val{i}"}
        )
        text_block = FakeTextBlock(text=f"Thinking about step {i}")
        messages = runtime.manage_context(
            messages, "assistant", [text_block, tool_block]
        )
        messages = runtime.manage_context(
            messages,
            "user",
            [{"type": "tool_result", "tool_use_id": f"call_{i}", "content": f"result_{i}"}],
        )
    return messages


def test_manage_context_no_compaction_under_threshold():
    """Messages below threshold are not compacted."""
    runtime = ClaudeCodeRuntime(MagicMock())
    messages = _build_long_conversation(runtime, 5)
    # 1 initial + 5*2 = 11 messages, well under 40
    assert len(messages) == 11


def test_manage_context_compacts_above_threshold():
    """Messages above MAX_CONTEXT_MESSAGES trigger compaction."""
    runtime = ClaudeCodeRuntime(MagicMock())
    messages = _build_long_conversation(runtime, 25)
    # 1 + 50 = 51 without compaction, should be compacted
    assert len(messages) <= MAX_CONTEXT_MESSAGES


def test_compact_context_preserves_recent():
    """Last CONTEXT_KEEP_RECENT messages are kept verbatim after compaction."""
    runtime = ClaudeCodeRuntime(MagicMock())
    messages = _build_long_conversation(runtime, 25)
    # Last messages should contain the most recent tool results
    last_user = [m for m in messages if m["role"] == "user"][-1]
    content = last_user["content"]
    assert isinstance(content, list)
    assert any("result_24" in str(item) for item in content)


def test_compact_context_summary_in_first_message():
    """Compacted exchanges appear as summary in the first user message."""
    runtime = ClaudeCodeRuntime(MagicMock())
    messages = _build_long_conversation(runtime, 25)
    first = messages[0]
    assert "Compacted context" in first["content"]
    # Summary should mention early tool calls
    assert "Tool0" in first["content"]


def test_compact_context_no_info_silently_lost():
    """Summary contains references to all compacted tool calls."""
    runtime = ClaudeCodeRuntime(MagicMock())
    # Build exactly enough to trigger one compaction
    messages = [{"role": "user", "content": "initial"}]
    for i in range(22):
        tool_block = FakeToolUseBlock(id=f"c{i}", name=f"Cmd{i}")
        messages = runtime.manage_context(messages, "assistant", [tool_block])
        messages = runtime.manage_context(
            messages, "user",
            [{"type": "tool_result", "tool_use_id": f"c{i}", "content": f"out{i}"}],
        )
    summary = messages[0]["content"]
    # The earliest tools should be in the summary (they were compacted)
    assert "Cmd0" in summary
    assert "Cmd1" in summary
