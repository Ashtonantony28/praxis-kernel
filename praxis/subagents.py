"""Parse .claude/agents/*.md into SubagentDef objects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MODEL_MAP: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-haiku-4-5",
    "opus": "claude-opus-4-6",
}


@dataclass(frozen=True)
class SubagentDef:
    name: str
    description: str
    tools: list[str]
    model: str  # full model ID
    system_prompt: str  # markdown body after frontmatter
    mode: str | None = None  # optional mode override from frontmatter


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML frontmatter from markdown body.

    Returns (frontmatter_dict, body_text).
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
    if not match:
        raise ValueError("No YAML frontmatter found (expected --- delimiters)")

    raw_yaml = match.group(1)
    body = match.group(2).strip()

    # Simple key: value parser — sufficient for our flat frontmatter.
    front: dict[str, str] = {}
    for line in raw_yaml.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            front[key.strip()] = value.strip()
    return front, body


def parse_agent_file(path: Path) -> SubagentDef:
    """Parse a single agent markdown file into a SubagentDef."""
    content = path.read_text()
    front, body = _parse_frontmatter(content)

    tools_str = front.get("tools", "")
    tools = [t.strip() for t in tools_str.split(",") if t.strip()]

    model_short = front.get("model", "sonnet")
    model = MODEL_MAP.get(model_short, model_short)

    mode_str = front.get("mode")  # None if not present

    return SubagentDef(
        name=front["name"],
        description=front.get("description", ""),
        tools=tools,
        model=model,
        system_prompt=body,
        mode=mode_str,
    )


def load_subagents(agents_dir: Path) -> dict[str, SubagentDef]:
    """Load all agent definitions from a directory of .md files."""
    defs: dict[str, SubagentDef] = {}
    for path in sorted(agents_dir.glob("*.md")):
        agent = parse_agent_file(path)
        defs[agent.name] = agent
    return defs
