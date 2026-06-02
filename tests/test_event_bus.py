"""Tests for praxis/event_bus.py — EventBus class and singleton."""

from __future__ import annotations

import asyncio
import sys
import threading
from unittest.mock import patch

import pytest

from praxis.event_bus import (
    AMBIENT_TRIGGERED,
    APPROVAL_ADDED,
    COMPACTION_FIRED,
    HEARTBEAT,
    SCHEDULE_FIRED,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_QUEUED,
    TASK_STARTED,
    TASK_TOKEN,
    EventBus,
    get_event_bus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run a coroutine in a fresh event loop (sync-test helper)."""
    return asyncio.run(coro)


@pytest.fixture()
def bus() -> EventBus:
    """Fresh EventBus for each test (not the singleton)."""
    return EventBus()


# ===========================================================================
# 1. Module-level constants
# ===========================================================================


def test_constant_task_queued():
    assert TASK_QUEUED == "task.queued"


def test_constant_task_started():
    assert TASK_STARTED == "task.started"


def test_constant_task_token():
    assert TASK_TOKEN == "task.token"


def test_constant_task_completed():
    assert TASK_COMPLETED == "task.completed"


def test_constant_task_failed():
    assert TASK_FAILED == "task.failed"


def test_constant_approval_added():
    assert APPROVAL_ADDED == "approval.added"


def test_constant_schedule_fired():
    assert SCHEDULE_FIRED == "schedule.fired"


def test_constant_ambient_triggered():
    assert AMBIENT_TRIGGERED == "ambient.triggered"


def test_constant_compaction_fired():
    assert COMPACTION_FIRED == "compaction.fired"


def test_constant_heartbeat():
    assert HEARTBEAT == "heartbeat"


# ===========================================================================
# 2. subscribe() / unsubscribe()
# ===========================================================================


def test_subscribe_returns_queue(bus):
    q = bus.subscribe(TASK_QUEUED)
    assert isinstance(q, asyncio.Queue)


def test_subscribe_queue_maxsize_100(bus):
    q = bus.subscribe(TASK_QUEUED)
    assert q.maxsize == 100


def test_subscribe_returns_different_queues_each_call(bus):
    q1 = bus.subscribe(TASK_QUEUED)
    q2 = bus.subscribe(TASK_QUEUED)
    assert q1 is not q2


def test_unsubscribe_removes_queue(bus):
    q = bus.subscribe(TASK_QUEUED)
    bus.unsubscribe(TASK_QUEUED, q)
    _run(bus.publish(TASK_QUEUED, "payload"))
    assert q.empty()


def test_unsubscribe_unknown_event_is_silent(bus):
    q = bus.subscribe(TASK_QUEUED)
    # Should not raise even if event was never registered
    bus.unsubscribe("nonexistent.event", q)


def test_unsubscribe_nonsubscribed_queue_is_silent(bus):
    _never_subscribed: asyncio.Queue = asyncio.Queue()
    bus.subscribe(TASK_QUEUED)
    # Should not raise
    bus.unsubscribe(TASK_QUEUED, _never_subscribed)


def test_subscribe_unsubscribe_resubscribe_cycle(bus):
    q = bus.subscribe(TASK_QUEUED)
    bus.unsubscribe(TASK_QUEUED, q)
    q2 = bus.subscribe(TASK_QUEUED)
    _run(bus.publish(TASK_QUEUED, "hello"))
    assert q.empty()
    assert not q2.empty()
    assert q2.get_nowait() == "hello"


# ===========================================================================
# 3. publish() — async
# ===========================================================================


def test_publish_delivers_to_single_subscriber(bus):
    q = bus.subscribe(TASK_QUEUED)
    _run(bus.publish(TASK_QUEUED, {"task_id": "t1"}))
    payload = q.get_nowait()
    assert payload == {"task_id": "t1"}


def test_publish_delivers_to_multiple_subscribers(bus):
    q1 = bus.subscribe(TASK_STARTED)
    q2 = bus.subscribe(TASK_STARTED)
    _run(bus.publish(TASK_STARTED, "go"))
    assert q1.get_nowait() == "go"
    assert q2.get_nowait() == "go"


def test_publish_none_payload(bus):
    q = bus.subscribe(HEARTBEAT)
    _run(bus.publish(HEARTBEAT))  # default payload=None
    assert q.get_nowait() is None


def test_publish_dict_payload(bus):
    q = bus.subscribe(TASK_COMPLETED)
    data = {"result": "ok", "tokens": 42}
    _run(bus.publish(TASK_COMPLETED, data))
    assert q.get_nowait() == data


def test_publish_does_not_deliver_to_wrong_event(bus):
    q = bus.subscribe(TASK_FAILED)
    _run(bus.publish(TASK_COMPLETED, "done"))
    assert q.empty()


def test_publish_no_subscribers_is_silent(bus):
    # Should not raise when there are no subscribers
    _run(bus.publish(SCHEDULE_FIRED, "payload"))


def test_publish_drop_on_full_emits_stderr_warning(bus, capsys):
    q = bus.subscribe(TASK_TOKEN)
    # Fill the queue to capacity
    for i in range(100):
        q.put_nowait(i)
    # Next publish should drop and warn
    _run(bus.publish(TASK_TOKEN, "overflow"))
    captured = capsys.readouterr()
    assert "queue full" in captured.err.lower() or "dropping" in captured.err.lower()
    assert q.full()


def test_publish_multiple_events_independent(bus):
    qa = bus.subscribe(TASK_QUEUED)
    qb = bus.subscribe(TASK_COMPLETED)
    _run(bus.publish(TASK_QUEUED, "queued-payload"))
    assert not qa.empty()
    assert qb.empty()
    _run(bus.publish(TASK_COMPLETED, "done-payload"))
    assert not qb.empty()
    assert qa.get_nowait() == "queued-payload"
    assert qb.get_nowait() == "done-payload"


# ===========================================================================
# 4. publish_sync()
# ===========================================================================


def test_publish_sync_delivers_to_subscriber_no_loop(bus):
    q = bus.subscribe(AMBIENT_TRIGGERED)
    bus.publish_sync(AMBIENT_TRIGGERED, "sync-payload")
    assert q.get_nowait() == "sync-payload"


def test_publish_sync_none_payload(bus):
    q = bus.subscribe(HEARTBEAT)
    bus.publish_sync(HEARTBEAT)
    assert q.get_nowait() is None


def test_publish_sync_drop_on_full_emits_stderr_warning(bus, capsys):
    q = bus.subscribe(COMPACTION_FIRED)
    for i in range(100):
        q.put_nowait(i)
    bus.publish_sync(COMPACTION_FIRED, "overflow-sync")
    captured = capsys.readouterr()
    assert "queue full" in captured.err.lower() or "dropping" in captured.err.lower()


def test_publish_sync_no_subscribers_is_silent(bus):
    bus.publish_sync("event.nobody.cares", "whatever")


def test_publish_sync_threadsafe_concurrent(bus):
    """Multiple threads calling publish_sync concurrently must not lose events."""
    q = bus.subscribe(TASK_QUEUED)
    errors: list[Exception] = []

    def worker():
        try:
            bus.publish_sync(TASK_QUEUED, "from-thread")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # All 10 events should have been delivered (queue fits 100)
    count = 0
    while not q.empty():
        q.get_nowait()
        count += 1
    assert count == 10


# ===========================================================================
# 5. Singleton — get_event_bus()
# ===========================================================================


def test_get_event_bus_returns_event_bus_instance():
    bus = get_event_bus()
    assert isinstance(bus, EventBus)


def test_get_event_bus_returns_same_instance_across_calls():
    b1 = get_event_bus()
    b2 = get_event_bus()
    assert b1 is b2


def test_singleton_preserves_subscriptions():
    """Subscriptions made via get_event_bus() persist across calls."""
    b1 = get_event_bus()
    q = b1.subscribe("singleton.test.event")
    b2 = get_event_bus()
    b2.publish_sync("singleton.test.event", "singleton-payload")
    assert q.get_nowait() == "singleton-payload"
    # Clean up to avoid leaking into other singleton tests
    b1.unsubscribe("singleton.test.event", q)
