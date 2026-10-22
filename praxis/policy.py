"""policy.py — shared §5 + mode policy contract.

SG1: evaluate() is an adapter that reproduces the current decision logic of
runtime/enforcement.enforce() and returns stable rule ids. SG2 replaces its
internals; the types and signatures here are the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import urlparse

from praxis.runtime import enforcement as _e

if TYPE_CHECKING:
    from praxis.modes.base import Mode

ORIGINS: frozenset[str] = frozenset(
    {"cli", "queue", "slack", "email", "webhook", "mcp", "telegram", "whatsapp"}
)


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: Mapping[str, Any]

    @property
    def action(self) -> str | None:
        action = self.input.get("action")
        return action if isinstance(action, str) and action else None


@dataclass(frozen=True)
class PolicyContext:
    workspace_root: Path
    allowed_domains: frozenset[str]
    mode: "Mode | None"
    origin: str = "cli"


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    rule: str
    reason: str


ALLOW = Verdict(True, "allow", "")


def _block(rule: str, reason: str) -> Verdict:
    return Verdict(False, rule, reason)


def _resolve(raw: str, root: Path) -> Path:
    path = Path(raw)
    return (path if path.is_absolute() else root / path).resolve()


def _control_plane(path: Path, root: Path) -> bool:
    return _e._is_under(path, root / ".claude") or _e._is_under(path, root / ".praxis" / "hooks")


def _check_file(raw: str, tool: str, root: Path) -> Verdict:
    resolved = _resolve(raw, root)
    if not _e._is_under(resolved, root):
        return _block("write.outside_workspace", f"{tool} would write outside WORKSPACE_ROOT: {resolved}")
    if _control_plane(resolved, root):
        return _block("write.control_plane", f"{tool} would modify the control plane: {resolved}")
    return ALLOW


def _check_bash(cmd: str, root: Path) -> Verdict:
    net = _e.NETWORK_CMD_RE.search(cmd)
    if net:
        external = bool(_e.EXTERNAL_URL_RE.search(cmd))
        if not (_e.LOCALHOST_RE.search(cmd) and not external):
            return _block("bash.network", f"Bash command invokes network egress ({net.group(0)})")
    if _e.BYPASS_RE.search(cmd):
        return _block("bash.interpreter_write", "interpreter -c/-e with file-write pattern")
    candidates: list[str] = []
    for regex in (_e._REDIRECT_RE, _e._DESTRUCTIVE_CMD_RE, _e._SED_INPLACE_CMD_RE):
        for m in regex.finditer(cmd):
            candidates.extend(_e._extract_paths(_e._segment_after(cmd, m.end())))
    for raw in candidates:
        resolved = _resolve(raw, root)
        if resolved in _e._SAFE_DEVICE_PATHS:
            continue
        if not _e._is_under(resolved, root):
            return _block("bash.write_outside_workspace", f"Bash command would write outside WORKSPACE_ROOT: {raw}")
        if _control_plane(resolved, root):
            return _block("bash.control_plane", f"Bash command would modify the control plane: {raw}")
    return ALLOW


def _check_domain(tool: str, url: Any, allowed: frozenset[str], require_url: bool) -> Verdict:
    if not url:
        if require_url:
            return _block("net.no_url", f"{tool} requires network egress and has no url")
        return ALLOW
    domain = urlparse(str(url)).hostname or ""
    if (require_url and not domain) or (domain and domain not in allowed):
        return _block("net.domain_not_allowed", f"{tool} domain '{domain}' not in ALLOWED_DOMAINS")
    return ALLOW


def evaluate(call: ToolCall, ctx: PolicyContext) -> Verdict:
    """Return the policy verdict for *call* under *ctx*.

    Mirrors enforce() except that relative paths resolve against
    ctx.workspace_root rather than the process cwd.
    """
    name, args, root = call.name, call.input, ctx.workspace_root

    if name in _e.NETWORK_TOOLS:
        return _check_domain(name, args.get("url", ""), ctx.allowed_domains, require_url=True)

    if name in _e.MUTATING_FILE_TOOLS:
        path = args.get("file_path") or args.get("notebook_path")
        if isinstance(path, str) and path:
            verdict = _check_file(path, name, root)
            if not verdict.allowed:
                return verdict

    if name == "Bash":
        cmd = args.get("command", "")
        if isinstance(cmd, str) and cmd.strip():
            verdict = _check_bash(cmd, root)
            if not verdict.allowed:
                return verdict

    if name == "WebResearch":
        verdict = _check_domain(name, args.get("url", ""), ctx.allowed_domains, require_url=False)
        if not verdict.allowed:
            return verdict

    if ctx.mode is not None and name in ctx.mode.denied_tools:
        return _block("mode.denied", f"Tool '{name}' is denied in '{ctx.mode.name}' mode")
    return ALLOW


def context_from_env(mode: "Mode | None" = None, origin: str = "cli") -> PolicyContext:
    """Build a PolicyContext from PRAXIS_WORKSPACE_ROOT / PRAXIS_ALLOWED_DOMAINS.

    Raises EnforcementError when PRAXIS_WORKSPACE_ROOT is unset.
    """
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin: {origin!r}")
    return PolicyContext(
        workspace_root=_e._workspace_root(),
        allowed_domains=_e._allowed_domains(),
        mode=mode,
        origin=origin,
    )


__all__ = ["ToolCall", "PolicyContext", "Verdict", "evaluate", "context_from_env", "ORIGINS"]
