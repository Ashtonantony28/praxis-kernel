"""Tests for OpenAICloudRuntime — cloud OpenAI-compatible endpoint provider."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from praxis.runtime.cloud import (
    OpenAICloudRuntime,
    RATE_LIMIT_BASE_DELAY,
    RATE_LIMIT_MAX_RETRIES,
)
from praxis.runtime.openai_base import MAX_CONTEXT_MESSAGES


# ---------- Fake OpenAI objects (same as test_local_runtime) ----------


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction
    type: str = "function"


@dataclass
class FakeMessage:
    content: str | None = None
    tool_calls: list[FakeToolCall] | None = None
    role: str = "assistant"


@dataclass
class FakeChoice:
    message: FakeMessage
    finish_reason: str = "stop"


@dataclass
class FakeCompletion:
    choices: list[FakeChoice] = field(default_factory=list)


class FakeChatCompletions:
    def __init__(self, responses: list[FakeCompletion]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeCompletion:
        self.calls.append(dict(kwargs))
        return self._responses.pop(0)


class FakeChat:
    def __init__(self, completions: FakeChatCompletions) -> None:
        self.completions = completions


class FakeOpenAIClient:
    def __init__(self, responses: list[FakeCompletion]) -> None:
        self.chat = FakeChat(FakeChatCompletions(responses))


# ---------- Fixtures ----------


SAMPLE_TOOL_SCHEMAS = [
    {
        "name": "Bash",
        "description": "Execute a bash command.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command"},
            },
            "required": ["command"],
        },
    }
]


def _noop_executor(name: str, args: dict[str, Any]) -> str:
    return f"executed {name}"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PRAXIS_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("PRAXIS_CLOUD_BASE_URL", raising=False)
    monkeypatch.delenv("PRAXIS_CLOUD_MODEL", raising=False)


@pytest.fixture
def mock_openai():
    mod = MagicMock()
    mod.OpenAI.return_value = MagicMock()
    mod.APIConnectionError = type("APIConnectionError", (Exception,), {})
    mod.AuthenticationError = type("AuthenticationError", (Exception,), {})
    mod.APIStatusError = type(
        "APIStatusError", (Exception,), {"status_code": 500, "message": "err"}
    )
    mod.RateLimitError = type("RateLimitError", (Exception,), {})
    with patch.dict(sys.modules, {"openai": mod}):
        yield mod


# ---------- from_env ----------


def test_from_env_defaults(monkeypatch, mock_openai):
    """from_env uses default base_url and model when only API key is set."""
    monkeypatch.setenv("PRAXIS_CLOUD_API_KEY", "sk-test-123")
    runtime = OpenAICloudRuntime.from_env()
    assert runtime.default_model == "gpt-4o"
    assert runtime.base_url == "https://api.openai.com/v1"
    mock_openai.OpenAI.assert_called_once_with(
        base_url="https://api.openai.com/v1", api_key="sk-test-123"
    )


def test_from_env_custom(monkeypatch, mock_openai):
    """from_env reads all PRAXIS_CLOUD_* env vars."""
    monkeypatch.setenv("PRAXIS_CLOUD_API_KEY", "sk-openrouter-xyz")
    monkeypatch.setenv("PRAXIS_CLOUD_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("PRAXIS_CLOUD_MODEL", "anthropic/claude-3.5-sonnet")
    runtime = OpenAICloudRuntime.from_env()
    assert runtime.default_model == "anthropic/claude-3.5-sonnet"
    assert runtime.base_url == "https://openrouter.ai/api/v1"
    mock_openai.OpenAI.assert_called_once_with(
        base_url="https://openrouter.ai/api/v1", api_key="sk-openrouter-xyz"
    )


def test_from_env_no_api_key(mock_openai):
    """from_env exits clearly when PRAXIS_CLOUD_API_KEY is unset."""
    with pytest.raises(SystemExit) as exc_info:
        OpenAICloudRuntime.from_env()
    assert "PRAXIS_CLOUD_API_KEY" in str(exc_info.value)


def test_from_env_no_openai_package():
    """from_env exits clearly when openai package is missing."""
    with patch.dict(sys.modules, {"openai": None}):
        with pytest.raises(SystemExit) as exc_info:
            OpenAICloudRuntime.from_env()
        assert "openai" in str(exc_info.value)


# ---------- run_loop ----------


def test_run_loop_simple_text():
    """run_loop returns text when model responds without tool calls."""
    response = FakeCompletion(
        choices=[FakeChoice(message=FakeMessage(content="Hello from GPT"))]
    )
    client = FakeOpenAIClient([response])
    runtime = OpenAICloudRuntime(client, default_model="gpt-4o", base_url="https://api.openai.com/v1")

    result = runtime.run_loop(
        model="gpt-4o",
        system="You are helpful.",
        user_message="Hi",
        tool_schemas=[],
        tool_executor=_noop_executor,
    )
    assert result == "Hello from GPT"


def test_run_loop_with_tool_call():
    """run_loop executes tool calls and feeds results back."""
    tool_call = FakeToolCall(
        id="call_1",
        function=FakeFunction(name="Bash", arguments='{"command": "ls"}'),
    )
    responses = [
        FakeCompletion(
            choices=[
                FakeChoice(
                    message=FakeMessage(content=None, tool_calls=[tool_call]),
                    finish_reason="tool_calls",
                )
            ]
        ),
        FakeCompletion(
            choices=[FakeChoice(message=FakeMessage(content="Files listed."))]
        ),
    ]
    client = FakeOpenAIClient(responses)
    runtime = OpenAICloudRuntime(client, default_model="gpt-4o", base_url="https://api.openai.com/v1")

    calls_log: list[tuple[str, dict]] = []

    def tracking_executor(name: str, args: dict[str, Any]) -> str:
        calls_log.append((name, args))
        return "file1.txt\nfile2.txt"

    result = runtime.run_loop(
        model="gpt-4o",
        system="sys",
        user_message="list files",
        tool_schemas=SAMPLE_TOOL_SCHEMAS,
        tool_executor=tracking_executor,
    )
    assert result == "Files listed."
    assert calls_log == [("Bash", {"command": "ls"})]


def test_run_loop_no_model_remapping():
    """Cloud runtime does NOT remap model IDs — passes them through."""
    response = FakeCompletion(
        choices=[FakeChoice(message=FakeMessage(content="ok"))]
    )
    client = FakeOpenAIClient([response])
    runtime = OpenAICloudRuntime(client, default_model="gpt-4o", base_url="https://api.openai.com/v1")

    runtime.run_loop(
        model="claude-sonnet-4-6",
        system="sys",
        user_message="hi",
        tool_schemas=[],
        tool_executor=_noop_executor,
    )
    # Cloud runtime passes through — no remapping
    assert client.chat.completions.calls[0]["model"] == "claude-sonnet-4-6"


# ---------- rate limit retry ----------


def test_rate_limit_retries_then_exits(mock_openai):
    """Cloud runtime retries on 429 then exits cleanly."""
    client = MagicMock()
    client.chat.completions.create.side_effect = mock_openai.RateLimitError()

    runtime = OpenAICloudRuntime(
        client, default_model="gpt-4o", base_url="https://api.openai.com/v1"
    )

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(SystemExit) as exc_info:
            runtime.run_loop(
                model="gpt-4o",
                system="sys",
                user_message="hi",
                tool_schemas=[],
                tool_executor=_noop_executor,
            )
    assert "rate limited" in str(exc_info.value)
    # Should have retried RATE_LIMIT_MAX_RETRIES times
    assert mock_sleep.call_count == RATE_LIMIT_MAX_RETRIES
    # Verify exponential delays
    delays = [call.args[0] for call in mock_sleep.call_args_list]
    assert delays == [5, 10, 20]


def test_rate_limit_retry_then_succeed(mock_openai):
    """Cloud runtime recovers after a 429 retry."""
    success_response = FakeCompletion(
        choices=[FakeChoice(message=FakeMessage(content="recovered"))]
    )

    call_count = [0]

    def side_effect(**kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise mock_openai.RateLimitError()
        return success_response

    client = MagicMock()
    client.chat.completions.create.side_effect = side_effect

    runtime = OpenAICloudRuntime(
        client, default_model="gpt-4o", base_url="https://api.openai.com/v1"
    )

    with patch("time.sleep"):
        result = runtime.run_loop(
            model="gpt-4o",
            system="sys",
            user_message="hi",
            tool_schemas=[],
            tool_executor=_noop_executor,
        )
    assert result == "recovered"
    assert call_count[0] == 3


# ---------- error handling ----------


def test_auth_error(mock_openai):
    """Auth errors produce a clean SystemExit."""
    client = MagicMock()
    client.chat.completions.create.side_effect = mock_openai.AuthenticationError()

    runtime = OpenAICloudRuntime(
        client, default_model="gpt-4o", base_url="https://api.openai.com/v1"
    )

    with pytest.raises(SystemExit) as exc_info:
        runtime.run_loop(
            model="gpt-4o",
            system="sys",
            user_message="hi",
            tool_schemas=[],
            tool_executor=_noop_executor,
        )
    assert "rejected authentication" in str(exc_info.value)


def test_connection_error(mock_openai):
    """Connection errors produce a clean SystemExit with URL."""
    client = MagicMock()
    client.chat.completions.create.side_effect = mock_openai.APIConnectionError()

    runtime = OpenAICloudRuntime(
        client, default_model="gpt-4o", base_url="https://api.openai.com/v1"
    )

    with pytest.raises(SystemExit) as exc_info:
        runtime.run_loop(
            model="gpt-4o",
            system="sys",
            user_message="hi",
            tool_schemas=[],
            tool_executor=_noop_executor,
        )
    assert "cannot connect" in str(exc_info.value)
    assert "api.openai.com" in str(exc_info.value)


def test_empty_choices():
    """Empty choices array produces a clean SystemExit."""
    response = FakeCompletion(choices=[])
    client = FakeOpenAIClient([response])
    runtime = OpenAICloudRuntime(
        client, default_model="gpt-4o", base_url="https://api.openai.com/v1"
    )

    with pytest.raises(SystemExit) as exc_info:
        runtime.run_loop(
            model="gpt-4o",
            system="sys",
            user_message="hi",
            tool_schemas=[],
            tool_executor=_noop_executor,
        )
    assert "empty response" in str(exc_info.value)


# ---------- context window management ----------


def _build_long_conversation(runtime, n_exchanges):
    """Build a messages list with system + user + n assistant/tool pairs."""
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "initial prompt"},
    ]
    for i in range(n_exchanges):
        assistant_msg = {
            "role": "assistant",
            "content": f"step {i}",
            "tool_calls": [
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": f"Tool{i}", "arguments": "{}"},
                }
            ],
        }
        messages = runtime.manage_context(messages, "assistant", assistant_msg)
        tool_result = {
            "role": "tool",
            "tool_call_id": f"call_{i}",
            "content": f"result_{i}",
        }
        messages = runtime.manage_context(messages, "tool", tool_result)
    return messages


def test_manage_context_no_compaction():
    """Messages below threshold are not compacted."""
    runtime = OpenAICloudRuntime(
        FakeOpenAIClient([]), default_model="gpt-4o", base_url="https://api.openai.com/v1"
    )
    messages = _build_long_conversation(runtime, 5)
    assert len(messages) == 12


def test_manage_context_compacts():
    """Messages above threshold trigger compaction."""
    runtime = OpenAICloudRuntime(
        FakeOpenAIClient([]), default_model="gpt-4o", base_url="https://api.openai.com/v1"
    )
    messages = _build_long_conversation(runtime, 25)
    assert len(messages) <= MAX_CONTEXT_MESSAGES


def test_compact_preserves_system_and_user():
    """System and user prompt messages survive compaction."""
    runtime = OpenAICloudRuntime(
        FakeOpenAIClient([]), default_model="gpt-4o", base_url="https://api.openai.com/v1"
    )
    messages = _build_long_conversation(runtime, 25)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "initial prompt" in messages[1]["content"]


# ---------- convergence routing ----------


def test_convergence_selects_cloud(monkeypatch, tmp_path):
    """Convergence config routes to cloud runtime when configured."""
    from praxis.convergence import ConvergenceConfig

    yaml_content = """
runtimes:
  default: cloud
cloud:
  base_url: https://openrouter.ai/api/v1
  model: meta-llama/llama-3-70b
"""
    (tmp_path / "convergence.yaml").write_text(yaml_content)
    monkeypatch.delenv("PRAXIS_RUNTIME", raising=False)

    config = ConvergenceConfig.load(tmp_path)
    assert config.default_runtime == "cloud"
    assert config.cloud_base_url == "https://openrouter.ai/api/v1"
    assert config.cloud_model == "meta-llama/llama-3-70b"
    assert config.needs_cloud() is True
    assert config.needs_claude() is False


def test_convergence_cloud_override(monkeypatch, tmp_path):
    """Per-subagent override routes scout to cloud."""
    from praxis.convergence import ConvergenceConfig

    yaml_content = """
runtimes:
  default: claude
  overrides:
    scout: cloud
cloud:
  base_url: https://api.groq.com/openai/v1
  model: llama3-70b-8192
"""
    (tmp_path / "convergence.yaml").write_text(yaml_content)
    monkeypatch.delenv("PRAXIS_RUNTIME", raising=False)

    config = ConvergenceConfig.load(tmp_path)
    assert config.default_runtime == "claude"
    assert config.runtime_for("scout") == "cloud"
    assert config.runtime_for("builder") == "claude"
    assert config.needs_cloud() is True
    assert config.needs_claude() is True


def test_convergence_env_var_override(monkeypatch, tmp_path):
    """PRAXIS_RUNTIME=cloud overrides file default."""
    from praxis.convergence import ConvergenceConfig

    (tmp_path / "convergence.yaml").write_text("runtimes:\n  default: claude\n")
    monkeypatch.setenv("PRAXIS_RUNTIME", "cloud")

    config = ConvergenceConfig.load(tmp_path)
    assert config.default_runtime == "cloud"
