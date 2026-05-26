"""Test runner integration via pytest subprocess."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Callable

from ..config import Config
from ..tools import _subprocess_env, _redact_secrets

SCHEMAS: dict[str, dict[str, Any]] = {
    "TestRunner": {
        "name": "TestRunner",
        "description": (
            "Run pytest and return results. "
            "Actions: run (run tests), run_failed (re-run last failures)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "run_failed"],
                    "description": "Test action to perform",
                },
                "path": {
                    "type": "string",
                    "description": "Test file or directory (default: tests/)",
                },
                "marker": {
                    "type": "string",
                    "description": "pytest marker expression (e.g. 'not slow')",
                },
                "keyword": {
                    "type": "string",
                    "description": "pytest keyword expression (-k filter)",
                },
            },
            "required": ["action"],
        },
    },
}


def execute_testrunner(args: dict[str, Any], config: Config) -> str:
    action = args.get("action", "")

    pytest_bin = shutil.which("pytest")
    if pytest_bin is None:
        return "Error: pytest not installed. Install with: pip install pytest"

    cmd = [pytest_bin]

    if action == "run":
        path = args.get("path", "tests/")
        cmd.extend([path, "-v", "--tb=short", "-q"])
    elif action == "run_failed":
        cmd.extend(["--lf", "-v", "--tb=short"])
    else:
        return f"Error: unknown TestRunner action '{action}'"

    marker = args.get("marker")
    if marker:
        cmd.extend(["-m", marker])

    keyword = args.get("keyword")
    if keyword:
        cmd.extend(["-k", keyword])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(config.workspace_root),
            env=_subprocess_env(config),
        )
    except subprocess.TimeoutExpired:
        return "Error: pytest timed out after 300s"

    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout.strip())
    if result.stderr:
        parts.append(result.stderr.strip())
    if result.returncode != 0:
        parts.append(f"Exit code: {result.returncode}")

    return _redact_secrets("\n".join(parts) or "(no output)")


IMPLEMENTATIONS: dict[str, Callable[[dict[str, Any], Config], str]] = {
    "TestRunner": execute_testrunner,
}
