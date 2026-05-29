"""enforcement.py — runtime-agnostic §5 boundary enforcement.

This is defense-in-depth layer 1: runs in-process before any tool fires,
mirroring the logic of .claude/hooks/escalation-boundary.py so that all
three runtimes (ClaudeCodeRuntime, OpenAIBaseRuntime/LocalRuntime,
OpenAICloudRuntime) enforce the §5 boundary even when the subprocess hook
is not in the execution path.

Do NOT modify .claude/hooks/escalation-boundary.py — that file is the
hook and this module must never alter it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from praxis.modes.base import Mode


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class EnforcementError(Exception):
    """Raised when a tool call violates the §5 boundary."""


# ---------------------------------------------------------------------------
# Tool sets (mirror the hook)
# ---------------------------------------------------------------------------

MUTATING_FILE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "NotebookEdit"})
NETWORK_TOOLS: frozenset[str] = frozenset({"WebFetch", "WebSearch"})

# ---------------------------------------------------------------------------
# Regexes (mirror the hook)
# ---------------------------------------------------------------------------

NETWORK_CMD_RE = re.compile(
    r"\b(curl|wget|nc|ncat|netcat|ssh|scp|sftp|rsync|ftp|telnet)\b"
)
EXTERNAL_URL_RE = re.compile(
    r"https?://(?!(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]))"
)
LOCALHOST_RE = re.compile(r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0|::1)\b")

_REDIRECT_RE = re.compile(r"(?:>>?|<>)")
_DESTRUCTIVE_CMD_RE = re.compile(
    r"\b(?:rm|mv|cp|chmod|chown|tee|truncate|install|ln)\b"
)
_SED_INPLACE_CMD_RE = re.compile(r"\bsed\b[^|;&]*?-i")
_PATH_TOKEN_RE = re.compile(
    r'"(/[^"]*)"'
    r"|'(/[^']*)'"
    r"|(?:(?<=\s)|(?:^))(/[^\s;|&\"']+)"
)

# Subprocess-write bypass pattern: interpreter -c/-e with file-write function
BYPASS_RE = re.compile(
    r"\b(python[23]?|perl|ruby|node)\b[^|;&\n]*-[ce]\b[^|;&\n]*(?:open|write|writeFile)",
    re.IGNORECASE,
)

# Device paths that are always safe to write to
_SAFE_DEVICE_PATHS: frozenset[Path] = frozenset(
    Path(p).resolve()
    for p in ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin")
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _workspace_root() -> Path:
    """Return PRAXIS_WORKSPACE_ROOT as a resolved Path.

    Raises EnforcementError if the env var is unset.
    """
    value = os.environ.get("PRAXIS_WORKSPACE_ROOT")
    if not value:
        raise EnforcementError("PRAXIS_WORKSPACE_ROOT is unset")
    return Path(value).resolve()


def _allowed_domains() -> frozenset[str]:
    """Return PRAXIS_ALLOWED_DOMAINS as a frozenset of domain strings."""
    raw = os.environ.get("PRAXIS_ALLOWED_DOMAINS", "")
    return frozenset(d.strip() for d in raw.split(",") if d.strip())


def _resolve(p: str) -> Path:
    """Resolve a raw path string to an absolute Path."""
    path = Path(p)
    if not path.is_absolute():
        path = Path(os.getcwd()) / path
    return path.resolve()


def _is_under(path: Path, root: Path) -> bool:
    """Return True if *path* is under *root* (inclusive of root itself)."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _segment_after(cmd: str, end: int) -> str:
    """Return command text from *end* up to the next pipe/semicolon/ampersand."""
    rest = cmd[end:]
    boundary = re.search(r"[|;&]", rest)
    return rest[: boundary.start()] if boundary else rest


def _extract_paths(segment: str) -> list[str]:
    """Extract absolute-path tokens from a command segment."""
    return [
        next(g for g in m.groups() if g is not None)
        for m in _PATH_TOKEN_RE.finditer(segment)
    ]


# ---------------------------------------------------------------------------
# Per-tool check functions
# ---------------------------------------------------------------------------

def _check_file_path(raw: str, tool_name: str, workspace_root: Path) -> None:
    """Block writes outside WORKSPACE_ROOT or into the control plane."""
    resolved = _resolve(raw)
    control_plane = workspace_root / ".claude"
    praxis_hooks = workspace_root / ".praxis" / "hooks"

    if not _is_under(resolved, workspace_root):
        raise EnforcementError(
            f"{tool_name} would write outside WORKSPACE_ROOT "
            f"({workspace_root}): {resolved}"
        )
    if _is_under(resolved, control_plane):
        raise EnforcementError(
            f"{tool_name} would modify the control plane: {resolved}"
        )
    if _is_under(resolved, praxis_hooks):
        raise EnforcementError(
            f"{tool_name} would modify the control plane: {resolved}"
        )


def _check_network_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    allowed_domains: frozenset[str],
) -> None:
    """Block WebFetch / WebSearch whose domain is not in ALLOWED_DOMAINS."""
    url = tool_input.get("url", "")
    if not url:
        raise EnforcementError(
            f"{tool_name} requires network egress; no 'url' argument provided "
            f"and PRAXIS_ALLOWED_DOMAINS enforcement is active"
        )
    domain = urlparse(str(url)).hostname or ""
    if not domain or domain not in allowed_domains:
        raise EnforcementError(
            f"{tool_name} requires network egress; domain '{domain}' not in "
            f"PRAXIS_ALLOWED_DOMAINS"
        )


def _check_bash(cmd: str, workspace_root: Path) -> None:
    """Block Bash commands that attempt network egress or write outside workspace."""
    # 1. Network commands
    net_match = NETWORK_CMD_RE.search(cmd)
    if net_match:
        targets_external = bool(EXTERNAL_URL_RE.search(cmd))
        targets_localhost_only = bool(LOCALHOST_RE.search(cmd)) and not targets_external
        if not targets_localhost_only:
            raise EnforcementError(
                f"Bash command invokes network egress "
                f"({net_match.group(0)}); ALLOWED_DOMAINS is empty"
            )

    # 2. Subprocess-write bypass (NEW — not in hook)
    if BYPASS_RE.search(cmd):
        raise EnforcementError(
            "Bash command uses interpreter -c/-e with file-write pattern "
            "(potential bypass)"
        )

    # 3. Redirect / destructive / sed-inplace path checks
    control_plane = workspace_root / ".claude"
    praxis_hooks = workspace_root / ".praxis" / "hooks"
    candidate_paths: list[str] = []

    for m in _REDIRECT_RE.finditer(cmd):
        candidate_paths.extend(_extract_paths(_segment_after(cmd, m.end())))

    for m in _DESTRUCTIVE_CMD_RE.finditer(cmd):
        candidate_paths.extend(_extract_paths(_segment_after(cmd, m.end())))

    for m in _SED_INPLACE_CMD_RE.finditer(cmd):
        candidate_paths.extend(_extract_paths(_segment_after(cmd, m.end())))

    for raw in candidate_paths:
        resolved = _resolve(raw)
        if resolved in _SAFE_DEVICE_PATHS:
            continue
        if not _is_under(resolved, workspace_root):
            raise EnforcementError(
                f"Bash command would write outside WORKSPACE_ROOT: {raw}"
            )
        if _is_under(resolved, control_plane) or _is_under(resolved, praxis_hooks):
            raise EnforcementError(
                f"Bash command would modify the control plane: {raw}"
            )


def _check_web_research(
    tool_input: dict[str, Any],
    allowed_domains: frozenset[str],
) -> None:
    """Block WebResearch fetch whose domain is not in ALLOWED_DOMAINS."""
    url = tool_input.get("url", "")
    if not url:
        return  # WebResearch may be called without a url (search action)
    domain = urlparse(str(url)).hostname or ""
    if domain and domain not in allowed_domains:
        raise EnforcementError(
            f"WebResearch fetch domain '{domain}' not in PRAXIS_ALLOWED_DOMAINS"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enforce(
    tool_name: str,
    tool_input: dict[str, Any],
    mode: "Mode | None" = None,
) -> None:
    """Check a tool call against §5 boundary rules.

    Raises EnforcementError(message) if the call is blocked.
    Returns None if allowed.
    Never silently passes a blocked action.

    Rules enforced:
      1. MUTATING_FILE_TOOLS — path must be under WORKSPACE_ROOT and not
         under the control plane (.claude/ or .praxis/hooks/).
      2. NETWORK_TOOLS (WebFetch, WebSearch) — domain must be in
         PRAXIS_ALLOWED_DOMAINS; blocked outright if no 'url' arg.
      3. Bash — network-egress commands blocked; redirect/destructive
         commands that would write outside workspace blocked; interpreter
         -c/-e file-write bypass pattern blocked.
      4. WebResearch — same domain check as NETWORK_TOOLS.
      5. Mode-based tool blocking (defense-in-depth layer 2) — runs after
         all §5 checks so §5 rules always take precedence.
    """
    workspace_root = _workspace_root()
    allowed_domains = _allowed_domains()

    if tool_name in NETWORK_TOOLS:
        _check_network_tool(tool_name, tool_input, allowed_domains)
        return  # no further checks needed for pure network tools

    if tool_name in MUTATING_FILE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if isinstance(path, str) and path:
            _check_file_path(path, tool_name, workspace_root)

    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if isinstance(cmd, str) and cmd.strip():
            _check_bash(cmd, workspace_root)

    if tool_name == "WebResearch":
        _check_web_research(tool_input, allowed_domains)

    # Mode-based tool blocking (defense-in-depth layer 2)
    # Runs after all §5 boundary checks so §5 rules always take precedence.
    if mode is not None and tool_name in mode.denied_tools:
        raise EnforcementError(
            f"Tool '{tool_name}' is denied in '{mode.name}' mode"
        )
