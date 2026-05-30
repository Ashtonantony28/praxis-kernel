"""Tests for Telegram adapter in praxis/integrations/telegram.py (TASK-H04)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

pytestmark = pytest.mark.anyio

from praxis.integrations.telegram import TelegramAdapter
from praxis.queue import Task, TaskQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_queue() -> MagicMock:
    mock_queue = MagicMock(spec=TaskQueue)
    mock_queue.append.return_value = None
    return mock_queue


def _make_config(workspace_root: Path) -> MagicMock:
    mock_config = MagicMock()
    mock_config.workspace_root = workspace_root
    return mock_config


def _make_adapter(workspace_root: Path, token: str = "test-token") -> tuple[TelegramAdapter, MagicMock]:
    queue = _make_mock_queue()
    config = _make_config(workspace_root)
    adapter = TelegramAdapter(token=token, queue=queue, config=config)
    return adapter, queue


def _make_update(text: str = "hello world", sender_id: int = 42, chat_id: int = 100, message_id: int = 1):
    """Build a minimal fake Telegram Update object."""
    mock_from_user = MagicMock()
    mock_from_user.id = sender_id

    mock_message = MagicMock()
    mock_message.from_user = mock_from_user
    mock_message.chat_id = chat_id
    mock_message.message_id = message_id
    mock_message.text = text

    mock_update = MagicMock()
    mock_update.message = mock_message
    return mock_update


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTelegramAdapterStart:
    async def test_start_raises_without_token(self, tmp_path: Path):
        """start() raises RuntimeError when token is empty/blank."""
        adapter, _ = _make_adapter(tmp_path, token="")

        with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
            await adapter.start()

    async def test_start_raises_with_blank_token(self, tmp_path: Path):
        """start() raises RuntimeError for a whitespace-only token."""
        adapter, _ = _make_adapter(tmp_path, token="   ")

        with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
            await adapter.start()


class TestTelegramOnMessage:
    async def test_on_message_creates_task_and_enqueues(self, tmp_path: Path):
        """on_message() creates a Task with message content and enqueues it."""
        adapter, mock_queue = _make_adapter(tmp_path)
        mock_update = _make_update(text="Tell me the weather", sender_id=55, chat_id=200)
        mock_context = MagicMock()

        await adapter.on_message(mock_update, mock_context)

        mock_queue.append.assert_called_once()
        task_arg = mock_queue.append.call_args[0][0]
        assert isinstance(task_arg, Task)
        assert "Tell me the weather" in task_arg.prompt
        assert "55" in task_arg.prompt   # sender_id in prompt
        assert "200" in task_arg.prompt  # chat_id in prompt

    async def test_on_message_with_none_message_does_not_enqueue(self, tmp_path: Path):
        """on_message() with update.message = None silently returns without enqueueing."""
        adapter, mock_queue = _make_adapter(tmp_path)

        mock_update = MagicMock()
        mock_update.message = None
        mock_context = MagicMock()

        await adapter.on_message(mock_update, mock_context)

        mock_queue.append.assert_not_called()


class TestTelegramStageReply:
    def test_stage_reply_writes_json(self, tmp_path: Path):
        """stage_reply() creates a JSON file at the correct path with all required fields."""
        adapter, _ = _make_adapter(tmp_path)

        path = adapter.stage_reply("task-1", "hello there", chat_id=42)

        expected_path = tmp_path / ".praxis" / "staging" / "telegram" / "replies" / "task-1.json"
        assert path == expected_path
        assert expected_path.exists()

        data = json.loads(expected_path.read_text(encoding="utf-8"))
        assert data["task_id"] == "task-1"
        assert data["chat_id"] == 42
        assert data["text"] == "hello there"
        assert "timestamp" in data

    def test_stage_reply_never_calls_api(self, tmp_path: Path):
        """stage_reply() never calls the Telegram Bot API."""
        adapter, _ = _make_adapter(tmp_path)

        with patch("praxis.integrations.telegram.TelegramAdapter.send_or_stage") as mock_send:
            adapter.stage_reply("task-2", "staged message", chat_id=99)
            mock_send.assert_not_called()

    def test_stage_reply_creates_parent_dirs(self, tmp_path: Path):
        """stage_reply() creates the staging directory tree if it doesn't exist."""
        adapter, _ = _make_adapter(tmp_path)
        staging_dir = tmp_path / ".praxis" / "staging" / "telegram" / "replies"
        assert not staging_dir.exists()

        adapter.stage_reply("task-3", "test", chat_id=1)

        assert staging_dir.exists()


class TestTelegramSendOrStage:
    def _base_config(self, sender_id: int = 7) -> dict:
        return {
            "autonomy": "autonomous",
            "trusted_contacts": [sender_id],
            "max_autonomous_reply_words": 50,
        }

    def test_send_or_stage_autonomous_when_conditions_met(self, tmp_path: Path):
        """Sends directly when autonomy=autonomous, sender trusted, text under word limit."""
        adapter, _ = _make_adapter(tmp_path)
        sender_id = 7
        channel_config = self._base_config(sender_id)

        # Bot is imported locally inside send_or_stage via `from telegram import Bot`.
        # We intercept asyncio.run / loop.run_until_complete so no real network call is made
        # and simply verify that stage_reply is NOT called (i.e. the gate chose to send).
        with patch.object(adapter, "stage_reply") as mock_stage:
            with patch("asyncio.get_event_loop") as mock_loop:
                mock_event_loop = MagicMock()
                mock_event_loop.is_running.return_value = False
                mock_event_loop.run_until_complete = MagicMock()
                mock_loop.return_value = mock_event_loop
                # Also patch asyncio.run as a fallback
                with patch("asyncio.run", MagicMock()):
                    # Patch telegram.Bot so the import inside the method works
                    mock_bot_cls = MagicMock()
                    mock_bot_instance = MagicMock()
                    mock_bot_instance.__aenter__ = AsyncMock(return_value=mock_bot_instance)
                    mock_bot_instance.__aexit__ = AsyncMock(return_value=None)
                    mock_bot_instance.send_message = AsyncMock()
                    mock_bot_cls.return_value = mock_bot_instance
                    import sys
                    import types
                    fake_telegram = types.ModuleType("telegram")
                    fake_telegram.Bot = mock_bot_cls
                    with patch.dict(sys.modules, {"telegram": fake_telegram}):
                        result = adapter.send_or_stage(
                            task_id="t1",
                            text="short reply",
                            chat_id=100,
                            sender_id=sender_id,
                            channel_config=channel_config,
                        )

        assert result is True
        mock_stage.assert_not_called()

    def test_send_or_stage_stages_when_not_trusted(self, tmp_path: Path):
        """Stages reply when sender_id is NOT in trusted_contacts."""
        adapter, _ = _make_adapter(tmp_path)
        channel_config = {
            "autonomy": "autonomous",
            "trusted_contacts": [999],  # different sender
            "max_autonomous_reply_words": 50,
        }

        with patch.object(adapter, "stage_reply") as mock_stage:
            result = adapter.send_or_stage(
                task_id="t2",
                text="hello",
                chat_id=100,
                sender_id=42,  # not in trusted_contacts
                channel_config=channel_config,
            )

        assert result is False
        mock_stage.assert_called_once_with(task_id="t2", text="hello", chat_id=100)

    def test_send_or_stage_stages_when_over_word_limit(self, tmp_path: Path):
        """Stages reply when word count exceeds max_autonomous_reply_words."""
        adapter, _ = _make_adapter(tmp_path)
        sender_id = 7
        channel_config = {
            "autonomy": "autonomous",
            "trusted_contacts": [sender_id],
            "max_autonomous_reply_words": 3,  # very low limit
        }
        long_text = "one two three four five"  # 5 words > 3

        with patch.object(adapter, "stage_reply") as mock_stage:
            result = adapter.send_or_stage(
                task_id="t3",
                text=long_text,
                chat_id=100,
                sender_id=sender_id,
                channel_config=channel_config,
            )

        assert result is False
        mock_stage.assert_called_once_with(task_id="t3", text=long_text, chat_id=100)

    def test_send_or_stage_stages_when_autonomy_is_staged(self, tmp_path: Path):
        """Stages reply when autonomy='staged', even if sender trusted and under word limit."""
        adapter, _ = _make_adapter(tmp_path)
        sender_id = 7
        channel_config = {
            "autonomy": "staged",   # <-- explicitly staged
            "trusted_contacts": [sender_id],
            "max_autonomous_reply_words": 50,
        }

        with patch.object(adapter, "stage_reply") as mock_stage:
            result = adapter.send_or_stage(
                task_id="t4",
                text="short reply",
                chat_id=100,
                sender_id=sender_id,
                channel_config=channel_config,
            )

        assert result is False
        mock_stage.assert_called_once_with(task_id="t4", text="short reply", chat_id=100)
