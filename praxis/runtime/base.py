"""Abstract Runtime interface — the contract a provider must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from praxis.modes.base import Mode

ToolExecutor = Callable[[str, dict[str, Any]], str]


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
        """Filter tool schemas by mode.denied_tools.

        Returns a new list excluding any tool whose 'name' field is in
        mode.denied_tools. If mode.denied_tools is empty, returns tools unchanged.
        """
        if not mode.denied_tools:
            return tools
        return [t for t in tools if t.get("name") not in mode.denied_tools]

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
            tool_executor: Callback — tool_executor(name, input) → result string.
                           The orchestrator provides this; it handles §5 hook
                           enforcement and Agent-as-subagent routing.
            max_turns: Safety cap on loop iterations.
            mode: Optional Mode object that filters tools and injects a
                  prompt suffix when active.

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
    ) -> list[dict[str, Any]]:
        """Extract tool_use blocks from the response, execute each via
        tool_executor, and return formatted tool_result entries.

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
