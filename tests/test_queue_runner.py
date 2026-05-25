"""Tests for praxis/queue_runner.py — queue processing loop and staged execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.checkpoint import CheckpointStore
from praxis.queue import Task, TaskQueue
from praxis.queue_runner import _run_atomic_task, _run_staged_task, _run_single_task


@pytest.fixture
def queue_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".praxis" / "queue"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def queue(queue_dir: Path) -> TaskQueue:
    q = TaskQueue(queue_dir)
    q.ensure_dirs()
    return q


@pytest.fixture
def cp_store(queue_dir: Path) -> CheckpointStore:
    return CheckpointStore(queue_dir)


@pytest.fixture
def mock_orch() -> MagicMock:
    orch = MagicMock()
    orch.run.return_value = "task output"
    return orch


# ---------- Atomic task ----------


def test_run_atomic_task_success(queue: TaskQueue, mock_orch: MagicMock):
    task = Task.create("do something")
    queue.append(task)

    _run_atomic_task(task, mock_orch, queue)

    mock_orch.run.assert_called_once_with("do something")
    tasks = queue._read_all()
    assert tasks[0].status == "done"
    assert tasks[0].result == "task output"
    assert (queue.results_dir / f"{task.id}.txt").read_text() == "task output"


def test_run_atomic_task_failure(queue: TaskQueue, mock_orch: MagicMock):
    mock_orch.run.side_effect = RuntimeError("boom")
    task = Task.create("fail task")
    queue.append(task)

    _run_atomic_task(task, mock_orch, queue)

    tasks = queue._read_all()
    assert tasks[0].status == "failed"
    assert "boom" in tasks[0].error


# ---------- Staged task ----------


def test_run_staged_task_all_stages(queue: TaskQueue, mock_orch: MagicMock, cp_store: CheckpointStore):
    mock_orch.run.side_effect = ["scout result", "builder result"]
    task = Task.create("staged", stages=["scout pass", "builder pass"])
    queue.append(task)

    _run_staged_task(task, mock_orch, queue, cp_store)

    assert mock_orch.run.call_count == 2
    tasks = queue._read_all()
    assert tasks[0].status == "done"
    assert "scout result" in tasks[0].result
    assert "builder result" in tasks[0].result
    # Checkpoint should be cleaned up after success
    assert not cp_store.exists(task.id)


def test_run_staged_task_failure_mid_stage(queue: TaskQueue, mock_orch: MagicMock, cp_store: CheckpointStore):
    mock_orch.run.side_effect = ["scout ok", RuntimeError("builder crash")]
    task = Task.create("staged", stages=["scout", "builder"])
    queue.append(task)

    _run_staged_task(task, mock_orch, queue, cp_store)

    tasks = queue._read_all()
    assert tasks[0].status == "failed"
    assert "builder crash" in tasks[0].error
    # Checkpoint preserved for debugging
    cp = cp_store.load(task.id)
    assert cp is not None
    assert 0 in cp.completed
    assert 1 not in cp.completed


def test_run_staged_task_resumes_from_checkpoint(queue: TaskQueue, mock_orch: MagicMock, cp_store: CheckpointStore):
    """A task with an existing checkpoint resumes from where it left off."""
    task = Task.create("staged", stages=["scout", "builder", "verifier"])
    queue.append(task)

    # Pre-populate checkpoint — scout already done
    from praxis.checkpoint import Checkpoint
    cp = Checkpoint(task_id=task.id, stages=task.stages)
    cp.mark_stage_done(0, "scout done")
    cp_store.save(cp)

    mock_orch.run.side_effect = ["builder done", "verifier done"]
    _run_staged_task(task, mock_orch, queue, cp_store)

    # Scout was skipped
    assert mock_orch.run.call_count == 2
    mock_orch.run.assert_any_call("builder")
    mock_orch.run.assert_any_call("verifier")
    tasks = queue._read_all()
    assert tasks[0].status == "done"


# ---------- _run_single_task dispatch ----------


def test_single_task_dispatches_atomic(queue: TaskQueue, mock_orch: MagicMock, cp_store: CheckpointStore):
    task = Task.create("no stages")
    queue.append(task)
    _run_single_task(task, mock_orch, queue, cp_store)
    mock_orch.run.assert_called_once_with("no stages")


def test_single_task_dispatches_staged(queue: TaskQueue, mock_orch: MagicMock, cp_store: CheckpointStore):
    mock_orch.run.side_effect = ["r1", "r2"]
    task = Task.create("with stages", stages=["s1", "s2"])
    queue.append(task)
    _run_single_task(task, mock_orch, queue, cp_store)
    assert mock_orch.run.call_count == 2


# ---------- Shutdown mid-stage ----------


def test_staged_task_pauses_on_shutdown(queue: TaskQueue, mock_orch: MagicMock, cp_store: CheckpointStore):
    """When shutdown is requested, the task is paused and set back to pending."""
    import praxis.queue_runner as qr

    task = Task.create("staged", stages=["s1", "s2", "s3"])
    queue.append(task)

    mock_orch.run.return_value = "s1 result"

    # After stage 0 completes, trigger shutdown
    original_run = mock_orch.run.side_effect

    def run_then_shutdown(prompt):
        result = "stage result"
        qr._shutdown_requested = True
        return result

    mock_orch.run.side_effect = run_then_shutdown

    try:
        _run_staged_task(task, mock_orch, queue, cp_store)
    finally:
        qr._shutdown_requested = False

    tasks = queue._read_all()
    assert tasks[0].status == "pending"
    cp = cp_store.load(task.id)
    assert cp is not None
    assert 0 in cp.completed
