"""Tests for praxis/integrations/ — all subprocess calls mocked."""

from __future__ import annotations

import json
import subprocess
import urllib.error
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
from praxis.integrations.web import (
    execute_web_research,
    _strip_html,
    _extract_domain,
    _check_domain,
    BRAVE_API_DOMAIN,
)
from praxis.integrations.files import (
    execute_filemanager,
    _resolve_path,
    _human_size,
)
from praxis.integrations.email import (
    execute_email,
    _parse_headers,
    _extract_body,
)
from praxis.integrations.calendar import (
    execute_calendar,
    _parse_ical,
    _parse_ical_datetime,
)


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
            "GitHub", "Analyze", "TestRunner", "Dependencies", "WebResearch",
            "FileManager", "Email", "Calendar", "Wiki", "Slack",
            "playwright", "notion", "linear",
        }

    def test_all_implementations_registered(self):
        assert set(INTEGRATION_IMPLEMENTATIONS.keys()) == {
            "GitHub", "Analyze", "TestRunner", "Dependencies", "WebResearch",
            "FileManager", "Email", "Calendar", "Wiki", "Slack",
            "playwright", "notion", "linear",
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
        assert len(schemas) == 13

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


# ---------- Web research ----------


class TestWebResearchHelpers:
    def test_strip_html_basic(self):
        html = "<h1>Title</h1><p>Hello <b>world</b></p>"
        text = _strip_html(html)
        assert "Title" in text
        assert "Hello world" in text
        assert "<" not in text

    def test_strip_html_scripts_removed(self):
        html = "<p>Before</p><script>alert('xss')</script><p>After</p>"
        text = _strip_html(html)
        assert "Before" in text
        assert "After" in text
        assert "alert" not in text

    def test_strip_html_style_removed(self):
        html = "<style>.foo{color:red}</style><p>Content</p>"
        text = _strip_html(html)
        assert "Content" in text
        assert "color" not in text

    def test_extract_domain(self):
        assert _extract_domain("https://example.com/path") == "example.com"
        assert _extract_domain("http://sub.example.com:8080/p") == "sub.example.com"
        assert _extract_domain("not-a-url") == ""

    def test_check_domain_allowed(self, config):
        cfg = Config(
            workspace_root=config.workspace_root,
            memory_root=config.memory_root,
            hook_path=config.hook_path,
            allowed_domains=frozenset({"example.com"}),
        )
        assert _check_domain("example.com", cfg) is None

    def test_check_domain_blocked(self, config):
        result = _check_domain("evil.com", config)
        assert result is not None
        assert "not in PRAXIS_ALLOWED_DOMAINS" in result

    def test_check_domain_empty(self, config):
        result = _check_domain("", config)
        assert "could not extract domain" in result


class TestWebResearchSearch:
    def _config_with_domains(self, config, domains):
        return Config(
            workspace_root=config.workspace_root,
            memory_root=config.memory_root,
            hook_path=config.hook_path,
            allowed_domains=frozenset(domains),
        )

    def test_missing_api_key(self, config, monkeypatch):
        monkeypatch.delenv("PRAXIS_WEB_SEARCH_API_KEY", raising=False)
        result = execute_web_research(
            {"action": "search", "query": "test"}, config
        )
        assert "PRAXIS_WEB_SEARCH_API_KEY not set" in result
        assert "brave.com" in result

    def test_search_domain_not_allowed(self, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_WEB_SEARCH_API_KEY", "test-key")
        result = execute_web_research(
            {"action": "search", "query": "test"}, config
        )
        assert "not in PRAXIS_ALLOWED_DOMAINS" in result

    def test_search_missing_query(self, config):
        result = execute_web_research({"action": "search"}, config)
        assert "'query' is required" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_search_success(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_WEB_SEARCH_API_KEY", "test-key")
        cfg = self._config_with_domains(config, {BRAVE_API_DOMAIN})

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "web": {
                "results": [
                    {"title": "Python Docs", "url": "https://python.org", "description": "Official docs"},
                    {"title": "PyPI", "url": "https://pypi.org", "description": "Package index"},
                ]
            }
        }).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = execute_web_research(
            {"action": "search", "query": "python", "n": 2}, cfg
        )
        assert "Python Docs" in result
        assert "python.org" in result
        assert "PyPI" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_search_no_results(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_WEB_SEARCH_API_KEY", "test-key")
        cfg = self._config_with_domains(config, {BRAVE_API_DOMAIN})

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"web": {"results": []}}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = execute_web_research(
            {"action": "search", "query": "xyznonexistent"}, cfg
        )
        assert "No results found" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_search_http_error(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_WEB_SEARCH_API_KEY", "test-key")
        cfg = self._config_with_domains(config, {BRAVE_API_DOMAIN})

        err = urllib.error.HTTPError(
            "https://api.search.brave.com/res/v1/web/search",
            401, "Unauthorized", {}, None
        )
        mock_urlopen.side_effect = err

        result = execute_web_research(
            {"action": "search", "query": "test"}, cfg
        )
        assert "401" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_search_url_error(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_WEB_SEARCH_API_KEY", "test-key")
        cfg = self._config_with_domains(config, {BRAVE_API_DOMAIN})

        mock_urlopen.side_effect = urllib.error.URLError("DNS lookup failed")

        result = execute_web_research(
            {"action": "search", "query": "test"}, cfg
        )
        assert "could not reach" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_search_timeout(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_WEB_SEARCH_API_KEY", "test-key")
        cfg = self._config_with_domains(config, {BRAVE_API_DOMAIN})

        mock_urlopen.side_effect = TimeoutError()

        result = execute_web_research(
            {"action": "search", "query": "test"}, cfg
        )
        assert "timed out" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_search_n_clamped(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_WEB_SEARCH_API_KEY", "test-key")
        cfg = self._config_with_domains(config, {BRAVE_API_DOMAIN})

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"web": {"results": []}}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        # n=100 should be clamped to 20
        execute_web_research(
            {"action": "search", "query": "test", "n": 100}, cfg
        )
        call_args = mock_urlopen.call_args[0][0]
        assert "count=20" in call_args.full_url


class TestWebResearchFetch:
    def _config_with_domains(self, config, domains):
        return Config(
            workspace_root=config.workspace_root,
            memory_root=config.memory_root,
            hook_path=config.hook_path,
            allowed_domains=frozenset(domains),
        )

    def test_fetch_domain_not_allowed(self, config):
        result = execute_web_research(
            {"action": "fetch", "url": "https://evil.com/page"}, config
        )
        assert "not in PRAXIS_ALLOWED_DOMAINS" in result

    def test_fetch_missing_url(self, config):
        result = execute_web_research({"action": "fetch"}, config)
        assert "'url' is required" in result

    def test_fetch_bad_scheme(self, config):
        cfg = self._config_with_domains(config, {"example.com"})
        result = execute_web_research(
            {"action": "fetch", "url": "ftp://example.com/file"}, cfg
        )
        assert "http://" in result or "https://" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_fetch_html_success(self, mock_urlopen, config):
        cfg = self._config_with_domains(config, {"docs.python.org"})

        mock_resp = MagicMock()
        mock_resp.read.return_value = (
            b"<html><body><h1>Python</h1><p>Hello world</p></body></html>"
        )
        mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = execute_web_research(
            {"action": "fetch", "url": "https://docs.python.org/3/"}, cfg
        )
        assert "Fetched" in result
        assert "Python" in result
        assert "Hello world" in result
        assert "<html>" not in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_fetch_plain_text(self, mock_urlopen, config):
        cfg = self._config_with_domains(config, {"example.com"})

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"Just plain text content"
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = execute_web_research(
            {"action": "fetch", "url": "https://example.com/file.txt"}, cfg
        )
        assert "Just plain text content" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_fetch_json_content(self, mock_urlopen, config):
        cfg = self._config_with_domains(config, {"api.example.com"})

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"key": "value"}'
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = execute_web_research(
            {"action": "fetch", "url": "https://api.example.com/data"}, cfg
        )
        assert '"key": "value"' in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_fetch_non_text_rejected(self, mock_urlopen, config):
        cfg = self._config_with_domains(config, {"example.com"})

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "image/png"}
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = execute_web_research(
            {"action": "fetch", "url": "https://example.com/img.png"}, cfg
        )
        assert "non-text content type" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_fetch_truncated(self, mock_urlopen, config):
        cfg = self._config_with_domains(config, {"example.com"})

        mock_resp = MagicMock()
        mock_resp.read.return_value = ("x" * 10000).encode()
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = execute_web_research(
            {"action": "fetch", "url": "https://example.com/big", "max_chars": 100},
            cfg,
        )
        # Content should be truncated
        fetched_line = result.split("\n")[0]
        assert "100 chars" in fetched_line

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_fetch_http_error(self, mock_urlopen, config):
        cfg = self._config_with_domains(config, {"example.com"})
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", {}, None
        )
        result = execute_web_research(
            {"action": "fetch", "url": "https://example.com/missing"}, cfg
        )
        assert "404" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_fetch_url_error(self, mock_urlopen, config):
        cfg = self._config_with_domains(config, {"example.com"})
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        result = execute_web_research(
            {"action": "fetch", "url": "https://example.com/"}, cfg
        )
        assert "could not fetch" in result

    @patch("praxis.integrations.web.urllib.request.urlopen")
    def test_fetch_timeout(self, mock_urlopen, config):
        cfg = self._config_with_domains(config, {"example.com"})
        mock_urlopen.side_effect = TimeoutError()
        result = execute_web_research(
            {"action": "fetch", "url": "https://example.com/"}, cfg
        )
        assert "timed out" in result


class TestWebResearchDispatch:
    def test_unknown_action(self, config):
        result = execute_web_research({"action": "bogus"}, config)
        assert "unknown" in result


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

    def test_web_search_key_redacted(self, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_WEB_SEARCH_API_KEY", "BSAsecretkey123")
        cfg = Config(
            workspace_root=config.workspace_root,
            memory_root=config.memory_root,
            hook_path=config.hook_path,
            allowed_domains=frozenset({"example.com"}),
        )
        with patch("praxis.integrations.web.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"The key BSAsecretkey123 leaked"
            mock_resp.headers = {"Content-Type": "text/plain"}
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = execute_web_research(
                {"action": "fetch", "url": "https://example.com/page"}, cfg
            )
            assert "BSAsecretkey123" not in result
            assert "[REDACTED]" in result


# ---------- File manager — path validation ----------


class TestFileManagerPaths:
    def test_resolve_path_none_returns_root(self, config):
        path, err = _resolve_path(None, config)
        assert err is None
        assert path == config.workspace_root

    def test_resolve_path_relative(self, config):
        path, err = _resolve_path("subdir/file.py", config)
        assert err is None
        assert str(path).startswith(str(config.workspace_root))

    def test_resolve_path_escapes_boundary(self, config):
        path, err = _resolve_path("../../etc/passwd", config)
        assert err is not None
        assert "escapes workspace boundary" in err

    def test_resolve_path_absolute_escape(self, config):
        # Even absolute paths that leave workspace are caught
        path, err = _resolve_path("/etc/passwd", config)
        assert err is not None
        assert "escapes workspace boundary" in err


class TestFileManagerHumanSize:
    def test_bytes(self):
        assert _human_size(42) == "42 B"

    def test_zero(self):
        assert _human_size(0) == "0 B"

    def test_kilobytes(self):
        result = _human_size(2048)
        assert "KB" in result

    def test_megabytes(self):
        result = _human_size(5 * 1024 * 1024)
        assert "MB" in result

    def test_gigabytes(self):
        result = _human_size(3 * 1024 * 1024 * 1024)
        assert "GB" in result


# ---------- File manager — search ----------


class TestFileManagerSearch:
    def test_search_missing_query(self, config):
        result = execute_filemanager({"action": "search"}, config)
        assert "'query' is required" in result

    def test_search_path_escape(self, config):
        result = execute_filemanager(
            {"action": "search", "query": "test", "path": "../../etc"}, config
        )
        assert "escapes workspace boundary" in result

    @patch("shutil.which", return_value=None)
    def test_search_grep_not_found(self, mock_which, config):
        result = execute_filemanager(
            {"action": "search", "query": "test"}, config
        )
        assert "grep not found" in result

    @patch("shutil.which", return_value="/usr/bin/grep")
    @patch("subprocess.run")
    def test_search_success(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="foo.py:1:def test_func():\nfoo.py:5:# test comment",
            stderr="",
        )
        result = execute_filemanager(
            {"action": "search", "query": "test"}, config
        )
        assert "test_func" in result
        assert "test comment" in result

    @patch("shutil.which", return_value="/usr/bin/grep")
    @patch("subprocess.run")
    def test_search_no_matches(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        result = execute_filemanager(
            {"action": "search", "query": "xyznonexistent"}, config
        )
        assert "No matches found" in result

    @patch("shutil.which", return_value="/usr/bin/grep")
    @patch("subprocess.run")
    def test_search_with_glob(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        execute_filemanager(
            {"action": "search", "query": "test", "glob": "*.py"}, config
        )
        cmd = mock_run.call_args[0][0]
        assert "--include" in cmd
        idx = cmd.index("--include")
        assert cmd[idx + 1] == "*.py"

    @patch("shutil.which", return_value="/usr/bin/grep")
    @patch("subprocess.run")
    def test_search_with_path(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        execute_filemanager(
            {"action": "search", "query": "test", "path": "praxis/"}, config
        )
        cmd = mock_run.call_args[0][0]
        assert str(config.workspace_root / "praxis") in cmd[-1]

    @patch("shutil.which", return_value="/usr/bin/grep")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("grep", 30))
    def test_search_timeout(self, mock_run, mock_which, config):
        result = execute_filemanager(
            {"action": "search", "query": "test"}, config
        )
        assert "timed out" in result

    @patch("shutil.which", return_value="/usr/bin/grep")
    @patch("subprocess.run")
    def test_search_truncates_long_output(self, mock_run, mock_which, config):
        long_output = "\n".join(f"file.py:{i}:match" for i in range(200))
        mock_run.return_value = MagicMock(returncode=0, stdout=long_output, stderr="")
        result = execute_filemanager(
            {"action": "search", "query": "match"}, config
        )
        assert "truncated" in result

    @patch("shutil.which", return_value="/usr/bin/grep")
    @patch("subprocess.run")
    def test_search_grep_error(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="bad pattern")
        result = execute_filemanager(
            {"action": "search", "query": "[invalid"}, config
        )
        assert "grep failed" in result


# ---------- File manager — summarize ----------


class TestFileManagerSummarize:
    def test_summarize_file(self, config):
        f = config.workspace_root / "hello.py"
        f.write_text("print('hello')\nprint('world')\n")
        result = execute_filemanager(
            {"action": "summarize", "path": "hello.py"}, config
        )
        assert "hello.py" in result
        assert "Lines: 2" in result
        assert ".py" in result
        assert "print('hello')" in result

    def test_summarize_directory(self, config):
        d = config.workspace_root / "mydir"
        d.mkdir()
        (d / "a.txt").write_text("aaa\n")
        (d / "b.txt").write_text("bbb\n")
        result = execute_filemanager(
            {"action": "summarize", "path": "mydir"}, config
        )
        assert "mydir" in result
        assert "Files: 2" in result

    def test_summarize_nonexistent(self, config):
        result = execute_filemanager(
            {"action": "summarize", "path": "nope.txt"}, config
        )
        assert "does not exist" in result

    def test_summarize_path_escape(self, config):
        result = execute_filemanager(
            {"action": "summarize", "path": "../../etc"}, config
        )
        assert "escapes workspace boundary" in result

    def test_summarize_workspace_root(self, config):
        (config.workspace_root / "f.txt").write_text("x\n")
        result = execute_filemanager({"action": "summarize"}, config)
        assert "Files:" in result

    def test_summarize_binary_file(self, config):
        f = config.workspace_root / "data.bin"
        f.write_bytes(b"\x00\x01\x02\xff" * 100)
        result = execute_filemanager(
            {"action": "summarize", "path": "data.bin"}, config
        )
        assert "data.bin" in result
        assert "Size:" in result

    def test_summarize_large_file_preview_truncated(self, config):
        f = config.workspace_root / "big.py"
        f.write_text("\n".join(f"line {i}" for i in range(100)) + "\n")
        result = execute_filemanager(
            {"action": "summarize", "path": "big.py"}, config
        )
        assert "more lines" in result


# ---------- File manager — git_status ----------


class TestFileManagerGitStatus:
    @patch("shutil.which", return_value=None)
    def test_git_not_installed(self, mock_which, config):
        result = execute_filemanager({"action": "git_status"}, config)
        assert "git not installed" in result

    @patch("shutil.which", return_value="/usr/bin/git")
    @patch("subprocess.run")
    def test_not_a_git_repo(self, mock_run, mock_which, config):
        mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="")
        result = execute_filemanager({"action": "git_status"}, config)
        assert "not a git repository" in result

    @patch("shutil.which", return_value="/usr/bin/git")
    @patch("subprocess.run")
    def test_git_status_success(self, mock_run, mock_which, config):
        def side_effect(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="true", stderr="")
            elif "branch" in cmd:
                return MagicMock(returncode=0, stdout="main\n", stderr="")
            elif "status" in cmd:
                return MagicMock(returncode=0, stdout=" M foo.py\n?? bar.py\n", stderr="")
            elif "log" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="abc1234 Fix bug\ndef5678 Add feature\n",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = execute_filemanager({"action": "git_status"}, config)
        assert "Branch: main" in result
        assert "foo.py" in result
        assert "bar.py" in result
        assert "Fix bug" in result
        assert "2 files" in result

    @patch("shutil.which", return_value="/usr/bin/git")
    @patch("subprocess.run")
    def test_git_status_clean(self, mock_run, mock_which, config):
        def side_effect(cmd, **kwargs):
            if "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="true", stderr="")
            elif "branch" in cmd:
                return MagicMock(returncode=0, stdout="main\n", stderr="")
            elif "status" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            elif "log" in cmd:
                return MagicMock(returncode=0, stdout="abc1234 Init\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = execute_filemanager({"action": "git_status"}, config)
        assert "Working tree clean" in result

    @patch("shutil.which", return_value="/usr/bin/git")
    @patch("subprocess.run")
    def test_git_status_timeout(self, mock_run, mock_which, config):
        mock_run.side_effect = subprocess.TimeoutExpired("git", 10)
        result = execute_filemanager({"action": "git_status"}, config)
        assert "timed out" in result


# ---------- File manager — disk_usage ----------


class TestFileManagerDiskUsage:
    @patch("subprocess.run")
    def test_disk_usage_success(self, mock_run, config):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="4.5M\t/workspace",
            stderr="",
        )
        result = execute_filemanager({"action": "disk_usage"}, config)
        assert "4.5M" in result

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("du", 30))
    def test_disk_usage_timeout(self, mock_run, config):
        result = execute_filemanager({"action": "disk_usage"}, config)
        assert "timed out" in result

    @patch("subprocess.run")
    def test_disk_usage_error(self, mock_run, config):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="permission denied"
        )
        result = execute_filemanager({"action": "disk_usage"}, config)
        assert "permission denied" in result

    def test_disk_usage_path_escape(self, config):
        result = execute_filemanager(
            {"action": "disk_usage", "path": "../../etc"}, config
        )
        assert "escapes workspace boundary" in result


# ---------- File manager — dispatch ----------


class TestFileManagerDispatch:
    def test_unknown_action(self, config):
        result = execute_filemanager({"action": "bogus"}, config)
        assert "unknown" in result


# ========== Email integration ==========


# ---------- Email — helpers ----------


class TestEmailParseHeaders:
    def test_basic_headers(self):
        raw = (
            b"From: alice@example.com\r\n"
            b"To: bob@example.com\r\n"
            b"Subject: Hello\r\n"
            b"Date: Mon, 25 May 2026 10:00:00 +0000\r\n"
            b"\r\n"
            b"Body text\r\n"
        )
        hdrs = _parse_headers(raw)
        assert hdrs["from"] == "alice@example.com"
        assert hdrs["to"] == "bob@example.com"
        assert hdrs["subject"] == "Hello"
        assert "2026" in hdrs["date"]

    def test_missing_headers(self):
        raw = b"\r\nJust a body\r\n"
        hdrs = _parse_headers(raw)
        assert hdrs["from"] == "(unknown)"
        assert hdrs["subject"] == "(no subject)"

    def test_malformed_date(self):
        raw = b"Date: not-a-date\r\n\r\n"
        hdrs = _parse_headers(raw)
        # Should not crash — falls back to raw string
        assert isinstance(hdrs["date"], str)


class TestEmailExtractBody:
    def test_plain_text(self):
        raw = (
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Hello world\r\n"
        )
        body = _extract_body(raw)
        assert "Hello world" in body

    def test_multipart_prefers_plain(self):
        raw = (
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/alternative; boundary=boundary\r\n"
            b"\r\n"
            b"--boundary\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Plain text body\r\n"
            b"--boundary\r\n"
            b"Content-Type: text/html\r\n"
            b"\r\n"
            b"<p>HTML body</p>\r\n"
            b"--boundary--\r\n"
        )
        body = _extract_body(raw)
        assert "Plain text body" in body
        assert "<p>" not in body

    def test_html_only_multipart(self):
        raw = (
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/alternative; boundary=b\r\n"
            b"\r\n"
            b"--b\r\n"
            b"Content-Type: text/html\r\n"
            b"\r\n"
            b"<p>Only HTML</p>\r\n"
            b"--b--\r\n"
        )
        body = _extract_body(raw)
        assert "HTML content" in body

    def test_empty_message(self):
        raw = b"Content-Type: text/plain\r\n\r\n"
        body = _extract_body(raw)
        assert "Empty message" in body


# ---------- Email — config errors ----------


class TestEmailConfig:
    def test_missing_host(self, config, monkeypatch):
        monkeypatch.delenv("PRAXIS_EMAIL_IMAP_HOST", raising=False)
        result = execute_email({"action": "list_emails"}, config)
        assert "PRAXIS_EMAIL_IMAP_HOST not set" in result
        assert "imap.gmail.com" in result

    def test_missing_user(self, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.delenv("PRAXIS_EMAIL_USER", raising=False)
        result = execute_email({"action": "list_emails"}, config)
        assert "PRAXIS_EMAIL_USER not set" in result

    def test_missing_password(self, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.delenv("PRAXIS_EMAIL_PASSWORD", raising=False)
        result = execute_email({"action": "list_emails"}, config)
        assert "PRAXIS_EMAIL_PASSWORD not set" in result
        assert "app password" in result


# ---------- Email — IMAP connection ----------


class TestEmailConnection:
    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_auth_failure(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "bad-password")

        import imaplib as imap_mod
        mock_imap.return_value.login.side_effect = imap_mod.IMAP4.error("LOGIN failed")

        result = execute_email({"action": "list_emails"}, config)
        assert "authentication failed" in result

    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_connection_error(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "pass")

        mock_imap.side_effect = OSError("Connection refused")

        result = execute_email({"action": "list_emails"}, config)
        assert "could not connect" in result


# ---------- Email — list ----------


class TestEmailList:
    def _setup_imap(self, mock_imap, emails):
        """Set up mock IMAP with a list of (id, header_bytes) tuples."""
        conn = MagicMock()
        mock_imap.return_value = conn
        conn.login.return_value = ("OK", [])
        conn.select.return_value = ("OK", [b"EXISTS"])

        all_ids = b" ".join(eid.encode() for eid, _ in emails)
        conn.search.return_value = ("OK", [all_ids])

        def fetch_side_effect(mid, parts):
            mid_str = mid.decode() if isinstance(mid, bytes) else mid
            for eid, raw in emails:
                if eid == mid_str:
                    return ("OK", [(b"1 RFC822.HEADER", raw)])
            return ("OK", [None])

        conn.fetch.side_effect = fetch_side_effect
        return conn

    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_list_success(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "pass")

        self._setup_imap(mock_imap, [
            ("1", b"From: alice@ex.com\r\nSubject: First\r\nDate: Mon, 25 May 2026 10:00:00 +0000\r\n\r\n"),
            ("2", b"From: bob@ex.com\r\nSubject: Second\r\nDate: Mon, 25 May 2026 11:00:00 +0000\r\n\r\n"),
        ])

        result = execute_email({"action": "list_emails", "n": 5}, config)
        assert "INBOX" in result
        assert "2 total" in result
        assert "First" in result
        assert "Second" in result

    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_list_empty_folder(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "pass")

        conn = MagicMock()
        mock_imap.return_value = conn
        conn.login.return_value = ("OK", [])
        conn.select.return_value = ("OK", [b"EXISTS"])
        conn.search.return_value = ("OK", [b""])

        result = execute_email({"action": "list_emails"}, config)
        assert "No emails" in result

    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_list_bad_folder(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "pass")

        conn = MagicMock()
        mock_imap.return_value = conn
        conn.login.return_value = ("OK", [])
        conn.select.return_value = ("NO", [b"Folder not found"])

        result = execute_email({"action": "list_emails", "folder": "BadFolder"}, config)
        assert "could not open folder" in result


# ---------- Email — search ----------


class TestEmailSearch:
    def test_missing_query(self, config):
        result = execute_email({"action": "search_emails"}, config)
        assert "'query' is required" in result

    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_search_success(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "pass")

        conn = MagicMock()
        mock_imap.return_value = conn
        conn.login.return_value = ("OK", [])
        conn.select.return_value = ("OK", [b"EXISTS"])
        conn.search.return_value = ("OK", [b"5 10"])
        conn.fetch.return_value = (
            "OK",
            [(b"1 RFC822.HEADER", b"From: sender@ex.com\r\nSubject: Match\r\nDate: Mon, 25 May 2026 10:00:00 +0000\r\n\r\n")],
        )

        result = execute_email(
            {"action": "search_emails", "query": "Match"}, config
        )
        assert "2 email(s)" in result
        assert "Match" in result

    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_search_no_results(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "pass")

        conn = MagicMock()
        mock_imap.return_value = conn
        conn.login.return_value = ("OK", [])
        conn.select.return_value = ("OK", [b"EXISTS"])
        conn.search.return_value = ("OK", [b""])

        result = execute_email(
            {"action": "search_emails", "query": "nonexistent"}, config
        )
        assert "No emails matching" in result

    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_search_raw_imap_syntax(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "pass")

        conn = MagicMock()
        mock_imap.return_value = conn
        conn.login.return_value = ("OK", [])
        conn.select.return_value = ("OK", [b"EXISTS"])
        conn.search.return_value = ("OK", [b"1"])
        conn.fetch.return_value = (
            "OK",
            [(b"1 RFC822.HEADER", b"From: a@b.com\r\nSubject: S\r\n\r\n")],
        )

        # Raw IMAP syntax should pass through
        execute_email(
            {"action": "search_emails", "query": "SINCE 01-Jan-2026"}, config
        )
        # Verify the raw criteria was passed to IMAP search
        conn.search.assert_called_with(None, "SINCE 01-Jan-2026")


# ---------- Email — read ----------


class TestEmailRead:
    def test_missing_message_id(self, config):
        result = execute_email({"action": "read_email"}, config)
        assert "'message_id' is required" in result

    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_read_success(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "pass")

        conn = MagicMock()
        mock_imap.return_value = conn
        conn.login.return_value = ("OK", [])
        conn.select.return_value = ("OK", [b"EXISTS"])

        raw_email = (
            b"From: alice@ex.com\r\n"
            b"To: bob@ex.com\r\n"
            b"Subject: Test email\r\n"
            b"Date: Mon, 25 May 2026 10:00:00 +0000\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"This is the body.\r\n"
        )
        conn.fetch.return_value = ("OK", [(b"1 RFC822", raw_email)])

        result = execute_email(
            {"action": "read_email", "message_id": "1"}, config
        )
        assert "alice@ex.com" in result
        assert "Test email" in result
        assert "This is the body" in result

    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_read_not_found(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "pass")

        conn = MagicMock()
        mock_imap.return_value = conn
        conn.login.return_value = ("OK", [])
        conn.select.return_value = ("OK", [b"EXISTS"])
        conn.fetch.return_value = ("NO", [None])

        result = execute_email(
            {"action": "read_email", "message_id": "999"}, config
        )
        assert "could not fetch" in result

    @patch("praxis.integrations.email.imaplib.IMAP4_SSL")
    def test_read_truncates_long_body(self, mock_imap, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "pass")

        conn = MagicMock()
        mock_imap.return_value = conn
        conn.login.return_value = ("OK", [])
        conn.select.return_value = ("OK", [b"EXISTS"])

        long_body = b"x" * 10000
        raw_email = (
            b"From: a@b.com\r\n"
            b"Subject: Long\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n" + long_body
        )
        conn.fetch.return_value = ("OK", [(b"1 RFC822", raw_email)])

        result = execute_email(
            {"action": "read_email", "message_id": "1"}, config
        )
        assert "more characters" in result


# ---------- Email — draft ----------


class TestEmailDraft:
    def test_draft_missing_to(self, config):
        result = execute_email(
            {"action": "draft_email", "subject": "S", "body": "B"}, config
        )
        assert "'to' is required" in result

    def test_draft_missing_subject(self, config):
        result = execute_email(
            {"action": "draft_email", "to": "a@b.com", "body": "B"}, config
        )
        assert "'subject' is required" in result

    def test_draft_missing_body(self, config):
        result = execute_email(
            {"action": "draft_email", "to": "a@b.com", "subject": "S"}, config
        )
        assert "'body' is required" in result

    def test_draft_creates_file(self, config):
        result = execute_email(
            {
                "action": "draft_email",
                "to": "recipient@example.com",
                "subject": "Test Draft",
                "body": "Hello from Praxis",
            },
            config,
        )
        assert "Draft saved to" in result
        assert "NOT been sent" in result
        assert "recipient@example.com" in result
        assert "Test Draft" in result

        # Verify file was created
        drafts = list(
            (config.workspace_root / ".praxis" / "staging" / "drafts").glob("*.eml")
        )
        assert len(drafts) == 1
        content = drafts[0].read_text()
        assert "To: recipient@example.com" in content
        assert "Subject: Test Draft" in content
        assert "Hello from Praxis" in content

    def test_draft_sanitizes_filename(self, config):
        execute_email(
            {
                "action": "draft_email",
                "to": "a@b.com",
                "subject": "Has/Bad<Chars>!",
                "body": "body",
            },
            config,
        )
        drafts = list(
            (config.workspace_root / ".praxis" / "staging" / "drafts").glob("*.eml")
        )
        assert len(drafts) == 1
        assert "/" not in drafts[0].name
        assert "<" not in drafts[0].name


# ---------- Email — dispatch ----------


class TestEmailDispatch:
    def test_unknown_action(self, config):
        result = execute_email({"action": "bogus"}, config)
        assert "unknown" in result


# ---------- Email — secret redaction ----------


class TestEmailSecretRedaction:
    def test_password_redacted(self, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "secret-app-password-123")
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@example.com")

        with patch("praxis.integrations.email.imaplib.IMAP4_SSL") as mock_imap:
            conn = MagicMock()
            mock_imap.return_value = conn
            conn.login.return_value = ("OK", [])
            conn.select.return_value = ("OK", [b"EXISTS"])
            conn.search.return_value = ("OK", [b"1"])
            conn.fetch.return_value = (
                "OK",
                [(b"1 RFC822.HEADER",
                  b"From: a@b.com\r\nSubject: secret-app-password-123 leaked\r\n\r\n")],
            )

            result = execute_email({"action": "list_emails"}, config)
            assert "secret-app-password-123" not in result
            assert "[REDACTED]" in result


# ========== Calendar integration ==========


# ---------- Calendar — iCal parsing ----------

SAMPLE_ICAL = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20260525T100000Z
DTEND:20260525T110000Z
SUMMARY:Team standup
LOCATION:Zoom
DESCRIPTION:Daily standup meeting
END:VEVENT
BEGIN:VEVENT
DTSTART:20260526T140000
DTEND:20260526T150000
SUMMARY:Code review
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260527
SUMMARY:All-day event
END:VEVENT
END:VCALENDAR
"""


class TestICalParsing:
    def test_parse_events(self):
        events = _parse_ical(SAMPLE_ICAL)
        assert len(events) == 3
        assert events[0].summary == "Team standup"
        assert events[0].location == "Zoom"
        assert events[0].description == "Daily standup meeting"

    def test_parse_utc_datetime(self):
        dt = _parse_ical_datetime("DTSTART:20260525T100000Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 5
        assert dt.hour == 10

    def test_parse_local_datetime(self):
        dt = _parse_ical_datetime("DTSTART:20260525T100000")
        assert dt is not None
        assert dt.hour == 10

    def test_parse_date_only(self):
        dt = _parse_ical_datetime("DTSTART;VALUE=DATE:20260525")
        assert dt is not None
        assert dt.year == 2026
        assert dt.hour == 0

    def test_parse_with_tzid(self):
        dt = _parse_ical_datetime("DTSTART;TZID=America/New_York:20260525T100000")
        assert dt is not None
        assert dt.hour == 10

    def test_parse_invalid(self):
        dt = _parse_ical_datetime("DTSTART:not-a-date")
        assert dt is None

    def test_parse_empty_calendar(self):
        events = _parse_ical("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
        assert events == []

    def test_parse_event_without_dtstart_skipped(self):
        ical = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "SUMMARY:No date\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        events = _parse_ical(ical)
        assert len(events) == 0

    def test_parse_summary_with_params(self):
        ical = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "DTSTART:20260525T100000Z\n"
            "SUMMARY;LANGUAGE=en:Parameterized title\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        events = _parse_ical(ical)
        assert len(events) == 1
        assert events[0].summary == "Parameterized title"

    def test_events_sorted_by_start(self):
        ical = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\nDTSTART:20260526T100000Z\nSUMMARY:Later\nEND:VEVENT\n"
            "BEGIN:VEVENT\nDTSTART:20260525T100000Z\nSUMMARY:Earlier\nEND:VEVENT\n"
            "END:VCALENDAR\n"
        )
        events = _parse_ical(ical)
        assert events[0].summary == "Earlier"
        assert events[1].summary == "Later"


# ---------- Calendar — config errors ----------


class TestCalendarConfig:
    def test_missing_url(self, config, monkeypatch):
        monkeypatch.delenv("PRAXIS_CALENDAR_URL", raising=False)
        result = execute_calendar({"action": "list_events"}, config)
        assert "PRAXIS_CALENDAR_URL not set" in result

    def test_bad_scheme(self, config, monkeypatch):
        monkeypatch.setenv("PRAXIS_CALENDAR_URL", "ftp://cal.example.com/feed.ics")
        result = execute_calendar({"action": "list_events"}, config)
        assert "http://" in result or "https://" in result

    def test_domain_not_allowed(self, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL",
            "https://calendar.google.com/feed.ics",
        )
        result = execute_calendar({"action": "list_events"}, config)
        assert "not in PRAXIS_ALLOWED_DOMAINS" in result


# ---------- Calendar — feed fetch errors ----------


class TestCalendarFetchErrors:
    def _config_with_domains(self, config, domains):
        return Config(
            workspace_root=config.workspace_root,
            memory_root=config.memory_root,
            hook_path=config.hook_path,
            allowed_domains=frozenset(domains),
        )

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_http_error(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://cal.example.com/feed.ics", 403, "Forbidden", {}, None
        )
        result = execute_calendar({"action": "list_events"}, cfg)
        assert "403" in result

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_url_error(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})
        mock_urlopen.side_effect = urllib.error.URLError("DNS failed")
        result = execute_calendar({"action": "list_events"}, cfg)
        assert "could not fetch" in result

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_timeout(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})
        mock_urlopen.side_effect = TimeoutError()
        result = execute_calendar({"action": "list_events"}, cfg)
        assert "timed out" in result


# ---------- Calendar — list_events ----------


class TestCalendarListEvents:
    def _config_with_domains(self, config, domains):
        return Config(
            workspace_root=config.workspace_root,
            memory_root=config.memory_root,
            hook_path=config.hook_path,
            allowed_domains=frozenset(domains),
        )

    def _mock_feed(self, mock_urlopen, ical_text):
        mock_resp = MagicMock()
        mock_resp.read.return_value = ical_text.encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_list_events_success(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})

        # Create event in near future
        from datetime import datetime, timedelta
        tomorrow = datetime.now() + timedelta(days=1)
        dt_str = tomorrow.strftime("%Y%m%dT%H%M%S")
        ical = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            f"DTSTART:{dt_str}\n"
            f"DTEND:{dt_str}\n"
            "SUMMARY:Upcoming meeting\n"
            "LOCATION:Room 42\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        self._mock_feed(mock_urlopen, ical)

        result = execute_calendar({"action": "list_events", "days": 7}, cfg)
        assert "Upcoming meeting" in result
        assert "1 total" in result

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_list_events_empty(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})
        self._mock_feed(mock_urlopen, "BEGIN:VCALENDAR\nEND:VCALENDAR\n")

        result = execute_calendar({"action": "list_events"}, cfg)
        assert "No events" in result


# ---------- Calendar — today ----------


class TestCalendarToday:
    def _config_with_domains(self, config, domains):
        return Config(
            workspace_root=config.workspace_root,
            memory_root=config.memory_root,
            hook_path=config.hook_path,
            allowed_domains=frozenset(domains),
        )

    def _mock_feed(self, mock_urlopen, ical_text):
        mock_resp = MagicMock()
        mock_resp.read.return_value = ical_text.encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_today_with_events(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})

        from datetime import datetime
        now = datetime.now()
        dt_str = now.strftime("%Y%m%dT") + "180000"
        ical = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            f"DTSTART:{dt_str}\n"
            f"DTEND:{dt_str}\n"
            "SUMMARY:Evening standup\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        self._mock_feed(mock_urlopen, ical)

        result = execute_calendar({"action": "today"}, cfg)
        assert "Evening standup" in result
        assert "1 event(s)" in result

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_today_empty(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})
        self._mock_feed(mock_urlopen, "BEGIN:VCALENDAR\nEND:VCALENDAR\n")

        result = execute_calendar({"action": "today"}, cfg)
        assert "No events today" in result


# ---------- Calendar — check_availability ----------


class TestCalendarAvailability:
    def _config_with_domains(self, config, domains):
        return Config(
            workspace_root=config.workspace_root,
            memory_root=config.memory_root,
            hook_path=config.hook_path,
            allowed_domains=frozenset(domains),
        )

    def _mock_feed(self, mock_urlopen, ical_text):
        mock_resp = MagicMock()
        mock_resp.read.return_value = ical_text.encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

    def test_missing_fields(self, config):
        result = execute_calendar(
            {"action": "check_availability", "date": "2026-05-25"}, config
        )
        assert "'date', 'start_time', and 'end_time' are required" in result

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_available(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})

        ical = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "DTSTART:20260525T100000\n"
            "DTEND:20260525T110000\n"
            "SUMMARY:Morning meeting\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        self._mock_feed(mock_urlopen, ical)

        result = execute_calendar(
            {
                "action": "check_availability",
                "date": "2026-05-25",
                "start_time": "14:00",
                "end_time": "15:00",
            },
            cfg,
        )
        assert "Available" in result
        assert "free" in result

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_conflict(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})

        ical = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "DTSTART:20260525T100000\n"
            "DTEND:20260525T110000\n"
            "SUMMARY:Blocking meeting\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        self._mock_feed(mock_urlopen, ical)

        result = execute_calendar(
            {
                "action": "check_availability",
                "date": "2026-05-25",
                "start_time": "10:30",
                "end_time": "11:30",
            },
            cfg,
        )
        assert "Conflict" in result
        assert "Blocking meeting" in result

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_bad_date_format(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})
        self._mock_feed(mock_urlopen, "BEGIN:VCALENDAR\nEND:VCALENDAR\n")

        result = execute_calendar(
            {
                "action": "check_availability",
                "date": "25/05/2026",
                "start_time": "10:00",
                "end_time": "11:00",
            },
            cfg,
        )
        assert "YYYY-MM-DD" in result

    @patch("praxis.integrations.calendar.urllib.request.urlopen")
    def test_end_before_start(self, mock_urlopen, config, monkeypatch):
        monkeypatch.setenv(
            "PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics"
        )
        cfg = self._config_with_domains(config, {"cal.example.com"})
        self._mock_feed(mock_urlopen, "BEGIN:VCALENDAR\nEND:VCALENDAR\n")

        result = execute_calendar(
            {
                "action": "check_availability",
                "date": "2026-05-25",
                "start_time": "15:00",
                "end_time": "14:00",
            },
            cfg,
        )
        assert "end_time must be after start_time" in result


# ---------- Calendar — propose_event ----------


class TestCalendarPropose:
    def test_missing_fields(self, config):
        result = execute_calendar(
            {"action": "propose_event", "title": "Meeting"}, config
        )
        assert "'title', 'date', 'start_time', and 'end_time' are required" in result

    def test_propose_creates_ics(self, config):
        result = execute_calendar(
            {
                "action": "propose_event",
                "title": "Sprint planning",
                "date": "2026-05-28",
                "start_time": "09:00",
                "end_time": "10:00",
                "description": "Weekly sprint planning session",
            },
            config,
        )
        assert "Event proposal saved to" in result
        assert "NOT been created" in result
        assert "Sprint planning" in result
        assert "09:00 - 10:00" in result

        # Verify .ics file was created
        events_dir = config.workspace_root / ".praxis" / "staging" / "events"
        ics_files = list(events_dir.glob("*.ics"))
        assert len(ics_files) == 1
        content = ics_files[0].read_text()
        assert "BEGIN:VCALENDAR" in content
        assert "SUMMARY:Sprint planning" in content
        assert "DESCRIPTION:Weekly sprint planning session" in content
        assert "DTSTART:20260528T090000" in content

    def test_propose_without_description(self, config):
        result = execute_calendar(
            {
                "action": "propose_event",
                "title": "Quick sync",
                "date": "2026-05-28",
                "start_time": "14:00",
                "end_time": "14:30",
            },
            config,
        )
        assert "Event proposal saved to" in result
        assert "NOT been created" in result

        events_dir = config.workspace_root / ".praxis" / "staging" / "events"
        ics_files = list(events_dir.glob("*.ics"))
        content = ics_files[0].read_text()
        assert "DESCRIPTION" not in content

    def test_propose_bad_date_format(self, config):
        result = execute_calendar(
            {
                "action": "propose_event",
                "title": "Bad date",
                "date": "May 28, 2026",
                "start_time": "09:00",
                "end_time": "10:00",
            },
            config,
        )
        assert "YYYY-MM-DD" in result

    def test_propose_sanitizes_filename(self, config):
        execute_calendar(
            {
                "action": "propose_event",
                "title": "Has/Bad<Chars>!",
                "date": "2026-05-28",
                "start_time": "09:00",
                "end_time": "10:00",
            },
            config,
        )
        events_dir = config.workspace_root / ".praxis" / "staging" / "events"
        ics_files = list(events_dir.glob("*.ics"))
        assert len(ics_files) == 1
        assert "/" not in ics_files[0].name
        assert "<" not in ics_files[0].name


# ---------- Calendar — dispatch ----------


class TestCalendarDispatch:
    def test_unknown_action(self, config):
        result = execute_calendar({"action": "bogus"}, config)
        assert "unknown" in result


# ---------- Calendar — secret redaction ----------


class TestCalendarSecretRedaction:
    def test_calendar_url_redacted(self, config, monkeypatch):
        url = "https://cal.google.com/private-abc123/basic.ics"
        monkeypatch.setenv("PRAXIS_CALENDAR_URL", url)
        cfg = Config(
            workspace_root=config.workspace_root,
            memory_root=config.memory_root,
            hook_path=config.hook_path,
            allowed_domains=frozenset({"cal.google.com"}),
        )

        from datetime import datetime, timedelta
        tomorrow = datetime.now() + timedelta(days=1)
        dt_str = tomorrow.strftime("%Y%m%dT%H%M%S")
        ical = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            f"DTSTART:{dt_str}\n"
            f"DTEND:{dt_str}\n"
            f"SUMMARY:URL is {url}\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )

        with patch("praxis.integrations.calendar.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = ical.encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = execute_calendar({"action": "list_events"}, cfg)
            assert url not in result
            assert "[REDACTED]" in result
