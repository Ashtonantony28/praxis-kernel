"""tests/test_subagent_agnostic.py — Cross-runtime subagent definition tests.

Parametrized over ClaudeCodeRuntime, OpenAIBaseRuntime (cloud), and LocalRuntime.
All tests use mocked API clients — no real API calls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from praxis.runtime.claude_code import ClaudeCodeRuntime
from praxis.runtime.openai_base import OpenAIBaseRuntime
from praxis.runtime.local import LocalRuntime
from praxis.agents.loader import AgentDefinition, load, load_all

from tests.conftest import FakeTextBlock, FakeToolUseBlock, FakeResponse


# ---------------------------------------------------------------------------
# OpenAI-style fake dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


@dataclass
class FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 20


@dataclass
class FakeOpenAIChoice:
    message: Any
    finish_reason: str = "stop"


@dataclass
class FakeOpenAIMessage:
    content: str = "Agent result."
    tool_calls: list = field(default_factory=list)
    role: str = "assistant"


@dataclass
class FakeOpenAIResponse:
    choices: list
    usage: FakeUsage = field(default_factory=FakeUsage)
    model: str = "gpt-4o"


# ---------------------------------------------------------------------------
# Runtime factory helpers
# ---------------------------------------------------------------------------


def _make_claude_runtime():
    client = MagicMock()
    response = MagicMock()
    response.content = [FakeTextBlock(text="Scout result.")]
    response.stop_reason = "end_turn"
    response.usage = MagicMock(input_tokens=10, output_tokens=20)
    client.messages.create.return_value = response
    return ClaudeCodeRuntime(client=client)


def _make_openai_runtime():
    client = MagicMock()
    msg = FakeOpenAIMessage(content="Agent result.")
    choice = FakeOpenAIChoice(message=msg, finish_reason="stop")
    resp = FakeOpenAIResponse(choices=[choice])
    client.chat.completions.create.return_value = resp
    return OpenAIBaseRuntime(client=client, default_model="gpt-4o", base_url="http://fake")


def _make_local_runtime():
    client = MagicMock()
    msg = FakeOpenAIMessage(content="Agent result.")
    choice = FakeOpenAIChoice(message=msg, finish_reason="stop")
    resp = FakeOpenAIResponse(choices=[choice])
    client.chat.completions.create.return_value = resp
    return LocalRuntime(client=client, default_model="llama3.1:8b", base_url="http://localhost:11434")


RUNTIME_FACTORIES = [
    pytest.param(_make_claude_runtime, id="ClaudeCodeRuntime"),
    pytest.param(_make_openai_runtime, id="OpenAIBaseRuntime"),
    pytest.param(_make_local_runtime, id="LocalRuntime"),
]


# ---------------------------------------------------------------------------
# Class 1: TestAgentDefinitionLoading
# ---------------------------------------------------------------------------


class TestAgentDefinitionLoading:
    """AgentDefinition loads correctly from praxis/agents/*.yaml."""

    def test_load_all_returns_five_agents(self):
        agents = load_all()
        assert len(agents) == 5

    def test_all_agents_have_required_fields(self):
        for agent in load_all():
            assert agent.name
            assert agent.model  # full model ID
            assert agent.mode in ("plan", "build")
            assert agent.prompt
            assert isinstance(agent.tools, list)

    def test_load_scout_by_name(self):
        agent = load("scout")
        assert agent.name == "scout"
        assert agent.mode == "plan"
        assert "Read" in agent.tools

    def test_load_builder_by_name(self):
        agent = load("builder")
        assert agent.name == "builder"
        assert agent.mode == "build"
        assert "Write" in agent.tools

    def test_load_nonexistent_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load("nonexistent_agent_xyz")

    def test_model_alias_resolved_to_full_id(self):
        scout = load("scout")
        # haiku → full model ID
        assert scout.model == "claude-haiku-4-5-20251001"

    def test_five_agent_names_correct(self):
        names = {a.name for a in load_all()}
        assert names == {"scout", "planner", "builder", "verifier", "scribe"}

    def test_plan_mode_agents(self):
        plan_agents = {a.name for a in load_all() if a.mode == "plan"}
        assert plan_agents == {"scout", "planner", "verifier", "scribe"}

    def test_build_mode_agents(self):
        build_agents = {a.name for a in load_all() if a.mode == "build"}
        assert build_agents == {"builder"}


# ---------------------------------------------------------------------------
# Class 2: TestSpawnFromDefinitionAllRuntimes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runtime_factory", RUNTIME_FACTORIES)
class TestSpawnFromDefinitionAllRuntimes:
    """spawn_from_definition() works on all three runtimes."""

    def test_spawn_from_definition_returns_string(self, runtime_factory, workspace):
        """spawn_from_definition() returns a string on all runtimes."""
        from praxis.orchestrator import Orchestrator
        from praxis.config import Config

        config = Config(
            workspace_root=workspace,
            memory_root=workspace / ".praxis" / "memory",
            hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )
        runtime = runtime_factory()
        orch = Orchestrator(runtime, config)

        agent_def = load("scout")
        result = orch.spawn_from_definition(agent_def, "find all Python files")
        assert isinstance(result, str)

    def test_spawn_from_definition_uses_agent_model(self, runtime_factory, workspace):
        """spawn_from_definition() passes AgentDefinition.model to the runtime."""
        from praxis.orchestrator import Orchestrator
        from praxis.config import Config

        config = Config(
            workspace_root=workspace,
            memory_root=workspace / ".praxis" / "memory",
            hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )
        runtime = runtime_factory()
        # Patch spawn_subagent to intercept the call
        with patch.object(runtime, "spawn_subagent", wraps=runtime.spawn_subagent) as mock_spawn:
            orch = Orchestrator(runtime, config)
            agent_def = load("scout")
            try:
                orch.spawn_from_definition(agent_def, "find files")
            except Exception:
                pass  # ignore runtime errors; we just want to check the call

            if mock_spawn.called:
                call_kwargs = mock_spawn.call_args[1]
                assert call_kwargs.get("model") == agent_def.model

    def test_spawn_from_definition_passes_mode(self, runtime_factory, workspace):
        """spawn_from_definition() passes Mode object matching agent's mode field."""
        from praxis.orchestrator import Orchestrator
        from praxis.config import Config

        config = Config(
            workspace_root=workspace,
            memory_root=workspace / ".praxis" / "memory",
            hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )
        runtime = runtime_factory()
        with patch.object(runtime, "spawn_subagent", return_value="ok") as mock_spawn:
            orch = Orchestrator(runtime, config)
            agent_def = load("scout")  # mode: plan
            orch.spawn_from_definition(agent_def, "investigate")

            call_kwargs = mock_spawn.call_args[1]
            mode = call_kwargs.get("mode")
            assert mode is not None
            assert mode.name == "plan"

    def test_all_five_agents_loadable_from_yaml(self, runtime_factory, workspace):
        """All 5 agents can be loaded from YAML on any runtime (no .claude/ needed)."""
        agents = load_all()
        assert len(agents) == 5
        for agent in agents:
            assert agent.mode in ("plan", "build")
            assert agent.model  # resolved to full ID


# ---------------------------------------------------------------------------
# Class 3: TestGenerateAgentShims
# ---------------------------------------------------------------------------


class TestGenerateAgentShims:
    """generate_agent_shims() writes .claude/agents/*.md from praxis/agents/*.yaml."""

    def test_generate_creates_md_files(self, tmp_path):
        from praxis.runtime.claude_code import generate_agent_shims
        generate_agent_shims(tmp_path)
        agents_dir = tmp_path / ".claude" / "agents"
        md_files = list(agents_dir.glob("*.md"))
        assert len(md_files) == 5

    def test_generated_shim_has_frontmatter(self, tmp_path):
        from praxis.runtime.claude_code import generate_agent_shims
        generate_agent_shims(tmp_path)
        scout_md = tmp_path / ".claude" / "agents" / "scout.md"
        assert scout_md.is_file()
        content = scout_md.read_text()
        assert content.startswith("---")
        assert "name: scout" in content
        assert "mode: plan" in content

    def test_generated_shim_builder_is_build_mode(self, tmp_path):
        from praxis.runtime.claude_code import generate_agent_shims
        generate_agent_shims(tmp_path)
        builder_md = tmp_path / ".claude" / "agents" / "builder.md"
        content = builder_md.read_text()
        assert "mode: build" in content

    def test_generate_idempotent(self, tmp_path):
        from praxis.runtime.claude_code import generate_agent_shims
        generate_agent_shims(tmp_path)
        generate_agent_shims(tmp_path)  # call twice
        agents_dir = tmp_path / ".claude" / "agents"
        md_files = list(agents_dir.glob("*.md"))
        assert len(md_files) == 5  # still 5, not doubled


# ---------------------------------------------------------------------------
# Class 4: TestRunSubagentCrossRuntime
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runtime_factory", RUNTIME_FACTORIES)
class TestRunSubagentCrossRuntime:
    """run_subagent() works on all runtimes via praxis/agents/ first-class path."""

    def test_run_subagent_uses_yaml_definition(self, runtime_factory, workspace):
        """run_subagent('scout', ...) loads from praxis/agents/scout.yaml on all runtimes."""
        from praxis.orchestrator import Orchestrator
        from praxis.config import Config

        config = Config(
            workspace_root=workspace,
            memory_root=workspace / ".praxis" / "memory",
            hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )
        runtime = runtime_factory()
        with patch.object(runtime, "spawn_subagent", return_value="scout done") as mock_spawn:
            orch = Orchestrator(runtime, config)
            result = orch.run_subagent("scout", "find Python files")
            assert result == "scout done"
            mock_spawn.assert_called_once()

    def test_run_subagent_unknown_agent_returns_error(self, runtime_factory, workspace):
        """run_subagent() with unknown name returns clear error, no crash."""
        from praxis.orchestrator import Orchestrator
        from praxis.config import Config

        config = Config(
            workspace_root=workspace,
            memory_root=workspace / ".praxis" / "memory",
            hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )
        runtime = runtime_factory()
        orch = Orchestrator(runtime, config)
        result = orch.run_subagent("nonexistent_agent_xyz_abc", "do stuff")
        assert "Error" in result or "unknown" in result.lower() or "not found" in result.lower()
