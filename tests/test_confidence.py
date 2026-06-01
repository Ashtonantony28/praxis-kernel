"""Tests for confidence-gated planning in praxis/orchestrator.py (TASK-I2F4)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.config import Config
from praxis.orchestrator import Orchestrator
from praxis.runtime import ClaudeCodeRuntime
from tests.conftest import FakeClient, FakeResponse, FakeTextBlock


class TestConfidenceGate:
    def test_threshold_zero_bypasses_check(self, config, monkeypatch):
        """PRAXIS_CONFIDENCE_THRESHOLD=0 skips confidence check entirely."""
        monkeypatch.setenv("PRAXIS_CONFIDENCE_THRESHOLD", "0")
        responses = [FakeResponse(content=[FakeTextBlock("Done!")], stop_reason="end_turn")]
        orch = Orchestrator(ClaudeCodeRuntime(FakeClient(responses)), config)

        with patch.object(orch, "_run_confidence_check") as mock_check:
            result = orch.run("Do a task")

        mock_check.assert_not_called()
        assert result == "Done!"

    def test_high_confidence_proceeds_normally(self, config, monkeypatch):
        """When confidence >= threshold, run() proceeds through the full agent loop."""
        monkeypatch.setenv("PRAXIS_CONFIDENCE_THRESHOLD", "0.7")
        # First call is for confidence check (spawn_subagent -> run_loop not called),
        # but we mock _run_confidence_check directly to isolate the test.
        responses = [FakeResponse(content=[FakeTextBlock("Built it!")], stop_reason="end_turn")]
        orch = Orchestrator(ClaudeCodeRuntime(FakeClient(responses)), config)

        with patch.object(orch, "_run_confidence_check", return_value={"plan": "...", "confidence": 0.9, "ambiguities": []}):
            result = orch.run("Build feature X")

        assert result == "Built it!"

    def test_low_confidence_stages_plan(self, config, tmp_path, monkeypatch):
        """When confidence < threshold, plan is staged and run() returns early."""
        monkeypatch.setenv("PRAXIS_CONFIDENCE_THRESHOLD", "0.7")
        orch = Orchestrator(ClaudeCodeRuntime(FakeClient([])), config)

        low_confidence_check = {
            "plan": "Unclear task",
            "confidence": 0.4,
            "ambiguities": ["What is the target?", "Which system?"],
        }
        with patch.object(orch, "_run_confidence_check", return_value=low_confidence_check):
            with patch.object(orch, "_stage_low_confidence_plan", wraps=orch._stage_low_confidence_plan):
                result = orch.run("Do the thing")

        assert "staged" in result.lower() or "awaiting" in result.lower()

    def test_low_confidence_writes_plan_file(self, config, tmp_path, monkeypatch):
        """Low-confidence run writes .praxis/staging/plans/{id}.json with status=awaiting_input."""
        monkeypatch.setenv("PRAXIS_CONFIDENCE_THRESHOLD", "0.7")
        orch = Orchestrator(ClaudeCodeRuntime(FakeClient([])), config)

        low_check = {"plan": "vague plan", "confidence": 0.3, "ambiguities": ["What scope?"]}
        with patch.object(orch, "_run_confidence_check", return_value=low_check):
            with patch("praxis.orchestrator.Orchestrator._stage_low_confidence_plan", wraps=orch._stage_low_confidence_plan):
                orch.run("Something vague")

        plans_dir = config.workspace_root / ".praxis" / "staging" / "plans"
        plan_files = list(plans_dir.glob("*.json"))
        assert len(plan_files) == 1
        plan_data = json.loads(plan_files[0].read_text())
        assert plan_data["status"] == "awaiting_input"
        assert plan_data["confidence"] == pytest.approx(0.3, abs=0.01)
        assert "What scope?" in plan_data["ambiguities"]

    def test_low_confidence_sends_notification(self, config, monkeypatch):
        """Low-confidence run calls Notifier.notify() with ambiguity details."""
        monkeypatch.setenv("PRAXIS_CONFIDENCE_THRESHOLD", "0.7")
        orch = Orchestrator(ClaudeCodeRuntime(FakeClient([])), config)

        low_check = {"plan": "unclear", "confidence": 0.2, "ambiguities": ["Missing context"]}
        with patch.object(orch, "_run_confidence_check", return_value=low_check):
            with patch("praxis.notifier.Notifier") as MockNotifier:
                mock_notifier_instance = MagicMock()
                MockNotifier.return_value = mock_notifier_instance
                orch.run("vague task")

        mock_notifier_instance.notify.assert_called_once()
        call_args = mock_notifier_instance.notify.call_args[0][0]
        assert "Missing context" in call_args or "ambiguous" in call_args.lower()

    def test_confidence_check_parse_error_defaults_high(self, config, monkeypatch):
        """If planner returns non-JSON, _run_confidence_check returns confidence=1.0."""
        monkeypatch.setenv("PRAXIS_CONFIDENCE_THRESHOLD", "0.7")
        orch = Orchestrator(ClaudeCodeRuntime(FakeClient([])), config)

        with patch.object(orch, "run_subagent", return_value="I cannot parse this as JSON!"):
            result = orch._run_confidence_check("some task")

        assert result["confidence"] == pytest.approx(1.0)
        assert result["ambiguities"] == []

    def test_run_confidence_check_disabled_at_zero(self, config, monkeypatch):
        """_run_confidence_check returns high confidence immediately when threshold=0."""
        monkeypatch.setenv("PRAXIS_CONFIDENCE_THRESHOLD", "0")
        orch = Orchestrator(ClaudeCodeRuntime(FakeClient([])), config)

        with patch.object(orch, "run_subagent") as mock_subagent:
            result = orch._run_confidence_check("task")

        mock_subagent.assert_not_called()
        assert result["confidence"] == pytest.approx(1.0)
