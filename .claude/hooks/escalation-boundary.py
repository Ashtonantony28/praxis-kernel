#!/usr/bin/env python3
"""PreToolUse hook enforcing the §5 Praxis escalation boundary.

Blocks any tool call that would:
  - write or edit a file outside WORKSPACE_ROOT,
  - write or edit anywhere under WORKSPACE_ROOT/.claude (the control plane),
  - reach the network — WebFetch/WebSearch are blocked outright, and Bash
    commands invoking curl/wget/nc/ssh/etc. are blocked unless they only
    target localhost.

Blocked calls exit 2 with a stderr reason; the orchestrator must escalate
to the human per §5 rather than retry.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

def _require_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(
            f"BLOCKED by §5 escalation boundary: {name} is unset.\n"
            "The control plane refuses to run without an explicit workspace "
            "root — silent misconfiguration is the failure mode this hook "
            "exists to prevent. Set it in .claude/settings.json (env block) "
            "or export it before launching Claude Code.\n"
        )
        sys.exit(2)
    return Path(value).resolve()


WORKSPACE_ROOT = _require_env("PRAXIS_WORKSPACE_ROOT")
MEMORY_ROOT = _require_env("PRAXIS_MEMORY_ROOT")
CONTROL_PLANE = WORKSPACE_ROOT / ".claude"
ALLOWED_DOMAINS: frozenset[str] = frozenset()

NETWORK_TOOLS = {"WebFetch", "WebSearch"}
MUTATING_FILE_TOOLS = {"Write", "Edit", "NotebookEdit"}

NETWORK_CMD_RE = re.compile(
    r"\b(curl|wget|nc|ncat|netcat|ssh|scp|sftp|rsync|ftp|telnet)\b"
)
EXTERNAL_URL_RE = re.compile(
    r"https?://(?!(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]))"
)
LOCALHOST_RE = re.compile(r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0|::1)\b")

# -- Path extraction (handles quoted paths with spaces) ----------------------
_PATH_TOKEN_RE = re.compile(
    r'"(/[^"]*)"'
    r"|'(/[^']*)'"
    r"|(?:(?<=\s)|(?:^))(/[^\s;|&\"']+)"
)
_REDIRECT_RE = re.compile(r"(?:>>?|<>)")
_DESTRUCTIVE_CMD_RE = re.compile(
    r"\b(?:rm|mv|cp|chmod|chown|tee|truncate|install|ln)\b"
)
_SED_INPLACE_CMD_RE = re.compile(r"\bsed\b[^|;&]*?-i")


def block(reason: str) -> "None":
    sys.stderr.write(
        f"BLOCKED by §5 escalation boundary: {reason}\n"
        "Escalate to the human per §5 — do not retry without approval.\n"
    )
    sys.exit(2)


def resolve(p: str) -> Path:
    path = Path(p)
    if not path.is_absolute():
        path = Path(os.getcwd()) / path
    return path.resolve()


def is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def check_file_path(raw: str, tool: str) -> None:
    resolved = resolve(raw)
    if not is_under(resolved, WORKSPACE_ROOT):
        block(
            f"{tool} would write outside WORKSPACE_ROOT "
            f"({WORKSPACE_ROOT}): {resolved}"
        )
    if is_under(resolved, CONTROL_PLANE):
        block(
            f"{tool} would modify the control plane ({CONTROL_PLANE}): "
            f"{resolved}"
        )


def _segment_after(cmd: str, end: int) -> str:
    """Return command text from *end* to the next pipe/semicolon/ampersand."""
    rest = cmd[end:]
    boundary = re.search(r"[|;&]", rest)
    return rest[: boundary.start()] if boundary else rest


def _extract_paths(segment: str) -> list[str]:
    """Extract absolute-path tokens, handling quoted paths with spaces."""
    return [
        next(g for g in m.groups() if g is not None)
        for m in _PATH_TOKEN_RE.finditer(segment)
    ]


def check_bash(cmd: str) -> None:
    net_match = NETWORK_CMD_RE.search(cmd)
    if net_match:
        targets_external = bool(EXTERNAL_URL_RE.search(cmd))
        targets_localhost_only = (
            bool(LOCALHOST_RE.search(cmd)) and not targets_external
        )
        if not targets_localhost_only:
            block(
                f"Bash command invokes network egress "
                f"({net_match.group(0)}); ALLOWED_DOMAINS is empty"
            )

    candidate_paths: list[str] = []

    for m in _REDIRECT_RE.finditer(cmd):
        candidate_paths.extend(
            _extract_paths(_segment_after(cmd, m.end()))
        )

    for m in _DESTRUCTIVE_CMD_RE.finditer(cmd):
        candidate_paths.extend(
            _extract_paths(_segment_after(cmd, m.end()))
        )

    for m in _SED_INPLACE_CMD_RE.finditer(cmd):
        candidate_paths.extend(
            _extract_paths(_segment_after(cmd, m.end()))
        )

    for raw in candidate_paths:
        resolved = resolve(raw)
        if not is_under(resolved, WORKSPACE_ROOT):
            block(f"Bash command would write outside WORKSPACE_ROOT: {raw}")
        if is_under(resolved, CONTROL_PLANE):
            block(f"Bash command would modify the control plane: {raw}")


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool = event.get("tool_name", "")
    args = event.get("tool_input") or {}

    if tool in NETWORK_TOOLS:
        block(f"{tool} requires network egress; ALLOWED_DOMAINS is empty")

    if tool in MUTATING_FILE_TOOLS:
        path = args.get("file_path") or args.get("notebook_path")
        if isinstance(path, str) and path:
            check_file_path(path, tool)

    if tool == "Bash":
        cmd = args.get("command", "")
        if isinstance(cmd, str) and cmd.strip():
            check_bash(cmd)

    sys.exit(0)


if __name__ == "__main__":
    main()
