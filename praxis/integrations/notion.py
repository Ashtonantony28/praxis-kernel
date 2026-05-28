"""Notion integration — read-safe / write-escalate.

Read actions (search, get_page, list_databases) call the Notion API directly.
Write actions (create_page, update_page, append_block) are STAGED to
.praxis/staging/external_actions.jsonl for human review via --approve.

Requires PRAXIS_NOTION_TOKEN env var and api.notion.com in PRAXIS_ALLOWED_DOMAINS.
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

_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_DOMAIN = "api.notion.com"
_NOTION_VERSION = "2022-06-28"
_STAGING_FILENAME = "external_actions.jsonl"


def _get_token() -> str | None:
    return os.environ.get("PRAXIS_NOTION_TOKEN")


def _check_domain(config: Config) -> str | None:
    if _NOTION_DOMAIN not in config.allowed_domains:
        return (
            f"notion: domain '{_NOTION_DOMAIN}' not in PRAXIS_ALLOWED_DOMAINS. "
            f"Add it to enable Notion API access."
        )
    return None


def _notion_request(method: str, path: str, body: dict | None, token: str) -> str:
    """Make a Notion API request. Returns response JSON as string or error string."""
    from ..tools import _redact_secrets

    url = f"{_NOTION_API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": _NOTION_VERSION,
        },
    )
    try:
        with urlopen(req, timeout=15) as resp:
            return _redact_secrets(resp.read().decode("utf-8", errors="replace"))
    except URLError as exc:
        return _redact_secrets(f"notion API error: {exc}")
    except Exception as exc:
        return _redact_secrets(f"notion request failed: {exc}")


def _stage_action(provider: str, action: str, params: dict, workspace_root: Path) -> str:
    """Append a staged action to external_actions.jsonl. Return confirmation."""
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


def _search(args: dict[str, Any], config: Config) -> str:
    token = _get_token()
    if not token:
        return "notion: PRAXIS_NOTION_TOKEN not set. Set it to your Notion integration token."
    err = _check_domain(config)
    if err:
        return err
    query = args.get("query", "")
    body: dict[str, Any] = {"query": query}
    filter_type = args.get("filter_type")
    if filter_type in ("page", "database"):
        body["filter"] = {"value": filter_type, "property": "object"}
    return _notion_request("POST", "/search", body, token)


def _get_page(args: dict[str, Any], config: Config) -> str:
    token = _get_token()
    if not token:
        return "notion: PRAXIS_NOTION_TOKEN not set."
    err = _check_domain(config)
    if err:
        return err
    page_id = args.get("page_id", "")
    if not page_id:
        return "notion get_page: 'page_id' is required."
    return _notion_request("GET", f"/pages/{page_id}", None, token)


def _list_databases(args: dict[str, Any], config: Config) -> str:
    token = _get_token()
    if not token:
        return "notion: PRAXIS_NOTION_TOKEN not set."
    err = _check_domain(config)
    if err:
        return err
    return _notion_request(
        "POST",
        "/search",
        {"filter": {"value": "database", "property": "object"}},
        token,
    )


def _create_page(args: dict[str, Any], config: Config) -> str:
    params = {
        "parent_id": args.get("parent_id", ""),
        "title": args.get("title", ""),
        "content": args.get("content", ""),
    }
    return _stage_action("notion", "create_page", params, config.workspace_root)


def _update_page(args: dict[str, Any], config: Config) -> str:
    params = {
        "page_id": args.get("page_id", ""),
        "properties": args.get("properties", {}),
    }
    return _stage_action("notion", "update_page", params, config.workspace_root)


def _append_block(args: dict[str, Any], config: Config) -> str:
    params = {
        "block_id": args.get("block_id", ""),
        "content": args.get("content", ""),
    }
    return _stage_action("notion", "append_block", params, config.workspace_root)


def _execute_notion(args: dict[str, Any], config: Config) -> str:
    action = args.get("action", "")
    dispatch = {
        "search": _search,
        "get_page": _get_page,
        "list_databases": _list_databases,
        "create_page": _create_page,
        "update_page": _update_page,
        "append_block": _append_block,
    }
    fn = dispatch.get(action)
    if fn is None:
        return f"notion: unknown action '{action}'. Valid: {', '.join(dispatch)}."
    return fn(args, config)


SCHEMAS: dict[str, dict[str, Any]] = {
    "notion": {
        "name": "notion",
        "description": (
            "Notion workspace integration. Read actions call the API directly. "
            "Write actions (create_page, update_page, append_block) are STAGED for "
            "human review — run 'python -m praxis --approve' to execute them. "
            "Requires PRAXIS_NOTION_TOKEN and api.notion.com in PRAXIS_ALLOWED_DOMAINS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "search",
                        "get_page",
                        "list_databases",
                        "create_page",
                        "update_page",
                        "append_block",
                    ],
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for search action).",
                },
                "filter_type": {
                    "type": "string",
                    "enum": ["page", "database"],
                    "description": "Filter by object type (for search).",
                },
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID.",
                },
                "parent_id": {
                    "type": "string",
                    "description": "Parent page or database ID (for create_page).",
                },
                "title": {
                    "type": "string",
                    "description": "Page title (for create_page).",
                },
                "content": {
                    "type": "string",
                    "description": "Page content or block content.",
                },
                "properties": {
                    "type": "object",
                    "description": "Properties dict (for update_page).",
                },
                "block_id": {
                    "type": "string",
                    "description": "Block ID to append to.",
                },
            },
            "required": ["action"],
        },
    }
}

IMPLEMENTATIONS: dict[str, Any] = {
    "notion": _execute_notion,
}
