"""Tests for the financial/cost circuit breaker (TASK-P01)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from praxis.runtime.cost import (
    CostCircuitBreaker,
    _DEFAULT_MAX_COST,
    _DEFAULT_PRICING,
    _MODEL_PRICING,
)


# ---------------------------------------------------------------------------
# Class 1: CostCircuitBreaker unit tests
# ---------------------------------------------------------------------------


class TestCostCircuitBreakerDefaults:
    """Tests 1-3: constructor defaults and env-var parsing."""

    def test_default_cap_when_env_not_set(self, monkeypatch):
        """Default cap is $2.00 when PRAXIS_MAX_SESSION_COST not set."""
        monkeypatch.delenv("PRAXIS_MAX_SESSION_COST", raising=False)
        breaker = CostCircuitBreaker.from_env()
        assert breaker.max_cost == _DEFAULT_MAX_COST
        assert breaker.max_cost == 2.00

    def test_cap_read_from_env_var(self, monkeypatch):
        """Cap is read from PRAXIS_MAX_SESSION_COST env var."""
        monkeypatch.setenv("PRAXIS_MAX_SESSION_COST", "5.50")
        breaker = CostCircuitBreaker.from_env()
        assert breaker.max_cost == 5.50

    def test_invalid_env_var_falls_back_to_default(self, monkeypatch, capsys):
        """Invalid PRAXIS_MAX_SESSION_COST logs warning and uses default."""
        monkeypatch.setenv("PRAXIS_MAX_SESSION_COST", "not-a-number")
        breaker = CostCircuitBreaker.from_env()
        assert breaker.max_cost == _DEFAULT_MAX_COST
        captured = capsys.readouterr()
        assert "warning" in captured.err
        assert "not-a-number" in captured.err


class TestCostAccumulation:
    """Tests 4-6: cost accumulation and call log."""

    def test_cost_accumulates_for_known_model(self):
        """Costs accumulate correctly for claude-sonnet-4-6."""
        breaker = CostCircuitBreaker(max_cost_usd=100.0)
        in_price, out_price = _MODEL_PRICING["claude-sonnet-4-6"]
        # 1000 input + 500 output
        expected = (1000 * in_price + 500 * out_price) / 1_000_000
        breaker.record_call("claude-sonnet-4-6", 1000, 500)
        assert abs(breaker.session_cost - expected) < 1e-9

    def test_multiple_calls_accumulate_in_call_log(self):
        """Multiple calls accumulate correctly in _call_log."""
        breaker = CostCircuitBreaker(max_cost_usd=100.0)
        breaker.record_call("claude-sonnet-4-6", 100, 50)
        breaker.record_call("claude-sonnet-4-6", 200, 100)
        assert len(breaker._call_log) == 2
        # Second entry session_total should be larger than first
        assert breaker._call_log[1]["session_total_usd"] > breaker._call_log[0]["call_cost_usd"]

    def test_unknown_model_uses_default_pricing(self):
        """Unknown model uses _DEFAULT_PRICING."""
        breaker = CostCircuitBreaker(max_cost_usd=100.0)
        in_price, out_price = _DEFAULT_PRICING
        expected = (1000 * in_price + 500 * out_price) / 1_000_000
        breaker.record_call("some-unknown-model-xyz", 1000, 500)
        assert abs(breaker.session_cost - expected) < 1e-9


class TestCircuitBreakerTrip:
    """Tests 7-10: breaker trips with exit code 3 and correct artifacts."""

    def test_breaker_trips_at_cap(self, tmp_path, monkeypatch):
        """Breaker trips (sys.exit(3)) when session_cost >= max_cost."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        breaker = CostCircuitBreaker(max_cost_usd=0.000001)
        with pytest.raises(SystemExit) as exc_info:
            breaker.record_call("claude-sonnet-4-6", 10000, 5000)
        assert exc_info.value.code == 3

    def test_trip_exit_code_is_3(self, tmp_path, monkeypatch):
        """Exit code on trip is exactly 3."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        breaker = CostCircuitBreaker(max_cost_usd=0.000001)
        with pytest.raises(SystemExit) as exc_info:
            breaker.record_call("claude-sonnet-4-6", 10000, 5000)
        assert exc_info.value.code == 3

    def test_trip_writes_json_with_correct_schema(self, tmp_path, monkeypatch):
        """Trip writes JSON to .praxis/logs/cost-circuit-break-*.json with correct schema."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        breaker = CostCircuitBreaker(max_cost_usd=0.000001)
        with pytest.raises(SystemExit):
            breaker.record_call("claude-sonnet-4-6", 10000, 5000)

        log_dir = tmp_path / ".praxis" / "logs"
        trace_files = list(log_dir.glob("cost-circuit-break-*.json"))
        assert len(trace_files) == 1

        data = json.loads(trace_files[0].read_text())
        assert data["event"] == "COST_CIRCUIT_BREAK"
        assert "timestamp" in data
        assert "max_cost_usd" in data
        assert "session_cost_usd" in data
        assert "call_count" in data
        assert "call_log" in data
        assert data["call_count"] == 1
        assert len(data["call_log"]) == 1

    def test_trip_stderr_mentions_session_cost_and_cap(self, tmp_path, monkeypatch, capsys):
        """Trip writes stderr message mentioning 'session cost' and 'cap'."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        breaker = CostCircuitBreaker(max_cost_usd=0.000001)
        with pytest.raises(SystemExit):
            breaker.record_call("claude-sonnet-4-6", 10000, 5000)
        captured = capsys.readouterr()
        assert "session cost" in captured.err
        assert "cap" in captured.err or "0.000001" in captured.err


# ---------------------------------------------------------------------------
# Class 2: ClaudeCodeRuntime integration tests
# ---------------------------------------------------------------------------


class TestClaudeCodeRuntimeCostIntegration:
    """Tests 11-13: ClaudeCodeRuntime wires up CostCircuitBreaker."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("PRAXIS_MAX_SESSION_COST", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    @pytest.fixture
    def mock_anthropic(self):
        mod = MagicMock()
        mod.Anthropic.return_value = MagicMock()
        mod.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mod.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mod.RateLimitError = type("RateLimitError", (Exception,), {})
        mod.APIStatusError = type("APIStatusError", (Exception,), {})
        with patch.dict(sys.modules, {"anthropic": mod}):
            yield mod

    def test_init_creates_cost_breaker_instance(self, mock_anthropic):
        """ClaudeCodeRuntime.__init__ creates a CostCircuitBreaker instance."""
        from praxis.runtime.claude_code import ClaudeCodeRuntime

        fake_client = MagicMock()
        runtime = ClaudeCodeRuntime(fake_client)
        assert hasattr(runtime, "_cost_breaker")
        assert isinstance(runtime._cost_breaker, CostCircuitBreaker)

    def test_run_loop_records_usage_when_present(self, monkeypatch, mock_anthropic):
        """run_loop() calls record_call() when response has usage."""
        from praxis.runtime.claude_code import ClaudeCodeRuntime

        fake_usage = SimpleNamespace(input_tokens=100, output_tokens=50)
        fake_response = SimpleNamespace(
            content=[],
            stop_reason="end_turn",
            usage=fake_usage,
        )

        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        runtime = ClaudeCodeRuntime(fake_client)
        recorded_calls = []

        def fake_record(model, input_tokens, output_tokens):
            recorded_calls.append((model, input_tokens, output_tokens))

        runtime._cost_breaker.record_call = fake_record

        runtime.run_loop(
            model="claude-sonnet-4-6",
            system="sys",
            user_message="hello",
            tool_schemas=[],
            tool_executor=lambda name, args: "ok",
        )

        assert len(recorded_calls) == 1
        assert recorded_calls[0] == ("claude-sonnet-4-6", 100, 50)

    def test_run_loop_trips_when_cost_exceeded(self, tmp_path, monkeypatch, mock_anthropic):
        """ClaudeCodeRuntime trips circuit breaker when accumulated cost >= cap."""
        from praxis.runtime.claude_code import ClaudeCodeRuntime

        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_MAX_SESSION_COST", "0.000001")

        fake_usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000)
        fake_response = SimpleNamespace(
            content=[],
            stop_reason="end_turn",
            usage=fake_usage,
        )

        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        runtime = ClaudeCodeRuntime(fake_client)

        with pytest.raises(SystemExit) as exc_info:
            runtime.run_loop(
                model="claude-sonnet-4-6",
                system="sys",
                user_message="hello",
                tool_schemas=[],
                tool_executor=lambda name, args: "ok",
            )
        assert exc_info.value.code == 3


# ---------------------------------------------------------------------------
# Class 3: OpenAIBaseRuntime integration tests
# ---------------------------------------------------------------------------


class TestOpenAIBaseRuntimeCostIntegration:
    """Tests 14-15: OpenAIBaseRuntime wires up CostCircuitBreaker."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("PRAXIS_MAX_SESSION_COST", raising=False)

    def test_init_creates_cost_breaker_instance(self):
        """OpenAIBaseRuntime.__init__ creates a CostCircuitBreaker instance."""
        from praxis.runtime.openai_base import OpenAIBaseRuntime

        fake_client = MagicMock()
        runtime = OpenAIBaseRuntime(
            fake_client, default_model="gpt-4o", base_url="http://localhost"
        )
        assert hasattr(runtime, "_cost_breaker")
        assert isinstance(runtime._cost_breaker, CostCircuitBreaker)

    def test_run_loop_records_usage_when_present(self):
        """OpenAIBaseRuntime.run_loop() records usage via record_call()."""
        from praxis.runtime.openai_base import OpenAIBaseRuntime

        fake_usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
        fake_choice = MagicMock()
        fake_choice.finish_reason = "stop"
        fake_choice.message.content = "done"
        fake_choice.message.tool_calls = None
        fake_response = MagicMock()
        fake_response.usage = fake_usage
        fake_response.choices = [fake_choice]

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response

        runtime = OpenAIBaseRuntime(
            fake_client, default_model="gpt-4o", base_url="http://localhost"
        )

        recorded_calls = []

        def fake_record(model, input_tokens, output_tokens):
            recorded_calls.append((model, input_tokens, output_tokens))

        runtime._cost_breaker.record_call = fake_record

        runtime.run_loop(
            model="gpt-4o",
            system="sys",
            user_message="hello",
            tool_schemas=[],
            tool_executor=lambda name, args: "ok",
        )

        assert len(recorded_calls) == 1
        assert recorded_calls[0] == ("gpt-4o", 100, 50)
