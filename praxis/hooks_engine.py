"""File-system event watcher — hooks_engine.py

FileWatcher monitors directories for CREATE events and enqueues Tasks
for the orchestrator, instructing it to examine new files via the Read tool.

IMPORTANT: The watcher NEVER reads file content itself. It only generates a
prompt that asks the orchestrator to use the Read tool. File content must
flow through the governed tool-call boundary, not be injected by the watcher.

Optional dependency: watchdog>=3.0  (pip install praxis[hooks])
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from praxis.queue import TaskQueue

_DEFAULT_TEMPLATE = (
    "A new file was created at {path}. "
    "Use the Read tool to read the file and decide what action to take. "
    "Do not infer its contents from the filename alone."
)


def _make_event_handler_class(
    FileSystemEventHandler,  # noqa: N803  (passed in after lazy import)
    *,
    extensions: set[str],
    debounce: float,
    template: str,
    queue: "TaskQueue",
):
    """Return a FileSystemEventHandler subclass bound to the given configuration.

    The class is built at call-time so we can inherit from the watchdog base
    class *after* it has been lazily imported.
    """

    class _PraxisEventHandler(FileSystemEventHandler):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self._extensions = extensions
            self._debounce = debounce
            self._template = template
            self._queue = queue
            self._timers: dict[str, threading.Timer] = {}
            # RLock so _enqueue (called by Timer) can re-enter from the same
            # thread when debounce=0 fires the timer synchronously in tests.
            self._lock = threading.RLock()

        def on_created(self, event) -> None:  # type: ignore[override]
            if event.is_directory:
                return
            path = Path(event.src_path)
            if self._extensions and path.suffix not in self._extensions:
                return
            self._schedule(path)

        def _schedule(self, path: Path) -> None:
            key = str(path)
            with self._lock:
                existing = self._timers.get(key)
                if existing is not None:
                    existing.cancel()
                timer = threading.Timer(self._debounce, self._enqueue, args=[path])
                timer.daemon = True
                # Register before start() so _enqueue can pop the key even
                # when the timer fires synchronously in tests (ImmediateTimer).
                self._timers[key] = timer
                timer.start()

        def _enqueue(self, path: Path) -> None:
            """Enqueue a Task for the orchestrator — never reads the file itself."""
            key = str(path)
            with self._lock:
                self._timers.pop(key, None)
            from praxis.queue import Task  # local import keeps module weight low

            prompt = self._template.format(path=path)
            task = Task.create(prompt=prompt, priority=5)
            self._queue.append(task)

    return _PraxisEventHandler


class FileWatcher:
    """Watches filesystem paths and enqueues Tasks on file CREATE events.

    Configuration via environment variables:
      PRAXIS_WATCH_PATHS       — comma-separated directories to watch
      PRAXIS_WATCH_EXTENSIONS  — comma-separated file extensions, e.g. ``.py,.md``
                                 (empty = watch all file types)
      PRAXIS_WATCH_DEBOUNCE    — seconds between last event and enqueue (default: 5)
      PRAXIS_WATCH_PROMPT_TEMPLATE — prompt string with ``{path}`` placeholder

    Requires: ``pip install praxis[hooks]``  (installs watchdog>=3.0)
    """

    def __init__(self, queue: "TaskQueue") -> None:
        self._queue = queue
        self._watch_paths: list[str] = [
            p.strip()
            for p in os.environ.get("PRAXIS_WATCH_PATHS", "").split(",")
            if p.strip()
        ]
        raw_ext = os.environ.get("PRAXIS_WATCH_EXTENSIONS", "")
        self._extensions: set[str] = {
            (e.strip() if e.strip().startswith(".") else f".{e.strip()}")
            for e in raw_ext.split(",")
            if e.strip()
        }
        self._debounce = float(os.environ.get("PRAXIS_WATCH_DEBOUNCE", "5"))
        self._template = os.environ.get("PRAXIS_WATCH_PROMPT_TEMPLATE", _DEFAULT_TEMPLATE)
        self._observer = None
        self._handler = None

    def start(self) -> None:
        """Start the watchdog Observer as a background daemon thread.

        Raises RuntimeError if watchdog is not installed.
        """
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError as exc:
            raise RuntimeError(
                "watchdog is required for FileWatcher. "
                "Install it with: pip install praxis[hooks]"
            ) from exc

        handler_cls = _make_event_handler_class(
            FileSystemEventHandler,
            extensions=self._extensions,
            debounce=self._debounce,
            template=self._template,
            queue=self._queue,
        )
        self._handler = handler_cls()

        self._observer = Observer()
        self._observer.daemon = True  # type: ignore[attr-defined]

        for path_str in self._watch_paths:
            resolved = Path(path_str).resolve()
            if resolved.is_dir():
                self._observer.schedule(self._handler, str(resolved), recursive=True)

        self._observer.start()

    def stop(self) -> None:
        """Gracefully stop the watchdog Observer and join the background thread."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
