"""praxis/agents/loader.py — load AgentDefinition from YAML files in praxis/agents/."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Model alias map (same as subagents.py)
MODEL_MAP: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}

REQUIRED_FIELDS = {"name", "model", "mode", "prompt", "tools"}


@dataclass(frozen=True)
class AgentDefinition:
    """A cross-runtime subagent definition loaded from praxis/agents/*.yaml."""
    name: str
    model: str          # full model ID (after alias resolution)
    mode: str           # "plan" or "build"
    prompt: str         # full system prompt text
    tools: list[str]    # list of tool names
    background: bool = False
    model_alias: str = ""  # original alias before resolution (for serialization)


def _load_yaml(path: Path) -> AgentDefinition:
    """Parse a single YAML file into an AgentDefinition. Raises ValueError on malformed input."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Agent file {path.name} must be a YAML mapping, got {type(data).__name__}")

    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise ValueError(f"Agent file {path.name} missing required fields: {sorted(missing)}")

    name = str(data["name"])
    model_alias = str(data["model"])
    model = MODEL_MAP.get(model_alias, model_alias)  # resolve alias or pass through
    mode = str(data["mode"])
    prompt = str(data["prompt"])

    raw_tools = data["tools"]
    if not isinstance(raw_tools, list):
        raise ValueError(f"Agent file {path.name}: 'tools' must be a list")
    tools = [str(t) for t in raw_tools]

    background = bool(data.get("background", False))

    return AgentDefinition(
        name=name,
        model=model,
        mode=mode,
        prompt=prompt,
        tools=tools,
        background=background,
        model_alias=model_alias,
    )


def _agents_dir() -> Path:
    """Return the canonical praxis/agents/ directory."""
    return Path(__file__).resolve().parent


def load(name: str) -> AgentDefinition:
    """Load a single agent by name from praxis/agents/{name}.yaml.

    Raises FileNotFoundError if the agent file doesn't exist.
    Raises ValueError if the file is malformed.
    """
    path = _agents_dir() / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"No agent definition found for '{name}' at {path}")
    return _load_yaml(path)


def load_all() -> list[AgentDefinition]:
    """Load all agent definitions from praxis/agents/*.yaml.

    Returns a list sorted by agent name.
    Raises ValueError if any file is malformed.
    """
    agents_dir = _agents_dir()
    results = []
    for path in sorted(agents_dir.glob("*.yaml")):
        results.append(_load_yaml(path))
    return results
