"""Core agent loop — drives the Runtime with tool dispatch and §5 hook."""

from __future__ import annotations

import re
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
        model = model or os.environ.get("PRAXIS_MODEL", "claude-haiku-4-5")

        # Confidence gate: run planner check before spawning full agent loop.
        # Default is 0 (disabled — opt-in). Set PRAXIS_CONFIDENCE_THRESHOLD=0.7 to enable.
        # PRAXIS_CONFIDENCE_THRESHOLD=0 disables the check entirely (existing behaviour).
        _conf_threshold = float(os.environ.get("PRAXIS_CONFIDENCE_THRESHOLD", "0"))
        if _conf_threshold > 0:
            _check = self._run_confidence_check(user_message)
            if _check.get("confidence", 1.0) < _conf_threshold:
                return self._stage_low_confidence_plan(user_message, _check, _conf_threshold)

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

        # Prepend semantic memory context — AFTER §5 governance block, before LLM call.
        # get_memory_store() returns None when PRAXIS_SEMANTIC_MEMORY != 'true' or deps absent.
        from .memory_store import get_memory_store as _get_memory_store  # lazy import
        _top_k = int(os.environ.get("PRAXIS_SEMANTIC_TOP_K", "6"))
        _mem_store = _get_memory_store(self.config.workspace_root)
        _system = self.system_prompt
        if _mem_store is not None:
            try:
                _results = _mem_store.search(user_message, top_k=_top_k)
                if _results:
                    mem_lines = ["## Relevant memory", ""]
                    for r in _results:
                        mem_lines.append(
                            f"- **{r['slug']}** (score={r['score']:.2f}): {r['content_preview']}"
                        )
                    _system = _system + "\n\n" + "\n".join(mem_lines)
            except Exception:
                pass

        result = self.runtime.run_loop(
            model=model,
            system=_system,
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

    def _run_confidence_check(self, user_message: str) -> dict:
        """Run planner in structured mode to assess task confidence before building.

        Returns {"plan": str, "confidence": float 0-1, "ambiguities": list[str]}.
        On any parse error or subagent failure, returns high confidence so execution
        is never blocked by a check failure.
        """
        import json as _json
        import os as _os

        threshold = float(_os.environ.get("PRAXIS_CONFIDENCE_THRESHOLD", "0"))
        if threshold <= 0:
            return {"plan": "", "confidence": 1.0, "ambiguities": []}

        structured_prompt = (
            "Analyze the following task and respond with ONLY a valid JSON object "
            "(no markdown fences, no explanation, no extra text):\n"
            '{"plan": "<one-sentence summary of what you would do>", '
            '"confidence": <float 0.0 to 1.0>, '
            '"ambiguities": ["<unclear aspect 1>", "<unclear aspect 2>"]}\n\n'
            "confidence=1.0 means the task is fully clear and unambiguous.\n"
            "confidence=0.0 means the task is completely unclear.\n"
            "List only genuine ambiguities; use [] if none.\n\n"
            f"Task:\n{user_message}"
        )
        try:
            response = self.run_subagent("planner", structured_prompt)
            # Extract the first {...} JSON object from the response
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                parsed = _json.loads(json_match.group(0))
                return {
                    "plan": str(parsed.get("plan", "")),
                    "confidence": float(parsed.get("confidence", 1.0)),
                    "ambiguities": list(parsed.get("ambiguities", [])),
                }
        except Exception:
            pass
        # Default to high confidence on any error — never block execution on check failure
        return {"plan": "", "confidence": 1.0, "ambiguities": []}

    def _stage_low_confidence_plan(
        self, user_message: str, check: dict, threshold: float
    ) -> str:
        """Stage a low-confidence plan and notify the user to approve/reject.

        Creates .praxis/staging/plans/{id}.json with status=awaiting_input.
        Sends notification via Notifier (best-effort).
        Returns a message to the caller describing the staged plan.
        """
        import uuid as _uuid
        import json as _json
        from datetime import datetime as _dt, timezone as _tz

        plan_id = str(_uuid.uuid4())
        confidence = check.get("confidence", 0.0)
        plan_text = check.get("plan", "")
        ambiguities = check.get("ambiguities", [])

        plans_dir = self.config.workspace_root / ".praxis" / "staging" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan_entry = {
            "id": plan_id,
            "task": user_message,
            "plan_text": plan_text,
            "ambiguities": ambiguities,
            "confidence": confidence,
            "threshold": threshold,
            "created_at": _dt.now(_tz.utc).isoformat(),
            "status": "awaiting_input",
        }
        plan_file = plans_dir / f"{plan_id}.json"
        plan_file.write_text(_json.dumps(plan_entry, indent=2), encoding="utf-8")

        import sys as _sys
        _sys.stderr.write(
            f"[praxis] confidence={confidence:.2f} < threshold={threshold:.2f}: "
            f"plan staged as {plan_id}\n"
        )

        # Notify — best-effort, never raises
        try:
            from .notifier import Notifier as _Notifier
            notifier = _Notifier(self.config.workspace_root)
            if ambiguities:
                ambiguity_lines = "\n".join(f"  - {a}" for a in ambiguities)
            else:
                ambiguity_lines = "  (none specified)"
            message = (
                f"[Praxis] Task is ambiguous — I need your input before proceeding:\n"
                f"{ambiguity_lines}\n\n"
                f"Plan: {plan_text}\n\n"
                f"Reply 'approve', 'reject', or clarify.\n"
                f"Plan ID: {plan_id}"
            )
            notifier.notify(message)
        except Exception:
            pass

        return (
            f"Plan staged (confidence={confidence:.2f} < threshold={threshold:.2f}). "
            f"ID: {plan_id}. "
            f"Awaiting your approval via --approve-plan {plan_id}."
        )

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
