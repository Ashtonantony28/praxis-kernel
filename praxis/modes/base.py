"""Mode dataclass — tool-capability bundle for runtime-agnostic permission filtering."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from praxis.policy import ToolCall

# Allow-list sentinel that matches no tool: an intersection that came out empty.
_NOTHING = "<none>"


def _split(entry: str) -> tuple[str, str | None]:
    """Split "tool:action" (or legacy "tool.action") into (tool, action)."""
    for sep in (":", "."):
        if sep in entry:
            tool, action = entry.split(sep, 1)
            return tool, action
    return entry, None


def _matches(entries: frozenset[str], tool: str, action: str | None) -> bool:
    for entry in entries:
        etool, eaction = _split(entry)
        if eaction is None:
            if etool == tool:
                return True
        elif action == eaction and etool.lower() == tool.lower():
            return True
    return False


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

    def permits(self, call: "ToolCall") -> bool:
        """Return True if *call* passes this mode's allow and deny lists.

        Entries are "tool" (every action) or "tool:action"; qualified entries
        match the tool name case-insensitively.
        """
        if _matches(self.denied_tools, call.name, call.action):
            return False
        return not self.allowed_tools or _matches(self.allowed_tools, call.name, call.action)

    def restrict(self, child: "Mode | None") -> "Mode":
        """Return the effective mode for a child run under self: never wider than self."""
        if child is None:
            return self
        if self.allowed_tools and child.allowed_tools:
            allowed = frozenset(
                e for e in self.allowed_tools if _matches(child.allowed_tools, *_split(e))
            ) | frozenset(
                e for e in child.allowed_tools if _matches(self.allowed_tools, *_split(e))
            )
            allowed = allowed or frozenset({_NOTHING})
        else:
            allowed = self.allowed_tools or child.allowed_tools
        suffix = self.prompt_suffix
        if child.prompt_suffix and child.prompt_suffix != suffix:
            suffix += child.prompt_suffix
        return Mode(
            name=self.name if child.name == self.name else f"{self.name}>{child.name}",
            allowed_tools=allowed,
            denied_tools=self.denied_tools | child.denied_tools,
            prompt_suffix=suffix,
            requires_confirmation=self.requires_confirmation or child.requires_confirmation,
            model_override=child.model_override or self.model_override,
        )

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
