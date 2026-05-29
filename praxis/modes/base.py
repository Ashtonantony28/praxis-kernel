"""Mode dataclass — tool-capability bundle for runtime-agnostic permission filtering."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Mode:
    """A tool-capability bundle that filters which tools a runtime may execute.

    allowed_tools: explicit allow-list (empty = all tools permitted except denied).
    denied_tools: explicit deny-list. When both are set, denied takes precedence.
    prompt_suffix: text appended to the system prompt when this mode is active.
    requires_confirmation: if True, the runtime should prompt before executing tools.
    model_override: optional model string that overrides the runtime default.
    """

    name: str
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    denied_tools: frozenset[str] = field(default_factory=frozenset)
    prompt_suffix: str = ""
    requires_confirmation: bool = False
    model_override: str | None = None

    @classmethod
    def load(cls, name: str) -> "Mode":
        """Load a Mode by name.

        Resolution order:
        1. User YAML override at <workspace_root>/praxis/modes.yaml
           (top-level ``modes:`` key, entry matching ``name``).
        2. Built-in module ``praxis.modes.{name}`` exporting a ``MODE`` constant.
        3. Raises ValueError if neither exists.
        """
        workspace_root = Path(os.environ.get("PRAXIS_WORKSPACE_ROOT", os.getcwd()))
        yaml_path = workspace_root / "praxis" / "modes.yaml"

        # 1. Try user YAML override
        if yaml_path.is_file():
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}
            modes_section = data.get("modes", {}) or {}
            if name in modes_section:
                entry = modes_section[name] or {}
                allowed_raw = entry.get("allowed_tools", []) or []
                denied_raw = entry.get("denied_tools", []) or []
                return cls(
                    name=name,
                    allowed_tools=frozenset(allowed_raw),
                    denied_tools=frozenset(denied_raw),
                    prompt_suffix=entry.get("prompt_suffix", ""),
                    requires_confirmation=bool(entry.get("requires_confirmation", False)),
                    model_override=entry.get("model_override") or None,
                )

        # 2. Try built-in module
        try:
            module = importlib.import_module(f"praxis.modes.{name}")
            candidate = getattr(module, "MODE", None)
            if isinstance(candidate, cls):
                return candidate
        except ImportError:
            pass

        # 3. Unknown mode
        raise ValueError(
            f"Unknown mode: {name!r}. Available built-ins: plan, build"
        )
