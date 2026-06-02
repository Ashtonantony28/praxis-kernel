"""Tests for token-based context compaction in ClaudeCodeRuntime."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from praxis.runtime.claude_code import (
    ClaudeCodeRuntime,
    _DEFAULT_CONTEXT_LIMIT,
    _MODEL_CONTEXT_LIMITS,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_runtime() -> ClaudeCodeRuntime:
    client = MagicMock()
    return ClaudeCodeRuntime(client)


def _summary_response(text: str = "Summary of conversation") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _pairs(n: int) -> list[dict]:
    """Return n alternating user/assistant message dicts (2*n messages)."""
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"user msg {i}"})
        msgs.append({"role": "assistant", "content": f"asst msg {i}"})
    return msgs


# ─── module-level constants ───────────────────────────────────────────────────

class TestModuleConstants:
    def test_model_limits_dict_exists(self):
        assert isinstance(_MODEL_CONTEXT_LIMITS, dict)
        assert len(_MODEL_CONTEXT_LIMITS) >= 1

    def test_default_context_limit(self):
        assert _DEFAULT_CONTEXT_LIMIT == 128_000

    def test_known_model_has_200k_limit(self):
        assert _MODEL_CONTEXT_LIMITS.get("claude-sonnet-4-6") == 200_000


# ─── session token tracking ──────────────────────────────────────────────────

class TestSessionTokenTracking:
    def test_initial_tokens_zero(self):
        rt = _make_runtime()
        assert rt._session_tokens == 0

    def test_tokens_are_int(self):
        rt = _make_runtime()
        assert isinstance(rt._session_tokens, int)


# ─── _maybe_compact: not triggered ───────────────────────────────────────────

class TestMaybeCompactNotTriggered:
    def test_not_triggered_well_below_threshold(self):
        rt = _make_runtime()
        rt._session_tokens = 1_000  # far below 128_000 * 0.8 = 102_400
        messages = _pairs(3)
        result = rt._maybe_compact(messages, system="sys", model="unknown-model")
        assert result is messages  # same object — nothing changed

    def test_not_triggered_just_below_threshold(self):
        rt = _make_runtime()
        max_ctx = _DEFAULT_CONTEXT_LIMIT
        rt._session_tokens = int(max_ctx * 0.8) - 1  # one below trigger
        messages = _pairs(3)
        result = rt._maybe_compact(messages, system="sys", model="unknown-model")
        assert result is messages

    def test_model_specific_limit_used(self):
        rt = _make_runtime()
        model = "claude-sonnet-4-6"  # 200_000 limit
        model_ctx = _MODEL_CONTEXT_LIMITS[model]
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

class TestMaybeCompactTriggered:
    def test_triggered_at_exact_threshold(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.8)  # exactly at threshold
        rt.client.messages.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(10), system="sys", model="unknown-model")

        rt.client.messages.create.assert_called_once()
        assert result is not _pairs(10)  # rebuilt list

    def test_triggered_above_threshold(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.95)
        rt.client.messages.create.return_value = _summary_response("Detailed summary")

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(8), system="sys", model="unknown-model")

        rt.client.messages.create.assert_called_once()
        assert isinstance(result, list)

    def test_threshold_env_var_respected(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_COMPACTION_THRESHOLD", "0.5")
        rt = _make_runtime()
        # 60% of default limit — above 0.5 threshold, below default 0.8
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.6)
        rt.client.messages.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(5), system="sys", model="unknown-model")

        rt.client.messages.create.assert_called_once()
        assert result is not _pairs(5)

    def test_high_threshold_not_triggered(self, monkeypatch):
        monkeypatch.setenv("PRAXIS_COMPACTION_THRESHOLD", "0.99")
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.8)  # below 99% threshold
        messages = _pairs(5)
        result = rt._maybe_compact(messages, system="sys", model="unknown-model")
        assert result is messages  # not triggered


# ─── history rebuild ─────────────────────────────────────────────────────────

class TestHistoryRebuild:
    def test_first_message_contains_summary(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.messages.create.return_value = _summary_response("MySpecialSummary")

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(10), system="sys", model="unknown-model")

        assert len(result) > 0
        assert "MySpecialSummary" in result[0]["content"]

    def test_first_message_is_user_role(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.messages.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(10), system="sys", model="unknown-model")

        assert result[0]["role"] == "user"

    def test_alternating_roles_maintained(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.messages.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(_pairs(12), system="sys", model="unknown-model")

        for i, msg in enumerate(result):
            expected_role = "user" if i % 2 == 0 else "assistant"
            assert msg["role"] == expected_role, (
                f"Message {i} has role {msg['role']!r}, expected {expected_role!r}"
            )

    def test_recent_messages_preserved(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.messages.create.return_value = _summary_response()

        messages = _pairs(15)  # 30 messages total
        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(messages, system="sys", model="unknown-model")

        # Last message of result should match last message of input
        assert result[-1] == messages[-1]

    def test_small_history_handled_gracefully(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.messages.create.return_value = _summary_response()

        messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        with patch("praxis.event_bus.get_event_bus"):
            result = rt._maybe_compact(messages, system="sys", model="unknown-model")

        assert isinstance(result, list)
        assert len(result) > 0


# ─── token reset ─────────────────────────────────────────────────────────────

class TestTokenReset:
    def test_tokens_reset_to_zero_after_compaction(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.messages.create.return_value = _summary_response()

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

class TestEventEmission:
    def test_compaction_fired_event_emitted(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.messages.create.return_value = _summary_response()

        mock_bus = MagicMock()
        with patch("praxis.event_bus.get_event_bus", return_value=mock_bus):
            rt._maybe_compact(_pairs(5), system="sys", model="unknown-model")

        mock_bus.publish_sync.assert_called_once()
        event_name = mock_bus.publish_sync.call_args[0][0]
        assert "compaction" in event_name

    def test_event_not_emitted_below_threshold(self):
        rt = _make_runtime()
        rt._session_tokens = 500  # well below threshold
        messages = _pairs(3)

        mock_bus = MagicMock()
        with patch("praxis.event_bus.get_event_bus", return_value=mock_bus):
            rt._maybe_compact(messages, system="sys", model="unknown-model")

        mock_bus.publish_sync.assert_not_called()

    def test_event_payload_contains_model(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.messages.create.return_value = _summary_response()

        mock_bus = MagicMock()
        with patch("praxis.event_bus.get_event_bus", return_value=mock_bus):
            rt._maybe_compact(_pairs(5), system="sys", model="test-model-x")

        payload = mock_bus.publish_sync.call_args[0][1]
        assert payload["model"] == "test-model-x"


# ─── failure resilience ──────────────────────────────────────────────────────

class TestCompactionFailureResilience:
    def test_returns_original_when_llm_call_fails(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.messages.create.side_effect = Exception("API down")

        messages = _pairs(5)
        result = rt._maybe_compact(messages, system="sys", model="unknown-model")

        assert result is messages  # unchanged

    def test_tokens_not_reset_on_failure(self):
        rt = _make_runtime()
        original_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt._session_tokens = original_tokens
        rt.client.messages.create.side_effect = RuntimeError("connection error")

        rt._maybe_compact(_pairs(5), system="sys", model="unknown-model")

        assert rt._session_tokens == original_tokens  # unchanged

    def test_event_bus_failure_does_not_propagate(self):
        rt = _make_runtime()
        rt._session_tokens = int(_DEFAULT_CONTEXT_LIMIT * 0.9)
        rt.client.messages.create.return_value = _summary_response()

        with patch("praxis.event_bus.get_event_bus", side_effect=ImportError("no bus")):
            # Should not raise
            result = rt._maybe_compact(_pairs(4), system="sys", model="unknown-model")

        assert isinstance(result, list)
