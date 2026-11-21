"""enforcement.py — in-process §5 + mode enforcement for every runtime.

A thin caller of praxis.policy.evaluate (SG2). The Claude Code hook calls the same
function once the owner installs scripts/hooks/escalation-boundary.py, so the two layers
cannot drift. The rule names below are re-exported for existing importers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from praxis.policy import (  # noqa: F401  (re-exports)
    BYPASS_RE,
    EXTERNAL_URL_RE,
    LOCALHOST_RE,
    MUTATING_FILE_TOOLS,
    NETWORK_CMD_RE,
    NETWORK_TOOLS,
    EnforcementError,
    ToolCall,
    context_from_env,
    evaluate,
)
from praxis.policy import workspace_root as _workspace_root  # noqa: F401

if TYPE_CHECKING:
    from praxis.modes.base import Mode


def _allowed_domains() -> frozenset[str]:
    """Return PRAXIS_ALLOWED_DOMAINS plus convergence.yaml allowed_domains."""
    from praxis.config import resolve_allowed_domains

    return resolve_allowed_domains()


def enforce(
    tool_name: str,
    tool_input: dict[str, Any],
    mode: "Mode | None" = None,
) -> None:
    """Raise EnforcementError(reason) if policy.evaluate blocks the call; return None if allowed.

    Raises EnforcementError when PRAXIS_WORKSPACE_ROOT is unset (fail closed).
    """
    verdict = evaluate(ToolCall(tool_name, tool_input), context_from_env(mode))
    if not verdict.allowed:
        raise EnforcementError(verdict.reason)
