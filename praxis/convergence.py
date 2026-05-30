"""Convergence config — multi-runtime routing from convergence.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_RUNTIMES = {"claude", "local", "cloud"}


@dataclass(frozen=True)
class TaskTypeRule:
    """Runtime routing rule for a detected task type."""

    runtime: str
    model: str | None = None


TASK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "audit": ["audit", "inventory", "verify", "inspect", "check", "scan", "baseline"],
    "implement": ["implement", "create", "build", "add feature", "fix", "refactor", "update code"],
    "review": ["review", "analyze", "analyse", "assess", "evaluate", "critique", "examine"],
    "scribe": ["update claude.md", "update readme", "update status.md", "scribe", "write docs", "write handoff", "document"],
}


def detect_task_type(prompt: str) -> str:
    """Detect task type from prompt via keyword matching.

    Returns the task type with the highest keyword match count.
    Ties broken arbitrarily. Returns "default" if no keywords match.
    This is a deterministic, cheap string match — no LLM call.
    """
    lower = prompt.lower()
    scores: dict[str, int] = {}
    for task_type, keywords in TASK_TYPE_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in lower)
        if count > 0:
            scores[task_type] = count
    if not scores:
        return "default"
    return max(scores, key=lambda t: scores[t])


@dataclass(frozen=True)
class ConvergenceConfig:
    """Parsed convergence.yaml — controls which runtime each role uses."""

    default_runtime: str = "claude"
    overrides: dict[str, str] = field(default_factory=dict)
    local_base_url: str = "http://localhost:11434"
    local_model: str = "llama3.1:8b"
    cloud_base_url: str = "https://api.openai.com/v1"
    cloud_model: str = "gpt-4o"
    task_type_rules: dict[str, TaskTypeRule] = field(default_factory=dict)
    agent_modes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, workspace_root: Path) -> "ConvergenceConfig":
        """Load from convergence.yaml, falling back to env vars / defaults.

        Precedence for default runtime:
          1. PRAXIS_RUNTIME env var (overrides file)
          2. convergence.yaml runtimes.default
          3. "claude"
        """
        path = workspace_root / "convergence.yaml"
        data: dict = {}
        if path.is_file():
            with open(path) as f:
                data = yaml.safe_load(f) or {}

        runtimes_section = data.get("runtimes", {})
        local_section = data.get("local", {})
        cloud_section = data.get("cloud", {})

        # Default runtime: env var > file > "claude"
        env_runtime = os.environ.get("PRAXIS_RUNTIME")
        file_default = runtimes_section.get("default", "claude")
        default_runtime = (env_runtime or file_default).lower()

        if default_runtime not in VALID_RUNTIMES:
            raise SystemExit(
                f"[praxis] fatal: unknown runtime {default_runtime!r} "
                f"in convergence config.\nValid runtimes: {', '.join(sorted(VALID_RUNTIMES))}"
            )

        # Per-subagent overrides from file only
        raw_overrides = runtimes_section.get("overrides", {}) or {}
        overrides: dict[str, str] = {}
        for role, rt in raw_overrides.items():
            rt_lower = rt.lower()
            if rt_lower not in VALID_RUNTIMES:
                raise SystemExit(
                    f"[praxis] fatal: unknown runtime {rt!r} for "
                    f"subagent '{role}' in convergence.yaml.\n"
                    f"Valid runtimes: {', '.join(sorted(VALID_RUNTIMES))}"
                )
            overrides[role] = rt_lower

        # Local runtime settings: env var > file > defaults
        local_base_url = os.environ.get(
            "PRAXIS_LOCAL_BASE_URL",
            local_section.get("base_url", "http://localhost:11434"),
        )
        local_model = os.environ.get(
            "PRAXIS_LOCAL_MODEL",
            local_section.get("model", "llama3.1:8b"),
        )

        # Cloud runtime settings: env var > file > defaults
        cloud_base_url = os.environ.get(
            "PRAXIS_CLOUD_BASE_URL",
            cloud_section.get("base_url", "https://api.openai.com/v1"),
        )
        cloud_model = os.environ.get(
            "PRAXIS_CLOUD_MODEL",
            cloud_section.get("model", "gpt-4o"),
        )

        # Task-type routing rules
        task_types_section = data.get("task_types", {}) or {}
        task_type_rules: dict[str, TaskTypeRule] = {}
        for task_type_name, rule_data in task_types_section.items():
            if not isinstance(rule_data, dict):
                continue
            rt = rule_data.get("runtime", "")
            if rt and rt.lower() not in VALID_RUNTIMES:
                raise SystemExit(
                    f"[praxis] fatal: unknown runtime {rt!r} for task_type "
                    f"'{task_type_name}' in convergence.yaml.\n"
                    f"Valid runtimes: {', '.join(sorted(VALID_RUNTIMES))}"
                )
            task_type_rules[task_type_name] = TaskTypeRule(
                runtime=rt.lower() if rt else "",
                model=rule_data.get("model"),
            )

        # Per-subagent mode overrides
        agents_section = data.get("agents", {}) or {}
        agent_modes: dict[str, str] = {}
        for agent_name, agent_data in agents_section.items():
            if isinstance(agent_data, dict) and "mode" in agent_data:
                agent_modes[agent_name] = str(agent_data["mode"])

        return cls(
            default_runtime=default_runtime,
            overrides=overrides,
            local_base_url=local_base_url,
            local_model=local_model,
            cloud_base_url=cloud_base_url,
            cloud_model=cloud_model,
            task_type_rules=task_type_rules,
            agent_modes=agent_modes,
        )

    def needs_local(self) -> bool:
        """Whether any route requires the local runtime."""
        return (
            self.default_runtime == "local"
            or "local" in self.overrides.values()
            or any(r.runtime == "local" for r in self.task_type_rules.values())
        )

    def needs_claude(self) -> bool:
        """Whether any route requires the claude runtime."""
        return (
            self.default_runtime == "claude"
            or "claude" in self.overrides.values()
            or any(r.runtime == "claude" for r in self.task_type_rules.values())
        )

    def needs_cloud(self) -> bool:
        """Whether any route requires the cloud runtime."""
        return (
            self.default_runtime == "cloud"
            or "cloud" in self.overrides.values()
            or any(r.runtime == "cloud" for r in self.task_type_rules.values())
        )

    def runtime_for(self, subagent_name: str) -> str:
        """Return the runtime name for a given subagent."""
        return self.overrides.get(subagent_name, self.default_runtime)

    def runtime_for_task_type(self, task_type: str) -> str:
        """Return runtime name for a detected task type.

        Priority: exact task_type match → "default" rule → default_runtime.
        Returns default_runtime if no task_type_rules are configured.
        """
        rule = self.task_type_rules.get(task_type)
        if rule and rule.runtime:
            return rule.runtime
        default_rule = self.task_type_rules.get("default")
        if default_rule and default_rule.runtime:
            return default_rule.runtime
        return self.default_runtime

    def model_for_task_type(self, task_type: str) -> str | None:
        """Return model override for a detected task type, or None."""
        rule = self.task_type_rules.get(task_type)
        if rule and rule.model:
            return rule.model
        default_rule = self.task_type_rules.get("default")
        if default_rule and default_rule.model:
            return default_rule.model
        return None

    def mode_for(self, agent_name: str) -> str | None:
        """Return mode override for a subagent from convergence.yaml, or None."""
        return self.agent_modes.get(agent_name)
