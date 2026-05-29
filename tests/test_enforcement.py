"""Tests for praxis/runtime/enforcement.py — cross-runtime §5 enforcement.

Part 1: Unit tests for enforce() called directly.
Part 2: Parametrized tests proving enforcement fires inside each runtime's
        execute_tool() and that the underlying tool_executor is never called
        when a call is blocked.
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

# Re-use the FakeToolUseBlock from conftest (already exported from there).
from tests.conftest import FakeToolUseBlock


# ---------------------------------------------------------------------------
# Helpers for OpenAI-style tool calls
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

def _make_claude_runtime(tmp_path):
    """ClaudeCodeRuntime with a mock Anthropic client."""
    client = MagicMock()
    return ClaudeCodeRuntime(client=client)


def _make_openai_runtime(tmp_path):
    """OpenAIBaseRuntime with a mock OpenAI client."""
    client = MagicMock()
    return OpenAIBaseRuntime(client=client, default_model="gpt-4o", base_url="http://fake")


def _make_local_runtime(tmp_path):
    """LocalRuntime with a mock OpenAI client (inherits execute_tool from OpenAIBaseRuntime)."""
    client = MagicMock()
    return LocalRuntime(client=client, default_model="llama3.1:8b", base_url="http://fake")


# ---------------------------------------------------------------------------
# Part 1 — Unit tests on enforce() directly
# ---------------------------------------------------------------------------

class TestEnforceDirect:
    """Call enforce() in isolation — no runtime involved."""

    def test_write_outside_workspace_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        with pytest.raises(EnforcementError, match="outside WORKSPACE_ROOT"):
            enforce("Write", {"file_path": "/tmp/evil.txt", "content": "bad"})

    def test_write_inside_workspace_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        # Should NOT raise — path is under workspace
        enforce("Write", {"file_path": str(tmp_path / "ok.txt"), "content": "fine"})

    def test_webfetch_non_allowlisted_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        with pytest.raises(EnforcementError, match="not in PRAXIS_ALLOWED_DOMAINS"):
            enforce("WebFetch", {"url": "https://example.com"})

    def test_webfetch_allowlisted_domain_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "example.com")
        # Should NOT raise
        enforce("WebFetch", {"url": "https://example.com/page"})

    def test_control_plane_write_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        control_plane_path = str(tmp_path / ".claude" / "settings.json")
        with pytest.raises(EnforcementError, match="control plane"):
            enforce("Write", {"file_path": control_plane_path, "content": "bad"})

    def test_bash_bypass_pattern_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        with pytest.raises(EnforcementError, match="bypass"):
            enforce("Bash", {"command": "python3 -c \"open('/etc/passwd', 'w').write('evil')\""})

    def test_bash_safe_echo_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        # Should NOT raise
        enforce("Bash", {"command": "echo hello"})

    def test_websearch_non_allowlisted_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        with pytest.raises(EnforcementError, match="not in PRAXIS_ALLOWED_DOMAINS"):
            enforce("WebSearch", {"url": "https://evil.com", "query": "test"})

    def test_workspace_root_unset_raises(self, monkeypatch):
        monkeypatch.delenv("PRAXIS_WORKSPACE_ROOT", raising=False)
        with pytest.raises(EnforcementError, match="PRAXIS_WORKSPACE_ROOT is unset"):
            enforce("Write", {"file_path": "/tmp/x.txt", "content": ""})

    def test_read_tool_not_blocked(self, tmp_path, monkeypatch):
        """Read is a read-only tool — enforcement must not block it regardless of path."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        # Must not raise even for paths outside workspace
        enforce("Read", {"file_path": "/etc/passwd"})

    def test_edit_outside_workspace_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        with pytest.raises(EnforcementError, match="outside WORKSPACE_ROOT"):
            enforce("Edit", {"file_path": "/tmp/outside.txt", "old_string": "a", "new_string": "b"})

    def test_edit_control_plane_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        cp_path = str(tmp_path / ".claude" / "hooks" / "escalation-boundary.py")
        with pytest.raises(EnforcementError, match="control plane"):
            enforce("Edit", {"file_path": cp_path, "old_string": "x", "new_string": "y"})

    def test_websearch_with_no_url_blocked(self, tmp_path, monkeypatch):
        """WebSearch with no url arg raises (no url → can't check domain)."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        with pytest.raises(EnforcementError):
            enforce("WebSearch", {"query": "test"})

    def test_webfetch_with_no_url_blocked(self, tmp_path, monkeypatch):
        """WebFetch with no url arg raises."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        with pytest.raises(EnforcementError):
            enforce("WebFetch", {})


# ---------------------------------------------------------------------------
# Part 2 — Parametrized cross-runtime enforcement tests
# ---------------------------------------------------------------------------

RUNTIME_FACTORIES = [
    pytest.param(_make_claude_runtime, id="ClaudeCodeRuntime"),
    pytest.param(_make_openai_runtime, id="OpenAIBaseRuntime"),
    pytest.param(_make_local_runtime, id="LocalRuntime"),
]


class TestCrossRuntimeEnforcement:
    """Same enforcement assertions run on every runtime.

    Proves that enforcement is wired into execute_tool() for all three
    runtimes and that the underlying tool_executor is never called when
    a call is blocked.
    """

    # ------------------------------------------------------------------
    # Routing helpers — each runtime has a different tool-call format
    # ------------------------------------------------------------------

    def _run_claude(self, runtime: ClaudeCodeRuntime, name: str, input_dict: dict, tool_executor):
        """Execute a single tool call through ClaudeCodeRuntime.execute_tool()."""
        block = FakeToolUseBlock(id="call-1", name=name, input=input_dict)
        return runtime.execute_tool([block], tool_executor)

    def _run_openai(self, runtime, name: str, input_dict: dict, tool_executor):
        """Execute a single tool call through OpenAI-style execute_tool()."""
        tc = FakeToolCall(
            id="call-1",
            function=FakeFunction(name=name, arguments=json.dumps(input_dict)),
        )
        return runtime.execute_tool([tc], tool_executor)

    def _run(self, runtime, name: str, input_dict: dict, tool_executor):
        """Route to the correct runner based on runtime type."""
        if isinstance(runtime, ClaudeCodeRuntime):
            return self._run_claude(runtime, name, input_dict, tool_executor)
        return self._run_openai(runtime, name, input_dict, tool_executor)

    # ------------------------------------------------------------------
    # Helpers for assertions
    # ------------------------------------------------------------------

    @staticmethod
    def _contents(results: list[dict]) -> list[str]:
        return [str(r.get("content", "")) for r in results]

    @staticmethod
    def _is_blocked(results: list[dict]) -> bool:
        return any("BLOCKED by §5" in str(r.get("content", "")) for r in results)

    # ------------------------------------------------------------------
    # Blocked scenarios — tool_executor must NEVER be called
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_write_outside_workspace_blocked(self, make_runtime, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        tool_executor = MagicMock(return_value="ok")

        results = self._run(
            runtime, "Write", {"file_path": "/tmp/evil.txt", "content": "bad"}, tool_executor
        )

        assert self._is_blocked(results), f"Expected BLOCKED, got: {results}"
        tool_executor.assert_not_called()

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_webfetch_non_allowlisted_blocked(self, make_runtime, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        tool_executor = MagicMock(return_value="fetched")

        results = self._run(
            runtime, "WebFetch", {"url": "https://example.com"}, tool_executor
        )

        assert self._is_blocked(results), f"Expected BLOCKED, got: {results}"
        tool_executor.assert_not_called()

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_control_plane_write_blocked(self, make_runtime, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        tool_executor = MagicMock(return_value="ok")
        cp_path = str(tmp_path / ".claude" / "settings.json")

        results = self._run(
            runtime, "Edit", {"file_path": cp_path, "old_string": "x", "new_string": "y"}, tool_executor
        )

        assert self._is_blocked(results), f"Expected BLOCKED, got: {results}"
        tool_executor.assert_not_called()

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_bash_bypass_blocked(self, make_runtime, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        tool_executor = MagicMock(return_value="ok")

        results = self._run(
            runtime,
            "Bash",
            {"command": "python3 -c \"open('/etc/passwd','w').write('x')\""},
            tool_executor,
        )

        assert self._is_blocked(results), f"Expected BLOCKED, got: {results}"
        tool_executor.assert_not_called()

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_websearch_non_allowlisted_blocked(self, make_runtime, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        tool_executor = MagicMock(return_value="results")

        results = self._run(
            runtime, "WebSearch", {"url": "https://evil.com", "query": "test"}, tool_executor
        )

        assert self._is_blocked(results), f"Expected BLOCKED, got: {results}"
        tool_executor.assert_not_called()

    # ------------------------------------------------------------------
    # Allowed scenarios — tool_executor MUST be called; no BLOCKED message
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_write_inside_workspace_allowed(self, make_runtime, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        tool_executor = MagicMock(return_value="written")
        good_path = str(tmp_path / "ok.txt")

        results = self._run(
            runtime, "Write", {"file_path": good_path, "content": "fine"}, tool_executor
        )

        tool_executor.assert_called_once()
        assert not self._is_blocked(results), f"Expected allowed, got BLOCKED: {results}"

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_webfetch_allowlisted_domain_allowed(self, make_runtime, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "allowed.example.com")
        runtime = make_runtime(tmp_path)
        tool_executor = MagicMock(return_value="page content")

        results = self._run(
            runtime, "WebFetch", {"url": "https://allowed.example.com/page"}, tool_executor
        )

        tool_executor.assert_called_once()
        assert not self._is_blocked(results), f"Expected allowed, got BLOCKED: {results}"

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_bash_safe_command_allowed(self, make_runtime, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        tool_executor = MagicMock(return_value="hello")

        results = self._run(
            runtime, "Bash", {"command": "echo hello"}, tool_executor
        )

        tool_executor.assert_called_once()
        assert not self._is_blocked(results), f"Expected allowed, got BLOCKED: {results}"

    @pytest.mark.parametrize("make_runtime", RUNTIME_FACTORIES)
    def test_read_tool_always_allowed(self, make_runtime, tmp_path, monkeypatch):
        """Read is never blocked by enforcement — even outside workspace."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = make_runtime(tmp_path)
        tool_executor = MagicMock(return_value="file contents")

        results = self._run(
            runtime, "Read", {"file_path": "/etc/passwd"}, tool_executor
        )

        tool_executor.assert_called_once()
        assert not self._is_blocked(results), f"Expected allowed, got BLOCKED: {results}"

    # ------------------------------------------------------------------
    # Multiple tool calls in one batch — blocked and allowed can coexist
    # ------------------------------------------------------------------

    def test_claude_mixed_batch(self, tmp_path, monkeypatch):
        """One blocked + one allowed call in the same batch — ClaudeCodeRuntime."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = _make_claude_runtime(tmp_path)
        tool_executor = MagicMock(return_value="ok")

        good_path = str(tmp_path / "file.txt")
        blocks = [
            FakeToolUseBlock(id="c1", name="Write", input={"file_path": "/tmp/evil.txt", "content": "bad"}),
            FakeToolUseBlock(id="c2", name="Write", input={"file_path": good_path, "content": "fine"}),
        ]
        results = runtime.execute_tool(blocks, tool_executor)

        assert len(results) == 2
        assert "BLOCKED by §5" in results[0]["content"]
        assert results[0]["tool_use_id"] == "c1"
        # Only the second (allowed) call reached tool_executor
        tool_executor.assert_called_once()
        assert results[1]["tool_use_id"] == "c2"

    def test_openai_mixed_batch(self, tmp_path, monkeypatch):
        """One blocked + one allowed call in the same batch — OpenAIBaseRuntime."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "")
        runtime = _make_openai_runtime(tmp_path)
        tool_executor = MagicMock(return_value="ok")

        good_path = str(tmp_path / "file.txt")
        tool_calls = [
            FakeToolCall(
                id="c1",
                function=FakeFunction(
                    name="Write",
                    arguments=json.dumps({"file_path": "/tmp/evil.txt", "content": "bad"}),
                ),
            ),
            FakeToolCall(
                id="c2",
                function=FakeFunction(
                    name="Write",
                    arguments=json.dumps({"file_path": good_path, "content": "fine"}),
                ),
            ),
        ]
        results = runtime.execute_tool(tool_calls, tool_executor)

        assert len(results) == 2
        assert "BLOCKED by §5" in results[0]["content"]
        assert results[0]["tool_call_id"] == "c1"
        tool_executor.assert_called_once()
        assert results[1]["tool_call_id"] == "c2"
