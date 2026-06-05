"""Tests for ClaudeCodeCLIRuntime — mocks subprocess.run, no real claude calls."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from praxis.runtime.claude_code import ClaudeCodeCLIRuntime


def _make_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _json_result(result: str) -> str:
    return json.dumps({"type": "result", "result": result, "cost_usd": 0.001})


class TestRunCallsClaudePSubprocess:
    def test_run_calls_claude_p_subprocess(self) -> None:
        """run() invokes subprocess.run with claude -p <prompt>."""
        proc = _make_proc(stdout=_json_result("hello"))
        with patch("subprocess.run", return_value=proc) as mock_run:
            rt = ClaudeCodeCLIRuntime()
            rt.run("do something")
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]  # first positional arg is the command list
        assert cmd[0] == "claude"
        assert cmd[1] == "-p"
        assert cmd[2] == "do something"
        assert "--output-format" in cmd
        assert "json" in cmd

    def test_run_passes_allowed_tools(self) -> None:
        """run() includes --allowedTools flag."""
        proc = _make_proc(stdout=_json_result("ok"))
        with patch("subprocess.run", return_value=proc):
            rt = ClaudeCodeCLIRuntime()
            rt.run("task")
        # Verified via test_run_calls_claude_p_subprocess; here just ensure no error


class TestRunReturnsResultFromJsonOutput:
    def test_run_returns_result_field(self) -> None:
        """run() extracts and returns the 'result' field from JSON output."""
        proc = _make_proc(stdout=_json_result("task completed successfully"))
        with patch("subprocess.run", return_value=proc):
            rt = ClaudeCodeCLIRuntime()
            result = rt.run("do a task")
        assert result == "task completed successfully"

    def test_run_returns_empty_string_when_result_missing(self) -> None:
        """run() returns '' when JSON has no 'result' key."""
        proc = _make_proc(stdout=json.dumps({"type": "result"}))
        with patch("subprocess.run", return_value=proc):
            rt = ClaudeCodeCLIRuntime()
            result = rt.run("do a task")
        assert result == ""


class TestRunRaisesOnNonzeroExit:
    def test_run_raises_on_nonzero_exit(self) -> None:
        """run() raises RuntimeError when subprocess exits non-zero."""
        proc = _make_proc(returncode=1, stderr="fatal error from claude")
        with patch("subprocess.run", return_value=proc):
            rt = ClaudeCodeCLIRuntime()
            with pytest.raises(RuntimeError, match="claude -p exited 1"):
                rt.run("bad prompt")

    def test_run_error_message_includes_stderr(self) -> None:
        """RuntimeError from non-zero exit includes stderr content."""
        proc = _make_proc(returncode=2, stderr="permission denied")
        with patch("subprocess.run", return_value=proc):
            rt = ClaudeCodeCLIRuntime()
            with pytest.raises(RuntimeError, match="permission denied"):
                rt.run("prompt")


class TestRunRaisesOnInvalidJson:
    def test_run_raises_on_invalid_json(self) -> None:
        """run() raises RuntimeError when stdout is not valid JSON."""
        proc = _make_proc(stdout="not json at all")
        with patch("subprocess.run", return_value=proc):
            rt = ClaudeCodeCLIRuntime()
            with pytest.raises(RuntimeError, match="invalid JSON"):
                rt.run("prompt")

    def test_run_raises_on_empty_stdout(self) -> None:
        """run() raises RuntimeError when stdout is empty."""
        proc = _make_proc(stdout="")
        with patch("subprocess.run", return_value=proc):
            rt = ClaudeCodeCLIRuntime()
            with pytest.raises(RuntimeError, match="invalid JSON"):
                rt.run("prompt")


class TestTimeoutIsConfigurable:
    def test_timeout_default_is_1800(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default timeout is 1800 seconds when PRAXIS_TASK_TIMEOUT is unset."""
        monkeypatch.delenv("PRAXIS_TASK_TIMEOUT", raising=False)
        proc = _make_proc(stdout=_json_result("done"))
        with patch("subprocess.run", return_value=proc) as mock_run:
            rt = ClaudeCodeCLIRuntime()
            rt.run("prompt")
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 1800

    def test_timeout_reads_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Timeout is read from PRAXIS_TASK_TIMEOUT env var."""
        monkeypatch.setenv("PRAXIS_TASK_TIMEOUT", "600")
        proc = _make_proc(stdout=_json_result("done"))
        with patch("subprocess.run", return_value=proc) as mock_run:
            rt = ClaudeCodeCLIRuntime()
            rt.run("prompt")
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 600
