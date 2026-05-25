"""Tests for LocalRuntime — OpenAI-compatible endpoint provider."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from praxis.runtime.local import LocalRuntime, MAX_CONTEXT_MESSAGES


# ---------- Fake OpenAI objects ----------


@dataclass
class FakeFunction:
    name: str
    arguments: str  # JSON string, like the real OpenAI SDK


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
    """Records calls and returns scripted responses."""

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


# ---------- from_env ----------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PRAXIS_LOCAL_BASE_URL", raising=False)
    monkeypatch.delenv("PRAXIS_LOCAL_MODEL", raising=False)


@pytest.fixture
def mock_openai():
    mod = MagicMock()
    mod.OpenAI.return_value = MagicMock()
    # Real exception classes so except clauses work
    mod.APIConnectionError = type("APIConnectionError", (Exception,), {})
    mod.AuthenticationError = type("AuthenticationError", (Exception,), {})
    mod.APIStatusError = type("APIStatusError", (Exception,), {})
    with patch.dict(sys.modules, {"openai": mod}):
        yield mod


def test_from_env_defaults(mock_openai):
    """from_env uses default base_url and model."""
    runtime = LocalRuntime.from_env()
    assert runtime.default_model == "llama3.1:8b"
    assert runtime.base_url == "http://localhost:11434"
    mock_openai.OpenAI.assert_called_once_with(
        base_url="http://localhost:11434/v1", api_key="ollama"
    )


def test_from_env_custom(monkeypatch, mock_openai):
    """from_env reads PRAXIS_LOCAL_BASE_URL and PRAXIS_LOCAL_MODEL."""
    monkeypatch.setenv("PRAXIS_LOCAL_BASE_URL", "http://gpu-box:8080")
    monkeypatch.setenv("PRAXIS_LOCAL_MODEL", "qwen2:7b")
    runtime = LocalRuntime.from_env()
    assert runtime.default_model == "qwen2:7b"
    assert runtime.base_url == "http://gpu-box:8080"
    mock_openai.OpenAI.assert_called_once_with(
        base_url="http://gpu-box:8080/v1", api_key="ollama"
    )


def test_from_env_no_openai():
    """from_env exits clearly when openai package is missing."""
    with patch.dict(sys.modules, {"openai": None}):
        with pytest.raises(SystemExit) as exc_info:
            LocalRuntime.from_env()
        assert "openai" in str(exc_info.value)


# ---------- run_loop ----------


def test_run_loop_simple_text():
    """run_loop returns text when model responds without tool calls."""
    response = FakeCompletion(
        choices=[FakeChoice(message=FakeMessage(content="Hello world"))]
    )
    client = FakeOpenAIClient([response])
    runtime = LocalRuntime(client)

    result = runtime.run_loop(
        model="llama3.1:8b",
        system="You are helpful.",
        user_message="Hi",
        tool_schemas=[],
        tool_executor=_noop_executor,
    )
    assert result == "Hello world"

    # Verify system prompt is in messages
    call = client.chat.completions.calls[0]
    assert call["messages"][0] == {"role": "system", "content": "You are helpful."}
    assert call["messages"][1] == {"role": "user", "content": "Hi"}


def test_run_loop_with_tool_call():
    """run_loop executes tool calls and feeds results back."""
    tool_call = FakeToolCall(
        id="call_1",
        function=FakeFunction(name="Bash", arguments='{"command": "ls"}'),
    )
    # First response: tool call, second response: final text
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
            choices=[FakeChoice(message=FakeMessage(content="Done listing files."))]
        ),
    ]
    client = FakeOpenAIClient(responses)
    runtime = LocalRuntime(client)

    calls_log: list[tuple[str, dict]] = []

    def tracking_executor(name: str, args: dict[str, Any]) -> str:
        calls_log.append((name, args))
        return "file1.txt\nfile2.txt"

    result = runtime.run_loop(
        model="llama3.1:8b",
        system="sys",
        user_message="list files",
        tool_schemas=SAMPLE_TOOL_SCHEMAS,
        tool_executor=tracking_executor,
    )
    assert result == "Done listing files."
    assert calls_log == [("Bash", {"command": "ls"})]

    # Second API call should include tool result in messages
    second_call = client.chat.completions.calls[1]
    tool_msg = [m for m in second_call["messages"] if m.get("role") == "tool"]
    assert len(tool_msg) == 1
    assert tool_msg[0]["tool_call_id"] == "call_1"
    assert tool_msg[0]["content"] == "file1.txt\nfile2.txt"


def test_run_loop_model_resolution():
    """Claude model IDs are replaced with default_model."""
    response = FakeCompletion(
        choices=[FakeChoice(message=FakeMessage(content="ok"))]
    )
    client = FakeOpenAIClient([response])
    runtime = LocalRuntime(client, default_model="mistral:7b")

    runtime.run_loop(
        model="claude-sonnet-4-6",
        system="sys",
        user_message="hi",
        tool_schemas=[],
        tool_executor=_noop_executor,
    )
    assert client.chat.completions.calls[0]["model"] == "mistral:7b"


def test_run_loop_non_claude_model_passthrough():
    """Non-Claude model IDs pass through unchanged."""
    response = FakeCompletion(
        choices=[FakeChoice(message=FakeMessage(content="ok"))]
    )
    client = FakeOpenAIClient([response])
    runtime = LocalRuntime(client)

    runtime.run_loop(
        model="qwen2:7b",
        system="sys",
        user_message="hi",
        tool_schemas=[],
        tool_executor=_noop_executor,
    )
    assert client.chat.completions.calls[0]["model"] == "qwen2:7b"


def test_run_loop_max_turns():
    """run_loop respects max_turns and returns last content."""
    # Always returns a tool call — should stop after max_turns
    tool_call = FakeToolCall(
        id="call_loop",
        function=FakeFunction(name="Bash", arguments='{"command": "echo hi"}'),
    )
    responses = [
        FakeCompletion(
            choices=[
                FakeChoice(
                    message=FakeMessage(content="still going", tool_calls=[tool_call]),
                    finish_reason="tool_calls",
                )
            ]
        )
        for _ in range(3)
    ]
    client = FakeOpenAIClient(responses)
    runtime = LocalRuntime(client)

    result = runtime.run_loop(
        model="llama3.1:8b",
        system="sys",
        user_message="loop",
        tool_schemas=SAMPLE_TOOL_SCHEMAS,
        tool_executor=_noop_executor,
        max_turns=3,
    )
    assert result == "still going"
    assert len(client.chat.completions.calls) == 3


# ---------- execute_tool ----------


def test_execute_tool_parses_json_args():
    """execute_tool parses JSON string arguments from tool calls."""
    client = FakeOpenAIClient([])
    runtime = LocalRuntime(client)

    tool_calls = [
        FakeToolCall(
            id="call_abc",
            function=FakeFunction(
                name="Read",
                arguments='{"file_path": "/tmp/test.txt"}',
            ),
        )
    ]

    received: list[tuple[str, dict]] = []

    def capture(name: str, args: dict[str, Any]) -> str:
        received.append((name, args))
        return "file contents"

    results = runtime.execute_tool(tool_calls, capture)
    assert len(results) == 1
    assert results[0]["role"] == "tool"
    assert results[0]["tool_call_id"] == "call_abc"
    assert results[0]["content"] == "file contents"
    assert received == [("Read", {"file_path": "/tmp/test.txt"})]


def test_execute_tool_dict_form():
    """execute_tool handles dict-form tool calls (for testing)."""
    client = FakeOpenAIClient([])
    runtime = LocalRuntime(client)

    tool_calls = [
        {
            "id": "call_dict",
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "pwd"}',
            },
        }
    ]

    results = runtime.execute_tool(tool_calls, _noop_executor)
    assert results[0]["tool_call_id"] == "call_dict"
    assert results[0]["content"] == "executed Bash"


# ---------- manage_context ----------


def test_manage_context_plain_string():
    """manage_context appends simple role/content messages."""
    runtime = LocalRuntime(FakeOpenAIClient([]))
    msgs: list[dict[str, Any]] = []

    result = runtime.manage_context(msgs, "user", "hello")
    assert result == [{"role": "user", "content": "hello"}]


def test_manage_context_dict_with_role():
    """manage_context appends full message dicts directly."""
    runtime = LocalRuntime(FakeOpenAIClient([]))
    msgs: list[dict[str, Any]] = []

    tool_result = {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
    result = runtime.manage_context(msgs, "tool", tool_result)
    assert result == [tool_result]


# ---------- _convert_tools ----------


def test_convert_tools():
    """Anthropic tool schemas are converted to OpenAI function format."""
    converted = LocalRuntime._convert_tools(SAMPLE_TOOL_SCHEMAS)
    assert len(converted) == 1
    assert converted[0]["type"] == "function"
    func = converted[0]["function"]
    assert func["name"] == "Bash"
    assert func["description"] == "Execute a bash command."
    assert func["parameters"]["required"] == ["command"]


def test_convert_tools_empty():
    """Empty schema list produces empty tool list."""
    assert LocalRuntime._convert_tools([]) == []


# ---------- spawn_subagent ----------


def test_spawn_subagent_delegates():
    """spawn_subagent calls run_loop with the same arguments."""
    response = FakeCompletion(
        choices=[FakeChoice(message=FakeMessage(content="subagent result"))]
    )
    client = FakeOpenAIClient([response])
    runtime = LocalRuntime(client)

    result = runtime.spawn_subagent(
        model="llama3.1:8b",
        system="You are a scout.",
        prompt="Find the file.",
        tool_schemas=[],
        tool_executor=_noop_executor,
    )
    assert result == "subagent result"


# ---------- _resolve_model ----------


def test_resolve_model_claude():
    runtime = LocalRuntime(FakeOpenAIClient([]), default_model="phi3:mini")
    assert runtime._resolve_model("claude-sonnet-4-6") == "phi3:mini"
    assert runtime._resolve_model("claude-opus-4-6") == "phi3:mini"
    assert runtime._resolve_model("claude-haiku-4-5-20251001") == "phi3:mini"


def test_resolve_model_local():
    runtime = LocalRuntime(FakeOpenAIClient([]))
    assert runtime._resolve_model("llama3.1:8b") == "llama3.1:8b"
    assert runtime._resolve_model("qwen2:7b") == "qwen2:7b"


# ---------- error handling ----------


def test_run_loop_connection_error(mock_openai):
    """Connection errors produce a clean SystemExit."""
    client = MagicMock()
    client.chat.completions.create.side_effect = mock_openai.APIConnectionError()

    runtime = LocalRuntime(client, base_url="http://localhost:11434")

    with pytest.raises(SystemExit) as exc_info:
        runtime.run_loop(
            model="llama3.1:8b",
            system="sys",
            user_message="hi",
            tool_schemas=[],
            tool_executor=_noop_executor,
        )
    assert "cannot connect" in str(exc_info.value)
    assert "localhost:11434" in str(exc_info.value)


def test_run_loop_auth_error(mock_openai):
    """Auth errors from local server produce a clean SystemExit."""
    client = MagicMock()
    client.chat.completions.create.side_effect = mock_openai.AuthenticationError()

    runtime = LocalRuntime(client)

    with pytest.raises(SystemExit) as exc_info:
        runtime.run_loop(
            model="llama3.1:8b",
            system="sys",
            user_message="hi",
            tool_schemas=[],
            tool_executor=_noop_executor,
        )
    assert "rejected authentication" in str(exc_info.value)


def test_run_loop_empty_choices():
    """Empty choices array produces a clean SystemExit."""
    response = FakeCompletion(choices=[])
    client = FakeOpenAIClient([response])
    runtime = LocalRuntime(client)

    with pytest.raises(SystemExit) as exc_info:
        runtime.run_loop(
            model="llama3.1:8b",
            system="sys",
            user_message="hi",
            tool_schemas=[],
            tool_executor=_noop_executor,
        )
    assert "empty response" in str(exc_info.value)


def test_execute_tool_malformed_json():
    """Malformed JSON arguments produce an error result, not a crash."""
    client = FakeOpenAIClient([])
    runtime = LocalRuntime(client)

    tool_calls = [
        FakeToolCall(
            id="call_bad",
            function=FakeFunction(name="Bash", arguments="not valid json{{{"),
        )
    ]

    results = runtime.execute_tool(tool_calls, _noop_executor)
    assert len(results) == 1
    assert results[0]["tool_call_id"] == "call_bad"
    assert "malformed" in results[0]["content"]


# ---------- context window management ----------


def _build_long_local_conversation(runtime, n_exchanges):
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


def test_local_manage_context_no_compaction():
    """Messages below threshold are not compacted."""
    runtime = LocalRuntime(FakeOpenAIClient([]))
    messages = _build_long_local_conversation(runtime, 5)
    # 2 initial + 5*2 = 12
    assert len(messages) == 12


def test_local_manage_context_compacts():
    """Messages above threshold trigger compaction."""
    runtime = LocalRuntime(FakeOpenAIClient([]))
    messages = _build_long_local_conversation(runtime, 25)
    assert len(messages) <= MAX_CONTEXT_MESSAGES


def test_local_compact_preserves_system_and_user():
    """System and user prompt messages survive compaction."""
    runtime = LocalRuntime(FakeOpenAIClient([]))
    messages = _build_long_local_conversation(runtime, 25)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "initial prompt" in messages[1]["content"]


def test_local_compact_summary_mentions_tools():
    """Compacted summary mentions tool names from older exchanges."""
    runtime = LocalRuntime(FakeOpenAIClient([]))
    messages = _build_long_local_conversation(runtime, 25)
    summary = messages[1]["content"]
    assert "Compacted context" in summary
    assert "Tool0" in summary
