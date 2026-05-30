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

        prompt = (
            f"[telegram] from={sender_id} chat={chat_id} "
            f"msg_id={message_id}: {text}"
        )
        task = Task.create(
            prompt=prompt,
            priority=0,
            stages=None,
        )
        # Attach telegram metadata for later use by send_or_stage
        task_dict = task.to_dict()
        task_dict["_telegram_sender_id"] = sender_id
        task_dict["_telegram_chat_id"] = chat_id

        self._queue.append(task)

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
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Running inside an async context — schedule as a task
                    asyncio.ensure_future(_send())
                else:
                    loop.run_until_complete(_send())
            except RuntimeError:
                asyncio.run(_send())

            return True

        # Gate did not pass — stage the reply
        self.stage_reply(task_id=task_id, text=text, chat_id=chat_id)
        return False
