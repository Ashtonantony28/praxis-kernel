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
    async def test_on_message_creates_task_and_enqueues(self, tmp_path: Path, monkeypatch):
        """on_message() creates a Task with message content and enqueues it."""
        monkeypatch.setenv("PRAXIS_OWNER_IDS", "telegram:55")
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
                with patch("asyncio.run", MagicMock(side_effect=lambda coro: coro.close())):
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


# ---------------------------------------------------------------------------
# SG8: trust tiers, --telegram-listen, inbound -> queue -> staged reply
# ---------------------------------------------------------------------------

import asyncio
import gc
import sys
import types
import warnings



def _write_convergence(root: Path, telegram: dict) -> None:
    import yaml

    (root / "convergence.yaml").write_text(yaml.safe_dump({"channels": {"telegram": telegram}}))


def _real_adapter(root: Path) -> tuple[TelegramAdapter, TaskQueue]:
    queue = TaskQueue(root / ".praxis" / "queue")
    queue.ensure_dirs()
    return TelegramAdapter(token="test-token", queue=queue, config=_make_config(root)), queue


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("PRAXIS_OWNER_IDS", raising=False)
    monkeypatch.delenv("PRAXIS_ADMIN_IDS", raising=False)
    return tmp_path


def _run(coro):
    return asyncio.run(coro)


class TestTelegramTrust:
    def test_unknown_sender_enqueues_nothing(self, ws: Path):
        adapter, queue = _real_adapter(ws)
        _run(adapter.on_message(_make_update(sender_id=999), None))
        assert queue.stats().get("pending", 0) == 0
        assert adapter.deliver_replies() == []

    def test_trusted_contact_enqueues_plan_mode(self, ws: Path):
        _write_convergence(ws, {"trusted_contacts": [55]})
        adapter, queue = _real_adapter(ws)
        _run(adapter.on_message(_make_update(sender_id=55), None))
        task = queue.claim()
        assert (task.origin, task.mode) == ("telegram", "plan")

    def test_owner_enqueues_unrestricted(self, ws: Path, monkeypatch):
        monkeypatch.setenv("PRAXIS_OWNER_IDS", "telegram:55")
        adapter, queue = _real_adapter(ws)
        _run(adapter.on_message(_make_update(sender_id=55), None))
        task = queue.claim()
        assert (task.origin, task.mode) == ("telegram", None)


class TestTelegramRoundTrip:
    def test_inbound_queue_staged_reply(self, ws: Path):
        _write_convergence(ws, {"autonomy": "staged", "trusted_contacts": [55]})
        adapter, queue = _real_adapter(ws)
        _run(adapter.on_message(_make_update(text="weather?", sender_id=55, chat_id=200), None))

        assert adapter.deliver_replies() == []  # still pending
        task = queue.claim()
        queue.update(task.id, status="done", result="Sunny today.")

        assert adapter.deliver_replies() == [task.id]
        staged = ws / ".praxis" / "staging" / "telegram" / "replies" / f"{task.id}.json"
        data = json.loads(staged.read_text())
        assert (data["chat_id"], data["text"]) == (200, "Sunny today.")
        assert adapter.deliver_replies() == []  # replied once

    def test_routes_survive_adapter_restart(self, ws: Path):
        _write_convergence(ws, {"trusted_contacts": [55]})
        adapter, queue = _real_adapter(ws)
        _run(adapter.on_message(_make_update(sender_id=55, chat_id=300), None))
        task = queue.claim()
        queue.update(task.id, status="done", result="hi")
        fresh, _ = _real_adapter(ws)
        assert fresh.deliver_replies() == [task.id]

    def test_failed_task_is_staged_never_sent(self, ws: Path):
        _write_convergence(ws, {"autonomy": "autonomous", "trusted_contacts": [55]})
        adapter, queue = _real_adapter(ws)
        _run(adapter.on_message(_make_update(sender_id=55), None))
        task = queue.claim()
        queue.update(task.id, status="failed", error="boom")
        with patch.object(adapter, "send_or_stage") as send:
            assert adapter.deliver_replies() == [task.id]
        send.assert_not_called()
        assert (ws / ".praxis" / "staging" / "telegram" / "replies" / f"{task.id}.json").exists()

    def test_deliver_uses_convergence_channel_config(self, ws: Path):
        cfg = {"autonomy": "autonomous", "trusted_contacts": [55], "max_autonomous_reply_words": 5}
        _write_convergence(ws, cfg)
        adapter, queue = _real_adapter(ws)
        _run(adapter.on_message(_make_update(sender_id=55, chat_id=200), None))
        task = queue.claim()
        queue.update(task.id, status="done", result="ok")
        with patch.object(adapter, "send_or_stage", return_value=False) as send:
            adapter.deliver_replies()
        send.assert_called_once_with(task.id, "ok", 200, 55, cfg)


def _fake_telegram_ext(captured: dict) -> dict:
    class _Updater:
        async def start_polling(self):
            captured["polling"] = True

        async def stop(self):
            captured["stopped"] = True

    class _App:
        updater = _Updater()

        def add_handler(self, handler):
            captured["handler"] = handler

        async def initialize(self):
            pass

        async def start(self):
            pass

        async def stop(self):
            pass

        async def shutdown(self):
            pass

    class _Builder:
        def token(self, _t):
            return self

        def build(self):
            return _App()

    ext = types.ModuleType("telegram.ext")
    ext.Application = types.SimpleNamespace(builder=_Builder)
    ext.MessageHandler = lambda _f, cb: cb
    ext.filters = types.SimpleNamespace(TEXT=MagicMock(), COMMAND=MagicMock())
    return {"telegram": types.ModuleType("telegram"), "telegram.ext": ext}


class TestTelegramListen:
    def test_parse_mode(self):
        from praxis.__main__ import _parse_mode

        assert _parse_mode(["praxis", "--telegram-listen"]) == "telegram_listen"

    def test_listen_round_trip_with_fake_bot(self, ws: Path):
        _write_convergence(ws, {"trusted_contacts": [55]})
        adapter, queue = _real_adapter(ws)
        captured: dict = {}

        async def _main():
            stop = asyncio.Event()
            listener = asyncio.create_task(adapter.listen(poll_interval=0.01, stop=stop))
            while "handler" not in captured:
                await asyncio.sleep(0.01)
            await captured["handler"](_make_update(text="ping", sender_id=55, chat_id=7), None)
            task = queue.claim()
            queue.update(task.id, status="done", result="pong")
            staged = ws / ".praxis" / "staging" / "telegram" / "replies" / f"{task.id}.json"
            for _ in range(200):
                if staged.exists():
                    break
                await asyncio.sleep(0.01)
            stop.set()
            await asyncio.wait_for(listener, 2)
            return staged

        with patch.dict(sys.modules, _fake_telegram_ext(captured)):
            staged = _run(_main())
        assert json.loads(staged.read_text())["text"] == "pong"
        assert captured["polling"] and captured["stopped"]


class TestTelegramDirectSendNoLeak:
    def test_autonomous_send_in_running_loop_is_awaited(self, tmp_path: Path):
        adapter, _ = _make_adapter(tmp_path)
        bot = MagicMock()
        bot.__aenter__ = AsyncMock(return_value=bot)
        bot.__aexit__ = AsyncMock(return_value=None)
        bot.send_message = AsyncMock()
        fake = types.ModuleType("telegram")
        fake.Bot = MagicMock(return_value=bot)
        cfg = {"autonomy": "autonomous", "trusted_contacts": [7], "max_autonomous_reply_words": 50}

        async def _main():
            assert adapter.send_or_stage("t", "hi", 1, 7, cfg) is True
            await adapter.drain_sends()

        with warnings.catch_warnings(record=True) as caught, patch.dict(sys.modules, {"telegram": fake}):
            warnings.simplefilter("always")
            _run(_main())
            gc.collect()
        bot.send_message.assert_awaited_once_with(chat_id=1, text="hi")
        assert not [w for w in caught if "never awaited" in str(w.message)]
