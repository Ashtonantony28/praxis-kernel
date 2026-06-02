"""Tests for token-based context compaction in OpenAIBaseRuntime."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from praxis.runtime.openai_base import (
    OpenAIBaseRuntime,
    _OPENAI_DEFAULT_CONTEXT_LIMIT,
    _OPENAI_MODEL_CONTEXT_LIMITS,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


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


def _make_runtime() -> OpenAIBaseRuntime:
    """Create an OpenAIBaseRuntime with a mock client."""
    client = MagicMock()
    return OpenAIBaseRuntime(client, default_model="gpt-4o", base_url="http://localhost")


def _summary_response(text: str = "Summary of conversation") -> FakeCompletion:
    return FakeCompletion(
        choices=[FakeChoice(message=FakeMessage(content=text))]
    )


def _pairs(n: int) -> list[dict]:
    """Return n alternating user/assistant message dicts (2*n messages)."""
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"user msg {i}"})
        msgs.append({"role": "assistant", "content": f"asst msg {i}"})
    return msgs


# ─── module-level constants ───────────────────────────────────────────────────


class TestOpenAIModuleConstants:
    def test_model_limits_dict_exists(self):
        assert isinstance(_OPENAI_MODEL_CONTEXT_LIMITS, dict)
        assert len(_OPENAI_MODEL_CONTEXT_LIMITS) >= 1

    def test_default_context_limit_is_128k(self):
        assert _OPENAI_DEFAULT_CONTEXT_LIMIT == 128_000

    def test_gpt4o_has_128k_limit(self):
        assert _OPENAI_MODEL_CONTEXT_LIMITS.get("gpt-4o") == 128_000

    def test_o1_has_200k_limit(self):
        assert _OPENAI_MODEL_CONTEXT_LIMITS.get("o1") == 200_000


# ─── session token tracking ──────────────────────────────────────────────────


class TestOpenAISessionTokenTracking:
    def test_initial_tokens_zero(self):
        rt = _make_runtime()
        assert rt._session_tokens == 0

    def test_tokens_are_int(self):
        rt = _make_runtime()
        assert isinstance(rt._session_tokens, int)

    def test_tokens_accumulate_from_usage(self):
        """Tokens from prompt_tokens + completion_tokens are summed into _session_tokens."""
        rt = _make_runtime()

        usage = MagicMock()
        usage.prompt_tokens = 1000
        usage.completion_tokens = 500
        # Simulate what run_loop does
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        rt._session_tokens += in_tok + out_tok

        assert rt._session_tokens == 1500


# ─── _maybe_compact: not triggered ───────────────────────────────────────────


class TestOpenAIMaybeCompactNotTriggered:
    def test_not_triggered_well_below_threshold(self):
        rt = _make_runtime()
        rt._session_tokens = 1_000  # far below 128_000 * 0.8 = 102_400
        messages = _pairs(3)
        result = rt._maybe_compact(messages, system="sys", model="unknown-model")
        assert result is messages  # same object — nothing changed

    def test_not_triggered_just_below_threshold(self):
        rt = _make_runtime()
        max_ctx = _OPENAI_DEFAULT_CONTEXT_LIMIT
        rt._session_tokens = int(max_ctx * 0.8) - 1  # one below trigger
        messages = _pairs(3)
        result = rt._maybe_compact(messages, system="sys", model="unknown-model")
        assert result is messages

    def test_model_specific_limit_used(self):
        rt = _make_runtime()
        model = "o1"  # 200_000 limit
        model_ctx = _OPENAI_MODEL_CONTEXT_LIMITS[model]
        # 50% of 200k — below default 80% threshold
        rt._session_tokens = int(model_ctx * 0.5)
        messages = _pairs(5)
        result = rt._maybe_compact(messages, system="sys", model=model)
        assert result is messages  # not triggered

    def test_unknown_model_uses_default_limit(self):
        rt = _make_runtime()
        rt._session_tokens = 500  # below 128_000 * 0.8
        messages = _pairs(2)
        result = rt._maybe_compact(messages, system="sys", model="nonexistent-model-xyz")
        assert result is messages


# ─── _maybe_compact: triggered ───────────────────────────────────────────────


class TestOpenAIMaybeCompactTriggered:
    def test_triggered_at_exact_threshold(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.8)
        rt.client.chat.completions.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(10), system="sys", model="unknown-model")

        rt.client.chat.completions.create.assert_called_once()
        assert isinstance(result, list)

    def test_triggered_above_threshold(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.95)
        rt.client.chat.completions.create.return_value = _summary_response("Detailed summary")

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(8), system="sys", model="unknown-model")

        rt.client.chat.completions.create.assert_called_once()
        assert isinstance(result, list)

    def test_threshold_env_var_respected(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_COMPACTION_THRESHOLD", "0.5")
        rt = _make_runtime()
        # 60% of default limit — above 0.5 threshold, below default 0.8
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.6)
        rt.client.chat.completions.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(5), system="sys", model="unknown-model")

        rt.client.chat.completions.create.assert_called_once()

    def test_high_threshold_not_triggered(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_COMPACTION_THRESHOLD", "0.99")
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.8)  # below 99% threshold
        messages = _pairs(5)
        result = rt._maybe_compact(messages, system="sys", model="unknown-model")
        assert result is messages  # not triggered


# ─── history rebuild ─────────────────────────────────────────────────────────


class TestOpenAIHistoryRebuild:
    def test_first_message_is_system_role(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(10), system="sys", model="unknown-model")

        assert result[0]["role"] == "system"

    def test_second_message_contains_summary(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.return_value = _summary_response("MySpecialSummary")

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(10), system="sys", model="unknown-model")

        assert len(result) >= 2
        assert result[1]["role"] == "user"
        assert "MySpecialSummary" in result[1]["content"]

    def test_third_message_is_assistant_ack(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(10), system="sys", model="unknown-model")

        assert len(result) >= 3
        assert result[2]["role"] == "assistant"

    def test_recent_messages_preserved(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.return_value = _summary_response()

        messages = _pairs(15)  # 30 messages total
        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(messages, system="sys", model="unknown-model")

        # Last message of result should match last message of input
        assert result[-1] == messages[-1]

    def test_small_history_handled_gracefully(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.return_value = _summary_response()

        messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(messages, system="sys", model="unknown-model")

        assert isinstance(result, list)
        assert len(result) > 0

    def test_system_message_content_matches(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(5), system="my-system-prompt", model="unknown-model")

        assert result[0]["content"] == "my-system-prompt"


# ─── token reset ─────────────────────────────────────────────────────────────


class TestOpenAITokenReset:
    def test_tokens_reset_to_zero_after_compaction(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus"):
            rt._maybe_compact(_pairs(5), system="sys", model="unknown-model")

        assert rt._session_tokens == 0

    def test_tokens_unchanged_when_not_triggered(self):
        rt = _make_runtime()
        rt._session_tokens = 12_345
        messages = _pairs(3)
        rt._maybe_compact(messages, system="sys", model="unknown-model")
        assert rt._session_tokens == 12_345


# ─── event emission ──────────────────────────────────────────────────────────


class TestOpenAIEventEmission:
    def test_compaction_fired_event_emitted(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.return_value = _summary_response()

        mock_bus = MagicMock()
        with patch("praxis.event_bus.get_event_bus", return_value=mock_bus):
            rt._maybe_compact(_pairs(5), system="sys", model="unknown-model")

        mock_bus.publish_sync.assert_called_once()
        event_name = mock_bus.publish_sync.call_args[0][0]
        assert "compaction" in event_name

    def test_event_not_emitted_below_threshold(self):
        rt = _make_runtime()
        rt._session_tokens = 500
        messages = _pairs(3)

        mock_bus = MagicMock()
        with patch("praxis.event_bus.get_event_bus", return_value=mock_bus):
            rt._maybe_compact(messages, system="sys", model="unknown-model")

        mock_bus.publish_sync.assert_not_called()

    def test_event_payload_contains_model(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.return_value = _summary_response()

        mock_bus = MagicMock()
        with patch("praxis.event_bus.get_event_bus", return_value=mock_bus):
            rt._maybe_compact(_pairs(5), system="sys", model="test-openai-model")

        payload = mock_bus.publish_sync.call_args[0][1]
        assert payload["model"] == "test-openai-model"


# ─── failure resilience ──────────────────────────────────────────────────────


class TestOpenAICompactionFailureResilience:
    def test_returns_original_when_llm_call_fails(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.side_effect = Exception("API down")

        messages = _pairs(5)
        result = rt._maybe_compact(messages, system="sys", model="unknown-model")

        assert result is messages  # unchanged

    def test_tokens_not_reset_on_failure(self):
        rt = _make_runtime()
        original_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt._session_tokens = original_tokens
        rt.client.chat.completions.create.side_effect = RuntimeError("connection error")

        rt._maybe_compact(_pairs(5), system="sys", model="unknown-model")

        assert rt._session_tokens == original_tokens  # unchanged

    def test_event_bus_failure_does_not_propagate(self):
        rt = _make_runtime()
        rt._session_tokens = int(_OPENAI_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.chat.completions.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus", side_effect=ImportError("no bus")):
            # Should not raise
            result = rt._maybe_compact(_pairs(4), system="sys", model="unknown-model")

        assert isinstance(result, list)


# ─── _extract_text ────────────────────────────────────────────────────────────


class TestOpenAIExtractText:
    def test_extracts_content_from_response(self):
        rt = _make_runtime()
        resp = _summary_response("Hello world")
        assert rt._extract_text(resp) == "Hello world"

    def test_returns_empty_string_for_no_choices(self):
        rt = _make_runtime()
        resp = FakeCompletion(choices=[])
        assert rt._extract_text(resp) == ""

    def test_returns_empty_string_for_none_content(self):
        rt = _make_runtime()
        resp = FakeCompletion(choices=[FakeChoice(message=FakeMessage(content=None))])
        assert rt._extract_text(resp) == ""


# ─── integration: run_loop token tracking ────────────────────────────────────


class TestOpenAIRunLoopTokenTracking:
    def _make_simple_response(self) -> Any:
        """A simple stop response with usage data."""
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "Done"
        resp.choices[0].message.tool_calls = None
        resp.choices[0].finish_reason = "stop"
        resp.usage = usage
        return resp

    def test_tokens_accumulate_from_run_loop(self):
        rt = _make_runtime()
        rt.client.chat.completions.create.return_value = self._make_simple_response()

        rt.run_loop(
            model="gpt-4o",
            system="system",
            user_message="hello",
            tool_schemas=[],
            tool_executor=lambda name, args: "ok",
        )

        assert rt._session_tokens == 150  # 100 + 50

    def test_compaction_triggered_in_run_loop(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_COMPACTION_THRESHOLD", "0.0001")  # trigger immediately
        rt = _make_runtime()

        # First call returns usage, second call (compaction) returns summary
        simple = self._make_simple_response()
        summary = _summary_response("compact summary")
        # run_loop calls _call_api; _maybe_compact also calls _call_api
        rt.client.chat.completions.create.side_effect = [simple, summary]

        with patch("praxis.event_bus.get_event_bus"):
            result = rt.run_loop(
                model="gpt-4o",
                system="system",
                user_message="hello",
                tool_schemas=[],
                tool_executor=lambda name, args: "ok",
            )

        assert rt.client.chat.completions.create.call_count == 2
        assert rt._session_tokens == 0  # reset after compaction
