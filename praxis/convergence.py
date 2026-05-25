"""Convergence config — multi-runtime routing from convergence.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_RUNTIMES = {"claude", "local"}


@dataclass(frozen=True)
class ConvergenceConfig:
    """Parsed convergence.yaml — controls which runtime each role uses."""

    default_runtime: str = "claude"
    overrides: dict[str, str] = field(default_factory=dict)
    local_base_url: str = "http://localhost:11434"
    local_model: str = "llama3.1:8b"

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

        return cls(
            default_runtime=default_runtime,
            overrides=overrides,
            local_base_url=local_base_url,
            local_model=local_model,
        )

    def needs_local(self) -> bool:
        """Whether any route requires the local runtime."""
        return self.default_runtime == "local" or "local" in self.overrides.values()

    def needs_claude(self) -> bool:
        """Whether any route requires the claude runtime."""
        return self.default_runtime == "claude" or "claude" in self.overrides.values()

    def runtime_for(self, subagent_name: str) -> str:
        """Return the runtime name for a given subagent."""
        return self.overrides.get(subagent_name, self.default_runtime)
