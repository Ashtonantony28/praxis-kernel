"""Wiki → Notion / Linear sync integration — read-safe / write-escalate.

sync_to_notion and sync_to_linear export wiki pages and STAGE the results to
.praxis/staging/external_actions.jsonl for human review via --approve.
They never call external APIs directly.

link_linear_issue updates a wiki page's frontmatter (inside WORKSPACE_ROOT) to
record the Linear issue ID and add a typed 'relates' link. This is a local write
inside WORKSPACE_ROOT — no boundary issue.

pull_linear_updates reads the Linear API (api.linear.app, already-allowlisted)
and stages proposed wiki page updates to .praxis/staging/wiki_updates.jsonl
for human review.

§5 boundary:
- Wiki reads are inside WORKSPACE_ROOT — no boundary issues.
- All external writes go to .praxis/staging/ — no live Notion/Linear writes.
- pull_linear_updates egress is only to api.linear.app (existing allowlisted domain).
- No new egress domains required.
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
from .. import wiki as _wiki

_STAGING_FILENAME = "external_actions.jsonl"
_WIKI_UPDATES_FILENAME = "wiki_updates.jsonl"
_LINEAR_API_URL = "https://api.linear.app/graphql"
_LINEAR_DOMAIN = "api.linear.app"


def _resolve_wiki_root(wiki_root: Path | None, config: Config | None) -> Path:
    """Resolve wiki root from explicit arg, config, or environment."""
    if wiki_root is not None:
        return wiki_root
    if config is not None:
        return config.workspace_root / "wiki"
    return _wiki._wiki_root()


def _resolve_workspace_root(wiki_root: Path | None, config: Config | None) -> Path:
    """Resolve workspace root from config, wiki_root parent, or environment."""
    if config is not None:
        return config.workspace_root
    if wiki_root is not None:
        # wiki_root is {workspace}/wiki — parent is workspace
        return wiki_root.parent
    from ..config import Config as _Config
    return _Config.from_env().workspace_root


def _stage_external(provider: str, action: str, params: dict, workspace_root: Path) -> str:
    """Append a staged write action to external_actions.jsonl. Returns confirmation."""
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


# ---------------------------------------------------------------------------
# sync_to_notion
# ---------------------------------------------------------------------------


def sync_to_notion(
    page_slug: str,
    notion_parent_id: str,
    *,
    wiki_root: Path | None = None,
    config: Config | None = None,
) -> str:
    """Export a wiki page to Notion block format and stage as a create_page action.

    Read-safe / write-escalate:
    - Reads wiki/pages/{page_slug}.md freely (inside WORKSPACE_ROOT).
    - Stages a Notion create_page action to .praxis/staging/external_actions.jsonl.
    - Never calls the Notion API directly.

    Parameters
    ----------
    page_slug:
        Wiki page slug (filename without .md).
    notion_parent_id:
        Notion parent page or database ID.
    wiki_root:
        Path to wiki/ directory. Defaults to config.workspace_root/wiki or env.
    config:
        Praxis Config. Used for workspace_root path.

    Returns
    -------
    str
        Staging confirmation or error message.
    """
    resolved_wiki_root = _resolve_wiki_root(wiki_root, config)
    workspace_root = _resolve_workspace_root(wiki_root, config)

    try:
        notion_blocks = _wiki.export_notion(page_slug, wiki_root=resolved_wiki_root)
    except _wiki.WikiError as exc:
        return f"wiki_sync sync_to_notion: {exc}"
    except Exception as exc:
        return f"wiki_sync sync_to_notion: unexpected error: {exc}"

    params = {
        "parent_id": notion_parent_id,
        "title": notion_blocks["title"],
        "blocks": notion_blocks["blocks"],
        "wiki_page_slug": page_slug,
    }
    return _stage_external("notion", "create_page", params, workspace_root)


# ---------------------------------------------------------------------------
# sync_to_linear
# ---------------------------------------------------------------------------


def sync_to_linear(
    page_slug: str,
    team_id: str,
    *,
    wiki_root: Path | None = None,
    config: Config | None = None,
) -> str:
    """Export a wiki page as a Linear issue description and stage as create_issue.

    Read-safe / write-escalate:
    - Reads wiki/pages/{page_slug}.md freely (inside WORKSPACE_ROOT).
    - Stages a Linear create_issue action to .praxis/staging/external_actions.jsonl.
    - Never calls the Linear API directly.

    Parameters
    ----------
    page_slug:
        Wiki page slug (filename without .md).
    team_id:
        Linear team ID.
    wiki_root:
        Path to wiki/ directory.
    config:
        Praxis Config. Used for workspace_root path.

    Returns
    -------
    str
        Staging confirmation or error message.
    """
    resolved_wiki_root = _resolve_wiki_root(wiki_root, config)
    workspace_root = _resolve_workspace_root(wiki_root, config)

    try:
        description = _wiki.export_linear(page_slug, wiki_root=resolved_wiki_root)
        # Extract title from page frontmatter
        page_path = resolved_wiki_root / "pages" / f"{page_slug}.md"
        content = page_path.read_text(encoding="utf-8")
        meta, _ = _wiki._parse_frontmatter(content)
        title = str(meta.get("entity", page_slug))
    except _wiki.WikiError as exc:
        return f"wiki_sync sync_to_linear: {exc}"
    except Exception as exc:
        return f"wiki_sync sync_to_linear: unexpected error: {exc}"

    params = {
        "title": title,
        "description": description,
        "team_id": team_id,
        "wiki_page_slug": page_slug,
    }
    return _stage_external("linear", "create_issue", params, workspace_root)


# ---------------------------------------------------------------------------
# link_linear_issue
# ---------------------------------------------------------------------------


def link_linear_issue(
    page_slug: str,
    issue_id: str,
    *,
    wiki_root: Path | None = None,
) -> str:
    """Add a typed 'relates' link from a wiki page to a Linear issue.

    Updates wiki/pages/{page_slug}.md frontmatter:
    - Adds ``linear_issue_id: {issue_id}`` field.
    - Adds a ``relates`` typed link to the Linear issue URL in the ``links:`` list.
      If the URL is already present, it is not duplicated.

    This is a local write inside WORKSPACE_ROOT — no boundary issues.

    Parameters
    ----------
    page_slug:
        Wiki page slug (filename without .md).
    issue_id:
        Linear issue ID (e.g. "PROJ-42" or a UUID).
    wiki_root:
        Path to wiki/ directory.

    Returns
    -------
    str
        Confirmation message or error string.
    """
    if wiki_root is None:
        wiki_root = _wiki._wiki_root()

    page_path = wiki_root / "pages" / f"{page_slug}.md"
    if not page_path.exists():
        return f"wiki_sync link_linear_issue: page not found: {page_slug}"

    try:
        content = page_path.read_text(encoding="utf-8")
        meta, body = _wiki._parse_frontmatter(content)

        # Record the issue ID in frontmatter
        meta["linear_issue_id"] = issue_id

        # Construct a reference URL
        issue_url = f"https://linear.app/issue/{issue_id}"

        # Ensure links list exists
        links = meta.get("links", [])
        if not isinstance(links, list):
            links = []

        # Add relates link if not already present (deduplicate by target URL)
        existing_targets = {
            lnk.get("target", "")
            for lnk in links
            if isinstance(lnk, dict)
        }
        if issue_url not in existing_targets:
            links.append({"type": "relates", "target": issue_url})
        meta["links"] = links

        # Write back
        new_content = _wiki._render_frontmatter(meta) + "\n" + body
        page_path.write_text(new_content, encoding="utf-8")

        return (
            f"Linked wiki page '{page_slug}' to Linear issue '{issue_id}'. "
            f"Updated frontmatter: linear_issue_id={issue_id!r}, "
            f"added relates link → {issue_url}"
        )
    except Exception as exc:
        return f"wiki_sync link_linear_issue: unexpected error: {exc}"


# ---------------------------------------------------------------------------
# pull_linear_updates
# ---------------------------------------------------------------------------

_GET_ISSUE_QUERY = """
query GetIssue($id: String!) {
  issue(id: $id) {
    id title description state { name } priority
    updatedAt
    comments { nodes { id body createdAt user { name } } }
  }
}
"""


def pull_linear_updates(
    *,
    wiki_root: Path | None = None,
    config: Config | None = None,
) -> str:
    """Read Linear issue updates for wiki pages with ``linear_issue_id`` and stage proposals.

    For each wiki page that has a ``linear_issue_id`` field in its frontmatter:
    1. Queries the Linear API for the current issue state (title, status, comments).
    2. Stages an update record to ``.praxis/staging/wiki_updates.jsonl`` for human review.

    §5 boundary:
    - Reads wiki/pages/ inside WORKSPACE_ROOT — safe.
    - Egress to api.linear.app only (pre-allowlisted domain).
    - Writes only to .praxis/staging/wiki_updates.jsonl inside WORKSPACE_ROOT.

    Parameters
    ----------
    wiki_root:
        Path to wiki/ directory.
    config:
        Praxis Config. Domain allowlist is enforced if config is provided.

    Returns
    -------
    str
        Summary of pages checked and updates staged, plus any errors.
    """
    resolved_wiki_root = _resolve_wiki_root(wiki_root, config)
    workspace_root = _resolve_workspace_root(wiki_root, config)

    # Require API key
    api_key = os.environ.get("PRAXIS_LINEAR_API_KEY")
    if not api_key:
        return "pull_linear_updates: PRAXIS_LINEAR_API_KEY not set."

    # Enforce domain allowlist when config is provided
    if config is not None and _LINEAR_DOMAIN not in config.allowed_domains:
        return (
            f"pull_linear_updates: domain '{_LINEAR_DOMAIN}' not in "
            f"PRAXIS_ALLOWED_DOMAINS. Add it to enable Linear API access."
        )

    # Find wiki pages with linear_issue_id
    pages_dir = resolved_wiki_root / "pages"
    if not pages_dir.exists():
        return "pull_linear_updates: No wiki pages found."

    linked_pages: list[dict] = []
    for page_file in sorted(pages_dir.glob("*.md")):
        try:
            content = page_file.read_text(encoding="utf-8")
            meta, _ = _wiki._parse_frontmatter(content)
            issue_id = meta.get("linear_issue_id")
            if issue_id:
                linked_pages.append({
                    "slug": page_file.stem,
                    "issue_id": str(issue_id),
                })
        except Exception:
            continue

    if not linked_pages:
        return "pull_linear_updates: No wiki pages with linear_issue_id found."

    # Stage updates
    staging_dir = workspace_root / ".praxis" / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    wiki_updates_file = staging_dir / _WIKI_UPDATES_FILENAME

    staged_count = 0
    errors: list[str] = []

    for page_info in linked_pages:
        slug = page_info["slug"]
        issue_id = page_info["issue_id"]

        try:
            body_bytes = json.dumps({
                "query": _GET_ISSUE_QUERY,
                "variables": {"id": issue_id},
            }).encode()
            req = Request(
                _LINEAR_API_URL,
                data=body_bytes,
                method="POST",
                headers={
                    "Authorization": api_key,
                    "Content-Type": "application/json",
                },
            )
            with urlopen(req, timeout=15) as resp:
                response_text = resp.read().decode("utf-8", errors="replace")

            response_data = json.loads(response_text)
            issue_data = response_data.get("data", {}).get("issue")
            if not issue_data:
                errors.append(f"{slug}: issue '{issue_id}' not found in Linear response")
                continue

            current_state = (issue_data.get("state") or {}).get("name", "")
            current_title = issue_data.get("title", "")
            comments_nodes = (issue_data.get("comments") or {}).get("nodes", [])

            entry = {
                "id": str(uuid.uuid4()),
                "page_slug": slug,
                "linear_issue_id": issue_id,
                "current_state": current_state,
                "current_title": current_title,
                "comment_count": len(comments_nodes),
                "latest_comments": comments_nodes[-3:] if comments_nodes else [],
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
            }
            with wiki_updates_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            staged_count += 1

        except URLError as exc:
            errors.append(f"{slug}: Linear API error: {exc}")
        except Exception as exc:
            errors.append(f"{slug}: {exc}")

    summary_lines = [
        f"pull_linear_updates: checked {len(linked_pages)} linked page(s), staged {staged_count} update(s)."
    ]
    if errors:
        summary_lines.append(f"Errors ({len(errors)}):")
        summary_lines.extend(f"  {e}" for e in errors)
    if staged_count > 0:
        summary_lines.append(f"Review updates: python -m praxis --list-staged")
        summary_lines.append(f"(Staged to: {wiki_updates_file})")

    return "\n".join(summary_lines)


# ---------------------------------------------------------------------------
# Integration dispatch
# ---------------------------------------------------------------------------


def execute_wiki_sync(args: dict[str, Any], config: Config) -> str:
    """Dispatch a WikiSync tool call to the appropriate function."""
    action = args.get("action", "")
    wiki_root = config.workspace_root / "wiki"

    if action == "sync_to_notion":
        page_slug = args.get("page_slug", "")
        notion_parent_id = args.get("notion_parent_id", "")
        if not page_slug:
            return "wiki_sync: 'page_slug' is required for sync_to_notion."
        if not notion_parent_id:
            return "wiki_sync: 'notion_parent_id' is required for sync_to_notion."
        return sync_to_notion(page_slug, notion_parent_id, wiki_root=wiki_root, config=config)

    elif action == "sync_to_linear":
        page_slug = args.get("page_slug", "")
        team_id = args.get("team_id", "")
        if not page_slug:
            return "wiki_sync: 'page_slug' is required for sync_to_linear."
        if not team_id:
            return "wiki_sync: 'team_id' is required for sync_to_linear."
        return sync_to_linear(page_slug, team_id, wiki_root=wiki_root, config=config)

    elif action == "link_linear_issue":
        page_slug = args.get("page_slug", "")
        issue_id = args.get("issue_id", "")
        if not page_slug:
            return "wiki_sync: 'page_slug' is required for link_linear_issue."
        if not issue_id:
            return "wiki_sync: 'issue_id' is required for link_linear_issue."
        return link_linear_issue(page_slug, issue_id, wiki_root=wiki_root)

    elif action == "pull_linear_updates":
        return pull_linear_updates(wiki_root=wiki_root, config=config)

    else:
        valid = ["sync_to_notion", "sync_to_linear", "link_linear_issue", "pull_linear_updates"]
        return f"wiki_sync: unknown action '{action}'. Valid: {', '.join(valid)}."


SCHEMAS: dict[str, dict[str, Any]] = {
    "WikiSync": {
        "name": "WikiSync",
        "description": (
            "Wiki → Notion/Linear sync. "
            "Actions: sync_to_notion (exports wiki page to Notion block format — staged, never sent), "
            "sync_to_linear (exports wiki page as Linear issue — staged, never sent), "
            "link_linear_issue (records Linear issue ID + relates link in wiki page frontmatter), "
            "pull_linear_updates (reads Linear API for linked pages, stages update proposals). "
            "All external writes go to .praxis/staging/ for human review via --approve."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "sync_to_notion",
                        "sync_to_linear",
                        "link_linear_issue",
                        "pull_linear_updates",
                    ],
                    "description": "Operation to perform.",
                },
                "page_slug": {
                    "type": "string",
                    "description": "Wiki page slug (filename without .md, e.g. 'alice').",
                },
                "notion_parent_id": {
                    "type": "string",
                    "description": "Notion parent page or database ID (for sync_to_notion).",
                },
                "team_id": {
                    "type": "string",
                    "description": "Linear team ID (for sync_to_linear).",
                },
                "issue_id": {
                    "type": "string",
                    "description": "Linear issue ID (for link_linear_issue).",
                },
            },
            "required": ["action"],
        },
    }
}

IMPLEMENTATIONS: dict[str, Any] = {
    "WikiSync": execute_wiki_sync,
}
