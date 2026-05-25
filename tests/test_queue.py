"""Tests for praxis/queue.py — task queue CRUD and crash recovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from praxis.queue import Task, TaskQueue


@pytest.fixture
def queue_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".praxis" / "queue"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def queue(queue_dir: Path) -> TaskQueue:
    return TaskQueue(queue_dir)


# ---------- Task dataclass ----------


def test_task_create():
    t = Task.create("do something", priority=2)
    assert t.prompt == "do something"
    assert t.priority == 2
    assert t.status == "pending"
    assert len(t.id) == 12
    assert t.created_at  # non-empty


def test_task_roundtrip():
    t = Task.create("test prompt")
    data = t.to_dict()
    t2 = Task.from_dict(data)
    assert t2.id == t.id
    assert t2.prompt == t.prompt
    assert t2.status == t.status


def test_task_from_dict_ignores_extra_fields():
    data = {"id": "abc", "prompt": "x", "extra_field": True}
    t = Task.from_dict(data)
    assert t.id == "abc"
    assert t.prompt == "x"


def test_task_create_with_stages():
    t = Task.create("multi-step", stages=["step1", "step2"])
    assert t.stages == ["step1", "step2"]


# ---------- TaskQueue CRUD ----------


def test_append_and_read(queue: TaskQueue):
    t = Task.create("task one")
    queue.append(t)
    tasks = queue._read_all()
    assert len(tasks) == 1
    assert tasks[0].id == t.id


def test_append_multiple(queue: TaskQueue):
    queue.append(Task.create("a"))
    queue.append(Task.create("b"))
    tasks = queue._read_all()
    assert len(tasks) == 2


def test_next_pending_returns_highest_priority(queue: TaskQueue):
    t_low = Task.create("low priority", priority=10)
    t_high = Task.create("high priority", priority=1)
    queue.append(t_low)
    queue.append(t_high)

    nxt = queue.next_pending()
    assert nxt is not None
    assert nxt.id == t_high.id


def test_next_pending_same_priority_returns_oldest(queue: TaskQueue):
    t1 = Task.create("first")
    t1.created_at = "2026-01-01T00:00:00Z"
    t2 = Task.create("second")
    t2.created_at = "2026-01-02T00:00:00Z"
    queue.append(t1)
    queue.append(t2)

    nxt = queue.next_pending()
    assert nxt is not None
    assert nxt.id == t1.id


def test_next_pending_skips_non_pending(queue: TaskQueue):
    t1 = Task.create("done task")
    t1.status = "done"
    t2 = Task.create("pending task")
    queue.append(t1)
    queue.append(t2)

    nxt = queue.next_pending()
    assert nxt is not None
    assert nxt.id == t2.id


def test_next_pending_empty_queue(queue: TaskQueue):
    assert queue.next_pending() is None


def test_next_pending_no_file(queue_dir: Path):
    q = TaskQueue(queue_dir)
    # No tasks.jsonl file exists
    assert q.next_pending() is None


def test_update_status(queue: TaskQueue):
    t = Task.create("test")
    queue.append(t)

    queue.update_status(t.id, "running")
    tasks = queue._read_all()
    assert tasks[0].status == "running"
    assert tasks[0].started_at is not None

    queue.update_status(t.id, "done", result="output")
    tasks = queue._read_all()
    assert tasks[0].status == "done"
    assert tasks[0].result == "output"
    assert tasks[0].completed_at is not None


def test_update_status_failed(queue: TaskQueue):
    t = Task.create("test")
    queue.append(t)
    queue.update_status(t.id, "failed", error="boom")
    tasks = queue._read_all()
    assert tasks[0].status == "failed"
    assert tasks[0].error == "boom"


# ---------- Result files ----------


def test_write_result(queue: TaskQueue):
    queue.write_result("abc123", "the result")
    result_file = queue.results_dir / "abc123.txt"
    assert result_file.exists()
    assert result_file.read_text() == "the result"


# ---------- Crash recovery ----------


def test_recover_interrupted(queue: TaskQueue):
    t1 = Task.create("running")
    t1.status = "running"
    t2 = Task.create("pending")
    queue.append(t1)
    queue.append(t2)

    recovered = queue.recover_interrupted()
    assert recovered == 1

    tasks = queue._read_all()
    assert tasks[0].status == "failed"
    assert "interrupted" in tasks[0].error
    assert tasks[1].status == "pending"


def test_recover_no_running(queue: TaskQueue):
    queue.append(Task.create("pending"))
    assert queue.recover_interrupted() == 0


def test_recover_empty_queue(queue: TaskQueue):
    assert queue.recover_interrupted() == 0


# ---------- Stats ----------


def test_stats(queue: TaskQueue):
    t1 = Task.create("a")
    t1.status = "done"
    t2 = Task.create("b")
    t2.status = "failed"
    t3 = Task.create("c")
    queue.append(t1)
    queue.append(t2)
    queue.append(t3)

    stats = queue.stats()
    assert stats == {"pending": 1, "running": 0, "done": 1, "failed": 1}


def test_stats_empty(queue: TaskQueue):
    stats = queue.stats()
    assert stats == {"pending": 0, "running": 0, "done": 0, "failed": 0}


# ---------- Ensure dirs ----------


def test_ensure_dirs(tmp_path: Path):
    queue_dir = tmp_path / "new" / "queue"
    q = TaskQueue(queue_dir)
    q.ensure_dirs()
    assert queue_dir.exists()
    assert q.results_dir.exists()
