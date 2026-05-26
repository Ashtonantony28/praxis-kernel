"""Codebase analysis integration via pylint, radon, coverage subprocess."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Callable

from ..config import Config
from ..tools import _subprocess_env, _redact_secrets

SCHEMAS: dict[str, dict[str, Any]] = {
    "Analyze": {
        "name": "Analyze",
        "description": (
            "Run codebase analysis tools. "
            "Actions: coverage (test coverage report), "
            "complexity (cyclomatic complexity via radon), "
            "lint (pylint errors and warnings)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["coverage", "complexity", "lint"],
                    "description": "The analysis to perform",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to analyze (default: workspace root)",
                },
            },
            "required": ["action"],
        },
    },
}


def _check_tool(name: str) -> str | None:
    """Return error message if tool is not installed, None if OK."""
    if shutil.which(name) is None:
        return f"Error: {name} not installed. Install with: pip install {name}"
    return None


def execute_analyze(args: dict[str, Any], config: Config) -> str:
    action = args.get("action", "")
    path = args.get("path", str(config.workspace_root))

    if action == "coverage":
        err = _check_tool("coverage")
        if err:
            return err
        try:
            result = subprocess.run(
                ["coverage", "report", "--show-missing"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(config.workspace_root),
                env=_subprocess_env(config),
            )
        except subprocess.TimeoutExpired:
            return "Error: coverage timed out"
        if result.returncode != 0 and "No data to report" in result.stderr:
            return "Error: no coverage data. Run tests with: coverage run -m pytest"
        output = result.stdout.strip() or result.stderr.strip()
        return _redact_secrets(output)

    elif action == "complexity":
        err = _check_tool("radon")
        if err:
            return err
        try:
            result = subprocess.run(
                ["radon", "cc", path, "-s", "-a"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(config.workspace_root),
                env=_subprocess_env(config),
            )
        except subprocess.TimeoutExpired:
            return "Error: radon timed out"
        output = result.stdout.strip() or "(no output)"
        return _redact_secrets(output)

    elif action == "lint":
        err = _check_tool("pylint")
        if err:
            return err
        try:
            result = subprocess.run(
                ["pylint", path, "--output-format=text",
                 "--disable=C,R", "--score=no"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(config.workspace_root),
                env=_subprocess_env(config),
            )
        except subprocess.TimeoutExpired:
            return "Error: pylint timed out"
        # pylint returns non-zero for warnings/errors — that's normal
        output = result.stdout.strip() or "(no issues found)"
        return _redact_secrets(output)

    else:
        return f"Error: unknown Analyze action '{action}'"


IMPLEMENTATIONS: dict[str, Callable[[dict[str, Any], Config], str]] = {
    "Analyze": execute_analyze,
}
