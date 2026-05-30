"""tests/test_subagent_mode_routing.py — Per-subagent mode routing tests.

Four test classes:
  TestSubagentDefModeField      (4 tests) — SubagentDef.mode field + parse_agent_file
  TestConvergenceAgentModes     (4 tests) — ConvergenceConfig.agent_modes / mode_for()
  TestOrchestratorSubagentMode  (5 tests) — run_subagent() mode routing logic
  TestAgentDefinitionModeRouting(4 tests) — praxis/agents/*.yaml + spawn_from_definition
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# TestSubagentDefModeField
# ---------------------------------------------------------------------------


class TestSubagentDefModeField:
    """Verify SubagentDef.mode field exists, defaults to None, and is parsed correctly."""

    def test_subagentdef_has_mode_field(self):
        """SubagentDef can be constructed with an explicit mode value."""
        from praxis.subagents import SubagentDef

        defn = SubagentDef(
            name="scout",
            description="test",
            tools=["Read"],
            model="claude-haiku-4-5-20251001",
            system_prompt="test",
            mode="plan",
        )
        assert defn.mode == "plan"

    def test_subagentdef_mode_defaults_to_none(self):
        """SubagentDef.mode is None when not provided."""
        from praxis.subagents import SubagentDef

        defn = SubagentDef(
            name="builder",
            description="test",
            tools=["Write"],
            model="claude-sonnet-4-6",
            system_prompt="test",
        )
        assert defn.mode is None

    def test_parse_agent_file_reads_mode_if_present(self, tmp_path):
        """parse_agent_file() extracts mode: from frontmatter."""
        from praxis.subagents import parse_agent_file

        md_content = (
            "---\n"
            "name: scout\n"
            "description: test\n"
            "tools: Read, Grep\n"
            "model: haiku\n"
            "mode: plan\n"
            "---\n\n"
            "System prompt text here.\n"
        )
        md_file = tmp_path / "scout.md"
        md_file.write_text(md_content)
        defn = parse_agent_file(md_file)
        assert defn.mode == "plan"

    def test_parse_agent_file_mode_none_if_absent(self, tmp_path):
        """parse_agent_file() sets mode=None when frontmatter has no mode key."""
        from praxis.subagents import parse_agent_file

        md_content = (
            "---\n"
            "name: builder\n"
            "description: test\n"
            "tools: Write\n"
            "model: sonnet\n"
            "---\n\n"
            "System prompt.\n"
        )
        md_file = tmp_path / "builder.md"
        md_file.write_text(md_content)
        defn = parse_agent_file(md_file)
        assert defn.mode is None


# ---------------------------------------------------------------------------
# TestConvergenceAgentModes
# ---------------------------------------------------------------------------


class TestConvergenceAgentModes:
    """Verify ConvergenceConfig.agent_modes and mode_for() behaviour."""

    def test_agent_modes_default_empty(self):
        """ConvergenceConfig() with no arguments has an empty agent_modes dict."""
        from praxis.convergence import ConvergenceConfig

        conv = ConvergenceConfig()
        assert conv.agent_modes == {}

    def test_mode_for_returns_none_when_no_override(self):
        """mode_for() returns None when agent has no override in convergence config."""
        from praxis.convergence import ConvergenceConfig

        conv = ConvergenceConfig()
        assert conv.mode_for("scout") is None

    def test_agent_modes_from_yaml(self, tmp_path):
        """Agents section in convergence.yaml populates agent_modes correctly."""
        from praxis.convergence import ConvergenceConfig

        yaml_content = {
            "runtimes": {"default": "claude"},
            "agents": {
                "scout": {"mode": "plan"},
                "builder": {"mode": "build"},
            },
        }
        (tmp_path / "convergence.yaml").write_text(yaml.dump(yaml_content))

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            conv = ConvergenceConfig.load(tmp_path)

        assert conv.agent_modes == {"scout": "plan", "builder": "build"}

    def test_mode_for_returns_override(self, tmp_path):
        """mode_for() returns the override value for a named agent."""
        from praxis.convergence import ConvergenceConfig

        yaml_content = {
            "runtimes": {"default": "claude"},
            "agents": {"scout": {"mode": "plan"}},
        }
        (tmp_path / "convergence.yaml").write_text(yaml.dump(yaml_content))

        with patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            conv = ConvergenceConfig.load(tmp_path)

        assert conv.mode_for("scout") == "plan"
        assert conv.mode_for("builder") is None


# ---------------------------------------------------------------------------
# TestOrchestratorSubagentMode
# ---------------------------------------------------------------------------


def _make_orch(workspace: Path, agent_modes: dict | None = None):
    """Create an Orchestrator with a mock runtime and pre-loaded SubagentDefs."""
    from praxis.orchestrator import Orchestrator
    from praxis.config import Config
    from praxis.subagents import SubagentDef

    runtime = MagicMock()
    runtime.spawn_subagent.return_value = "subagent result"

    config = Config(
        workspace_root=workspace,
        memory_root=workspace / ".praxis" / "memory",
        hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
        allowed_domains=frozenset(),
    )
    orch = Orchestrator(runtime, config, agent_modes=agent_modes or {})

    # Override the loaded subagents with known test definitions
    orch.subagents["scout"] = SubagentDef(
        name="scout",
        description="read-only scout",
        tools=["Read"],
        model="claude-haiku-4-5-20251001",
        system_prompt="You are Scout.",
        mode="plan",
    )
    orch.subagents["builder"] = SubagentDef(
        name="builder",
        description="builder",
        tools=["Write"],
        model="claude-sonnet-4-6",
        system_prompt="You are Builder.",
        mode="build",
    )
    return orch, runtime


class TestOrchestratorSubagentMode:
    """Verify run_subagent() applies the correct mode when spawning subagents."""

    def test_scout_mode_plan_passed_to_spawn_subagent(self, workspace):
        """Scout (mode=plan in SubagentDef) → spawn_subagent receives mode.name=='plan'."""
        orch, runtime = _make_orch(workspace)

        # Patch the praxis/agents/loader.load to raise FileNotFoundError so
        # the fallback .claude/agents/ path (with our injected SubagentDefs) is used.
        with patch("praxis.agents.loader.load", side_effect=FileNotFoundError("no yaml")):
            orch.run_subagent("scout", "find files")

        runtime.spawn_subagent.assert_called_once()
        call_kwargs = runtime.spawn_subagent.call_args[1]
        assert call_kwargs["mode"] is not None
        assert call_kwargs["mode"].name == "plan"

    def test_builder_mode_build_passed_to_spawn_subagent(self, workspace):
        """Builder (mode=build in SubagentDef) → spawn_subagent receives mode.name=='build'."""
        orch, runtime = _make_orch(workspace)

        with patch("praxis.agents.loader.load", side_effect=FileNotFoundError("no yaml")):
            orch.run_subagent("builder", "implement the feature")

        runtime.spawn_subagent.assert_called_once()
        call_kwargs = runtime.spawn_subagent.call_args[1]
        assert call_kwargs["mode"] is not None
        assert call_kwargs["mode"].name == "build"

    def test_convergence_mode_override_takes_priority(self, workspace):
        """Convergence override wins over SubagentDef.mode (builder override plan > build)."""
        orch, runtime = _make_orch(workspace, agent_modes={"builder": "plan"})

        with patch("praxis.agents.loader.load", side_effect=FileNotFoundError("no yaml")):
            orch.run_subagent("builder", "implement")

        runtime.spawn_subagent.assert_called_once()
        call_kwargs = runtime.spawn_subagent.call_args[1]
        assert call_kwargs["mode"] is not None
        assert call_kwargs["mode"].name == "plan"

    def test_no_mode_passes_none_to_spawn_subagent(self, workspace):
        """A SubagentDef with mode=None and no convergence override → spawn_subagent mode=None."""
        from praxis.subagents import SubagentDef

        orch, runtime = _make_orch(workspace)
        orch.subagents["no-mode-agent"] = SubagentDef(
            name="no-mode-agent",
            description="test",
            tools=[],
            model="claude-sonnet-4-6",
            system_prompt="",
            mode=None,
        )

        with patch("praxis.agents.loader.load", side_effect=FileNotFoundError("no yaml")):
            orch.run_subagent("no-mode-agent", "do something")

        runtime.spawn_subagent.assert_called_once()
        call_kwargs = runtime.spawn_subagent.call_args[1]
        assert call_kwargs.get("mode") is None

    def test_unknown_mode_logs_warning_and_calls_spawn(self, workspace):
        """An unresolvable mode string logs a warning; spawn_subagent is still called."""
        from praxis.subagents import SubagentDef
        import sys

        orch, runtime = _make_orch(workspace)
        orch.subagents["weird"] = SubagentDef(
            name="weird",
            description="test",
            tools=[],
            model="claude-sonnet-4-6",
            system_prompt="",
            mode="nonexistent_mode",
        )

        stderr_output: list[str] = []
        original_write = sys.stderr.write

        def capture_write(s: str) -> None:
            stderr_output.append(s)
            return original_write(s)

        with patch("praxis.agents.loader.load", side_effect=FileNotFoundError("no yaml")):
            with patch.object(sys.stderr, "write", side_effect=capture_write):
                orch.run_subagent("weird", "do something")

        # spawn_subagent must still be invoked despite the bad mode
        runtime.spawn_subagent.assert_called_once()
        # A warning message should have been written to stderr
        combined = "".join(stderr_output)
        assert "warning" in combined.lower() or "nonexistent_mode" in combined


# ---------------------------------------------------------------------------
# TestAgentDefinitionModeRouting
# ---------------------------------------------------------------------------


class TestAgentDefinitionModeRouting:
    """Verify praxis/agents/*.yaml modes and spawn_from_definition() mode wiring."""

    def test_all_yaml_agents_have_mode(self):
        """Every agent in praxis/agents/ declares mode as 'plan' or 'build'."""
        from praxis.agents.loader import load_all

        agents = load_all()
        assert len(agents) == 5, f"Expected 5 agents, got {len(agents)}"
        for agent in agents:
            assert agent.mode in ("plan", "build"), (
                f"{agent.name} has unexpected mode {agent.mode!r}; expected 'plan' or 'build'"
            )

    def test_scout_yaml_is_plan_mode(self):
        """praxis/agents/scout.yaml declares mode: plan."""
        from praxis.agents.loader import load

        agent = load("scout")
        assert agent.mode == "plan"

    def test_builder_yaml_is_build_mode(self):
        """praxis/agents/builder.yaml declares mode: build."""
        from praxis.agents.loader import load

        agent = load("builder")
        assert agent.mode == "build"

    def test_spawn_from_definition_passes_mode(self, workspace):
        """spawn_from_definition() with AgentDefinition(mode='plan') → spawn_subagent mode.name=='plan'."""
        from praxis.orchestrator import Orchestrator
        from praxis.config import Config
        from praxis.agents.loader import AgentDefinition

        runtime = MagicMock()
        runtime.spawn_subagent.return_value = "ok"

        config = Config(
            workspace_root=workspace,
            memory_root=workspace / ".praxis" / "memory",
            hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )
        orch = Orchestrator(runtime, config)

        agent_def = AgentDefinition(
            name="scout",
            model="claude-haiku-4-5-20251001",
            mode="plan",
            prompt="You are Scout.",
            tools=["Read"],
            background=False,
            model_alias="haiku",
        )
        orch.spawn_from_definition(agent_def, "find files")

        runtime.spawn_subagent.assert_called_once()
        call_kwargs = runtime.spawn_subagent.call_args[1]
        assert call_kwargs["mode"] is not None
        assert call_kwargs["mode"].name == "plan"
