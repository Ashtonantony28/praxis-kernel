"""Abstract Runtime interface — the contract a provider must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from praxis.modes.base import Mode
    from praxis.policy import PolicyContext, ToolCall

ToolExecutor = Callable[["ToolCall", "PolicyContext"], str]
# Old callback signature; only ClaudeCodeCLIRuntime still declares it (migrates in SG4).
LegacyToolExecutor = Callable[[str, dict[str, Any]], str]


def check_call(name: str, args: dict[str, Any], mode: "Mode | None") -> tuple["ToolCall", "PolicyContext"]:
    """Layer 1 check for one call; raises EnforcementError when blocked."""
    from praxis.policy import ToolCall, context_from_env

    from .enforcement import enforce

    ctx = context_from_env(mode)
    enforce(name, args, mode=mode)
    return ToolCall(name, args), ctx


class Runtime(ABC):
    """Abstract interface for provider-specific API communication.

    The Orchestrator owns *what* to do (tools, hooks, subagents, config).
    The Runtime owns *how* to talk to the API (protocol, message format,
    response parsing).

    Four responsibilities:
      run_loop       — full agent conversation loop
      spawn_subagent — separate session for a subagent
      execute_tool   — parse tool calls from a response, invoke callback
      manage_context — append a message to conversation history
    """

    def apply_mode(
        self,
        mode: "Mode",
        tools: list[dict],
    ) -> list[dict]:
        """Drop schemas the mode rules out for every action.

        A tool denied only for some actions ("notion:create_page") stays visible;
        those calls are blocked per call by enforce().
        """
        from praxis.modes.base import _matches, _split

        qualified = {t.lower() for t, a in map(_split, mode.allowed_tools) if a}

        def visible(name: str) -> bool:
            if _matches(mode.denied_tools, name, None):
                return False
            allowed = mode.allowed_tools
            return not allowed or _matches(allowed, name, None) or name.lower() in qualified

        return [t for t in tools if visible(t.get("name", ""))]

    @abstractmethod
    def run_loop(
        self,
        *,
        model: str,
        system: str,
        user_message: str,
        tool_schemas: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        max_turns: int = 50,
        mode: "Mode | None" = None,
    ) -> str:
        """Run the full agent conversation loop and return final text output.

        Args:
            model: Model identifier (provider-specific).
            system: System prompt text.
            user_message: Initial user message.
            tool_schemas: Tool definitions in the provider's format.
            tool_executor: Callback — tool_executor(call, ctx) → result string.
                           ctx.mode is this loop's mode. The orchestrator
                           provides this; it handles §5 hook enforcement and
                           Agent-as-subagent routing.
            max_turns: Safety cap on loop iterations.
            mode: Optional Mode that filters tools, injects a prompt suffix
                  and is checked on every call of this loop only. Never
                  stored on the runtime, so nested loops cannot leak it.

        Returns:
            Final text output from the model.
        """

    @abstractmethod
    def spawn_subagent(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        tool_schemas: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        max_turns: int = 50,
        mode: "Mode | None" = None,
    ) -> str:
        """Run a subagent session and return its text output.

        Default behavior is identical to run_loop, but a provider may use
        a separate session, instance, or isolation mechanism.
        """

    @abstractmethod
    def execute_tool(
        self,
        response_content: list[Any],
        tool_executor: ToolExecutor,
        mode: "Mode | None" = None,
    ) -> list[dict[str, Any]]:
        """Extract tool_use blocks from the response, check each against §5
        and *mode*, execute allowed ones via tool_executor, and return
        formatted tool_result entries.

        Provider-specific because response block format and tool_result
        format differ between APIs.
        """

    @abstractmethod
    def manage_context(
        self,
        messages: list[dict[str, Any]],
        role: str,
        content: Any,
    ) -> list[dict[str, Any]]:
        """Append a message to the conversation history.

        Provider-specific because message format may differ between APIs.
        Returns the updated messages list.
        """
