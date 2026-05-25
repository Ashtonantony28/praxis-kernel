"""Tests for praxis.orchestrator — end-to-end agent loop with mocked SDK."""

from __future__ import annotations

from pathlib import Path

from praxis.config import Config
from praxis.orchestrator import Orchestrator
from praxis.runtime import ClaudeCodeRuntime
from tests.conftest import (
    FakeClient,
    FakeResponse,
    FakeTextBlock,
    FakeToolUseBlock,
)


def test_orchestrator_init(config: Config, workspace: Path):
    client = FakeClient([])
    orch = Orchestrator(ClaudeCodeRuntime(client), config)
    assert orch.system_prompt == "# Test System Prompt\n"
    assert "scout" in orch.subagents
    assert "builder" in orch.subagents
    assert len(orch.subagents) == 5


def test_run_end_turn_immediately(config: Config):
    responses = [FakeResponse(content=[FakeTextBlock("Hello!")], stop_reason="end_turn")]
    client = FakeClient(responses)
    orch = Orchestrator(ClaudeCodeRuntime(client), config)
    result = orch.run("Hi")
    assert result == "Hello!"
    assert len(client.messages.calls) == 1


def test_tool_dispatch_read(config: Config, workspace: Path):
    """Model requests Read, gets file content, then responds."""
    responses = [
        FakeResponse(
            content=[
                FakeToolUseBlock(
                    id="tool_1",
                    name="Read",
                    input={"file_path": str(workspace / "sample.txt")},
                )
            ],
            stop_reason="tool_use",
        ),
        FakeResponse(
            content=[FakeTextBlock("I read the file.")],
            stop_reason="end_turn",
        ),
    ]
    client = FakeClient(responses)
    orch = Orchestrator(ClaudeCodeRuntime(client), config)
    result = orch.run("Read sample.txt")

    assert result == "I read the file."
    assert len(client.messages.calls) == 2

    # Verify tool result was sent back
    second_call = client.messages.calls[1]
    user_msg = second_call["messages"][-1]
    assert user_msg["role"] == "user"
    tool_result = user_msg["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "line one" in tool_result["content"]


def test_hook_blocks_tool(config: Config, workspace: Path):
    """Model requests Write outside workspace — hook blocks it."""
    responses = [
        FakeResponse(
            content=[
                FakeToolUseBlock(
                    id="tool_1",
                    name="Write",
                    input={"file_path": "/tmp/evil.txt", "content": "bad"},
                )
            ],
            stop_reason="tool_use",
        ),
        FakeResponse(
            content=[FakeTextBlock("I was blocked.")],
            stop_reason="end_turn",
        ),
    ]
    client = FakeClient(responses)
    orch = Orchestrator(ClaudeCodeRuntime(client), config)
    result = orch.run("Write to /tmp")

    assert result == "I was blocked."
    # Verify the tool result contained the block message
    second_call = client.messages.calls[1]
    tool_result = second_call["messages"][-1]["content"][0]
    assert "BLOCKED" in tool_result["content"]


def test_subagent_dispatch(config: Config, workspace: Path):
    """Model requests Agent(scout), gets subagent response."""
    # Call sequence: orch(tool_use) → subagent(end_turn) → orch(end_turn)
    client = FakeClient([
        FakeResponse(
            content=[
                FakeToolUseBlock(
                    id="tool_1",
                    name="Agent",
                    input={"name": "scout", "prompt": "Find files"},
                )
            ],
            stop_reason="tool_use",
        ),
        FakeResponse(
            content=[FakeTextBlock("Found 3 files.")],
            stop_reason="end_turn",
        ),
        FakeResponse(
            content=[FakeTextBlock("Scout found files.")],
            stop_reason="end_turn",
        ),
    ])
    orch = Orchestrator(ClaudeCodeRuntime(client), config)
    result = orch.run("Investigate")
    assert result == "Scout found files."

    # 3 API calls total: orchestrator, subagent, orchestrator
    assert len(client.messages.calls) == 3

    # Subagent call should use haiku model
    subagent_call = client.messages.calls[1]
    assert subagent_call["model"] == "claude-haiku-4-5-20251001"


def test_unknown_subagent(config: Config):
    responses = [
        FakeResponse(
            content=[
                FakeToolUseBlock(
                    id="tool_1",
                    name="Agent",
                    input={"name": "nonexistent", "prompt": "do stuff"},
                )
            ],
            stop_reason="tool_use",
        ),
        FakeResponse(
            content=[FakeTextBlock("Error handled.")],
            stop_reason="end_turn",
        ),
    ]
    client = FakeClient(responses)
    orch = Orchestrator(ClaudeCodeRuntime(client), config)
    result = orch.run("Bad agent")
    assert result == "Error handled."

    tool_result = client.messages.calls[1]["messages"][-1]["content"][0]
    assert "unknown subagent" in tool_result["content"]


def test_subagent_routing_with_override(config: Config, workspace: Path):
    """Subagent uses overridden runtime when configured."""
    # Create two separate FakeClients to track which gets called
    default_client = FakeClient([
        FakeResponse(
            content=[
                FakeToolUseBlock(
                    id="tool_1",
                    name="Agent",
                    input={"name": "scout", "prompt": "Find files"},
                )
            ],
            stop_reason="tool_use",
        ),
        FakeResponse(
            content=[FakeTextBlock("Done.")],
            stop_reason="end_turn",
        ),
    ])
    override_client = FakeClient([
        FakeResponse(
            content=[FakeTextBlock("Scout found files via override.")],
            stop_reason="end_turn",
        ),
    ])

    default_runtime = ClaudeCodeRuntime(default_client)
    override_runtime = ClaudeCodeRuntime(override_client)

    orch = Orchestrator(
        default_runtime,
        config,
        runtime_overrides={"scout": override_runtime},
    )
    result = orch.run("Investigate")
    assert result == "Done."

    # Default client: 2 calls (initial + after tool result)
    assert len(default_client.messages.calls) == 2
    # Override client: 1 call (subagent)
    assert len(override_client.messages.calls) == 1


def test_unknown_tool(config: Config):
    responses = [
        FakeResponse(
            content=[
                FakeToolUseBlock(
                    id="tool_1",
                    name="MagicTool",
                    input={},
                )
            ],
            stop_reason="tool_use",
        ),
        FakeResponse(
            content=[FakeTextBlock("Handled.")],
            stop_reason="end_turn",
        ),
    ]
    client = FakeClient(responses)
    orch = Orchestrator(ClaudeCodeRuntime(client), config)
    result = orch.run("Use magic")
    assert result == "Handled."

    tool_result = client.messages.calls[1]["messages"][-1]["content"][0]
    assert "unknown tool" in tool_result["content"]
