"""PreToolUse hook integration — runs escalation-boundary.py per §5."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from .config import Config


@dataclass
class HookResult:
    allowed: bool
    reason: str | None = None


def _log_denial(
    config: Config, tool_name: str, tool_input: dict[str, Any], reason: str
) -> None:
    """Append a denied tool call to .praxis/security/denials.jsonl (best-effort)."""
    from datetime import datetime, timezone

    try:
        log_dir = config.workspace_root / ".praxis" / "security"
        log_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_name": tool_name,
            "tool_input": tool_input,
            "reason": reason,
        }
        with (log_dir / "denials.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # denial logging must never crash the hook path


def run_pretool_hook(
    config: Config, tool_name: str, tool_input: dict[str, Any]
) -> HookResult:
    """Invoke the §5 escalation-boundary hook before a tool call.

    Returns HookResult with allowed=True if the tool may proceed.
    """
    if not config.hook_path.exists():
        return HookResult(allowed=True)

    event = json.dumps({"tool_name": tool_name, "tool_input": tool_input})

    env = {**os.environ}
    env["PRAXIS_WORKSPACE_ROOT"] = str(config.workspace_root)
    env["PRAXIS_MEMORY_ROOT"] = str(config.memory_root)

    try:
        result = subprocess.run(
            [sys.executable, str(config.hook_path)],
            input=event,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return HookResult(allowed=False, reason="Hook timed out")

    if result.returncode == 0:
        return HookResult(allowed=True)

    reason = result.stderr.strip() if result.stderr else f"Hook exited {result.returncode}"
    _log_denial(config, tool_name, tool_input, reason)
    return HookResult(allowed=False, reason=reason)
