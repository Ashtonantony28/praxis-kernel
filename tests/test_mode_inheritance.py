"""SG3: mode semantics end to end through Orchestrator on every API runtime.

A scripted fake client drives the real orchestrator loop (hook, enforcement,
subagent dispatch). Turns are consumed in order across parent and child loops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from praxis.modes.base import Mode
from praxis.orchestrator import Orchestrator
from praxis.runtime.claude_code import ClaudeCodeRuntime
from praxis.runtime.cloud import OpenAICloudRuntime
from praxis.runtime.local import LocalRuntime

from tests.conftest import FakeResponse, FakeTextBlock, FakeToolUseBlock


@dataclass
class _Fn:
    name: str
    arguments: str


@dataclass
class _TC:
    id: str
    function: _Fn


@dataclass
class _Msg:
    content: str | None
    tool_calls: list = field(default_factory=list)


@dataclass
class _Choice:
    message: _Msg
    finish_reason: str


@dataclass
class _OAIResponse:
    choices: list


class _Script:
    """Scripted turns: ("tool", name, input) or ("text", str)."""

    def __init__(self, turns: list[tuple]) -> None:
        self.turns = list(turns)
        self.n = 0

    def next(self) -> tuple:
        self.n += 1
        return self.turns.pop(0) if self.turns else ("text", "done")


class _AnthropicClient:
    def __init__(self, script: _Script) -> None:
        self.messages = MagicMock()
        self.messages.create.side_effect = lambda **kw: self._reply(script)

    @staticmethod
    def _reply(script: _Script) -> FakeResponse:
        turn = script.next()
        if turn[0] == "text":
            return FakeResponse([FakeTextBlock(turn[1])], "end_turn")
        return FakeResponse([FakeToolUseBlock(f"t{script.n}", turn[1], turn[2])], "tool_use")


class _OpenAIClient:
    def __init__(self, script: _Script) -> None:
        self.chat = MagicMock()
        self.chat.completions.create.side_effect = lambda **kw: self._reply(script)

    @staticmethod
    def _reply(script: _Script) -> _OAIResponse:
        turn = script.next()
        if turn[0] == "text":
            return _OAIResponse([_Choice(_Msg(turn[1]), "stop")])
        tc = _TC(f"t{script.n}", _Fn(turn[1], json.dumps(turn[2])))
        return _OAIResponse([_Choice(_Msg(None, [tc]), "tool_calls")])


RUNTIMES = [
    pytest.param(lambda s: ClaudeCodeRuntime(_AnthropicClient(s)), id="claude"),
    pytest.param(lambda s: OpenAICloudRuntime(_OpenAIClient(s), default_model="gpt-4o", base_url="http://fake"), id="cloud"),
    pytest.param(lambda s: LocalRuntime(_OpenAIClient(s)), id="local"),
]

INTEGRATION_WRITES = [
    ("linear", {"action": "create_issue", "title": "t", "team_id": "x"}),
    ("linear", {"action": "update_issue", "issue_id": "1", "title": "t"}),
    ("linear", {"action": "add_comment", "issue_id": "1", "body": "b"}),
    ("notion", {"action": "create_page", "title": "t", "parent_id": "p"}),
    ("notion", {"action": "update_page", "page_id": "p", "title": "t"}),
    ("notion", {"action": "append_block", "page_id": "p", "content": "c"}),
    ("Email", {"action": "draft_email", "to": "a@b.c", "subject": "s", "body": "b"}),
    ("Calendar", {"action": "propose_event", "title": "t", "start": "2026-01-01T10:00"}),
    ("Wiki", {"action": "ingest", "source": "notes.md"}),
    ("Slack", {"action": "stage_message", "message": "m"}),
    ("Slack", {"action": "post_approval_request", "message": "m"}),
    ("Slack", {"action": "notify", "message": "m"}),
    ("playwright", {"action": "screenshot", "url": "https://x", "output_path": "a.png"}),
]


def _orch(make, turns, config):
    script = _Script(turns)
    runtime = make(script)
    spawn = MagicMock(wraps=runtime.spawn_subagent)
    runtime.spawn_subagent = spawn
    return Orchestrator(runtime, config), spawn, script


def _spy_integrations(monkeypatch) -> dict[str, MagicMock]:
    from praxis import orchestrator as orch_mod

    spies = {}
    for name in {n for n, _ in INTEGRATION_WRITES}:
        spies[name] = MagicMock(return_value="staged")
        monkeypatch.setitem(orch_mod.INTEGRATION_IMPLEMENTATIONS, name, spies[name])
    return spies


@pytest.mark.parametrize("make", RUNTIMES)
def test_plan_session_cannot_stage_integration_writes(make, config, monkeypatch):
    spies = _spy_integrations(monkeypatch)
    turns = [("tool", n, a) for n, a in INTEGRATION_WRITES] + [("text", "plan")]
    orch, _, _ = _orch(make, turns, config)

    orch.run("do it", mode=Mode.load("plan"))

    assert all(not s.called for s in spies.values()), {n: s.call_args_list for n, s in spies.items()}
    staging = config.workspace_root / ".praxis" / "staging"
    assert {p.name for p in staging.iterdir()} == {"plans"}


@pytest.mark.parametrize("make", RUNTIMES)
def test_build_session_reaches_integrations(make, config, monkeypatch):
    spies = _spy_integrations(monkeypatch)
    turns = [("tool", n, a) for n, a in INTEGRATION_WRITES] + [("text", "done")]
    orch, _, _ = _orch(make, turns, config)

    orch.run("do it", mode=Mode.load("build"))

    assert sum(s.call_count for s in spies.values()) == len(INTEGRATION_WRITES)


@pytest.mark.parametrize("make", RUNTIMES)
@pytest.mark.parametrize("agent", ["builder", "scribe", "verifier"])
def test_plan_session_cannot_spawn_writing_agent(make, agent, config):
    orch, spawn, script = _orch(make, [("tool", "Agent", {"name": agent, "prompt": "go"}), ("text", "plan")], config)

    orch.run("do it", mode=Mode.load("plan"))

    spawn.assert_not_called()
    assert script.n == 2


@pytest.mark.parametrize("make", RUNTIMES)
def test_plan_session_spawns_scout_under_plan(make, config):
    orch, spawn, _ = _orch(make, [("tool", "Agent", {"name": "scout", "prompt": "look"}), ("text", "found"), ("text", "plan")], config)

    orch.run("do it", mode=Mode.load("plan"))

    spawn.assert_called_once()
    child = spawn.call_args.kwargs["mode"]
    from praxis.policy import ToolCall

    assert not child.permits(ToolCall("Write", {}))
    assert not child.permits(ToolCall("Agent", {}))
    assert child.permits(ToolCall("Read", {}))


@pytest.mark.parametrize("make", RUNTIMES)
def test_parent_mode_restored_after_subagent(make, config):
    """Build parent spawns plan-mode scout; scout's Write is blocked, parent's later Write is not."""
    child_out = config.workspace_root / "child.txt"
    parent_out = config.workspace_root / "parent.txt"
    turns = [
        ("tool", "Agent", {"name": "scout", "prompt": "look"}),
        ("tool", "Write", {"file_path": str(child_out), "content": "c"}),
        ("text", "found"),
        ("tool", "Write", {"file_path": str(parent_out), "content": "p"}),
        ("text", "done"),
    ]
    orch, spawn, _ = _orch(make, turns, config)

    orch.run("do it", mode=Mode.load("build"))

    spawn.assert_called_once()
    assert not child_out.exists()
    assert parent_out.read_text() == "p"


@pytest.mark.parametrize("make", RUNTIMES)
def test_plan_parent_still_plan_after_subagent(make, config):
    out = config.workspace_root / "parent.txt"
    turns = [
        ("tool", "Agent", {"name": "scout", "prompt": "look"}),
        ("text", "found"),
        ("tool", "Write", {"file_path": str(out), "content": "p"}),
        ("tool", "linear", {"action": "create_issue", "title": "t", "team_id": "x"}),
        ("text", "plan"),
    ]
    orch, spawn, _ = _orch(make, turns, config)

    orch.run("do it", mode=Mode.load("plan"))

    spawn.assert_called_once()
    assert not out.exists()
    assert not (config.workspace_root / ".praxis" / "staging" / "external_actions.jsonl").exists()
