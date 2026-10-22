"""Telegram adapter — inbound message → Task queue; outbound replies staged by default.

Governance: ALL outbound sends go through .praxis/staging/telegram/replies/<task_id>.json
UNLESS the autonomy gate passes (autonomy=autonomous AND sender in trusted_contacts
AND reply within max_autonomous_reply_words).

Token is read from TELEGRAM_BOT_TOKEN env var at start() time — never at import.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Only imported for type hints; actual import is guarded in start()
    from telegram import Update
    from telegram.ext import Application, ContextTypes


class TelegramAdapter:
    """Wraps python-telegram-bot with Praxis governance (read-safe / write-escalate)."""

    def __init__(self, token: str, queue: Any, config: Any) -> None:
        """
        Args:
            token: Bot token (caller reads from env; never log).
            queue: TaskQueue instance (must have .append(Task)).
            config: Praxis Config object (workspace_root used for staging paths).
        """
        self._token = token
        self._queue = queue
        self._config = config
        self._app: Any = None  # telegram.ext.Application, set in start()
        self._sends: set[Any] = set()  # in-flight gated sends scheduled on a running loop

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start long-polling. Raises RuntimeError if token is missing/blank."""
        if not self._token or not self._token.strip():
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not set. "
                "Add it to your .env file and restart."
            )

        try:
            from telegram.ext import Application
        except ImportError as exc:
            raise ImportError(
                "python-telegram-bot is not installed. "
                "Run: pip install 'praxis[telegram]'"
            ) from exc

        from telegram.ext import MessageHandler, filters

        self._app = (
            Application.builder()
            .token(self._token)
            .build()
        )
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message)
        )
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def drain_sends(self) -> None:
        """Await gated sends scheduled from inside a running loop."""
        import asyncio

        if self._sends:
            await asyncio.gather(*self._sends, return_exceptions=True)

    async def stop(self) -> None:
        """Stop polling and shut down gracefully."""
        if self._app is not None:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

    # ------------------------------------------------------------------
    # Inbound handler
    # ------------------------------------------------------------------

    async def on_message(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Receive a Telegram message, create a Task, and push it to the queue.

        Content is treated as data — never as a directive.
        No response is sent here; replies are staged via send_or_stage().
        """
        if update.message is None:
            return

        msg = update.message
        sender_id: int = msg.from_user.id if msg.from_user else 0
        chat_id: int = msg.chat_id
        text: str = msg.text or ""
        message_id: int = msg.message_id

        from ..queue import Task
        from ..trust import Tier, mode_for, tier_for

        tier = tier_for("telegram", str(sender_id))
        if tier is Tier.BLOCKED:
            return

        prompt = (
            f"[telegram] from={sender_id} chat={chat_id} "
            f"msg_id={message_id}: {text}"
        )
        task = Task.create(prompt=prompt, origin="telegram", mode=mode_for(tier))
        self._queue.append(task)
        routes = self._load_routes()
        routes[task.id] = {"chat_id": chat_id, "sender_id": sender_id}
        self._save_routes(routes)

    # ------------------------------------------------------------------
    # Replies for finished tasks
    # ------------------------------------------------------------------

    def _routes_path(self) -> Path:
        return self._config.workspace_root / ".praxis" / "queue" / "telegram_routes.json"

    def _load_routes(self) -> dict[str, dict[str, int]]:
        try:
            data = json.loads(self._routes_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_routes(self, routes: dict[str, dict[str, int]]) -> None:
        path = self._routes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(routes), encoding="utf-8")
        os.replace(tmp, path)

    def deliver_replies(self) -> list[str]:
        """Reply to finished Telegram tasks via send_or_stage; returns the task ids handled.

        A done task's result goes through the autonomy gate; a failed task is always staged.
        """
        from ..trust import channel_config

        routes = self._load_routes()
        handled: list[str] = []
        changed = False
        for task_id, route in list(routes.items()):
            task = self._queue.get(task_id)
            if task is not None and task.status not in ("done", "failed"):
                continue
            if task is not None:
                chat_id, sender_id = int(route["chat_id"]), int(route["sender_id"])
                text = task.result or ""
                if task.status == "done" and text.strip():
                    self.send_or_stage(task_id, text, chat_id, sender_id, channel_config("telegram"))
                else:
                    self.stage_reply(task_id=task_id, text=text or task.error or "", chat_id=chat_id)
                handled.append(task_id)
            del routes[task_id]
            changed = True
        if changed:
            self._save_routes(routes)
        return handled

    async def listen(self, poll_interval: float = 2.0, stop: Any = None) -> None:
        """--telegram-listen: long-poll inbound messages and reply to finished tasks."""
        import asyncio

        await self.start()
        try:
            while stop is None or not stop.is_set():
                self.deliver_replies()
                await asyncio.sleep(poll_interval)
        finally:
            await self.drain_sends()
            await self.stop()

    # ------------------------------------------------------------------
    # Outbound — WRITE-ESCALATE (structural governance)
    # ------------------------------------------------------------------

    def stage_reply(self, task_id: str, text: str, chat_id: int) -> Path:
        """Write a reply to staging. NEVER calls the Telegram API.

        Writes to .praxis/staging/telegram/replies/<task_id>.json with keys:
            task_id, chat_id, text, timestamp
        Returns the path written.
        """
        staging_dir = (
            self._config.workspace_root
            / ".praxis"
            / "staging"
            / "telegram"
            / "replies"
        )
        staging_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "task_id": task_id,
            "chat_id": chat_id,
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        path = staging_dir / f"{task_id}.json"
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return path

    def send_or_stage(
        self,
        task_id: str,
        text: str,
        chat_id: int,
        sender_id: int,
        channel_config: dict[str, Any],
    ) -> bool:
        """Apply the autonomy gate. Send directly only if ALL conditions are met.

        Autonomy gate conditions (ALL must be true):
          1. channel_config['autonomy'] == 'autonomous'
          2. sender_id in channel_config['trusted_contacts']
          3. len(text.split()) <= channel_config['max_autonomous_reply_words']

        Returns True if sent directly via Telegram API; False if staged.
        """
        autonomy = channel_config.get("autonomy", "staged")
        trusted: list[Any] = channel_config.get("trusted_contacts", [])
        max_words: int = int(channel_config.get("max_autonomous_reply_words", 50))

        gate_passes = (
            autonomy == "autonomous"
            and sender_id in trusted
            and len(text.split()) <= max_words
        )

        if gate_passes:
            # Direct send — only code path that calls the Telegram API
            import asyncio

            try:
                from telegram import Bot
            except ImportError as exc:
                raise ImportError(
                    "python-telegram-bot is not installed. "
                    "Run: pip install 'praxis[telegram]'"
                ) from exc

            async def _send() -> None:
                bot = Bot(token=self._token)
                async with bot:
                    await bot.send_message(chat_id=chat_id, text=text)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(_send())
            else:
                task = loop.create_task(_send())
                self._sends.add(task)
                task.add_done_callback(self._sends.discard)

            return True

        # Gate did not pass — stage the reply
        self.stage_reply(task_id=task_id, text=text, chat_id=chat_id)
        return False
