"""Pub/sub event bus for Praxis-Kernel.

EventBus is a singleton that connects queue_runner, scheduler, and ambient
monitor to WebSocket clients and the web UI. Events do not cause tool calls.

Cross-process: a publisher process attaches ``.praxis/events.jsonl`` (one
``{ts, type, data}`` JSON line per event, see schemas/event.json) and the
gateway tails it with :class:`EventLogTailer`, fanning events out in-process.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Event name constants
# ---------------------------------------------------------------------------

TASK_QUEUED: str = "task.queued"
TASK_STARTED: str = "task.started"
TASK_TOKEN: str = "task.token"
TASK_COMPLETED: str = "task.completed"
TASK_FAILED: str = "task.failed"
APPROVAL_ADDED: str = "approval.added"
SCHEDULE_FIRED: str = "schedule.fired"
AMBIENT_TRIGGERED: str = "ambient.triggered"
COMPACTION_FIRED: str = "compaction.fired"
HEARTBEAT: str = "heartbeat"

# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

_QUEUE_MAXSIZE = 100
_LOG_MAX_BYTES = 10 * 1024 * 1024


class EventBus:
    """Thread-safe pub/sub bus.

    Subscribers receive events via ``asyncio.Queue`` instances.  Each call to
    :meth:`subscribe` returns a *new* queue with ``maxsize=100``.  When a
    subscriber's queue is full the event is dropped and a warning is written to
    *stderr*.  A queue subscribed inside a running loop is only touched from
    that loop: publishes from other threads go through ``call_soon_threadsafe``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[tuple[asyncio.Queue[Any], asyncio.AbstractEventLoop | None]]] = {}
        self._log_path: Path | None = None
        self._log_max_bytes = _LOG_MAX_BYTES
        self._tailing = 0

    # ------------------------------------------------------------------
    # Subscribe / unsubscribe
    # ------------------------------------------------------------------

    def subscribe(self, event: str) -> asyncio.Queue[Any]:
        """Subscribe to *event*; returns a new bounded queue (events dropped when full)."""
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        with self._lock:
            self._subscribers.setdefault(event, []).append((q, loop))
        return q

    def unsubscribe(self, event: str, queue: asyncio.Queue[Any]) -> None:
        """Remove *queue* from *event*; silent no-op if it was never subscribed."""
        with self._lock:
            subs = self._subscribers.get(event)
            if subs is not None:
                subs[:] = [s for s in subs if s[0] is not queue]

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(self, event: str, payload: Any = None) -> None:
        """Publish *event* with *payload* to all subscribers.

        Wildcard subscribers (subscribed to ``"*"``) receive an envelope dict
        ``{"type": event, "data": payload}`` so they can identify the event type.
        """
        self.publish_sync(event, payload)

    def publish_sync(self, event: str, payload: Any = None) -> None:
        """Publish from any thread, with or without a running loop; never blocks."""
        self._deliver(event, payload)
        if self._log_path is not None and not self._tailing:
            self._append_log(event, payload)

    def _deliver(self, event: str, payload: Any) -> None:
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        with self._lock:
            targets = [(q, loop, payload) for q, loop in self._subscribers.get(event, [])]
            if event != "*":
                envelope = {"type": event, "data": payload}
                targets += [(q, loop, envelope) for q, loop in self._subscribers.get("*", [])]
            for q, loop, item in targets:
                if loop is None or loop is current or not loop.is_running():
                    _put(q, event, item)
                else:
                    try:
                        loop.call_soon_threadsafe(_put, q, event, item)
                    except RuntimeError:  # loop closed meanwhile
                        pass

    # ------------------------------------------------------------------
    # Cross-process log
    # ------------------------------------------------------------------

    def attach_log(self, path: Path | None, max_bytes: int = 0) -> None:
        """Also append every publish to *path* (None detaches). Rotates to ``<path>.1``."""
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._log_path = Path(path) if path is not None else None
        self._log_max_bytes = max_bytes or _LOG_MAX_BYTES

    def _append_log(self, event: str, payload: Any) -> None:
        path = self._log_path
        if path is None:
            return
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "type": event, "data": payload}
        data = (json.dumps(rec, default=str) + "\n").encode()
        try:
            for _ in range(3):
                with open(path, "ab") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        current = os.stat(path).st_ino == os.fstat(f.fileno()).st_ino
                    except FileNotFoundError:
                        current = False
                    if not current:  # rotated by another writer while we waited
                        continue
                    size = os.fstat(f.fileno()).st_size
                    if size and size + len(data) > self._log_max_bytes:
                        os.replace(path, path.with_name(path.name + ".1"))
                        continue
                    f.write(data)
                    return
        except OSError as exc:
            print(f"[EventBus] event log write failed: {exc}", file=sys.stderr)


def _put(q: asyncio.Queue[Any], event: str, item: Any) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        print(
            f"[EventBus] subscriber queue full for event {event!r}; dropping payload",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_bus_lock = threading.Lock()
_bus_instance: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the process-level :class:`EventBus` singleton."""
    global _bus_instance
    if _bus_instance is None:
        with _bus_lock:
            if _bus_instance is None:
                _bus_instance = EventBus()
    return _bus_instance


# ---------------------------------------------------------------------------
# Cross-process event log
# ---------------------------------------------------------------------------


def event_log_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".praxis" / "events.jsonl"


def enable_event_log(workspace_root: Path) -> Path:
    """Attach the singleton bus to the workspace event log (publisher processes)."""
    path = event_log_path(workspace_root)
    get_event_bus().attach_log(path)
    return path


class EventLogTailer:
    """Follow an events.jsonl file and deliver new events to a bus.

    Starts at the current end of file (history is not replayed). On truncation
    it restarts from the top; on rotation it drains the old file, then reads
    the new one from the start. Malformed lines are skipped.
    """

    def __init__(self, path: Path, bus: EventBus | None = None, interval: float = 0.25) -> None:
        self.path = Path(path)
        self._bus = bus or get_event_bus()
        self._interval = interval
        self._f: Any = None
        self._buf = b""
        self._task: asyncio.Task[None] | None = None
        self._open(at_end=True)

    def _open(self, at_end: bool = False) -> bool:
        try:
            self._f = open(self.path, "rb")
        except FileNotFoundError:
            return False
        if at_end:
            self._f.seek(0, os.SEEK_END)
        self._buf = b""
        return True

    def _close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None

    def poll(self) -> list[dict[str, Any]]:
        """Return complete events appended since the last poll."""
        if self._f is None and not self._open():
            return []
        st = os.fstat(self._f.fileno())
        if st.st_size < self._f.tell():
            self._f.seek(0)
            self._buf = b""
        lines = (self._buf + self._f.read()).split(b"\n")
        self._buf = lines.pop()
        events = [e for e in map(_parse_event, lines) if e is not None]
        try:
            rotated = os.stat(self.path).st_ino != st.st_ino
        except FileNotFoundError:
            rotated = True
        if rotated:
            self._close()
            events += self.poll()
        return events

    async def _run(self) -> None:
        while True:
            try:
                for e in self.poll():
                    self._bus._deliver(e["type"], e["data"])
            except OSError as exc:
                print(f"[EventBus] event log tail failed: {exc}", file=sys.stderr)
                self._close()
            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        self._bus._tailing += 1
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            self._bus._tailing -= 1
        self._close()


def _parse_event(line: bytes) -> dict[str, Any] | None:
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("type"), str) or not rec["type"]:
        return None
    return {"ts": rec.get("ts"), "type": rec["type"], "data": rec.get("data")}


def event_log_lifespan(workspace_root: Path):
    """Starlette lifespan that tails the workspace event log while the app runs."""

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any):
        tailer = EventLogTailer(event_log_path(workspace_root))
        await tailer.start()
        try:
            yield
        finally:
            await tailer.stop()

    return lifespan
