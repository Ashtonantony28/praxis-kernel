"""Tests for praxis/scheduler.py — CronScheduler, scheduler thread integration, and CLI commands."""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from praxis.queue import Task, TaskQueue
from praxis.scheduler import CronScheduler, ScheduledTask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_queue() -> MagicMock:
    """Return a MagicMock that quacks like TaskQueue with an empty queue."""
    mock_queue = MagicMock(spec=TaskQueue)
    mock_queue._read_all.return_value = []
    mock_queue.ensure_dirs.return_value = None
    mock_queue.append.return_value = None
    return mock_queue


def _past_iso(hours: int = 1) -> str:
    """Return an ISO8601 string that is `hours` hours in the past (UTC, timezone-aware)."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _future_iso(hours: int = 1) -> str:
    """Return an ISO8601 string that is `hours` hours in the future (UTC, timezone-aware)."""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


# ---------------------------------------------------------------------------
# TestScheduledTask
# ---------------------------------------------------------------------------


class TestScheduledTask:
    def test_fields_and_defaults(self):
        """ScheduledTask sets correct defaults for optional fields."""
        task = ScheduledTask(id="x", name="n", prompt="p", schedule="0 7 * * *")
        assert task.enabled is True
        assert task.last_run is None
        assert task.next_run is None

    def test_to_dict_roundtrip(self):
        """to_dict() followed by from_dict() produces an equal object."""
        task = ScheduledTask(
            id="abc",
            name="daily",
            prompt="run me",
            schedule="0 7 * * *",
            enabled=True,
            last_run=None,
            next_run=None,
            created_at="2026-05-28T00:00:00+00:00",
        )
        assert ScheduledTask.from_dict(task.to_dict()) == task

    def test_created_at_set_automatically(self):
        """created_at is auto-populated to a non-empty string."""
        task = ScheduledTask(id="x", name="n", prompt="p", schedule="0 7 * * *")
        assert task.created_at != ""
        assert isinstance(task.created_at, str)


# ---------------------------------------------------------------------------
# TestCronSchedulerLoad
# ---------------------------------------------------------------------------


class TestCronSchedulerLoad:
    def test_empty_file_creates_empty_list(self, tmp_path: Path):
        """schedule_file absent → load() initialises _tasks to []."""
        schedule_file = tmp_path / "schedule" / "tasks.json"
        log_file = tmp_path / "scheduler.log"
        scheduler = CronScheduler(
            queue=_make_mock_queue(),
            schedule_file=schedule_file,
            log_file=log_file,
        )
        scheduler.load()
        assert scheduler.list_tasks() == []

    def test_valid_json_loads_tasks(self, tmp_path: Path):
        """Valid JSON with one task dict → load() hydrates _tasks with 1 ScheduledTask."""
        schedule_file = tmp_path / "tasks.json"
        task_dict = {
            "id": "t1",
            "name": "test",
            "prompt": "hello",
            "schedule": "0 7 * * *",
            "enabled": True,
            "last_run": None,
            "next_run": None,
            "created_at": "2026-05-28T00:00:00+00:00",
        }
        schedule_file.write_text(json.dumps([task_dict]), encoding="utf-8")
        scheduler = CronScheduler(
            queue=_make_mock_queue(),
            schedule_file=schedule_file,
            log_file=tmp_path / "scheduler.log",
        )
        scheduler.load()
        tasks = scheduler.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].id == "t1"
        assert tasks[0].name == "test"

    def test_creates_schedule_dir_if_absent(self, tmp_path: Path):
        """load() creates parent directory even when it doesn't exist yet."""
        nested = tmp_path / "a" / "b" / "c"
        schedule_file = nested / "tasks.json"
        scheduler = CronScheduler(
            queue=_make_mock_queue(),
            schedule_file=schedule_file,
            log_file=tmp_path / "scheduler.log",
        )
        scheduler.load()
        assert nested.exists()

    def test_corrupt_json_raises(self, tmp_path: Path):
        """Corrupt JSON content → load() raises json.JSONDecodeError."""
        schedule_file = tmp_path / "tasks.json"
        schedule_file.write_text("not valid json", encoding="utf-8")
        scheduler = CronScheduler(
            queue=_make_mock_queue(),
            schedule_file=schedule_file,
            log_file=tmp_path / "scheduler.log",
        )
        with pytest.raises(json.JSONDecodeError):
            scheduler.load()


# ---------------------------------------------------------------------------
# TestCronSchedulerDueTasks
# ---------------------------------------------------------------------------


class TestCronSchedulerDueTasks:
    def _make_scheduler_with_task(
        self,
        tmp_path: Path,
        next_run: str | None,
        enabled: bool = True,
        prompt: str = "test prompt",
    ) -> tuple[CronScheduler, MagicMock]:
        """Helper: create scheduler with one pre-loaded task and a mock queue."""
        schedule_file = tmp_path / "tasks.json"
        log_file = tmp_path / "scheduler.log"
        mock_queue = _make_mock_queue()
        scheduler = CronScheduler(
            queue=mock_queue,
            schedule_file=schedule_file,
            log_file=log_file,
        )
        # Inject a task directly (avoids needing croniter for add_task)
        task = ScheduledTask(
            id="t-due",
            name="my-task",
            prompt=prompt,
            schedule="0 7 * * *",
            enabled=enabled,
            last_run=None,
            next_run=next_run,
            created_at="2026-05-28T00:00:00+00:00",
        )
        scheduler._tasks = [task]
        return scheduler, mock_queue

    def test_due_task_dispatched(self, tmp_path: Path):
        """Task with next_run in the past → tick() calls queue.append()."""
        scheduler, mock_queue = self._make_scheduler_with_task(
            tmp_path, next_run=_past_iso(1)
        )
        with patch("praxis.scheduler._compute_next_run", return_value=_future_iso(24)):
            dispatched = scheduler.tick()
        assert "my-task" in dispatched
        mock_queue.append.assert_called_once()

    def test_future_task_not_dispatched(self, tmp_path: Path):
        """Task with next_run far in the future → tick() does NOT call queue.append()."""
        scheduler, mock_queue = self._make_scheduler_with_task(
            tmp_path, next_run=_future_iso(24)
        )
        dispatched = scheduler.tick()
        assert dispatched == []
        mock_queue.append.assert_not_called()

    def test_disabled_task_not_dispatched(self, tmp_path: Path):
        """Disabled task with past next_run → tick() skips it."""
        scheduler, mock_queue = self._make_scheduler_with_task(
            tmp_path, next_run=_past_iso(1), enabled=False
        )
        dispatched = scheduler.tick()
        assert dispatched == []
        mock_queue.append.assert_not_called()

    def test_pending_task_dedup(self, tmp_path: Path):
        """Task already pending in queue (same prompt) → tick() does NOT append again."""
        prompt = "duplicate prompt"
        scheduler, mock_queue = self._make_scheduler_with_task(
            tmp_path, next_run=_past_iso(1), prompt=prompt
        )
        # Simulate task already pending
        existing = Task.create(prompt=prompt)
        existing.status = "pending"
        mock_queue._read_all.return_value = [existing]

        with patch("praxis.scheduler._compute_next_run", return_value=_future_iso(24)):
            dispatched = scheduler.tick()
        assert dispatched == []
        mock_queue.append.assert_not_called()

    def test_running_task_dedup(self, tmp_path: Path):
        """Task already running in queue (same prompt) → tick() does NOT append again."""
        prompt = "running duplicate"
        scheduler, mock_queue = self._make_scheduler_with_task(
            tmp_path, next_run=_past_iso(1), prompt=prompt
        )
        # Simulate task already running
        existing = Task.create(prompt=prompt)
        existing.status = "running"
        mock_queue._read_all.return_value = [existing]

        with patch("praxis.scheduler._compute_next_run", return_value=_future_iso(24)):
            dispatched = scheduler.tick()
        assert dispatched == []
        mock_queue.append.assert_not_called()

    def test_scheduler_emits_schedule_fired(self, tmp_path: Path):
        """SCHEDULE_FIRED is published via the event bus when tick() dispatches a due task."""
        from praxis.event_bus import SCHEDULE_FIRED

        scheduler, mock_queue = self._make_scheduler_with_task(
            tmp_path, next_run=_past_iso(1), prompt="do the thing"
        )
        mock_bus = MagicMock()
        with (
            patch("praxis.event_bus.get_event_bus", return_value=mock_bus),
            patch("praxis.scheduler._compute_next_run", return_value=_future_iso(24)),
        ):
            scheduler.tick()

        mock_bus.publish_sync.assert_called_once_with(
            SCHEDULE_FIRED,
            {"schedule_id": "t-due", "name": "my-task", "prompt": "do the thing"},
        )


# ---------------------------------------------------------------------------
# TestCronSchedulerAddRemove
# ---------------------------------------------------------------------------


class TestCronSchedulerAddRemove:
    def _make_scheduler(self, tmp_path: Path) -> CronScheduler:
        return CronScheduler(
            queue=_make_mock_queue(),
            schedule_file=tmp_path / "tasks.json",
            log_file=tmp_path / "scheduler.log",
        )

    def test_add_task_sets_correct_fields(self, tmp_path: Path):
        """add_task() creates a ScheduledTask with correct name/schedule/prompt/next_run."""
        scheduler = self._make_scheduler(tmp_path)
        fixed_next = _future_iso(24)
        with patch("praxis.scheduler._compute_next_run", return_value=fixed_next):
            task = scheduler.add_task("my-name", "0 7 * * *", "my prompt")
        assert task.name == "my-name"
        assert task.schedule == "0 7 * * *"
        assert task.prompt == "my prompt"
        assert task.next_run == fixed_next
        assert task.enabled is True
        assert task.id != ""

    def test_remove_task_by_id(self, tmp_path: Path):
        """add_task() then remove_task(id) → list_tasks() is empty."""
        scheduler = self._make_scheduler(tmp_path)
        with patch("praxis.scheduler._compute_next_run", return_value=_future_iso(1)):
            task = scheduler.add_task("t", "0 7 * * *", "p")
        scheduler.remove_task(task.id)
        assert scheduler.list_tasks() == []

    def test_enable_disable_toggle(self, tmp_path: Path):
        """add_task() creates enabled task; disable_task() sets False; enable_task() sets True again."""
        scheduler = self._make_scheduler(tmp_path)
        with patch("praxis.scheduler._compute_next_run", return_value=_future_iso(1)):
            task = scheduler.add_task("t", "0 7 * * *", "p")
        assert scheduler.list_tasks()[0].enabled is True
        scheduler.disable_task(task.id)
        assert scheduler.list_tasks()[0].enabled is False
        scheduler.enable_task(task.id)
        assert scheduler.list_tasks()[0].enabled is True

    def test_remove_unknown_id_raises_key_error(self, tmp_path: Path):
        """remove_task() with nonexistent id raises KeyError."""
        scheduler = self._make_scheduler(tmp_path)
        with pytest.raises(KeyError):
            scheduler.remove_task("nonexistent-id")


# ---------------------------------------------------------------------------
# TestSchedulerThread
# ---------------------------------------------------------------------------


class TestSchedulerThread:
    def test_start_scheduler_thread_starts_daemon_thread(self, tmp_path: Path):
        """_start_scheduler_thread() starts a daemon Thread named 'praxis-scheduler'."""
        from praxis.queue_runner import _start_scheduler_thread

        mock_queue = _make_mock_queue()
        started_threads: list[threading.Thread] = []

        def capture_start(self_thread):
            started_threads.append(self_thread)

        # The function does `from praxis.scheduler import CronScheduler` internally.
        # Patch the class in the source module so the local import picks up the mock.
        mock_sched_instance = MagicMock()
        with (
            patch("praxis.scheduler.CronScheduler", return_value=mock_sched_instance) as mock_sched_cls,
            patch.object(threading.Thread, "start", capture_start),
        ):
            _start_scheduler_thread(mock_queue, tmp_path)

        assert len(started_threads) == 1
        t = started_threads[0]
        assert t.daemon is True
        assert t.name == "praxis-scheduler"

    def test_missing_croniter_logs_warning_not_crash(self, tmp_path: Path, capsys):
        """If praxis.scheduler import raises ImportError, _start_scheduler_thread returns without raising."""
        from praxis.queue_runner import _start_scheduler_thread

        mock_queue = _make_mock_queue()

        # Simulate the module not being importable by removing it from sys.modules
        # and replacing with a broken stand-in, or by patching builtins.__import__.
        import importlib
        import builtins

        real_import = builtins.__import__

        def broken_import(name, *args, **kwargs):
            if name == "praxis.scheduler" or (name == "praxis" and "scheduler" in str(args)):
                raise ImportError("croniter not installed")
            return real_import(name, *args, **kwargs)

        # Simpler: remove praxis.scheduler from sys.modules temporarily and replace
        import praxis.scheduler as _sched_mod  # ensure it is loaded first
        original_module = sys.modules.get("praxis.scheduler")

        # Replace with a fake module that raises ImportError on CronScheduler access
        import types
        broken_mod = types.ModuleType("praxis.scheduler")
        # Do not define CronScheduler — the function catches ImportError on the import itself.
        # We need to make the `from praxis.scheduler import CronScheduler` line fail.
        # The easiest way: temporarily remove from sys.modules so the import re-runs,
        # but that would re-import successfully. Instead: put a stub that raises on access.

        # Approach: patch builtins.__import__ to raise ImportError for praxis.scheduler
        def raise_on_scheduler(name, *args, **kwargs):
            if name == "praxis.scheduler":
                raise ImportError("croniter not installed")
            return real_import(name, *args, **kwargs)

        # Remove praxis.scheduler from cache so the import runs fresh
        sys.modules.pop("praxis.scheduler", None)
        with patch.object(builtins, "__import__", side_effect=raise_on_scheduler):
            # Should not raise
            _start_scheduler_thread(mock_queue, tmp_path)

        # Restore original module
        if original_module is not None:
            sys.modules["praxis.scheduler"] = original_module

        captured = capsys.readouterr()
        assert "scheduler" in captured.err.lower() or "croniter" in captured.err.lower()

    def test_poll_interval_from_env(self, tmp_path: Path, monkeypatch):
        """PRAXIS_SCHEDULER_POLL_INTERVAL is read from env; thread is still started."""
        from praxis.queue_runner import _start_scheduler_thread

        monkeypatch.setenv("PRAXIS_SCHEDULER_POLL_INTERVAL", "30")
        mock_queue = _make_mock_queue()
        started_threads: list[threading.Thread] = []

        def capture_start(self_thread):
            started_threads.append(self_thread)

        mock_sched_instance = MagicMock()
        with (
            patch("praxis.scheduler.CronScheduler", return_value=mock_sched_instance),
            patch.object(threading.Thread, "start", capture_start),
        ):
            _start_scheduler_thread(mock_queue, tmp_path)

        assert len(started_threads) == 1


# ---------------------------------------------------------------------------
# TestSchedulerCLI
# ---------------------------------------------------------------------------


class TestSchedulerCLI:
    def test_parse_mode_schedule_add(self):
        """--schedule-add in argv → _parse_mode returns 'schedule_add'."""
        from praxis.__main__ import _parse_mode
        assert _parse_mode(["--schedule-add", "name", "0 7 * * *", "prompt"]) == "schedule_add"

    def test_parse_mode_schedule_list(self):
        """--schedule-list in argv → _parse_mode returns 'schedule_list'."""
        from praxis.__main__ import _parse_mode
        assert _parse_mode(["--schedule-list"]) == "schedule_list"

    def test_parse_mode_schedule_enable(self):
        """--schedule-enable in argv → _parse_mode returns 'schedule_enable'."""
        from praxis.__main__ import _parse_mode
        assert _parse_mode(["--schedule-enable", "some-id"]) == "schedule_enable"

    def test_parse_mode_schedule_disable(self):
        """--schedule-disable in argv → _parse_mode returns 'schedule_disable'."""
        from praxis.__main__ import _parse_mode
        assert _parse_mode(["--schedule-disable", "some-id"]) == "schedule_disable"

    def test_parse_mode_schedule_remove(self):
        """--schedule-remove in argv → _parse_mode returns 'schedule_remove'."""
        from praxis.__main__ import _parse_mode
        assert _parse_mode(["--schedule-remove", "some-id"]) == "schedule_remove"

    def test_schedule_list_empty(self, tmp_path: Path, capsys, monkeypatch):
        """--schedule-list with no tasks prints 'No scheduled tasks.'"""
        from praxis.__main__ import main

        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))

        mock_config = MagicMock()
        mock_config.workspace_root = tmp_path

        mock_scheduler = MagicMock()
        mock_scheduler.list_tasks.return_value = []

        with (
            patch("praxis.__main__.Config.from_env", return_value=mock_config),
            patch("praxis.__main__._make_scheduler", return_value=mock_scheduler),
            patch.object(sys, "argv", ["praxis", "--schedule-list"]),
        ):
            main()

        captured = capsys.readouterr()
        assert "No scheduled tasks." in captured.out

    def test_schedule_add_prints_confirmation(self, tmp_path: Path, capsys, monkeypatch):
        """--schedule-add with valid args prints the new task id and next_run."""
        from praxis.__main__ import main

        mock_config = MagicMock()
        mock_config.workspace_root = tmp_path

        added_task = ScheduledTask(
            id="new-id-123",
            name="my-task",
            prompt="do the thing",
            schedule="0 7 * * *",
            next_run="2026-05-29T07:00:00+00:00",
            created_at="2026-05-28T00:00:00+00:00",
        )
        mock_scheduler = MagicMock()
        mock_scheduler.add_task.return_value = added_task

        with (
            patch("praxis.__main__.Config.from_env", return_value=mock_config),
            patch("praxis.__main__._make_scheduler", return_value=mock_scheduler),
            patch.object(sys, "argv", ["praxis", "--schedule-add", "my-task", "0 7 * * *", "do the thing"]),
        ):
            main()

        captured = capsys.readouterr()
        assert "new-id-123" in captured.out
        mock_scheduler.add_task.assert_called_once_with("my-task", "0 7 * * *", "do the thing")
        mock_scheduler.save.assert_called_once()

    def test_schedule_remove_calls_remove_task(self, tmp_path: Path, capsys, monkeypatch):
        """--schedule-remove calls remove_task(id) and prints confirmation."""
        from praxis.__main__ import main

        mock_config = MagicMock()
        mock_config.workspace_root = tmp_path
        mock_scheduler = MagicMock()
        mock_scheduler.remove_task.return_value = None  # success

        with (
            patch("praxis.__main__.Config.from_env", return_value=mock_config),
            patch("praxis.__main__._make_scheduler", return_value=mock_scheduler),
            patch.object(sys, "argv", ["praxis", "--schedule-remove", "task-id-xyz"]),
        ):
            main()

        captured = capsys.readouterr()
        mock_scheduler.remove_task.assert_called_once_with("task-id-xyz")
        assert "task-id-xyz" in captured.out

    def test_schedule_enable_calls_enable_task(self, tmp_path: Path, capsys, monkeypatch):
        """--schedule-enable calls enable_task(id) and saves."""
        from praxis.__main__ import main

        mock_config = MagicMock()
        mock_config.workspace_root = tmp_path
        mock_scheduler = MagicMock()
        mock_scheduler.enable_task.return_value = None

        with (
            patch("praxis.__main__.Config.from_env", return_value=mock_config),
            patch("praxis.__main__._make_scheduler", return_value=mock_scheduler),
            patch.object(sys, "argv", ["praxis", "--schedule-enable", "eid-123"]),
        ):
            main()

        mock_scheduler.enable_task.assert_called_once_with("eid-123")
        mock_scheduler.save.assert_called_once()

    def test_schedule_disable_calls_disable_task(self, tmp_path: Path, capsys, monkeypatch):
        """--schedule-disable calls disable_task(id) and saves."""
        from praxis.__main__ import main

        mock_config = MagicMock()
        mock_config.workspace_root = tmp_path
        mock_scheduler = MagicMock()
        mock_scheduler.disable_task.return_value = None

        with (
            patch("praxis.__main__.Config.from_env", return_value=mock_config),
            patch("praxis.__main__._make_scheduler", return_value=mock_scheduler),
            patch.object(sys, "argv", ["praxis", "--schedule-disable", "did-456"]),
        ):
            main()

        mock_scheduler.disable_task.assert_called_once_with("did-456")
        mock_scheduler.save.assert_called_once()
