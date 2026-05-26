"""GitHub integration via `gh` CLI subprocess."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Callable

from ..config import Config
from ..tools import _subprocess_env, _redact_secrets

SCHEMAS: dict[str, dict[str, Any]] = {
    "GitHub": {
        "name": "GitHub",
        "description": (
            "Interact with GitHub via the gh CLI. "
            "Actions: pr_list, pr_view, issue_list, issue_view, pr_diff."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pr_list", "pr_view", "issue_list", "issue_view", "pr_diff"],
                    "description": "The GitHub operation to perform",
                },
                "number": {
                    "type": "integer",
                    "description": "PR or issue number (required for pr_view, issue_view, pr_diff)",
                },
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "merged", "all"],
                    "description": "Filter by state (for list actions, default: open)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max items to return (for list actions, default: 20)",
                },
            },
            "required": ["action"],
        },
    },
}


def _run_gh(args: list[str], config: Config, timeout: int = 30) -> str:
    """Run a gh CLI command and return output."""
    gh = shutil.which("gh")
    if gh is None:
        return (
            "Error: GitHub CLI (gh) not installed. "
            "Install from https://cli.github.com"
        )

    try:
        result = subprocess.run(
            [gh, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(config.workspace_root),
            env=_subprocess_env(config),
        )
    except subprocess.TimeoutExpired:
        return f"Error: gh command timed out after {timeout}s"

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "auth login" in stderr or "authentication" in stderr.lower():
            return "Error: GitHub CLI not authenticated. Run: gh auth login"
        return f"Error: gh failed: {stderr}"

    return _redact_secrets(result.stdout.strip() or "(no output)")


def execute_github(args: dict[str, Any], config: Config) -> str:
    action = args.get("action", "")
    number = args.get("number")
    state = args.get("state", "open")
    limit = args.get("limit", 20)

    if action == "pr_list":
        return _run_gh(
            ["pr", "list", "--state", state, "--limit", str(limit),
             "--json", "number,title,state,author,url"],
            config,
        )
    elif action == "pr_view":
        if number is None:
            return "Error: 'number' is required for pr_view"
        return _run_gh(
            ["pr", "view", str(number),
             "--json", "number,title,body,state,reviews,comments"],
            config,
        )
    elif action == "issue_list":
        return _run_gh(
            ["issue", "list", "--state", state, "--limit", str(limit),
             "--json", "number,title,state,labels,url"],
            config,
        )
    elif action == "issue_view":
        if number is None:
            return "Error: 'number' is required for issue_view"
        return _run_gh(
            ["issue", "view", str(number),
             "--json", "number,title,body,state,comments"],
            config,
        )
    elif action == "pr_diff":
        if number is None:
            return "Error: 'number' is required for pr_diff"
        return _run_gh(["pr", "diff", str(number)], config)
    else:
        return f"Error: unknown GitHub action '{action}'"


IMPLEMENTATIONS: dict[str, Callable[[dict[str, Any], Config], str]] = {
    "GitHub": execute_github,
}
