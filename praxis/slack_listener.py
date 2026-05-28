"""Slack socket mode listener — receives commands from Slack and queues them as tasks.

Requires: pip install praxis[slack]  (installs slack_sdk>=3.0)
Auth: PRAXIS_SLACK_BOT_TOKEN (xoxb- prefix) + PRAXIS_SLACK_APP_TOKEN (xapp- prefix)
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .queue import Task, TaskQueue


class SlackSocketListener:
    """Receives Slack DMs and slash commands; queues them as Praxis tasks."""

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        workspace_root: str | Path,
        queue: TaskQueue,
    ) -> None:
        self._bot_token = bot_token
        self._app_token = app_token
        self._workspace_root = Path(workspace_root)
        self._queue = queue
        self._approvals_dir = (
            self._workspace_root / ".praxis" / "staging" / "slack" / "approvals"
        )
        self._running = False

    def start(self) -> None:
        """Connect to Slack via socket mode and process events until stopped."""
        try:
            import slack_sdk  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "slack_sdk not installed. Run: pip install praxis[slack]"
            )

        # Validate tokens (warn on bad bot token, hard-fail on missing app token)
        if not self._bot_token or not self._bot_token.startswith("xoxb-"):
            sys.stderr.write(
                "[praxis] warning: PRAXIS_SLACK_BOT_TOKEN should be an xoxb- token\n"
            )

        if not self._app_token:
            raise RuntimeError(
                "PRAXIS_SLACK_APP_TOKEN not set. Socket mode requires an xapp- token."
            )

        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.request import SocketModeRequest  # noqa: F401
        from slack_sdk.socket_mode.response import SocketModeResponse  # noqa: F401
        from slack_sdk.web import WebClient

        web_client = WebClient(token=self._bot_token)
        socket_client = SocketModeClient(
            app_token=self._app_token,
            web_client=web_client,
        )

        socket_client.socket_mode_request_listeners.append(
            self._make_handler(socket_client)
        )

        signal.signal(signal.SIGTERM, self._handle_sigterm)

        self._running = True
        socket_client.connect()
        sys.stderr.write("[praxis] Slack listener connected. Waiting for events...\n")

        while self._running:
            time.sleep(1)

        socket_client.close()
        sys.stderr.write("[praxis] Slack listener stopped.\n")

    def _make_handler(self, socket_client: Any) -> Any:
        """Return a closure registered as the socket mode event handler."""
        from slack_sdk.socket_mode.response import SocketModeResponse

        def handler(client: Any, req: Any) -> None:
            # Always ACK first
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )
            req_type = req.type
            if req_type == "events_api":
                event = req.payload.get("event", {})
                if (
                    event.get("type") == "message"
                    and event.get("channel_type") == "im"
                ):
                    # Ignore bot messages to prevent loops
                    if not event.get("bot_id"):
                        self._handle_message(event)
            elif req_type == "slash_commands":
                self._handle_slash_command(req.payload)
            elif req_type == "interactive":
                payload = req.payload
                if payload.get("type") == "block_actions":
                    self._handle_block_action(payload)

        return handler

    def _handle_message(self, event: dict) -> None:
        """Enqueue a task from an incoming DM."""
        text = event.get("text", "").strip()
        if not text:
            return
        user_id = event.get("user", "unknown")
        task = Task.create(prompt=text, priority=5)
        self._queue.append(task)
        sys.stderr.write(
            f"[slack_listener] Enqueued task {task.id} from DM by {user_id}: "
            f"{text[:60]}\n"
        )

    def _handle_slash_command(self, payload: dict) -> None:
        """Enqueue a task from a slash command, then ACK the response_url."""
        command = payload.get("command", "")
        text = payload.get("text", "").strip()
        user_id = payload.get("user_id", "unknown")
        prompt = f"{command} {text}".strip() if text else command
        task = Task.create(prompt=prompt, priority=5)
        self._queue.append(task)
        sys.stderr.write(
            f"[slack_listener] Enqueued task {task.id} from slash command "
            f"{command} by {user_id}\n"
        )
        self._send_ack(
            payload.get("response_url", ""),
            f"Task queued (id={task.id}): {prompt[:80]}",
        )

    def _handle_block_action(self, payload: dict) -> None:
        """Process approval button clicks from interactive block messages."""
        actions = payload.get("actions", [])
        user = payload.get("user", {})
        user_id = user.get("id", "unknown")
        for action in actions:
            action_id = action.get("action_id", "")
            value = action.get("value", "")
            if action_id.startswith("approval_"):
                approval_id = action_id.removeprefix("approval_")
                new_status = "approved" if value == "approve" else "rejected"
                self._update_approval(approval_id, new_status, user_id)

    def _update_approval(
        self, approval_id: str, status: str, user_id: str
    ) -> None:
        """Atomically update an approval record on disk."""
        path = self._approvals_dir / f"{approval_id}.json"
        if not path.exists():
            sys.stderr.write(
                f"[slack_listener] Warning: approval {approval_id} not found\n"
            )
            return
        record = json.loads(path.read_text(encoding="utf-8"))
        record["status"] = status
        record["responded_at"] = datetime.now().isoformat()
        record["note"] = f"Responded by Slack user {user_id}"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        tmp.replace(path)
        sys.stderr.write(
            f"[slack_listener] Approval {approval_id} set to {status} by {user_id}\n"
        )

    def _send_ack(self, response_url: str, message: str) -> None:
        """Fire-and-forget POST to Slack response_url — errors are logged only."""
        if not response_url:
            return
        payload = json.dumps(
            {"text": message, "response_type": "ephemeral"}
        ).encode("utf-8")
        req = urllib.request.Request(response_url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as exc:
            sys.stderr.write(f"[slack_listener] Warning: ack failed: {exc}\n")

    def _handle_sigterm(self, signum: int, frame: Any) -> None:
        """Handle SIGTERM by setting the run flag to False."""
        sys.stderr.write(
            "[praxis] SIGTERM received, stopping Slack listener...\n"
        )
        self._running = False
