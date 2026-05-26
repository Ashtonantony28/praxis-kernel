"""Dependency management integration via pip and pip-audit subprocess."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Callable

from ..config import Config
from ..tools import _subprocess_env, _redact_secrets

SCHEMAS: dict[str, dict[str, Any]] = {
    "Dependencies": {
        "name": "Dependencies",
        "description": (
            "Check dependency health. "
            "Actions: outdated (list outdated packages), "
            "audit (check for known vulnerabilities via pip-audit)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["outdated", "audit"],
                    "description": "Dependency check to perform",
                },
            },
            "required": ["action"],
        },
    },
}


def execute_dependencies(args: dict[str, Any], config: Config) -> str:
    action = args.get("action", "")

    if action == "outdated":
        pip_bin = shutil.which("pip")
        if pip_bin is None:
            return "Error: pip not found"
        try:
            result = subprocess.run(
                [pip_bin, "list", "--outdated", "--format=json"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(config.workspace_root),
                env=_subprocess_env(config),
            )
        except subprocess.TimeoutExpired:
            return "Error: pip timed out"
        if result.returncode != 0:
            return f"Error: pip failed: {result.stderr.strip()}"
        output = result.stdout.strip() or "[]"
        return _redact_secrets(output)

    elif action == "audit":
        audit_bin = shutil.which("pip-audit")
        if audit_bin is None:
            return "Error: pip-audit not installed. Install with: pip install pip-audit"
        try:
            result = subprocess.run(
                [audit_bin, "--format=json"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(config.workspace_root),
                env=_subprocess_env(config),
            )
        except subprocess.TimeoutExpired:
            return "Error: pip-audit timed out"
        # pip-audit returns non-zero if vulnerabilities found — still valid output
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        return _redact_secrets(output)

    else:
        return f"Error: unknown Dependencies action '{action}'"


IMPLEMENTATIONS: dict[str, Callable[[dict[str, Any], Config], str]] = {
    "Dependencies": execute_dependencies,
}
