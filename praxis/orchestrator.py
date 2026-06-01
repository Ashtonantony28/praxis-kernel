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
    from .agents.loader import AgentDefinition


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
        agent_modes: dict[str, str] | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config
        self.runtime_overrides = runtime_overrides or {}
        self.agent_modes = agent_modes or {}
        self.system_prompt = self._load_system_prompt()
        self.subagents = load_subagents(config.workspace_root / ".claude" / "agents")
        from .memory.conversation_log import ConversationLog
        self._conv_log = ConversationLog(config.workspace_root)

    def _load_system_prompt(self) -> str:
        path = self.config.workspace_root / "praxis-system-prompt.md"
        governance_text = path.read_text()

        # Append SOUL.md persona context AFTER the §5 governance block — never before.
        # Content is treated as data (user context), not directives. Never logged.
        soul_path = self.config.workspace_root / ".praxis" / "SOUL.md"
        if soul_path.exists():
            soul_text = soul_path.read_text()
            return governance_text + "\n\n" + soul_text

        return governance_text

    def run(self, user_message: str, model: str | None = None, mode: "Mode | None" = None) -> str:
        """Run the orchestrator agent loop with the full system prompt."""
        import os
        model = model or os.environ.get("PRAXIS_MODEL", "claude-sonnet-4-6")
        all_schemas = get_tool_schemas() + get_integration_schemas()

        # Prepend recent interactions to context (max 500 tokens ≈ 2000 chars)
        _recent = self._conv_log.recent(5)
        if _recent:
            lines = []
            total_chars = 0
            for entry in _recent:
                snippet = (
                    f"- [{entry.get('ts','')[:10]}] {entry.get('task_type','task')}: "
                    f"{entry.get('prompt','')[:120]} → {entry.get('outcome','')}"
                )
                if total_chars + len(snippet) > 2000:
                    break
                lines.append(snippet)
                total_chars += len(snippet)
            if lines:
                history_block = "Recent interactions:\n" + "\n".join(lines)
                user_message = history_block + "\n\n---\n\n" + user_message

        result = self.runtime.run_loop(
            model=model,
            system=self.system_prompt,
            user_message=user_message,
            tool_schemas=all_schemas,
            tool_executor=self._execute_with_hook,
            mode=mode,
        )

        # Caller (Scribe / queue_runner) appends to conv_log after task.

        # Stage plan output when mode requires confirmation (e.g. plan mode)
        if mode is not None and mode.requires_confirmation:
            import uuid
            import json
            from datetime import datetime, timezone
            plan_id = str(uuid.uuid4())
            plans_dir = self.config.workspace_root / ".praxis" / "staging" / "plans"
            plans_dir.mkdir(parents=True, exist_ok=True)
            plan_file = plans_dir / f"{plan_id}.json"
            plan_entry = {
                "id": plan_id,
                "task": user_message,
                "plan_text": result,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
            }
            plan_file.write_text(json.dumps(plan_entry, indent=2), encoding="utf-8")
            import sys as _sys
            _sys.stderr.write(f"[praxis] plan staged: {plan_id}\n")

        return result

    def run_subagent(self, name: str, prompt: str) -> str:
        """Spawn a subagent session by name.

        Tries praxis/agents/{name}.yaml first (cross-runtime native definition),
        falls back to .claude/agents/{name}.md (Claude Code SDK discovery).
        Applies per-subagent mode: convergence.yaml override > definition mode > None.
        """
        import sys as _sys

        # Try native YAML definition first
        try:
            from .agents.loader import load as _load_agent
            agent_def = _load_agent(name)
            return self.spawn_from_definition(agent_def, prompt)
        except FileNotFoundError:
            pass  # fall through to .claude/agents/ discovery
        except Exception as e:
            _sys.stderr.write(
                f"[praxis] warning: could not load praxis/agents/{name}.yaml ({e}); trying .claude/agents/\n"
            )

        # Fallback: use .claude/agents/ SubagentDef
        if name not in self.subagents:
            available = ", ".join(sorted(self.subagents))
            return f"Error: unknown subagent '{name}'. Available: {available}"
        defn = self.subagents[name]
        runtime = self.runtime_overrides.get(name, self.runtime)
        core_schemas = get_tool_schemas(defn.tools)
        integration_schemas = get_integration_schemas(defn.tools)

        # Determine effective mode: convergence.yaml override > SubagentDef.mode > None
        effective_mode = None
        mode_str = self.agent_modes.get(name) or defn.mode
        if mode_str:
            try:
                from .modes import Mode as _Mode
                effective_mode = _Mode.load(mode_str)
                _sys.stderr.write(f"[praxis] subagent '{name}' mode: {mode_str}\n")
            except (ImportError, ValueError) as e:
                _sys.stderr.write(
                    f"[praxis] warning: subagent '{name}' mode '{mode_str}' not found ({e}); using session mode\n"
                )

        return runtime.spawn_subagent(
            model=defn.model,
            system=defn.system_prompt,
            prompt=prompt,
            tool_schemas=core_schemas + integration_schemas,
            tool_executor=self._execute_with_hook,
            mode=effective_mode,
        )

    def spawn_from_definition(self, agent_def: "AgentDefinition", prompt: str) -> str:
        """Spawn a subagent using an AgentDefinition from praxis/agents/.

        Works on all three runtimes (Claude, Cloud, Local) because it passes
        the full agent definition directly rather than relying on .claude/agents/ discovery.
        """
        import sys as _sys

        runtime = self.runtime_overrides.get(agent_def.name, self.runtime)
        core_schemas = get_tool_schemas(agent_def.tools)
        integration_schemas = get_integration_schemas(agent_def.tools)

        # Determine effective mode: convergence.yaml override > AgentDefinition.mode > None
        effective_mode = None
        mode_str = self.agent_modes.get(agent_def.name) or agent_def.mode
        if mode_str:
            try:
                from .modes import Mode as _Mode
                effective_mode = _Mode.load(mode_str)
                _sys.stderr.write(f"[praxis] subagent '{agent_def.name}' mode: {mode_str}\n")
            except (ImportError, ValueError) as e:
                _sys.stderr.write(
                    f"[praxis] warning: subagent '{agent_def.name}' mode '{mode_str}' not found ({e}); using session mode\n"
                )

        return runtime.spawn_subagent(
            model=agent_def.model,
            system=agent_def.prompt,
            prompt=prompt,
            tool_schemas=core_schemas + integration_schemas,
            tool_executor=self._execute_with_hook,
            mode=effective_mode,
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
