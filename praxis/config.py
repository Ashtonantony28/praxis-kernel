"""Configuration resolution per §0 — restrictive fallback if unset."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    workspace_root: Path
    memory_root: Path
    hook_path: Path
    allowed_domains: frozenset[str]

    @classmethod
    def from_env(cls) -> Config:
        ws = os.environ.get("PRAXIS_WORKSPACE_ROOT")
        workspace_root = Path(ws).resolve() if ws else Path.cwd().resolve()

        mem = os.environ.get("PRAXIS_MEMORY_ROOT")
        memory_root = Path(mem).resolve() if mem else workspace_root / ".praxis" / "memory"

        hook_path = workspace_root / ".claude" / "hooks" / "escalation-boundary.py"

        allowed_domains = resolve_allowed_domains()

        return cls(
            workspace_root=workspace_root,
            memory_root=memory_root,
            hook_path=hook_path,
            allowed_domains=allowed_domains,
        )


_file_domains_cache: dict[Path, tuple[int, frozenset[str]]] = {}


def resolve_allowed_domains() -> frozenset[str]:
    """PRAXIS_ALLOWED_DOMAINS plus convergence.yaml ``allowed_domains`` (as trust.py: env, then file).

    The file is read from PRAXIS_WORKSPACE_ROOT only, and cached by mtime.
    """
    raw = os.environ.get("PRAXIS_ALLOWED_DOMAINS", "")
    env = {d.strip() for d in raw.split(",") if d.strip()}
    root = os.environ.get("PRAXIS_WORKSPACE_ROOT")
    return frozenset(env | _file_domains(Path(root) / "convergence.yaml")) if root else frozenset(env)


def _file_domains(path: Path) -> frozenset[str]:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return frozenset()
    hit = _file_domains_cache.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    import yaml

    try:
        data = yaml.safe_load(path.read_text())
        raw = data.get("allowed_domains") if isinstance(data, dict) else None
    except (OSError, yaml.YAMLError):
        raw = None
    domains = frozenset(
        str(d).strip() for d in (raw if isinstance(raw, list) else []) if str(d).strip()
    )
    _file_domains_cache[path] = (mtime, domains)
    return domains
