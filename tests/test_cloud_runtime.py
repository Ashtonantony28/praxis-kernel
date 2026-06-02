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


def test_run_loop_remaps_claude_model():
    """Cloud runtime remaps Claude model IDs to the configured default."""
    response = FakeCompletion(
        choices=[FakeChoice(message=FakeMessage(content="ok"))]
    )
    client = FakeOpenAIClient([response])
    runtime = OpenAICloudRuntime(client, default_model="gemini-2.5-flash", base_url="https://generativelanguage.googleapis.com/v1beta/openai/")

    runtime.run_loop(
        model="claude-sonnet-4-6",
        system="sys",
        user_message="hi",
        tool_schemas=[],
        tool_executor=_noop_executor,
    )
    # Claude IDs are replaced with the cloud default (subagent defs hardcode claude-*)
    assert client.chat.completions.calls[0]["model"] == "gemini-2.5-flash"


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
    assert delays == [5, 10, 20, 40, 60]


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


def test_503_retries_then_exits(mock_openai):
    """Cloud runtime retries on 503 service unavailable then exits cleanly."""
    err_503 = type("APIStatusError", (Exception,), {"status_code": 503, "message": "overloaded"})
    client = MagicMock()
    client.chat.completions.create.side_effect = err_503()

    with patch.dict(sys.modules["openai"].__dict__ if "openai" in sys.modules else {}, {}):
        # Patch APIStatusError on the mock_openai module so the handler sees 503
        mock_openai.APIStatusError = err_503
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
    assert "service unavailable" in str(exc_info.value)
    assert mock_sleep.call_count == RATE_LIMIT_MAX_RETRIES


def test_503_retry_then_succeed(mock_openai):
    """Cloud runtime recovers after a 503 retry."""
    err_503 = type("APIStatusError", (Exception,), {"status_code": 503, "message": "overloaded"})
    mock_openai.APIStatusError = err_503

    success_response = FakeCompletion(
        choices=[FakeChoice(message=FakeMessage(content="recovered after 503"))]
    )
    call_count = {"n": 0}

    def side_effect(**kwargs: Any) -> FakeCompletion:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise err_503()
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
    assert result == "recovered after 503"


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


def test_resolve_model_passthrough(mock_openai):
    """Non-Claude model IDs are passed through unchanged."""
    rt = OpenAICloudRuntime(mock_openai, default_model="gemini-2.5-flash", base_url="http://x")
    assert rt._resolve_model("gemini-2.5-flash") == "gemini-2.5-flash"
    assert rt._resolve_model("gpt-4o") == "gpt-4o"


def test_resolve_model_substitutes_claude(mock_openai):
    """Claude model IDs are replaced with the configured cloud default."""
    rt = OpenAICloudRuntime(mock_openai, default_model="gemini-2.5-flash", base_url="http://x")
    assert rt._resolve_model("claude-haiku-4-5-20251001") == "gemini-2.5-flash"
    assert rt._resolve_model("claude-sonnet-4-6") == "gemini-2.5-flash"
    assert rt._resolve_model("claude-opus-4-6") == "gemini-2.5-flash"


# ---------- _maybe_compact: token-based compaction ----------


def _make_summary_response(text: str = "Summary") -> FakeCompletion:
    return FakeCompletion(choices=[FakeChoice(message=FakeMessage(content=text))])


def _make_rt_above_threshold(
    monkeypatch, *, model: str = "gpt-4o", threshold: str = "0.8"
) -> OpenAICloudRuntime:
    from praxis.runtime.openai_base import _OPENAI_MODEL_CONTEXT_LIMITS, _OPENAI_DEFAULT_CONTEXT_LIMIT
    monkeypatch.setenv("PRAXIS_COMPACTION_THRESHOLD", threshold)
    max_ctx = _OPENAI_MODEL_CONTEXT_LIMITS.get(model, _OPENAI_DEFAULT_CONTEXT_LIMIT)
    tokens = int(max_ctx * float(threshold)) + 1
    # Supply one summary response for the compaction call
    client = FakeOpenAIClient([_make_summary_response("OpenAI summary")])
    rt = OpenAICloudRuntime(client, default_model=model, base_url="https://api.openai.com/v1")
    rt._cost_breaker = MagicMock()
    rt._session_tokens = tokens
    return rt


def test_openai_runtime_compaction_triggered(monkeypatch):
    """_maybe_compact fires when _session_tokens >= threshold * max_ctx."""
    rt = _make_rt_above_threshold(monkeypatch)
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "ok"},
    ]
    with patch("praxis.event_bus.get_event_bus"):
        result = rt._maybe_compact(msgs, system="sys", model="gpt-4o")

    assert result is not msgs  # rebuilt
    assert rt._session_tokens == 0  # reset


def test_openai_compaction_not_triggered_below_threshold(monkeypatch):
    """_maybe_compact returns unchanged messages when below threshold."""
    from praxis.runtime.openai_base import _OPENAI_DEFAULT_CONTEXT_LIMIT
    monkeypatch.setenv("PRAXIS_COMPACTION_THRESHOLD", "0.9")
    client = FakeOpenAIClient([])
    rt = OpenAICloudRuntime(client, default_model="gpt-4o", base_url="http://x")
    rt._session_tokens = 100  # far below 128_000 * 0.9
    msgs = [{"role": "user", "content": "hi"}]
    result = rt._maybe_compact(msgs, system="sys", model="unknown-model")
    assert result is msgs  # unchanged


def test_openai_compaction_rebuilt_history_starts_with_system(monkeypatch):
    """Rebuilt history preserves system message at position 0."""
    rt = _make_rt_above_threshold(monkeypatch)
    msgs = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "done"},
    ]
    with patch("praxis.event_bus.get_event_bus"):
        result = rt._maybe_compact(msgs, system="you are helpful", model="gpt-4o")

    assert result[0]["role"] == "system"


def test_openai_compaction_summary_in_rebuilt_history(monkeypatch):
    """Summary text appears in the rebuilt messages."""
    rt = _make_rt_above_threshold(monkeypatch)
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "result"},
    ]
    with patch("praxis.event_bus.get_event_bus"):
        result = rt._maybe_compact(msgs, system="sys", model="gpt-4o")

    full_text = " ".join(m.get("content", "") for m in result)
    assert "OpenAI summary" in full_text


def test_openai_compaction_tokens_reset(monkeypatch):
    """_session_tokens is reset to 0 after successful compaction."""
    rt = _make_rt_above_threshold(monkeypatch)
    rt._maybe_compact(
        [{"role": "user", "content": "x"}],
        system="sys",
        model="gpt-4o",
    )
    assert rt._session_tokens == 0


def test_openai_compaction_returns_unchanged_on_api_failure(monkeypatch):
    """On LLM failure, returns original messages unchanged."""
    from praxis.runtime.openai_base import _OPENAI_DEFAULT_CONTEXT_LIMIT
    monkeypatch.setenv("PRAXIS_COMPACTION_THRESHOLD", "0.1")
    client = FakeOpenAIClient([])
    rt = OpenAICloudRuntime(client, default_model="gpt-4o", base_url="http://x")
    rt._cost_breaker = MagicMock()
    rt._session_tokens = 999_999_999  # far above threshold

    # Inject an exception into _call_api
    with patch.object(rt, "_call_api", side_effect=RuntimeError("API down")):
        msgs = [{"role": "user", "content": "original"}]
        result = rt._maybe_compact(msgs, system="sys", model="gpt-4o")

    assert result is msgs  # unchanged


def test_openai_compaction_event_emitted(monkeypatch):
    """COMPACTION_FIRED event is published on successful compaction."""
    rt = _make_rt_above_threshold(monkeypatch)
    msgs = [{"role": "user", "content": "task"}]

    mock_bus = MagicMock()
    with patch("praxis.event_bus.get_event_bus", return_value=mock_bus):
        rt._maybe_compact(msgs, system="sys", model="gpt-4o")

    mock_bus.publish_sync.assert_called_once()
    payload = mock_bus.publish_sync.call_args[0][1]
    assert payload["model"] == "gpt-4o"


def test_openai_compaction_threshold_env_var_respected(monkeypatch):
    """PRAXIS_COMPACTION_THRESHOLD=0.5 fires at 50% of context."""
    from praxis.runtime.openai_base import _OPENAI_DEFAULT_CONTEXT_LIMIT
    monkeypatch.setenv("PRAXIS_COMPACTION_THRESHOLD", "0.5")
    tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.6)  # above 0.5, below default 0.8

    client = FakeOpenAIClient([_make_summary_response()])
    rt = OpenAICloudRuntime(client, default_model="gpt-4o", base_url="http://x")
    rt._cost_breaker = MagicMock()
    rt._session_tokens = tokens

    with patch("praxis.event_bus.get_event_bus"):
        result = rt._maybe_compact(
            [{"role": "user", "content": "x"}], system="sys", model="unknown-model"
        )

    assert rt._session_tokens == 0  # compaction fired
