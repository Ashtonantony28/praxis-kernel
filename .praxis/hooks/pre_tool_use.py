#!/usr/bin/env python3
"""PreToolUse hook enforcing the §5 Praxis escalation boundary.

Blocks any tool call that would:
  - write or edit a file outside WORKSPACE_ROOT;
  - write or edit anywhere in the control plane —
    `.claude/` (except `.claude/agents/`) and `.praxis/hooks/`;
  - reach the network — WebFetch/WebSearch are blocked outright, and
    Bash commands invoking curl/wget/nc/ssh/scp/sftp/rsync/ftp/telnet
    are blocked unless they only target localhost.

Blocked calls exit 2 with a stderr reason; the orchestrator must
escalate to the human per §5 rather than retry.

This hook is a *defense-in-depth* layer over the OS-level sandbox.
Regex-based Bash inspection has known gaps (e.g. writes performed
inside `python -c` strings are invisible to it); the sandbox is the
authoritative boundary.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

WORKSPACE_ROOT = Path("/home/user/LinuxAgenticClaudeOS").resolve()

CONTROL_PLANE_ROOTS: tuple[Path, ...] = (
    WORKSPACE_ROOT / ".claude",
    WORKSPACE_ROOT / ".praxis" / "hooks",
)
CONTROL_PLANE_EXEMPTIONS: tuple[Path, ...] = (
    WORKSPACE_ROOT / ".claude" / "agents",
)

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

DESTRUCTIVE_CMD_RE = re.compile(
    r"\b(?:rm|mv|cp|chmod|chown|tee|truncate|install|ln|dd|touch|mkdir|rmdir)\b"
    r"([^|;&\n]*)"
)
REDIRECT_PATH_RE = re.compile(r"(?:>>?|<>)\s*([^\s;|&'\"]+)")
SED_INPLACE_RE = re.compile(r"\bsed\b[^|;&\n]*?\s-i\b([^|;&\n]*)")
ABSOLUTE_PATH_RE = re.compile(r"(/[^\s;|&'\"<>]+)")


def block(reason: str) -> None:
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


def is_control_plane(path: Path) -> bool:
    for exempt in CONTROL_PLANE_EXEMPTIONS:
        if is_under(path, exempt):
            return False
    for root in CONTROL_PLANE_ROOTS:
        if is_under(path, root):
            return True
    return False


def check_file_path(raw: str, tool: str) -> None:
    resolved = resolve(raw)
    if not is_under(resolved, WORKSPACE_ROOT):
        block(
            f"{tool} would write outside WORKSPACE_ROOT "
            f"({WORKSPACE_ROOT}): {resolved}"
        )
    if is_control_plane(resolved):
        block(f"{tool} would modify the control plane: {resolved}")


def strip_quoted(cmd: str) -> str:
    """Blank out single- and double-quoted substrings.

    Avoids false positives where a destructive or network command name
    appears inside a literal string (e.g. a commit message arg). The
    OS-level sandbox is the authoritative defense against paths hidden
    inside quotes; this hook only inspects unquoted command surface.
    """
    cmd = re.sub(r"'[^']*'", " ", cmd)
    cmd = re.sub(r'"[^"]*"', " ", cmd)
    return cmd


def check_bash_paths(cmd: str) -> None:
    surface = strip_quoted(cmd)
    candidate_paths: list[str] = []

    for match in DESTRUCTIVE_CMD_RE.finditer(surface):
        tail = match.group(1)
        candidate_paths += ABSOLUTE_PATH_RE.findall(tail)

    candidate_paths += REDIRECT_PATH_RE.findall(surface)

    for match in SED_INPLACE_RE.finditer(surface):
        candidate_paths += ABSOLUTE_PATH_RE.findall(match.group(1))

    for raw in candidate_paths:
        if not raw.startswith("/"):
            continue
        resolved = resolve(raw)
        if not is_under(resolved, WORKSPACE_ROOT):
            block(f"Bash command would write outside WORKSPACE_ROOT: {raw}")
        if is_control_plane(resolved):
            block(f"Bash command would modify the control plane: {raw}")


def check_bash_network(cmd: str) -> None:
    surface = strip_quoted(cmd)
    net_match = NETWORK_CMD_RE.search(surface)
    if not net_match:
        return
    targets_external = bool(EXTERNAL_URL_RE.search(surface))
    targets_localhost_only = (
        bool(LOCALHOST_RE.search(surface)) and not targets_external
    )
    if targets_localhost_only:
        return
    block(
        f"Bash command invokes network egress "
        f"({net_match.group(0)}); ALLOWED_DOMAINS is empty"
    )


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
            check_bash_network(cmd)
            check_bash_paths(cmd)

    sys.exit(0)


if __name__ == "__main__":
    main()
