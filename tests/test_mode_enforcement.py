"""tests/test_mode_enforcement.py — Mode-aware enforcement tests.

Part 1: Unit tests for enforce() with mode= parameter.
Part 2: Parametrized cross-runtime tests proving mode enforcement fires
        inside execute_tool() for ClaudeCodeRuntime, OpenAIBaseRuntime,
        and LocalRuntime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from praxis.runtime.enforcement import EnforcementError, enforce
from praxis.runtime.claude_code import ClaudeCodeRuntime
from praxis.runtime.openai_base import OpenAIBaseRuntime
from praxis.runtime.local import LocalRuntime
from praxis.modes.plan import MODE as plan_mode
from praxis.modes.build import MODE as build_mode

from tests.conftest import FakeToolUseBlock


# ---------------------------------------------------------------------------
# OpenAI-style fake dataclasses (same pattern as test_enforcement.py)
# ---------------------------------------------------------------------------

@dataclass
class FakeFunction:
    name: str
    arguments: str  # JSON string


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


# ---------------------------------------------------------------------------
# Runtime factory helpers
# ---------------------------------------------------------------------------

def _make_claude(tmp_path):
    """ClaudeCodeRuntime with a mock Anthropic client."""
    client = MagicMock()
    return ClaudeCodeRuntime(client=client)


def _make_openai(tmp_path):
    """OpenAIBaseRuntime with a mock OpenAI client."""
    client = MagicMock()
    return OpenAIBaseRuntime(client=client, default_model="gpt-4o", base_url="http://fake")


def _make_local(tmp_path):
    """LocalRuntime with a mock client (inherits execute_tool from OpenAIBaseRuntime)."""
    client = MagicMock()
    return LocalRuntime(client=client, default_model="llama3.1:8b", base_url="http://fake")


# ---------------------------------------------------------------------------
# Part 1 — Unit tests on enforce() with mode=
# ---------------------------------------------------------------------------

class TestEnforceModeCheck:
    """Call enforce() directly with mode= parameter."""

    def test_plan_mode_blocks_write(self, tmp_path, monkeypatch):
        """enforce('Write', ..., mode=plan_mode) raises EnforcementError."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        good_path = str(tmp_path / "file.txt")
        with pytest.raises(EnforcementError):
            enforce("Write", {"file_path": good_path, "content": "x"}, mode=plan_mode)

    def test_plan_mode_blocks_bash(self, tmp_path, monkeypatch):
        """enforce('Bash', ..., mode=plan_mode) raises EnforcementError."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        with pytest.raises(EnforcementError):
            enforce("Bash", {"command": "echo hi"}, mode=plan_mode)

    def test_plan_mode_allows_read(self, tmp_path, monkeypatch):
        """enforce('Read', ..., mode=plan_mode) does NOT raise (Read not denied)."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        # Should NOT raise — Read is not in plan's denied_tools
        enforce("Read", {"file_path": str(tmp_path / "x")}, mode=plan_mode)

    def test_build_mode_allows_write_inside_workspace(self, tmp_path, monkeypatch):
        """enforce('Write', workspace_path, mode=build_mode) does NOT raise."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        good_path = str(tmp_path / "ok.txt")
        # build mode has no denied_tools — should not raise
        enforce("Write", {"file_path": good_path, "content": "data"}, mode=build_mode)

    def test_no_mode_allows_write_inside_workspace(self, tmp_path, monkeypatch):
        """enforce('Write', workspace_path, mode=None) does NOT raise."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        good_path = str(tmp_path / "ok.txt")
        enforce("Write", {"file_path": good_path, "content": "data"}, mode=None)

    def test_error_message_mentions_tool_name(self, tmp_path, monkeypatch):
        """EnforcementError message for mode-denied call mentions the tool name."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        good_path = str(tmp_path / "file.txt")
        with pytest.raises(EnforcementError) as exc_info:
            enforce("Write", {"file_path": good_path, "content": "x"}, mode=plan_mode)
        assert "Write" in str(exc_info.value)

    def test_error_message_mentions_mode_name(self, tmp_path, monkeypatch):
        """EnforcementError message for mode-denied call mentions the mode name."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        good_path = str(tmp_path / "file.txt")
        with pytest.raises(EnforcementError) as exc_info:
            enforce("Write", {"file_path": good_path, "content": "x"}, mode=plan_mode)
        assert "plan" in str(exc_info.value)

    def test_empty_denied_tools_mode_does_not_block(self, tmp_path, monkeypatch):
        """Mode with empty denied_tools does not block any tool."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        good_path = str(tmp_path / "ok.txt")
        # build_mode.denied_tools is empty — must not block
        enforce("Write", {"file_path": good_path, "content": "x"}, mode=build_mode)
        enforce("Edit", {"file_path": good_path, "old_string": "a", "new_string": "b"}, mode=build_mode)


# ---------------------------------------------------------------------------
# Part 2 — Cross-runtime parametrized mode enforcement tests
# ---------------------------------------------------------------------------

RUNTIME_FACTORIES = [
    pytest.param(_make_claude, id="ClaudeCodeRuntime"),
    pytest.param(_make_openai, id="OpenAIBaseRuntime"),
    pytest.param(_make_local, id="LocalRuntime"),
]


class TestCrossRuntimeModeEnforcement:
    """Mode enforcement runs inside execute_tool() for all three runtimes."""

    # ------------------------------------------------------------------
    # Routing helpers — each runtime uses a different call format
    # ------------------------------------------------------------------

    def _run_claude(self, runtime: ClaudeCodeRuntime, name: str, input_dict: dict, tool_executor):
        block = FakeToolUseBlock(id="call-1", name=name, input=input_dict)
        return runtime.execute_tool([block], tool_executor)

    def _run_openai(self, runtime, name: str, input_dict: dict, tool_executor):
        tc = FakeToolCall(
            id="call-1",
            function=FakeFunction(name=name, arguments=json.dumps(input_dict)),
        )
        return runtime.execute_tool([tc], tool_executor)

    def _run(self, runtime, name: str, input_dict: dict, tool_executor):
        if isinstance(runtime, ClaudeCodeRuntime):
            return self._run_claude(runtime, name, input_dict, tool_executor)
        return self._run_openai(runtime, name, input_dict, tool_executor)

    # ------------------------------------------------------------------
    # Assertion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_blocked(results: list[dict]) -> bool:
        return any("BLOCKED by §5" in str(r.get("content", "")) for r in results)

    # ------------------------------------------------------------------
    # Scenario 1 — plan mode blocks Write
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_plan_mode_blocks_write(self, make_runtime, tmp_path, monkeypatch):
        """In plan mode, Write is blocked; tool_executor is never called."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        runtime._current_mode = plan_mode
        tool_executor = MagicMock(return_value="ok")

        good_path = str(tmp_path / "file.txt")
        results = self._run(
            runtime, "Write", {"file_path": good_path, "content": "data"}, tool_executor
        )

        assert self._is_blocked(results), f"Expected BLOCKED, got: {results}"
        tool_executor.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 2 — plan mode blocks Bash
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_plan_mode_blocks_bash(self, make_runtime, tmp_path, monkeypatch):
        """In plan mode, Bash is blocked; tool_executor is never called."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        runtime._current_mode = plan_mode
        tool_executor = MagicMock(return_value="ok")

        results = self._run(
            runtime, "Bash", {"command": "echo hi"}, tool_executor
        )

        assert self._is_blocked(results), f"Expected BLOCKED, got: {results}"
        tool_executor.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 3 — plan mode allows Read
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_plan_mode_allows_read(self, make_runtime, tmp_path, monkeypatch):
        """In plan mode, Read is NOT blocked; tool_executor IS called."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        runtime._current_mode = plan_mode
        tool_executor = MagicMock(return_value="file contents")

        results = self._run(
            runtime, "Read", {"file_path": str(tmp_path / "x.txt")}, tool_executor
        )

        tool_executor.assert_called_once()
        assert not self._is_blocked(results), f"Expected allowed, got BLOCKED: {results}"

    # ------------------------------------------------------------------
    # Scenario 4 — build mode allows Write
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_build_mode_allows_write(self, make_runtime, tmp_path, monkeypatch):
        """In build mode (no restrictions), Write inside workspace is allowed."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        runtime._current_mode = build_mode
        tool_executor = MagicMock(return_value="written")

        good_path = str(tmp_path / "output.txt")
        results = self._run(
            runtime, "Write", {"file_path": good_path, "content": "data"}, tool_executor
        )

        tool_executor.assert_called_once()
        assert not self._is_blocked(results), f"Expected allowed, got BLOCKED: {results}"
