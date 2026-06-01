"""Notifier — push task results and morning handoff to Slack and/or Telegram.

Auto-detects backends from env:
  PRAXIS_SLACK_WEBHOOK_URL           -> Slack (incoming webhook)
  PRAXIS_TELEGRAM_BOT_TOKEN +
  PRAXIS_TELEGRAM_CHAT_ID            -> Telegram (sendMessage API)

Never raises. All failures logged to .praxis/logs/notifier.log.
Credentials never logged or printed.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


class Notifier:
    """Push notifications to Slack and/or Telegram. Never raises."""

    def __init__(self, workspace_root: Path) -> None:
        self._slack_url: str | None = os.environ.get("PRAXIS_SLACK_WEBHOOK_URL") or None
        self._tg_token: str | None = os.environ.get("PRAXIS_TELEGRAM_BOT_TOKEN") or None
        self._tg_chat_id: str | None = os.environ.get("PRAXIS_TELEGRAM_CHAT_ID") or None
        self._log_path = workspace_root / ".praxis" / "logs" / "notifier.log"

    def _redact_secrets(self, text: str) -> str:
        """Replace credential values with [REDACTED]."""
        if self._slack_url and self._slack_url in text:
            text = text.replace(self._slack_url, "[REDACTED]")
        if self._tg_token and self._tg_token in text:
            text = text.replace(self._tg_token, "[REDACTED]")
        return text

    def _log_failure(self, msg: str) -> None:
        """Append timestamp + redacted msg to notifier.log. Best-effort, never raises."""
        try:
            safe_msg = self._redact_secrets(msg)
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).isoformat()
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"[{ts}] {safe_msg}\n")
        except Exception:  # noqa: BLE001
            pass

    def notify(self, message: str, channel: str = "default") -> None:
        """Send message to all configured backends. Never raises."""
        if self._slack_url:
            try:
                payload = json.dumps({"text": message}).encode("utf-8")
                req = urllib.request.Request(
                    self._slack_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
            except Exception as exc:  # noqa: BLE001
                self._log_failure(f"Slack notify failed (url=[REDACTED]): {exc}")

        if self._tg_token and self._tg_chat_id:
            try:
                url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
                payload = json.dumps(
                    {"chat_id": self._tg_chat_id, "text": message}
                ).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
            except Exception as exc:  # noqa: BLE001
                self._log_failure(f"Telegram notify failed: {exc}")

    def notify_task_complete(
        self, task_prompt: str, outcome: str, summary: str
    ) -> None:
        """Format and send a task-complete notification."""
        message = (
            f"✓ Task done [{outcome}]\n"
            f"{task_prompt[:80]}\n\n"
            f"{summary[:200]}"
        )
        self.notify(message)

    def notify_morning_handoff(self, handoff_path: Path) -> None:
        """Read handoff file (first 800 chars) and send as notification."""
        try:
            text = handoff_path.read_text(encoding="utf-8")[:800]
            message = f"\U0001f4cb Morning handoff:\n{text}"
        except (FileNotFoundError, OSError):
            message = "\U0001f4cb Morning handoff: (no file found)"
        self.notify(message)
