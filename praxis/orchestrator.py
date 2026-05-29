"""Core agent loop — drives the Runtime with tool dispatch and §5 hook."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import Config
from .hooks import run_pretool_hook
from .integrations import INTEGRATION_IMPLEMENTATIONS, get_integration_schemas
from .runtime.base import Runtime
from .subagents import load_subagents
from .tools import TOOL_IMPLEMENTATIONS, get_tool_schemas

if TYPE_CHECKING:
    from .modes.base import Mode


class Orchestrator:
    """Minimal orchestrator that makes praxis-system-prompt.md executable.

    Owns the *what*: which tools, which hooks, which subagents, which config.
    Delegates the *how* (API protocol) to the Runtime.
    """

    def __init__(
        self,
        runtime: Runtime,
        config: Config,
        *,
        runtime_overrides: dict[str, Runtime] | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config
        self.runtime_overrides = runtime_overrides or {}
        self.system_prompt = self._load_system_prompt()
        self.subagents = load_subagents(config.workspace_root / ".claude" / "agents")

    def _load_system_prompt(self) -> str:
        path = self.config.workspace_root / "praxis-system-prompt.md"
        return path.read_text()

    def run(self, user_message: str, model: str | None = None, mode: "Mode | None" = None) -> str:
        """Run the orchestrator agent loop with the full system prompt."""
        import os
        model = model or os.environ.get("PRAXIS_MODEL", "claude-sonnet-4-6")
        all_schemas = get_tool_schemas() + get_integration_schemas()
        return self.runtime.run_loop(
            model=model,
            system=self.system_prompt,
            user_message=user_message,
            tool_schemas=all_schemas,
            tool_executor=self._execute_with_hook,
            mode=mode,
        )

    def run_subagent(self, name: str, prompt: str) -> str:
        """Spawn a subagent session by name.

        Uses runtime_overrides to route subagents to different runtimes
        (e.g., scout → local, builder → claude). Falls back to default.
        """
        if name not in self.subagents:
            available = ", ".join(sorted(self.subagents))
            return f"Error: unknown subagent '{name}'. Available: {available}"
        defn = self.subagents[name]
        runtime = self.runtime_overrides.get(name, self.runtime)
        core_schemas = get_tool_schemas(defn.tools)
        integration_schemas = get_integration_schemas(defn.tools)
        return runtime.spawn_subagent(
            model=defn.model,
            system=defn.system_prompt,
            prompt=prompt,
            tool_schemas=core_schemas + integration_schemas,
            tool_executor=self._execute_with_hook,
        )

    def _execute_with_hook(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> str:
        # §5 hook check — every tool, every time
        hook = run_pretool_hook(self.config, tool_name, tool_input)
        if not hook.allowed:
            return f"BLOCKED by §5 escalation boundary: {hook.reason}"

        # Agent tool is dispatched here, not in tools.py
        if tool_name == "Agent":
            return self.run_subagent(
                tool_input.get("name", ""), tool_input.get("prompt", "")
            )

        impl = TOOL_IMPLEMENTATIONS.get(tool_name) or INTEGRATION_IMPLEMENTATIONS.get(tool_name)
        if impl is None:
            return f"Error: unknown tool '{tool_name}'"

        try:
            return impl(tool_input, self.config)
        except Exception as exc:
            return f"Error executing {tool_name}: {exc}"
