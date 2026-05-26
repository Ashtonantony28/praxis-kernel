"""Tests for praxis/integrations/ — all subprocess calls mocked."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from praxis.config import Config
from praxis.integrations import (
    INTEGRATION_SCHEMAS,
    INTEGRATION_IMPLEMENTATIONS,
    get_integration_schemas,
)
from praxis.integrations.github import execute_github, _run_gh
from praxis.integrations.codebase import execute_analyze
from praxis.integrations.testrunner import execute_testrunner
from praxis.integrations.dependencies import execute_dependencies


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        workspace_root=tmp_path,
        memory_root=tmp_path / ".praxis" / "memory",
        hook_path=tmp_path / ".claude" / "hooks" / "escalation-boundary.py",
        allowed_domains=frozenset(),
    )


# ---------- Registry tests ----------


class TestRegistry:
    def test_all_schemas_registered(self):
        assert set(INTEGRATION_SCHEMAS.keys()) == {
            "GitHub", "Analyze", "TestRunner", "Dependencies"
        }

    def test_all_implementations_registered(self):
        assert set(INTEGRATION_IMPLEMENTATIONS.keys()) == {
            "GitHub", "Analyze", "TestRunner", "Dependencies"
        }

    def test_schema_format(self):
        for name, schema in INTEGRATION_SCHEMAS.items():
            assert schema["name"] == name
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"]["type"] == "object"
            assert "action" in schema["input_schema"]["properties"]

    def test_get_integration_schemas_all(self):
        schemas = get_integration_schemas()
        assert len(schemas) == 4

    def test_get_integration_schemas_filtered(self):
        schemas = get_integration_schemas(["GitHub", "TestRunner"])
        assert len(schemas) == 2
        names = {s["name"] for s in schemas}
        assert names == {"GitHub", "TestRunner"}

    def test_get_integration_schemas_unknown_ignored(self):
        schemas = get_integration_schemas(["GitHub", "NoSuchTool"])
        assert len(schemas) == 1


# ---------- GitHub integration ----------


class TestGitHub:
    @patch("shutil.which", return_value=None)
    def test_gh_not_installed(self, mock_which, config):
        result = execute_github({"action": "pr_list"}, config)
        assert "not installed" in result
        assert "cli.github.com" in result

    @patch("shutil.which", return_value="/usr/bin/gh")
    @patch("subprocess.run")
    def test_pr_list(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"number":1,"title":"Fix bug"}]',
            stderr="",
        )
        result = execute_github({"action": "pr_list"}, config)
        assert "Fix bug" in result
        cmd = mock_run.call_args[0][0]
        assert "pr" in cmd and "list" in cmd

    @patch("shutil.which", return_value="/usr/bin/gh")
    @patch("subprocess.run")
    def test_pr_list_with_state_and_limit(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        execute_github({"action": "pr_list", "state": "closed", "limit": 5}, config)
        cmd = mock_run.call_args[0][0]
        assert "--state" in cmd
        idx = cmd.index("--state")
        assert cmd[idx + 1] == "closed"
        assert "--limit" in cmd
        idx = cmd.index("--limit")
        assert cmd[idx + 1] == "5"

    @patch("shutil.which", return_value="/usr/bin/gh")
    @patch("subprocess.run")
    def test_pr_view(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"number":42,"title":"My PR"}', stderr=""
        )
        result = execute_github({"action": "pr_view", "number": 42}, config)
        assert "My PR" in result

    def test_pr_view_missing_number(self, config):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            result = execute_github({"action": "pr_view"}, config)
            assert "required" in result

    @patch("shutil.which", return_value="/usr/bin/gh")
    @patch("subprocess.run")
    def test_issue_list(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        result = execute_github({"action": "issue_list"}, config)
        cmd = mock_run.call_args[0][0]
        assert "issue" in cmd and "list" in cmd

    @patch("shutil.which", return_value="/usr/bin/gh")
    @patch("subprocess.run")
    def test_issue_view(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"number":7}', stderr=""
        )
        result = execute_github({"action": "issue_view", "number": 7}, config)
        assert "7" in result

    def test_issue_view_missing_number(self, config):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            result = execute_github({"action": "issue_view"}, config)
            assert "required" in result

    @patch("shutil.which", return_value="/usr/bin/gh")
    @patch("subprocess.run")
    def test_pr_diff(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="diff --git a/f.py b/f.py", stderr=""
        )
        result = execute_github({"action": "pr_diff", "number": 1}, config)
        assert "diff" in result

    def test_pr_diff_missing_number(self, config):
        with patch("shutil.which", return_value="/usr/bin/gh"):
            result = execute_github({"action": "pr_diff"}, config)
            assert "required" in result

    def test_unknown_action(self, config):
        result = execute_github({"action": "bogus"}, config)
        assert "unknown" in result

    @patch("shutil.which", return_value="/usr/bin/gh")
    @patch("subprocess.run")
    def test_auth_failure(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="To get started with GitHub CLI, please run: gh auth login",
        )
        result = execute_github({"action": "pr_list"}, config)
        assert "not authenticated" in result

    @patch("shutil.which", return_value="/usr/bin/gh")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 30))
    def test_timeout(self, mock_run, mock_which, config):
        result = execute_github({"action": "pr_list"}, config)
        assert "timed out" in result

    @patch("shutil.which", return_value="/usr/bin/gh")
    @patch("subprocess.run")
    def test_generic_error(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="repository not found"
        )
        result = execute_github({"action": "pr_list"}, config)
        assert "repository not found" in result


# ---------- Codebase analysis ----------


class TestAnalyze:
    @patch("shutil.which", return_value=None)
    def test_coverage_not_installed(self, mock_which, config):
        result = execute_analyze({"action": "coverage"}, config)
        assert "not installed" in result
        assert "coverage" in result

    @patch("shutil.which", return_value="/usr/bin/coverage")
    @patch("subprocess.run")
    def test_coverage_success(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Name    Stmts   Miss  Cover\nfoo.py  10      2     80%",
            stderr="",
        )
        result = execute_analyze({"action": "coverage"}, config)
        assert "80%" in result

    @patch("shutil.which", return_value="/usr/bin/coverage")
    @patch("subprocess.run")
    def test_coverage_no_data(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="No data to report"
        )
        result = execute_analyze({"action": "coverage"}, config)
        assert "no coverage data" in result

    @patch("shutil.which", return_value=None)
    def test_complexity_not_installed(self, mock_which, config):
        result = execute_analyze({"action": "complexity"}, config)
        assert "radon" in result
        assert "not installed" in result

    @patch("shutil.which", return_value="/usr/bin/radon")
    @patch("subprocess.run")
    def test_complexity_success(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="foo.py\n    F 1:0 my_func - A (2)",
            stderr="",
        )
        result = execute_analyze({"action": "complexity"}, config)
        assert "my_func" in result

    @patch("shutil.which", return_value=None)
    def test_lint_not_installed(self, mock_which, config):
        result = execute_analyze({"action": "lint"}, config)
        assert "pylint" in result
        assert "not installed" in result

    @patch("shutil.which", return_value="/usr/bin/pylint")
    @patch("subprocess.run")
    def test_lint_success(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=4,  # pylint uses non-zero for warnings
            stdout="foo.py:1: W0611: Unused import os",
            stderr="",
        )
        result = execute_analyze({"action": "lint"}, config)
        assert "Unused import" in result

    @patch("shutil.which", return_value="/usr/bin/pylint")
    @patch("subprocess.run")
    def test_lint_clean(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = execute_analyze({"action": "lint"}, config)
        assert "no issues found" in result

    def test_unknown_action(self, config):
        result = execute_analyze({"action": "bogus"}, config)
        assert "unknown" in result

    @patch("shutil.which", return_value="/usr/bin/coverage")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("coverage", 60))
    def test_coverage_timeout(self, mock_run, mock_which, config):
        result = execute_analyze({"action": "coverage"}, config)
        assert "timed out" in result

    @patch("shutil.which", return_value="/usr/bin/radon")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("radon", 60))
    def test_complexity_timeout(self, mock_run, mock_which, config):
        result = execute_analyze({"action": "complexity"}, config)
        assert "timed out" in result

    @patch("shutil.which", return_value="/usr/bin/pylint")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pylint", 120))
    def test_lint_timeout(self, mock_run, mock_which, config):
        result = execute_analyze({"action": "lint"}, config)
        assert "timed out" in result

    @patch("shutil.which", return_value="/usr/bin/radon")
    @patch("subprocess.run")
    def test_complexity_with_path(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=0, stdout="(no output)", stderr="")
        execute_analyze({"action": "complexity", "path": "praxis/"}, config)
        cmd = mock_run.call_args[0][0]
        assert "praxis/" in cmd


# ---------- Test runner ----------


class TestTestRunner:
    @patch("shutil.which", return_value=None)
    def test_pytest_not_installed(self, mock_which, config):
        result = execute_testrunner({"action": "run"}, config)
        assert "not installed" in result
        assert "pytest" in result

    @patch("shutil.which", return_value="/usr/bin/pytest")
    @patch("subprocess.run")
    def test_run_default(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="5 passed in 0.5s",
            stderr="",
        )
        result = execute_testrunner({"action": "run"}, config)
        assert "5 passed" in result
        cmd = mock_run.call_args[0][0]
        assert "tests/" in cmd

    @patch("shutil.which", return_value="/usr/bin/pytest")
    @patch("subprocess.run")
    def test_run_custom_path(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        execute_testrunner({"action": "run", "path": "tests/test_foo.py"}, config)
        cmd = mock_run.call_args[0][0]
        assert "tests/test_foo.py" in cmd

    @patch("shutil.which", return_value="/usr/bin/pytest")
    @patch("subprocess.run")
    def test_run_with_marker(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        execute_testrunner({"action": "run", "marker": "not slow"}, config)
        cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "not slow"

    @patch("shutil.which", return_value="/usr/bin/pytest")
    @patch("subprocess.run")
    def test_run_with_keyword(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        execute_testrunner({"action": "run", "keyword": "test_foo"}, config)
        cmd = mock_run.call_args[0][0]
        assert "-k" in cmd
        idx = cmd.index("-k")
        assert cmd[idx + 1] == "test_foo"

    @patch("shutil.which", return_value="/usr/bin/pytest")
    @patch("subprocess.run")
    def test_run_failed(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="1 passed", stderr=""
        )
        result = execute_testrunner({"action": "run_failed"}, config)
        cmd = mock_run.call_args[0][0]
        assert "--lf" in cmd

    @patch("shutil.which", return_value="/usr/bin/pytest")
    @patch("subprocess.run")
    def test_test_failures(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="FAILED test_foo.py::test_bar",
            stderr="",
        )
        result = execute_testrunner({"action": "run"}, config)
        assert "FAILED" in result
        assert "Exit code: 1" in result

    def test_unknown_action(self, config):
        with patch("shutil.which", return_value="/usr/bin/pytest"):
            result = execute_testrunner({"action": "bogus"}, config)
            assert "unknown" in result

    @patch("shutil.which", return_value="/usr/bin/pytest")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 300))
    def test_timeout(self, mock_run, mock_which, config):
        result = execute_testrunner({"action": "run"}, config)
        assert "timed out" in result


# ---------- Dependencies ----------


class TestDependencies:
    @patch("shutil.which", return_value="/usr/bin/pip")
    @patch("subprocess.run")
    def test_outdated(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"name":"requests","version":"2.28.0","latest_version":"2.31.0"}]',
            stderr="",
        )
        result = execute_dependencies({"action": "outdated"}, config)
        assert "requests" in result
        cmd = mock_run.call_args[0][0]
        assert "--outdated" in cmd
        assert "--format=json" in cmd

    @patch("shutil.which", return_value=None)
    def test_pip_not_found(self, mock_which, config):
        result = execute_dependencies({"action": "outdated"}, config)
        assert "pip not found" in result

    @patch("shutil.which", return_value="/usr/bin/pip")
    @patch("subprocess.run")
    def test_outdated_error(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="something broke"
        )
        result = execute_dependencies({"action": "outdated"}, config)
        assert "something broke" in result

    @patch("shutil.which", return_value=None)
    def test_audit_not_installed(self, mock_which, config):
        result = execute_dependencies({"action": "audit"}, config)
        assert "pip-audit" in result
        assert "not installed" in result

    @patch("shutil.which", return_value="/usr/bin/pip-audit")
    @patch("subprocess.run")
    def test_audit_clean(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"dependencies":[],"vulnerabilities":[]}',
            stderr="",
        )
        result = execute_dependencies({"action": "audit"}, config)
        assert "vulnerabilities" in result

    @patch("shutil.which", return_value="/usr/bin/pip-audit")
    @patch("subprocess.run")
    def test_audit_with_vulns(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=1,  # non-zero = vulns found
            stdout='{"vulnerabilities":[{"name":"flask","id":"CVE-2023-1234"}]}',
            stderr="",
        )
        result = execute_dependencies({"action": "audit"}, config)
        assert "CVE-2023-1234" in result

    def test_unknown_action(self, config):
        result = execute_dependencies({"action": "bogus"}, config)
        assert "unknown" in result

    @patch("shutil.which", return_value="/usr/bin/pip")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pip", 60))
    def test_outdated_timeout(self, mock_run, mock_which, config):
        result = execute_dependencies({"action": "outdated"}, config)
        assert "timed out" in result

    @patch("shutil.which", return_value="/usr/bin/pip-audit")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pip-audit", 120))
    def test_audit_timeout(self, mock_run, mock_which, config):
        result = execute_dependencies({"action": "audit"}, config)
        assert "timed out" in result


# ---------- Orchestrator integration ----------


class TestOrchestratorIntegration:
    """Verify integration tools are wired into the orchestrator dispatch."""

    def test_orchestrator_dispatches_integration_tool(self, workspace):
        """Integration tools are reachable via _execute_with_hook."""
        from praxis.orchestrator import Orchestrator
        from tests.conftest import FakeClient, FakeResponse, FakeTextBlock
        from praxis.runtime.claude_code import ClaudeCodeRuntime

        ws_config = Config(
            workspace_root=workspace,
            memory_root=workspace / ".praxis" / "memory",
            hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )
        client = FakeClient([FakeResponse(content=[FakeTextBlock("ok")])])
        runtime = ClaudeCodeRuntime(client)
        orch = Orchestrator(runtime, ws_config)

        with patch("shutil.which", return_value=None):
            result = orch._execute_with_hook("GitHub", {"action": "pr_list"})
        assert "not installed" in result  # proves dispatch reached github.py

    def test_unknown_tool_still_errors(self, workspace):
        from praxis.orchestrator import Orchestrator
        from tests.conftest import FakeClient, FakeResponse, FakeTextBlock
        from praxis.runtime.claude_code import ClaudeCodeRuntime

        ws_config = Config(
            workspace_root=workspace,
            memory_root=workspace / ".praxis" / "memory",
            hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )
        client = FakeClient([FakeResponse(content=[FakeTextBlock("ok")])])
        runtime = ClaudeCodeRuntime(client)
        orch = Orchestrator(runtime, ws_config)

        result = orch._execute_with_hook("NoSuchTool", {})
        assert "unknown tool" in result


# ---------- Secret redaction ----------


class TestSecretRedaction:
    def test_github_token_redacted(self, config, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
        with patch("shutil.which", return_value="/usr/bin/gh"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="token is ghp_secret123 here",
                    stderr="",
                )
                result = execute_github({"action": "pr_list"}, config)
                assert "ghp_secret123" not in result
                assert "[REDACTED]" in result
