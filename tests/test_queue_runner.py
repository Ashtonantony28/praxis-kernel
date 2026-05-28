"""Tests for praxis/queue_runner.py — queue processing loop and staged execution."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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


# ---------- Rate limiting ----------


class TestRateLimiting:
    """Tests for PRAXIS_MAX_CONCURRENT_TASKS rate limit in run_queue_loop()."""

    def test_rate_limit_respected(self, tmp_path: Path):
        """When running==max_concurrent, loop sleeps without calling next_pending()."""
        import praxis.queue_runner as qr

        mock_queue = MagicMock()
        mock_queue.stats.return_value = {"pending": 1, "running": 3, "done": 0, "failed": 0}
        mock_queue.next_pending.return_value = None

        call_count = 0

        def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                qr._shutdown_requested = True

        with patch.dict(os.environ, {"PRAXIS_MAX_CONCURRENT_TASKS": "3"}):
            with patch("praxis.queue_runner.TaskQueue", return_value=mock_queue):
                with patch("praxis.queue_runner.CheckpointStore"):
                    with patch("praxis.queue_runner.Config"):
                        with patch("praxis.queue_runner.ConvergenceConfig"):
                            with patch("praxis.queue_runner._create_runtimes_for_queue", return_value=(MagicMock(), {}, {})):
                                with patch("praxis.queue_runner.Orchestrator"):
                                    with patch("praxis.queue_runner.time.sleep", side_effect=fake_sleep):
                                        try:
                                            qr.run_queue_loop(tmp_path)
                                        finally:
                                            qr._shutdown_requested = False

        # next_pending must never have been called because running >= max_concurrent
        mock_queue.next_pending.assert_not_called()

    def test_rate_limit_allows_below_cap(self, tmp_path: Path):
        """When running < max_concurrent, loop calls next_pending() normally."""
        import praxis.queue_runner as qr

        mock_queue = MagicMock()
        # 2 running, cap is 3 → slot available
        mock_queue.stats.return_value = {"pending": 1, "running": 2, "done": 0, "failed": 0}
        mock_queue.next_pending.return_value = None  # returns None so loop sleeps then exits

        sleep_count = 0

        def fake_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            qr._shutdown_requested = True

        with patch.dict(os.environ, {"PRAXIS_MAX_CONCURRENT_TASKS": "3"}):
            with patch("praxis.queue_runner.TaskQueue", return_value=mock_queue):
                with patch("praxis.queue_runner.CheckpointStore"):
                    with patch("praxis.queue_runner.Config"):
                        with patch("praxis.queue_runner.ConvergenceConfig"):
                            with patch("praxis.queue_runner._create_runtimes_for_queue", return_value=(MagicMock(), {}, {})):
                                with patch("praxis.queue_runner.Orchestrator"):
                                    with patch("praxis.queue_runner.time.sleep", side_effect=fake_sleep):
                                        try:
                                            qr.run_queue_loop(tmp_path)
                                        finally:
                                            qr._shutdown_requested = False

        # next_pending was called (slot was available)
        mock_queue.next_pending.assert_called()

    def test_rate_limit_env_var_override(self, tmp_path: Path):
        """When PRAXIS_MAX_CONCURRENT_TASKS=1, cap is respected at 1."""
        import praxis.queue_runner as qr

        mock_queue = MagicMock()
        # 1 running, cap=1 → at cap → should NOT call next_pending
        mock_queue.stats.return_value = {"pending": 1, "running": 1, "done": 0, "failed": 0}
        mock_queue.next_pending.return_value = None

        call_count = 0

        def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                qr._shutdown_requested = True

        with patch.dict(os.environ, {"PRAXIS_MAX_CONCURRENT_TASKS": "1"}):
            with patch("praxis.queue_runner.TaskQueue", return_value=mock_queue):
                with patch("praxis.queue_runner.CheckpointStore"):
                    with patch("praxis.queue_runner.Config"):
                        with patch("praxis.queue_runner.ConvergenceConfig"):
                            with patch("praxis.queue_runner._create_runtimes_for_queue", return_value=(MagicMock(), {}, {})):
                                with patch("praxis.queue_runner.Orchestrator"):
                                    with patch("praxis.queue_runner.time.sleep", side_effect=fake_sleep):
                                        try:
                                            qr.run_queue_loop(tmp_path)
                                        finally:
                                            qr._shutdown_requested = False

        # At cap (running=1, max=1) → next_pending never called
        mock_queue.next_pending.assert_not_called()

    def test_rate_limit_default_is_3(self, tmp_path: Path):
        """When PRAXIS_MAX_CONCURRENT_TASKS is unset, default of 3 is used."""
        import praxis.queue_runner as qr

        mock_queue = MagicMock()
        # 3 running, unset env → default 3 → at cap → should NOT call next_pending
        mock_queue.stats.return_value = {"pending": 1, "running": 3, "done": 0, "failed": 0}
        mock_queue.next_pending.return_value = None

        call_count = 0

        def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                qr._shutdown_requested = True

        # Remove the env var if present to test the default
        env_without_key = {k: v for k, v in os.environ.items() if k != "PRAXIS_MAX_CONCURRENT_TASKS"}
        with patch.dict(os.environ, env_without_key, clear=True):
            with patch("praxis.queue_runner.TaskQueue", return_value=mock_queue):
                with patch("praxis.queue_runner.CheckpointStore"):
                    with patch("praxis.queue_runner.Config"):
                        with patch("praxis.queue_runner.ConvergenceConfig"):
                            with patch("praxis.queue_runner._create_runtimes_for_queue", return_value=(MagicMock(), {}, {})):
                                with patch("praxis.queue_runner.Orchestrator"):
                                    with patch("praxis.queue_runner.time.sleep", side_effect=fake_sleep):
                                        try:
                                            qr.run_queue_loop(tmp_path)
                                        finally:
                                            qr._shutdown_requested = False

        # At cap with default=3 → next_pending never called
        mock_queue.next_pending.assert_not_called()
