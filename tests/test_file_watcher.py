"""Tests for praxis/hooks_engine.py — FileWatcher with mocked watchdog.

watchdog is an optional dependency; these tests mock it via sys.modules so
they run in any environment regardless of whether watchdog is installed.

The debounce timer is intercepted with a synchronous ImmediateTimer so tests
are deterministic without sleeping.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from praxis.queue import TaskQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ImmediateTimer:
    """Drop-in replacement for threading.Timer that fires synchronously in start()."""

    daemon = False

    def __init__(self, delay: float, callback, args: tuple = (), kwargs: dict | None = None) -> None:
        self._callback = callback
        self._args = args
        self._kwargs = kwargs or {}
        self._cancelled = False

    def start(self) -> None:
        if not self._cancelled:
            self._callback(*self._args, **self._kwargs)

    def cancel(self) -> None:
        self._cancelled = True


def _make_mock_watchdog():
    """Build minimal mock watchdog module hierarchy."""
    mock_fsh = object  # plain object as a trivial base class
    mock_events = MagicMock()
    mock_events.FileSystemEventHandler = mock_fsh

    mock_observer = MagicMock()
    mock_observers = MagicMock()
    mock_observers.Observer.return_value = mock_observer

    mock_watchdog = MagicMock()

    return {
        "watchdog": mock_watchdog,
        "watchdog.observers": mock_observers,
        "watchdog.events": mock_events,
    }, mock_observer


def _make_event(src_path: str, is_directory: bool = False) -> MagicMock:
    event = MagicMock()
    event.src_path = src_path
    event.is_directory = is_directory
    return event


def _start_watcher(watcher, mock_modules: dict) -> None:
    """Start a FileWatcher inside a mocked watchdog sys.modules context."""
    with patch.dict(sys.modules, mock_modules):
        watcher.start()


# ---------------------------------------------------------------------------
# FileWatcher — constructor / env parsing
# ---------------------------------------------------------------------------

class TestFileWatcherInit:
    def test_parses_watch_paths(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", f"{tmp_path}/a,{tmp_path}/b")
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        assert len(w._watch_paths) == 2

    def test_empty_watch_paths(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PRAXIS_WATCH_PATHS", raising=False)
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        assert w._watch_paths == []

    def test_parses_extensions_with_dot(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", ".py,.md")
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        assert ".py" in w._extensions
        assert ".md" in w._extensions

    def test_parses_extensions_without_dot(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", "py,md")
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        assert ".py" in w._extensions
        assert ".md" in w._extensions

    def test_default_debounce(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PRAXIS_WATCH_DEBOUNCE", raising=False)
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        assert w._debounce == 5.0

    def test_custom_debounce(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "10")
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        assert w._debounce == 10.0

    def test_default_template_contains_path_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PRAXIS_WATCH_PROMPT_TEMPLATE", raising=False)
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        assert "{path}" in w._template


# ---------------------------------------------------------------------------
# FileWatcher — start / stop
# ---------------------------------------------------------------------------

class TestFileWatcherStartStop:
    def test_start_raises_without_watchdog(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PRAXIS_WATCH_PATHS", raising=False)
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        # Remove watchdog from sys.modules so the import fails
        with patch.dict(sys.modules, {"watchdog": None, "watchdog.observers": None, "watchdog.events": None}):
            with pytest.raises(RuntimeError, match="watchdog"):
                w.start()

    def test_start_creates_handler(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", ".txt")
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        mock_mods, _ = _make_mock_watchdog()
        _start_watcher(w, mock_mods)
        assert w._handler is not None

    def test_stop_is_safe_before_start(self, tmp_path):
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        w.stop()  # must not raise

    def test_stop_calls_observer_stop_and_join(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        mock_mods, mock_observer = _make_mock_watchdog()
        _start_watcher(w, mock_mods)
        w.stop()
        mock_observer.stop.assert_called_once()
        mock_observer.join.assert_called_once()

    def test_stop_clears_observer(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        from praxis.hooks_engine import FileWatcher
        q = TaskQueue(tmp_path / "q")
        w = FileWatcher(q)
        mock_mods, _ = _make_mock_watchdog()
        _start_watcher(w, mock_mods)
        w.stop()
        assert w._observer is None


# ---------------------------------------------------------------------------
# _PraxisEventHandler — core behaviour
# ---------------------------------------------------------------------------

def _make_handler(tmp_path, extensions=None, debounce=0.0, template=None):
    """Helper: start a FileWatcher with ImmediateTimer patched and return its handler."""
    from praxis.hooks_engine import FileWatcher

    if extensions is not None:
        ext_str = ",".join(extensions)
    else:
        ext_str = ""

    import os
    env_patch = {
        "PRAXIS_WATCH_PATHS": str(tmp_path),
        "PRAXIS_WATCH_EXTENSIONS": ext_str,
        "PRAXIS_WATCH_DEBOUNCE": str(debounce),
    }
    if template:
        env_patch["PRAXIS_WATCH_PROMPT_TEMPLATE"] = template

    q = TaskQueue(tmp_path / "q")
    mock_mods, _ = _make_mock_watchdog()

    with patch.dict("os.environ", env_patch, clear=False):
        # Re-instantiate so env vars are picked up
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)

    return w._handler, q


class TestFileWatcherEnqueuesOnCreate:
    """test_file_watcher_enqueues_on_create — required by feature spec."""

    def test_file_watcher_enqueues_on_create(self, tmp_path, monkeypatch):
        """A CREATE event for a matching file enqueues a Task with priority=5."""
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", ".py")
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "0")

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        event = _make_event(str(tmp_path / "script.py"))

        with patch("threading.Timer", ImmediateTimer):
            handler.on_created(event)

        tasks = q._read_all()
        assert len(tasks) == 1
        assert tasks[0].priority == 5
        assert "script.py" in tasks[0].prompt

    def test_task_prompt_contains_file_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", ".md")
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "0")

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        event = _make_event(str(tmp_path / "notes.md"))

        with patch("threading.Timer", ImmediateTimer):
            handler.on_created(event)

        tasks = q._read_all()
        assert str(tmp_path / "notes.md") in tasks[0].prompt

    def test_task_prompt_does_not_contain_file_content(self, tmp_path, monkeypatch):
        """Watcher must never embed file content in the prompt."""
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", ".txt")
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "0")

        target = tmp_path / "secret.txt"
        target.write_text("TOP SECRET CONTENT")

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        event = _make_event(str(target))

        with patch("threading.Timer", ImmediateTimer):
            handler.on_created(event)

        tasks = q._read_all()
        assert len(tasks) == 1
        assert "TOP SECRET CONTENT" not in tasks[0].prompt

    def test_prompt_instructs_read_tool_usage(self, tmp_path, monkeypatch):
        """Default template must reference the Read tool."""
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.delenv("PRAXIS_WATCH_EXTENSIONS", raising=False)
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "0")
        monkeypatch.delenv("PRAXIS_WATCH_PROMPT_TEMPLATE", raising=False)

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        event = _make_event(str(tmp_path / "data.json"))

        with patch("threading.Timer", ImmediateTimer):
            handler.on_created(event)

        tasks = q._read_all()
        assert "Read" in tasks[0].prompt

    def test_directory_event_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.delenv("PRAXIS_WATCH_EXTENSIONS", raising=False)
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "0")

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        event = _make_event(str(tmp_path / "subdir"), is_directory=True)

        with patch("threading.Timer", ImmediateTimer):
            handler.on_created(event)

        tasks = q._read_all()
        assert len(tasks) == 0


class TestFileWatcherIgnoresNonMatchingExtension:
    """test_file_watcher_ignores_non_matching_extension — required by feature spec."""

    def test_file_watcher_ignores_non_matching_extension(self, tmp_path, monkeypatch):
        """A CREATE event for a file with a non-matching extension is ignored."""
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", ".py")
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "0")

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        event = _make_event(str(tmp_path / "image.png"))

        with patch("threading.Timer", ImmediateTimer):
            handler.on_created(event)

        tasks = q._read_all()
        assert len(tasks) == 0

    def test_multiple_extensions_only_matching_enqueued(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", ".py,.md")
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "0")

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        with patch("threading.Timer", ImmediateTimer):
            handler.on_created(_make_event(str(tmp_path / "script.py")))
            handler.on_created(_make_event(str(tmp_path / "readme.md")))
            handler.on_created(_make_event(str(tmp_path / "data.csv")))

        tasks = q._read_all()
        assert len(tasks) == 2
        prompts = [t.prompt for t in tasks]
        assert any("script.py" in p for p in prompts)
        assert any("readme.md" in p for p in prompts)
        assert not any("data.csv" in p for p in prompts)

    def test_empty_extensions_matches_all(self, tmp_path, monkeypatch):
        """When PRAXIS_WATCH_EXTENSIONS is empty, all file types are matched."""
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", "")
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "0")

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        with patch("threading.Timer", ImmediateTimer):
            handler.on_created(_make_event(str(tmp_path / "anything.xyz")))

        tasks = q._read_all()
        assert len(tasks) == 1


# ---------------------------------------------------------------------------
# Debounce behaviour
# ---------------------------------------------------------------------------

class TestDebounce:
    def test_rapid_events_produce_one_task(self, tmp_path, monkeypatch):
        """Multiple rapid CREATE events for the same path are collapsed to one task.

        Uses a RecordingTimer (start is a no-op) so we control exactly when
        timers fire and can verify the debounce cancellation logic.
        """
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", ".py")
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "5")

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        timers_created: list = []

        class RecordingTimer:
            """Timer that does NOT fire on start() — caller must call .fire()."""
            daemon = False

            def __init__(self, delay, callback, args=(), kwargs=None):
                self.callback = callback
                self.args = args
                self._cancelled = False
                timers_created.append(self)

            def start(self) -> None:
                pass  # intentionally deferred

            def cancel(self) -> None:
                self._cancelled = True

            def fire(self) -> None:
                if not self._cancelled:
                    self.callback(*self.args)

        event = _make_event(str(tmp_path / "module.py"))

        # Fire three rapid CREATE events for the same path
        with patch("threading.Timer", RecordingTimer):
            handler.on_created(event)
            handler.on_created(event)
            handler.on_created(event)

        # Three timers created; first two should be cancelled
        assert len(timers_created) == 3
        assert timers_created[0]._cancelled, "first timer should be cancelled"
        assert timers_created[1]._cancelled, "second timer should be cancelled"
        assert not timers_created[2]._cancelled, "last timer should survive"

        # Only the surviving timer fires → exactly one task enqueued
        timers_created[2].fire()
        tasks = q._read_all()
        assert len(tasks) == 1

    def test_different_paths_produce_separate_tasks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", ".py")
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "0")

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        with patch("threading.Timer", ImmediateTimer):
            handler.on_created(_make_event(str(tmp_path / "a.py")))
            handler.on_created(_make_event(str(tmp_path / "b.py")))

        tasks = q._read_all()
        assert len(tasks) == 2


# ---------------------------------------------------------------------------
# Custom prompt template
# ---------------------------------------------------------------------------

class TestCustomTemplate:
    def test_custom_template_used(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_WATCH_PATHS", str(tmp_path))
        monkeypatch.setenv("PRAXIS_WATCH_EXTENSIONS", ".log")
        monkeypatch.setenv("PRAXIS_WATCH_DEBOUNCE", "0")
        monkeypatch.setenv("PRAXIS_WATCH_PROMPT_TEMPLATE", "Log alert: {path}")

        from praxis.hooks_engine import FileWatcher

        q = TaskQueue(tmp_path / "q")
        mock_mods, _ = _make_mock_watchdog()
        w = FileWatcher(q)
        _start_watcher(w, mock_mods)
        handler = w._handler

        event = _make_event(str(tmp_path / "app.log"))

        with patch("threading.Timer", ImmediateTimer):
            handler.on_created(event)

        tasks = q._read_all()
        assert tasks[0].prompt.startswith("Log alert:")
        assert "app.log" in tasks[0].prompt
