"""Linear integration — read-safe / write-escalate.

Read actions (list_issues, get_issue, list_teams) call the Linear GraphQL API.
Write actions (create_issue, update_issue, add_comment) are STAGED to
.praxis/staging/external_actions.jsonl for human review via --approve.

Requires PRAXIS_LINEAR_API_KEY and api.linear.app in PRAXIS_ALLOWED_DOMAINS.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..config import Config

_LINEAR_API_URL = "https://api.linear.app/graphql"
_LINEAR_DOMAIN = "api.linear.app"
_STAGING_FILENAME = "external_actions.jsonl"


def _get_api_key() -> str | None:
    return os.environ.get("PRAXIS_LINEAR_API_KEY")


def _check_domain(config: Config) -> str | None:
    if _LINEAR_DOMAIN not in config.allowed_domains:
        return (
            f"linear: domain '{_LINEAR_DOMAIN}' not in PRAXIS_ALLOWED_DOMAINS. "
            f"Add it to enable Linear API access."
        )
    return None


def _linear_query(query: str, variables: dict, api_key: str) -> str:
    """Execute a GraphQL query against the Linear API."""
    from ..tools import _redact_secrets

    body = json.dumps({"query": query, "variables": variables}).encode()
    req = Request(
        _LINEAR_API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=15) as resp:
            return _redact_secrets(resp.read().decode("utf-8", errors="replace"))
    except URLError as exc:
        return _redact_secrets(f"linear API error: {exc}")
    except Exception as exc:
        return _redact_secrets(f"linear request failed: {exc}")


def _stage_action(provider: str, action: str, params: dict, workspace_root: Path) -> str:
    """Append a staged action to external_actions.jsonl."""
    staging_dir = workspace_root / ".praxis" / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / _STAGING_FILENAME
    entry = {
        "id": str(uuid.uuid4()),
        "provider": provider,
        "action": action,
        "params": params,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    with staging_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return (
        f"Staged {provider} '{action}' action for human review. "
        f"Run 'python -m praxis --approve' to review and execute. "
        f"(Staged to: {staging_file})"
    )


_LIST_ISSUES_QUERY = """
query ListIssues($teamId: String, $first: Int) {
  issues(filter: { team: { id: { eq: $teamId } } }, first: $first) {
    nodes { id title state { name } priority assignee { name } createdAt }
  }
}
"""

_GET_ISSUE_QUERY = """
query GetIssue($id: String!) {
  issue(id: $id) {
    id title description state { name } priority
    assignee { name email } createdAt updatedAt
    comments { nodes { id body createdAt user { name } } }
  }
}
"""

_LIST_TEAMS_QUERY = """
query ListTeams {
  teams { nodes { id name key description } }
}
"""


def _list_issues(args: dict[str, Any], config: Config) -> str:
    api_key = _get_api_key()
    if not api_key:
        return "linear: PRAXIS_LINEAR_API_KEY not set."
    err = _check_domain(config)
    if err:
        return err
    variables: dict[str, Any] = {"first": int(args.get("first", 20))}
    team_id = args.get("team_id")
    if team_id:
        variables["teamId"] = team_id
    return _linear_query(_LIST_ISSUES_QUERY, variables, api_key)


def _get_issue(args: dict[str, Any], config: Config) -> str:
    api_key = _get_api_key()
    if not api_key:
        return "linear: PRAXIS_LINEAR_API_KEY not set."
    err = _check_domain(config)
    if err:
        return err
    issue_id = args.get("issue_id", "")
    if not issue_id:
        return "linear get_issue: 'issue_id' is required."
    return _linear_query(_GET_ISSUE_QUERY, {"id": issue_id}, api_key)


def _list_teams(args: dict[str, Any], config: Config) -> str:
    api_key = _get_api_key()
    if not api_key:
        return "linear: PRAXIS_LINEAR_API_KEY not set."
    err = _check_domain(config)
    if err:
        return err
    return _linear_query(_LIST_TEAMS_QUERY, {}, api_key)


def _create_issue(args: dict[str, Any], config: Config) -> str:
    params = {
        "title": args.get("title", ""),
        "description": args.get("description", ""),
        "team_id": args.get("team_id", ""),
    }
    return _stage_action("linear", "create_issue", params, config.workspace_root)


def _update_issue(args: dict[str, Any], config: Config) -> str:
    params = {
        "issue_id": args.get("issue_id", ""),
        "updates": args.get("params", {}),
    }
    return _stage_action("linear", "update_issue", params, config.workspace_root)


def _add_comment(args: dict[str, Any], config: Config) -> str:
    params = {
        "issue_id": args.get("issue_id", ""),
        "body": args.get("body", ""),
    }
    return _stage_action("linear", "add_comment", params, config.workspace_root)


def _execute_linear(args: dict[str, Any], config: Config) -> str:
    action = args.get("action", "")
    dispatch = {
        "list_issues": _list_issues,
        "get_issue": _get_issue,
        "list_teams": _list_teams,
        "create_issue": _create_issue,
        "update_issue": _update_issue,
        "add_comment": _add_comment,
    }
    fn = dispatch.get(action)
    if fn is None:
        return f"linear: unknown action '{action}'. Valid: {', '.join(dispatch)}."
    return fn(args, config)


SCHEMAS: dict[str, dict[str, Any]] = {
    "linear": {
        "name": "linear",
        "description": (
            "Linear project management integration. Read actions call the API directly. "
            "Write actions (create_issue, update_issue, add_comment) are STAGED for "
            "human review — run 'python -m praxis --approve' to execute them. "
            "Requires PRAXIS_LINEAR_API_KEY and api.linear.app in PRAXIS_ALLOWED_DOMAINS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_issues",
                        "get_issue",
                        "list_teams",
                        "create_issue",
                        "update_issue",
                        "add_comment",
                    ],
                },
                "team_id": {
                    "type": "string",
                    "description": "Linear team ID.",
                },
                "issue_id": {
                    "type": "string",
                    "description": "Linear issue ID.",
                },
                "first": {
                    "type": "integer",
                    "description": "Max issues to list (default 20).",
                },
                "title": {
                    "type": "string",
                    "description": "Issue title (for create_issue).",
                },
                "description": {
                    "type": "string",
                    "description": "Issue description (for create_issue).",
                },
                "params": {
                    "type": "object",
                    "description": "Update params (for update_issue).",
                },
                "body": {
                    "type": "string",
                    "description": "Comment body (for add_comment).",
                },
            },
            "required": ["action"],
        },
    }
}

IMPLEMENTATIONS: dict[str, Any] = {
    "linear": _execute_linear,
}
