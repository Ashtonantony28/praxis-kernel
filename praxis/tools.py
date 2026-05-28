"""Tool schemas and implementations for the orchestrator."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import Config


def _subprocess_env(config: Config) -> dict[str, str]:
    """Build explicit env for subprocess calls.

    Ensures auth tokens and workspace config propagate to child processes.
    Mirrors the pattern in hooks.py.
    """
    env = {**os.environ}
    env["PRAXIS_WORKSPACE_ROOT"] = str(config.workspace_root)
    env["PRAXIS_MEMORY_ROOT"] = str(config.memory_root)
    return env


def _redact_secrets(text: str) -> str:
    """Strip auth tokens from subprocess output (§5.8 secret filtering)."""
    for var in (
        "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "GITHUB_TOKEN",
        "PRAXIS_WEB_SEARCH_API_KEY", "PRAXIS_EMAIL_PASSWORD", "PRAXIS_CALENDAR_URL",
        "PRAXIS_SLACK_WEBHOOK_URL", "PRAXIS_SLACK_BOT_TOKEN", "PRAXIS_SLACK_APP_TOKEN",
        "PRAXIS_NOTION_TOKEN", "PRAXIS_LINEAR_API_KEY",
    ):
        val = os.environ.get(var)
        if val and val in text:
            text = text.replace(val, "[REDACTED]")
    return text

# ---------- JSON schemas for the Anthropic API ----------

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "Bash": {
        "name": "Bash",
        "description": "Execute a bash command and return its output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds"},
            },
            "required": ["command"],
        },
    },
    "Read": {
        "name": "Read",
        "description": "Read a file and return its contents with line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to file"},
                "offset": {"type": "integer", "description": "Line offset (0-based)"},
                "limit": {"type": "integer", "description": "Max lines to read"},
            },
            "required": ["file_path"],
        },
    },
    "Edit": {
        "name": "Edit",
        "description": "Replace old_string with new_string in a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to file"},
                "old_string": {"type": "string", "description": "Text to find"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    "Write": {
        "name": "Write",
        "description": "Write content to a file, creating directories as needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to file"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["file_path", "content"],
        },
    },
    "Grep": {
        "name": "Grep",
        "description": "Search file contents for a regex pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "path": {"type": "string", "description": "Directory or file to search"},
            },
            "required": ["pattern"],
        },
    },
    "Glob": {
        "name": "Glob",
        "description": "Find files matching a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern"},
                "path": {"type": "string", "description": "Directory to search in"},
            },
            "required": ["pattern"],
        },
    },
    "Agent": {
        "name": "Agent",
        "description": "Spawn a named subagent to handle a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Subagent name (scout, planner, builder, verifier, scribe)",
                },
                "prompt": {"type": "string", "description": "Task for the subagent"},
            },
            "required": ["name", "prompt"],
        },
    },
}


# ---------- Tool implementations ----------


def execute_bash(args: dict[str, Any], config: Config) -> str:
    cmd = args["command"]
    timeout = args.get("timeout", 120)
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(config.workspace_root),
            env=_subprocess_env(config),
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"

    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout)
    if result.stderr:
        parts.append(f"STDERR:\n{result.stderr}")
    if result.returncode != 0:
        parts.append(f"Exit code: {result.returncode}")
    return _redact_secrets("\n".join(parts) or "(no output)")


def execute_read(args: dict[str, Any], config: Config) -> str:
    path = Path(args["file_path"])
    if not path.exists():
        return f"Error: file not found: {path}"
    lines = path.read_text().splitlines()
    offset = args.get("offset", 0)
    limit = args.get("limit", len(lines) - offset)
    selected = lines[offset : offset + limit]
    return "\n".join(f"{i + offset + 1}\t{line}" for i, line in enumerate(selected))


def execute_edit(args: dict[str, Any], config: Config) -> str:
    path = Path(args["file_path"])
    if not path.exists():
        return f"Error: file not found: {path}"
    content = path.read_text()
    old = args["old_string"]
    new = args["new_string"]
    if old not in content:
        return f"Error: old_string not found in {path}"
    count = content.count(old)
    if count > 1:
        return f"Error: old_string appears {count} times; must be unique"
    content = content.replace(old, new, 1)
    path.write_text(content)
    return f"Edited {path}"


def execute_write(args: dict[str, Any], config: Config) -> str:
    path = Path(args["file_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"])
    return f"Wrote {path}"


def execute_grep(args: dict[str, Any], config: Config) -> str:
    pattern = args["pattern"]
    search_path = args.get("path", str(config.workspace_root))
    cmd = ["grep", "-rn", "--", pattern, search_path]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=_subprocess_env(config),
        )
    except subprocess.TimeoutExpired:
        return "Grep timed out"
    return _redact_secrets(result.stdout or "(no matches)")


def execute_glob(args: dict[str, Any], config: Config) -> str:
    pattern = args["pattern"]
    base = Path(args.get("path", str(config.workspace_root)))
    matches = sorted(str(p) for p in base.glob(pattern))
    return "\n".join(matches) if matches else "(no matches)"


TOOL_IMPLEMENTATIONS: dict[str, Callable[[dict[str, Any], Config], str]] = {
    "Bash": execute_bash,
    "Read": execute_read,
    "Edit": execute_edit,
    "Write": execute_write,
    "Grep": execute_grep,
    "Glob": execute_glob,
    # Agent is dispatched by the orchestrator, not here.
}


def get_tool_schemas(tool_names: list[str] | None = None) -> list[dict[str, Any]]:
    """Return tool schemas for the given names, or all if None."""
    if tool_names is None:
        return list(TOOL_SCHEMAS.values())
    return [TOOL_SCHEMAS[n] for n in tool_names if n in TOOL_SCHEMAS]
