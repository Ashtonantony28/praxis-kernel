"""In-process pub/sub event bus for Praxis-Kernel.

EventBus is a singleton that connects queue_runner, scheduler, and ambient
monitor to WebSocket clients and the web UI. Events do not cause tool calls.
"""

from __future__ import annotations

import asyncio
import sys
import threading
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


class EventBus:
    """Thread-safe in-process pub/sub bus.

    Subscribers receive events via ``asyncio.Queue`` instances.  Each call to
    :meth:`subscribe` returns a *new* queue with ``maxsize=100``.  When a
    subscriber's queue is full the event is dropped and a warning is written to
    *stderr*.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._async_lock = asyncio.Lock()
        self._subscribers: dict[str, list[asyncio.Queue[Any]]] = {}

    # ------------------------------------------------------------------
    # Subscribe / unsubscribe
    # ------------------------------------------------------------------

    def subscribe(self, event: str) -> asyncio.Queue[Any]:
        """Subscribe to *event*.

        Returns a new :class:`asyncio.Queue` from which the caller pulls
        delivered payloads.  The queue has ``maxsize=100``; events are dropped
        (not blocked) when it is full.
        """
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        with self._lock:
            self._subscribers.setdefault(event, []).append(q)
        return q

    def unsubscribe(self, event: str, queue: asyncio.Queue[Any]) -> None:
        """Remove *queue* from the subscriber list for *event*.

        Silent no-op if the queue was never subscribed.
        """
        with self._lock:
            subs = self._subscribers.get(event)
            if subs is None:
                return
            try:
                subs.remove(queue)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Publish (async)
    # ------------------------------------------------------------------

    async def publish(self, event: str, payload: Any = None) -> None:
        """Publish *event* with *payload* to all subscribers.

        Acquiring the async lock prevents concurrent publishes on the same
        event from interleaving.  Each subscriber's queue receives a
        *put_nowait*; if the queue is full the event is dropped and a warning
        is emitted to *stderr*.
        """
        async with self._async_lock:
            with self._lock:
                queues = list(self._subscribers.get(event, []))

        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                print(
                    f"[EventBus] subscriber queue full for event {event!r}; dropping payload",
                    file=sys.stderr,
                )

    # ------------------------------------------------------------------
    # Publish (sync)
    # ------------------------------------------------------------------

    def publish_sync(self, event: str, payload: Any = None) -> None:
        """Publish *event* from a synchronous (non-async) context.

        * If an event loop is already running in the current thread the
          coroutine is scheduled via :func:`asyncio.ensure_future`.
        * Otherwise a temporary event loop is created via
          :func:`asyncio.run`.
        """
        try:
            loop = asyncio.get_running_loop()
            # Inside a running event loop — schedule without blocking.
            asyncio.ensure_future(self.publish(event, payload), loop=loop)
        except RuntimeError:
            # No running event loop in this thread.
            asyncio.run(self.publish(event, payload))


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
