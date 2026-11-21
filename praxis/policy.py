"""policy.py — the one §5 + mode policy engine (SG2).

evaluate() is the single decision function. runtime/enforcement.enforce() and
.claude/hooks/escalation-boundary.py are thin callers, so every runtime and the
Claude Code hook give the same verdict for the same call (tests/test_policy_parity.py).

The module imports only the standard library at load time: the hook imports it in a
fresh interpreter per tool call. Because the hook runs this code, the engine files are
part of the control plane (CONTROL_FILES) and agents cannot edit them.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import urlparse

if TYPE_CHECKING:
    from praxis.modes.base import Mode

ORIGINS: frozenset[str] = frozenset(
    {"cli", "queue", "slack", "email", "webhook", "mcp", "telegram", "whatsapp"}
)


class EnforcementError(Exception):
    """Raised when a tool call violates the §5 boundary (re-exported by runtime.enforcement)."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: Mapping[str, Any]

    @property
    def action(self) -> str | None:
        action = self.input.get("action") if isinstance(self.input, Mapping) else None
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

# ---------------------------------------------------------------------------
# Tool sets
# ---------------------------------------------------------------------------

MUTATING_FILE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "NotebookEdit"})
NETWORK_TOOLS: frozenset[str] = frozenset({"WebFetch", "WebSearch"})
# Read-only tools confined to WORKSPACE_ROOT, with the input keys that name a path.
READ_TOOLS: Mapping[str, tuple[str, ...]] = {
    "Read": ("file_path",),
    "NotebookRead": ("notebook_path",),
    "Grep": ("path",),
    "Glob": ("path",),
    "LS": ("path",),
}

# ---------------------------------------------------------------------------
# Control plane and secrets
# ---------------------------------------------------------------------------

CONTROL_DIRS: tuple[str, ...] = (".claude", ".praxis/hooks", ".praxis/security")
# The engine itself (the hook imports these) and the file that widens egress and trust.
CONTROL_FILES: tuple[str, ...] = (
    "praxis/__init__.py",
    "praxis/policy.py",
    "praxis/config.py",
    "praxis/runtime/enforcement.py",
    "convergence.yaml",
)

SECRET_NAMES: frozenset[str] = frozenset({
    ".env", ".netrc", ".pgpass", ".git-credentials", ".pypirc", ".npmrc",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials.json",
})
SECRET_SUFFIXES: tuple[str, ...] = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
SECRET_DIRS: frozenset[str] = frozenset({".ssh", ".aws", ".gnupg", ".docker"})
ENV_TEMPLATES: frozenset[str] = frozenset({".env.example", ".env.sample", ".env.template"})

# ---------------------------------------------------------------------------
# Bash patterns
# ---------------------------------------------------------------------------

NETWORK_CMD_RE = re.compile(
    r"(?<![\w./-])(curl|wget|nc|ncat|netcat|socat|ssh|scp|sftp|rsync|ftp|telnet|nslookup|dig|whois)\b"
    r"|\bgit\s+(?:clone|fetch|pull|push|ls-remote|remote\s+update|submodule\s+update)\b"
    r"|\b(?:pip3?|uv\s+pip|npm|pnpm|yarn)\s+(?:install|add|i|ci|download)\b"
    r"|/dev/(?:tcp|udp)/"
)
EXTERNAL_URL_RE = re.compile(
    r"https?://(?!(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]))"
)
LOCALHOST_RE = re.compile(r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0|::1)\b")
URL_RE = re.compile(r"https?://[^\s'\"`;|&<>()]+")
INTERPRETER_RE = re.compile(
    r"\b(?:python[23]?(?:\.\d+)?|perl|ruby|node|php|bash|sh|zsh)\b[^|;&\n]*\s-[A-Za-z]*[ce]\b"
)

_REDIRECT_RE = re.compile(r"(?:>>?|<>)")
_DESTRUCTIVE_CMD_RE = re.compile(
    r"\b(?:rm|rmdir|unlink|shred|mv|cp|chmod|chown|tee|truncate|install|ln|dd|touch|mkdir)\b"
)
_SED_INPLACE_CMD_RE = re.compile(r"\b(?:sed\b[^|;&]*?-i|perl\b[^|;&]*?-[A-Za-z]*i)")
_TOKEN_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'|([^\s;|&<>()\'"`=]+)')

# Subprocess-write bypass pattern: interpreter -c/-e with file-write function
BYPASS_RE = re.compile(
    r"\b(python[23]?|perl|ruby|node)\b[^|;&\n]*-[ce]\b[^|;&\n]*(?:open|write|writeFile)",
    re.IGNORECASE,
)

# Device paths that are always safe to write to
_SAFE_DEVICE_PATHS: frozenset[Path] = frozenset(
    Path(p).resolve() for p in ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin")
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _block(rule: str, reason: str) -> Verdict:
    return Verdict(False, rule, reason)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve(raw: str, root: Path) -> Path:
    """Resolve *raw* (``~`` and ``$VAR`` expanded) against *root*."""
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    return (path if path.is_absolute() else root / path).resolve()


def is_control_plane(path: Path, root: Path) -> bool:
    return any(_is_under(path, root / d) for d in CONTROL_DIRS) or any(
        path == root / f for f in CONTROL_FILES
    )


def is_secret(path: Path, root: Path) -> bool:
    name = path.name
    if name in SECRET_NAMES or name.endswith(SECRET_SUFFIXES):
        return True
    if name.startswith(".env.") and name not in ENV_TEMPLATES:
        return True
    rel = path.relative_to(root) if _is_under(path, root) else path
    return any(part in SECRET_DIRS for part in rel.parts) or _is_under(path, root / ".praxis" / "security")


def _check_write(raw: str, tool: str, root: Path, prefix: str) -> Verdict:
    resolved = _resolve(raw, root)
    if resolved in _SAFE_DEVICE_PATHS:
        return ALLOW
    if not _is_under(resolved, root):
        return _block(f"{prefix}.write_outside_workspace" if prefix == "bash" else "write.outside_workspace",
                      f"{tool} would write outside WORKSPACE_ROOT ({root}): {raw}")
    if is_control_plane(resolved, root):
        return _block(f"{prefix}.control_plane", f"{tool} would modify the control plane: {resolved}")
    if is_secret(resolved, root):
        return _block(f"{prefix}.secret", f"{tool} would write a secrets file: {resolved}")
    return ALLOW


def _check_read(raw: str, tool: str, root: Path) -> Verdict:
    resolved = _resolve(raw, root)
    if not _is_under(resolved, root):
        return _block("read.outside_workspace", f"{tool} would read outside WORKSPACE_ROOT ({root}): {raw}")
    if is_secret(resolved, root):
        return _block("read.secret", f"{tool} would read a secrets file: {resolved}")
    return ALLOW


def _tokens(text: str) -> list[str]:
    return [next(g for g in m.groups() if g is not None) for m in _TOKEN_RE.finditer(text)]


def _segment_after(cmd: str, end: int) -> str:
    """Return command text from *end* up to the next pipe/semicolon/ampersand/newline."""
    rest = cmd[end:]
    boundary = re.search(r"[|;&\n]", rest)
    return rest[: boundary.start()] if boundary else rest


def _write_targets(segment: str) -> list[str]:
    """Path-like tokens of a write segment (options and bare words without a slash skipped)."""
    out = []
    for tok in _tokens(segment):
        if not tok or tok.startswith("-") or tok.isdigit():
            continue
        out.append(tok)
    return out


def _host_allowed(host: str, allowed: frozenset[str]) -> bool:
    return bool(host) and (host in allowed or bool(LOCALHOST_RE.fullmatch(host)))


def _check_bash(cmd: str, root: Path, allowed: frozenset[str]) -> Verdict:
    net = NETWORK_CMD_RE.search(cmd)
    if net:
        external = bool(EXTERNAL_URL_RE.search(cmd))
        if not (LOCALHOST_RE.search(cmd) and not external):
            return _block("bash.network", f"Bash command invokes network egress ({net.group(0).strip()})")
    if INTERPRETER_RE.search(cmd):
        for url in URL_RE.findall(cmd):
            host = urlparse(url).hostname or ""
            if not _host_allowed(host, allowed):
                return _block("bash.network", f"Bash interpreter reaches '{host}', not in ALLOWED_DOMAINS")
    if BYPASS_RE.search(cmd):
        return _block("bash.interpreter_write", "Bash command uses interpreter -c/-e with file-write pattern (potential bypass)")

    for tok in _tokens(cmd):
        if tok and not tok.startswith("-"):
            if is_secret(_resolve(tok, root), root):
                return _block("bash.secret", f"Bash command touches a secrets file: {tok}")

    for regex in (_REDIRECT_RE, _DESTRUCTIVE_CMD_RE, _SED_INPLACE_CMD_RE):
        for m in regex.finditer(cmd):
            for raw in _write_targets(_segment_after(cmd, m.end())):
                if regex is not _REDIRECT_RE and "/" not in raw and not raw.startswith(("~", "$", "..")):
                    if raw not in CONTROL_FILES:
                        continue  # bare word in the cwd: a file name or an argument
                if "$" in os.path.expandvars(raw):
                    return _block("bash.unresolved_target", f"Bash write target has an unset variable: {raw}")
                verdict = _check_write(raw, "Bash command", root, "bash")
                if not verdict.allowed:
                    return verdict
                if regex is _REDIRECT_RE:
                    break  # only the token right after > is the target
    return ALLOW


def _check_domain(tool: str, url: Any, allowed: frozenset[str], require_url: bool) -> Verdict:
    if not url:
        if require_url:
            return _block("net.no_url", f"{tool} requires network egress and has no url "
                                        f"(ALLOWED_DOMAINS enforcement is active)")
        return ALLOW
    domain = urlparse(str(url)).hostname or ""
    if (require_url and not domain) or (domain and domain not in allowed):
        return _block("net.domain_not_allowed",
                      f"{tool} requires network egress; domain '{domain}' not in PRAXIS_ALLOWED_DOMAINS")
    return ALLOW


def _path_arg(args: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[bool, str | None]:
    """(well_formed, path): a key that is present must hold a non-empty string."""
    for key in keys:
        if key in args and args[key] is not None:
            value = args[key]
            if not isinstance(value, str) or not value:
                return False, None
            return True, value
    return True, None


def _evaluate(call: ToolCall, ctx: PolicyContext) -> Verdict:
    name, args, root = call.name, call.input, ctx.workspace_root
    if not isinstance(name, str) or not isinstance(args, Mapping):
        return _block("input.malformed", "tool call has no name or its input is not an object")

    if name in NETWORK_TOOLS:
        verdict = _check_domain(name, args.get("url", ""), ctx.allowed_domains, require_url=True)
        if not verdict.allowed:
            return verdict

    if name in MUTATING_FILE_TOOLS:
        ok, path = _path_arg(args, ("file_path", "notebook_path"))
        if not ok or path is None:
            return _block("input.malformed", f"{name} needs a file path string")
        verdict = _check_write(path, name, root, "write")
        if not verdict.allowed:
            return verdict

    if name in READ_TOOLS:
        ok, path = _path_arg(args, READ_TOOLS[name])
        if not ok:
            return _block("input.malformed", f"{name} path must be a string")
        if path is not None:
            verdict = _check_read(path, name, root)
            if not verdict.allowed:
                return verdict
        pattern = args.get("pattern") if name == "Glob" else args.get("glob")
        if isinstance(pattern, str) and (".." in Path(pattern).parts or pattern.startswith(("/", "~"))):
            return _block("read.outside_workspace", f"{name} pattern leaves WORKSPACE_ROOT: {pattern}")

    if name == "Wiki" and call.action == "ingest":
        source = args.get("source")
        if isinstance(source, str) and "\n" not in source and (
            source.startswith(("/", "~", ".")) or "/" in source
        ):
            verdict = _check_read(source, "Wiki ingest", root)
            if not verdict.allowed:
                return verdict

    if name == "Bash":
        cmd = args.get("command", "")
        if not isinstance(cmd, str):
            return _block("input.malformed", "Bash command must be a string")
        if cmd.strip():
            verdict = _check_bash(cmd, root, ctx.allowed_domains)
            if not verdict.allowed:
                return verdict

    if name == "WebResearch":
        verdict = _check_domain(name, args.get("url", ""), ctx.allowed_domains, require_url=False)
        if not verdict.allowed:
            return verdict

    if ctx.mode is not None and not ctx.mode.permits(call):
        label = f"{name}:{call.action}" if call.action else name
        return _block("mode.denied", f"Tool '{label}' is denied in '{ctx.mode.name}' mode")
    return ALLOW


def evaluate(call: ToolCall, ctx: PolicyContext) -> Verdict:
    """Return the policy verdict for *call* under *ctx*. Never raises: an error blocks.

    Relative paths resolve against ctx.workspace_root, not the process cwd.
    """
    try:
        return _evaluate(call, ctx)
    except Exception as exc:  # fail closed
        return _block("policy.error", f"policy check failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

_origin: contextvars.ContextVar[str] = contextvars.ContextVar("praxis_policy_origin", default="cli")


def set_origin(origin: str) -> contextvars.Token:
    """Set the origin for policy contexts built in this context (queue_runner, per task)."""
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin: {origin!r}")
    return _origin.set(origin)


def reset_origin(token: contextvars.Token) -> None:
    _origin.reset(token)


@contextlib.contextmanager
def origin_scope(origin: str):
    """Policy contexts built inside this block carry *origin* (Task.origin -> PolicyContext.origin)."""
    token = set_origin(origin if origin in ORIGINS else "queue")
    try:
        yield
    finally:
        _origin.reset(token)


def workspace_root() -> Path:
    value = os.environ.get("PRAXIS_WORKSPACE_ROOT")
    if not value:
        raise EnforcementError("PRAXIS_WORKSPACE_ROOT is unset")
    return Path(value).resolve()


def context_from_env(mode: "Mode | None" = None, origin: str | None = None) -> PolicyContext:
    """Build a PolicyContext from PRAXIS_WORKSPACE_ROOT / allowed domains / the current origin.

    Raises EnforcementError when PRAXIS_WORKSPACE_ROOT is unset.
    """
    origin = _origin.get() if origin is None else origin
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin: {origin!r}")
    from praxis.config import resolve_allowed_domains

    return PolicyContext(
        workspace_root=workspace_root(),
        allowed_domains=resolve_allowed_domains(),
        mode=mode,
        origin=origin,
    )


__all__ = [
    "ToolCall", "PolicyContext", "Verdict", "EnforcementError", "evaluate", "context_from_env",
    "set_origin", "reset_origin", "origin_scope", "ORIGINS",
]
