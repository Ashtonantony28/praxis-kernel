#!/usr/bin/env python3
"""PreToolUse hook enforcing the §5 Praxis escalation boundary.

A thin caller of praxis.policy.evaluate (SG2), the same engine runtime/enforcement.enforce()
uses, so the hook and the in-process layer give identical verdicts. It fails closed: an
unset PRAXIS_WORKSPACE_ROOT, unreadable or malformed input, or a policy engine that cannot
be imported all block the call.

Blocked calls exit 2 with a stderr reason; the orchestrator must escalate to the human per
§5 rather than retry.

Installed by the owner at .claude/hooks/escalation-boundary.py (see STATUS.md, NEEDS HUMAN).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def block(reason: str) -> None:
    sys.stderr.write(
        f"BLOCKED by §5 escalation boundary: {reason}\n"
        "Escalate to the human per §5 — do not retry without approval.\n"
    )
    sys.exit(2)


def _load_engine():
    try:
        import yaml  # noqa: F401  (from site-packages, before the repo joins sys.path)
    except ImportError:
        pass
    repo = str(Path(__file__).resolve().parents[2])
    if repo not in sys.path:
        sys.path.append(repo)  # used only when praxis is not installed; installed packages win
    from praxis.policy import ToolCall, context_from_env, evaluate

    return ToolCall, context_from_env, evaluate


def main() -> None:
    if not os.environ.get("PRAXIS_WORKSPACE_ROOT"):
        block(
            "PRAXIS_WORKSPACE_ROOT is unset. The control plane refuses to run without an "
            "explicit workspace root. Set it in .claude/settings.json (env block) or export it "
            "before launching Claude Code."
        )
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        block("malformed hook input (not JSON)")
    if not isinstance(event, dict) or not isinstance(event.get("tool_name"), str):
        block("malformed hook input (no tool_name)")
    args = event.get("tool_input")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        block("malformed hook input (tool_input is not an object)")
    try:
        ToolCall, context_from_env, evaluate = _load_engine()
        verdict = evaluate(ToolCall(event["tool_name"], args), context_from_env())
    except Exception as exc:  # fail closed
        block(f"policy engine unavailable ({type(exc).__name__}: {exc})")
    if not verdict.allowed:
        block(f"{verdict.reason} [{verdict.rule}]")
    sys.exit(0)


if __name__ == "__main__":
    main()
