"""Slack integration — outbound notifications + local message/approval staging.

Notify action sends Praxis-to-user alerts via incoming webhook (autonomous).
Stage action composes user-attributed messages locally — NEVER sends them.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from ..tools import _redact_secrets

SCHEMAS: dict[str, dict[str, Any]] = {
    "Slack": {
        "name": "Slack",
        "description": (
            "Slack integration — send Praxis notifications via webhook (autonomous) "
            "and stage user-attributed messages/approval requests locally (write-escalate). "
            "Actions: notify, stage_message, list_staged, post_approval_request, "
            "get_approval, list_approvals."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "notify",
                        "stage_message",
                        "list_staged",
                        "post_approval_request",
                        "get_approval",
                        "list_approvals",
                    ],
                    "description": "The Slack operation to perform",
                },
                "message": {
                    "type": "string",
                    "description": "Body text for notify or stage_message actions",
                },
                "title": {
                    "type": "string",
                    "description": "Optional bold header for notify or post_approval_request",
                },
                "channel": {
                    "type": "string",
                    "description": "Optional channel override for notify (e.g. #general)",
                },
                "recipient": {
                    "type": "string",
                    "description": "@user or #channel for stage_message",
                },
                "subject": {
                    "type": "string",
                    "description": "Optional label/subject for stage_message",
                },
                "description": {
                    "type": "string",
                    "description": "Full description for post_approval_request",
                },
                "approval_id": {
                    "type": "string",
                    "description": (
                        "Optional UUID for post_approval_request; "
                        "required for get_approval"
                    ),
                },
                "status": {
                    "type": "string",
                    "description": (
                        "Optional filter for list_approvals: "
                        "'pending', 'approved', or 'rejected'"
                    ),
                },
            },
            "required": ["action"],
        },
    },
}

# ---------- Domain check helpers (mirrors web.py pattern) ----------

SLACK_WEBHOOK_DOMAIN = "hooks.slack.com"


def _extract_domain(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return parsed.hostname or ""


def _check_domain(domain: str, config: Config) -> str | None:
    if not domain:
        return "Error: could not extract domain from URL"
    if domain not in config.allowed_domains:
        return (
            f"Error: domain '{domain}' not in PRAXIS_ALLOWED_DOMAINS. "
            f"Add it to the PRAXIS_ALLOWED_DOMAINS env var to allow access."
        )
    return None


# ---------- Internal webhook helper ----------


def _notify_webhook(
    message: str,
    title: str | None,
    channel: str | None,
    config: Config,
) -> str:
    """POST a message to the configured Slack incoming webhook.

    Used by both _notify and _post_approval_request.
    Returns a plain-string result (already redacted before returning).
    """
    url = os.environ.get("PRAXIS_SLACK_WEBHOOK_URL", "")
    if not url:
        return (
            "Error: PRAXIS_SLACK_WEBHOOK_URL not set. "
            "Set it to your Slack incoming webhook URL."
        )

    domain = _extract_domain(url)
    domain_err = _check_domain(domain, config)
    if domain_err is not None:
        return domain_err

    # Build payload
    if title:
        payload: dict[str, Any] = {"text": f"*{title}*\n{message}"}
    else:
        payload = {"text": message}
    if channel:
        payload["channel"] = channel

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")

    result: str
    try:
        urllib.request.urlopen(req, timeout=15)
        result = "Notification sent successfully."
    except urllib.error.HTTPError as exc:
        result = f"Error: Slack webhook returned HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        result = f"Error: could not reach Slack webhook: {exc.reason}"
    except TimeoutError:
        result = "Error: Slack webhook request timed out"

    return _redact_secrets(result)


# ---------- Action handlers ----------


def _notify(args: dict[str, Any], config: Config) -> str:
    """Send a Praxis-to-user notification via the incoming webhook (autonomous)."""
    message = args.get("message", "").strip()
    if not message:
        return "Error: 'message' is required for notify."
    return _notify_webhook(message, args.get("title"), args.get("channel"), config)


def _stage_message(args: dict[str, Any], config: Config) -> str:
    """Compose a user-attributed Slack message locally — NEVER sends it."""
    recipient = args.get("recipient", "").strip()
    if not recipient:
        return "Error: 'recipient' is required for stage_message."
    message = args.get("message", "").strip()
    if not message:
        return "Error: 'message' is required for stage_message."

    msg_id = str(uuid.uuid4())
    staging_dir = (
        config.workspace_root / ".praxis" / "staging" / "slack" / "messages"
    )
    staging_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "id": msg_id,
        "created_at": datetime.now().isoformat(),
        "recipient": recipient,
        "subject": args.get("subject"),
        "message": message,
        "status": "staged",
    }
    (staging_dir / f"{msg_id}.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )

    return _redact_secrets(
        f"Message staged to .praxis/staging/slack/messages/{msg_id}.json"
        " — review and send manually. This message will NOT be sent automatically."
    )


def _list_staged(args: dict[str, Any], config: Config) -> str:
    """List all staged Slack messages (read-only)."""
    staging_dir = (
        config.workspace_root / ".praxis" / "staging" / "slack" / "messages"
    )
    if not staging_dir.exists():
        return "No staged messages."

    files = sorted(staging_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return "No staged messages."

    lines: list[str] = []
    for f in files:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            preview = r.get("subject") or r["message"][:50]
            lines.append(
                f"- {r['id']} | {r['created_at']} | to: {r['recipient']} | {preview}"
            )
        except Exception:
            lines.append(f"- {f.name} (unreadable)")

    return _redact_secrets(f"Staged messages ({len(files)}):\n" + "\n".join(lines))


def _post_approval_request(args: dict[str, Any], config: Config) -> str:
    """Stage an approval request locally and optionally notify via webhook."""
    title = args.get("title", "").strip()
    if not title:
        return "Error: 'title' is required for post_approval_request."
    description = args.get("description", "").strip()
    if not description:
        return "Error: 'description' is required for post_approval_request."

    approval_id = args.get("approval_id") or str(uuid.uuid4())
    approvals_dir = (
        config.workspace_root / ".praxis" / "staging" / "slack" / "approvals"
    )
    approvals_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "id": approval_id,
        "created_at": datetime.now().isoformat(),
        "title": title,
        "description": description,
        "status": "pending",
        "responded_at": None,
        "note": None,
    }
    (approvals_dir / f"{approval_id}.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )

    notify_msg = (
        f"Approval needed: {title}\n\n"
        f"{description}\n\n"
        f"Approval ID: {approval_id}\n"
        f"Check status: praxis get_approval({approval_id})"
    )
    notify_result = _notify_webhook(notify_msg, title="Approval Request", channel=None, config=config)

    if "sent successfully" in notify_result:
        notify_status = "Notification sent."
    else:
        notify_status = f"Note: could not send notification ({notify_result})"

    return _redact_secrets(
        f"Approval request staged to .praxis/staging/slack/approvals/{approval_id}.json"
        f" (approval_id={approval_id}). {notify_status}"
    )


def _get_approval(args: dict[str, Any], config: Config) -> str:
    """Read the status of a staged approval request (read-only)."""
    approval_id = args.get("approval_id", "").strip()
    if not approval_id:
        return "Error: 'approval_id' is required for get_approval."

    path = (
        config.workspace_root
        / ".praxis"
        / "staging"
        / "slack"
        / "approvals"
        / f"{approval_id}.json"
    )
    if not path.exists():
        return f"Error: approval '{approval_id}' not found."

    try:
        r = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Error: could not read approval record: {exc}"

    lines = [
        f"Approval: {r.get('id', approval_id)}",
        f"  title:        {r.get('title', '')}",
        f"  status:       {r.get('status', '')}",
        f"  created_at:   {r.get('created_at', '')}",
        f"  responded_at: {r.get('responded_at') or '—'}",
        f"  note:         {r.get('note') or '—'}",
        f"  description:  {r.get('description', '')}",
    ]
    return _redact_secrets("\n".join(lines))


def _list_approvals(args: dict[str, Any], config: Config) -> str:
    """List all staged approval requests, optionally filtered by status (read-only)."""
    approvals_dir = (
        config.workspace_root / ".praxis" / "staging" / "slack" / "approvals"
    )
    if not approvals_dir.exists():
        return "No approval records."

    files = sorted(approvals_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return "No approval records."

    status_filter = args.get("status", "").strip() or None
    records: list[dict[str, Any]] = []
    for f in files:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            records.append(r)
        except Exception:
            pass

    if status_filter:
        records = [r for r in records if r.get("status") == status_filter]
        if not records:
            return f"No approvals with status '{status_filter}'."

    lines: list[str] = []
    for r in records:
        lines.append(
            f"- {r.get('id', '?')} | {r.get('created_at', '')} "
            f"| status: {r.get('status', '')} | {r.get('title', '')}"
        )

    return _redact_secrets(f"Approvals ({len(records)}):\n" + "\n".join(lines))


# ---------- Dispatch ----------

_ACTIONS: dict[str, Callable[[dict[str, Any], Config], str]] = {
    "notify": _notify,
    "stage_message": _stage_message,
    "list_staged": _list_staged,
    "post_approval_request": _post_approval_request,
    "get_approval": _get_approval,
    "list_approvals": _list_approvals,
}


def execute_slack(args: dict[str, Any], config: Config) -> str:
    """Dispatch a Slack action."""
    action = args.get("action", "")
    if action not in _ACTIONS:
        return f"Error: unknown Slack action '{action}'. Valid: {', '.join(_ACTIONS)}"
    return _ACTIONS[action](args, config)


IMPLEMENTATIONS: dict[str, Callable[[dict[str, Any], Config], str]] = {
    "Slack": execute_slack,
}
