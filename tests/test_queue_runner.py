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


# ---------- Loop robustness (SG6) ----------


class _FakeRuntime:
    def __init__(self) -> None:
        from praxis.runtime.cost import CostCircuitBreaker
        self._cost_breaker = CostCircuitBreaker(100.0)


class _FakeOrch:
    """Orchestrator stand-in: behaviour keyed on the prompt."""

    def __init__(self, runtime, *_a, **_kw) -> None:
        self.runtime = runtime
        self.runtime_overrides = {}
        self.agent_modes = {}
        self.calls: list[str] = []

    def run(self, prompt, model=None, mode=None):
        self.calls.append(prompt)
        if prompt == "exit":
            raise SystemExit(1)
        if prompt.startswith("spend:"):
            # gpt-4o input is $2.50 / 1M tokens
            self.runtime._cost_breaker.record_call("gpt-4o", int(float(prompt[6:]) / 2.5 * 1_000_000), 0)
        return f"ok {prompt}"


def _run_loop(tmp_path: Path, prompts: list, monkeypatch, **task_kw) -> tuple[TaskQueue, _FakeOrch, list]:
    import praxis.queue_runner as qr

    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    queue_dir = tmp_path / ".praxis" / "queue"
    q = TaskQueue(queue_dir)
    tasks = []
    for i, p in enumerate(prompts):
        kw = task_kw.get(p, {})
        t = Task.create(p, priority=i, **kw)
        q.append(t)
        tasks.append(t)
    rt = _FakeRuntime()
    orch = _FakeOrch(rt)

    def fake_sleep(_):
        qr._shutdown_requested = True

    with (
        patch("praxis.queue_runner.TaskQueue", return_value=q),
        patch("praxis.queue_runner.Config"),
        patch("praxis.queue_runner.ConvergenceConfig"),
        patch("praxis.queue_runner._create_runtimes_for_queue", return_value=(rt, {}, {"x": rt})),
        patch("praxis.queue_runner.Orchestrator", return_value=orch),
        patch("praxis.queue_runner._start_scheduler_thread"),
        patch("praxis.queue_runner.time.sleep", side_effect=fake_sleep),
    ):
        try:
            qr.run_queue_loop(tmp_path)
        finally:
            qr._shutdown_requested = False
    return q, orch, tasks


def _statuses(q: TaskQueue) -> dict:
    return {t.prompt: t.status for t in q._read_all()}


def test_loop_survives_system_exit(tmp_path: Path, monkeypatch):
    q, orch, _ = _run_loop(tmp_path, ["a", "exit", "b"], monkeypatch)
    assert _statuses(q) == {"a": "done", "exit": "failed", "b": "done"}
    assert orch.calls.count("exit") == 1  # SystemExit is not retried
    failed = [t for t in q._read_all() if t.status == "failed"][0]
    assert "SystemExit" in failed.error


def test_keyboard_interrupt_fails_task_then_propagates(queue: TaskQueue):
    t = Task.create("ki")
    queue.append(t)
    orch = MagicMock()
    orch.run.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        _run_atomic_task(queue.claim(), orch, queue)
    assert queue._read_all()[0].status == "failed"


def test_staged_task_system_exit_fails_one_task(queue: TaskQueue, cp_store: CheckpointStore):
    t = Task.create("staged", stages=["s1", "s2"])
    queue.append(t)
    orch = MagicMock()
    orch.run.side_effect = ["one", SystemExit(2)]
    _run_staged_task(queue.claim(), orch, queue, cp_store)
    assert queue._read_all()[0].status == "failed"
    assert cp_store.load(t.id).completed


def test_guard_catches_errors_outside_task_body(queue: TaskQueue, cp_store: CheckpointStore):
    import praxis.queue_runner as qr
    t = Task.create("x")
    queue.append(t)
    with patch("praxis.queue_runner._run_atomic_task", side_effect=SystemExit(9)):
        qr._run_task_guarded(queue.claim(), MagicMock(), queue, cp_store)
    assert queue._read_all()[0].status == "failed"


def test_cost_trip_fails_one_task(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_MAX_SESSION_COST", "2.0")
    q, _, _ = _run_loop(tmp_path, ["a", "spend:3", "b"], monkeypatch)
    assert _statuses(q) == {"a": "done", "spend:3": "failed", "b": "done"}


def test_cost_breaker_is_per_task(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_MAX_SESSION_COST", "2.0")
    q, _, _ = _run_loop(tmp_path, ["spend:1.5", "spend:1.6", "spend:1.7"], monkeypatch)
    assert set(_statuses(q).values()) == {"done"}


def test_cost_budget_usd_overrides_default(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_MAX_SESSION_COST", "2.0")
    q, _, _ = _run_loop(
        tmp_path, ["spend:1", "spend:1.5"], monkeypatch,
        **{"spend:1": {"cost_budget_usd": 0.5}, "spend:1.5": {"cost_budget_usd": 5.0}},
    )
    assert _statuses(q) == {"spend:1": "failed", "spend:1.5": "done"}


def test_retry_does_not_restage_actions(tmp_path: Path, queue: TaskQueue, cp_store: CheckpointStore):
    from praxis.integrations import linear
    from praxis.staging import STAGING_FILENAME, read_entries

    t = Task.create("file an issue")
    queue.append(t)
    attempts = []

    def run(prompt, **_):
        attempts.append(linear._stage_action("linear", "create_issue", {"title": "Flaky"}, tmp_path))
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return "done"

    orch = MagicMock()
    orch.run.side_effect = run
    with patch("praxis.queue_runner.time.sleep"):
        _run_single_task(queue.claim(), orch, queue, cp_store)

    entries = read_entries(tmp_path / ".praxis" / "staging" / STAGING_FILENAME)
    assert len(entries) == 1 and entries[0]["task_id"] == t.id
    assert attempts[1].startswith("Already staged")
    assert queue._read_all()[0].status == "done"


def test_task_type_routing_keeps_overrides_modes_and_model(queue: TaskQueue, cp_store: CheckpointStore):
    from praxis.convergence import ConvergenceConfig, TaskTypeRule

    conv = ConvergenceConfig(
        default_runtime="claude",
        overrides={"researcher": "local"},
        agent_modes={"researcher": "plan"},
        task_type_rules={"code": TaskTypeRule(runtime="cloud", model="gpt-4o")},
    )
    base = MagicMock()
    base.runtime_overrides = {"researcher": "local-rt"}
    base.agent_modes = {"researcher": "plan"}
    runtimes = {"claude": MagicMock(), "cloud": MagicMock(), "local": MagicMock()}
    t = Task.create("x")
    queue.append(t)
    with (
        patch("praxis.queue_runner.detect_task_type", return_value="code"),
        patch("praxis.queue_runner.Orchestrator") as orch_cls,
    ):
        orch_cls.return_value.run.return_value = "r"
        _run_single_task(queue.claim(), base, queue, cp_store, conv=conv, all_runtimes=runtimes, config=MagicMock())
    args, kwargs = orch_cls.call_args
    assert args[0] is runtimes["cloud"]
    assert kwargs["runtime_overrides"] == {"researcher": "local-rt"}
    assert kwargs["agent_modes"] == {"researcher": "plan"}
    orch_cls.return_value.run.assert_called_once_with("x", model="gpt-4o")
    base.run.assert_not_called()


# ---------- Daemon crash recovery ----------


class TestDaemonCrashRecovery:
    """Verify that run_queue_loop() recovers interrupted tasks at startup."""

    def test_run_queue_loop_recovers_running_tasks(self, tmp_path: Path):
        """Tasks stuck in 'running' state (from a previous crash) are marked 'failed'
        when the queue loop restarts, before any new task is processed."""
        import praxis.queue_runner as qr

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True)
        (queue_dir / "results").mkdir()

        real_queue = TaskQueue(queue_dir)
        real_queue.ensure_dirs()

        # Simulate a task that was left in 'running' state by a prior crash
        interrupted = Task.create("was running when daemon died")
        real_queue.append(interrupted)
        real_queue.update_status(interrupted.id, "running")

        # Verify the task is indeed 'running' before we start the loop
        pre = real_queue._read_all()
        assert pre[0].status == "running"

        # We'll pass the real queue to the loop via mock so we can inspect it
        # after recover_interrupted() is called at startup, before any tasks run.
        recovered_count = []

        original_recover = real_queue.recover_interrupted

        def spy_recover():
            count = original_recover()
            recovered_count.append(count)
            return count

        real_queue.recover_interrupted = spy_recover  # type: ignore[method-assign]

        # Patch enough of run_queue_loop so it exits immediately after startup
        call_count = 0

        def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            qr._shutdown_requested = True

        with patch("praxis.queue_runner.TaskQueue", return_value=real_queue):
            with patch("praxis.queue_runner.CheckpointStore"):
                with patch("praxis.queue_runner.Config"):
                    with patch("praxis.queue_runner.ConvergenceConfig"):
                        with patch(
                            "praxis.queue_runner._create_runtimes_for_queue",
                            return_value=(MagicMock(), {}, {}),
                        ):
                            with patch("praxis.queue_runner.Orchestrator"):
                                with patch(
                                    "praxis.queue_runner._start_scheduler_thread"
                                ):
                                    with patch(
                                        "praxis.queue_runner.time.sleep",
                                        side_effect=fake_sleep,
                                    ):
                                        try:
                                            qr.run_queue_loop(tmp_path)
                                        finally:
                                            qr._shutdown_requested = False

        # recover_interrupted() must have been called
        assert recovered_count, "recover_interrupted() was never called by run_queue_loop()"
        assert recovered_count[0] == 1, f"Expected 1 recovered task, got {recovered_count[0]}"

        # The interrupted task must now be marked 'failed'
        post = real_queue._read_all()
        assert post[0].status == "failed"
        assert "interrupted" in (post[0].error or "")

    def test_recover_does_not_affect_pending_or_done(self, tmp_path: Path):
        """recover_interrupted() must only touch 'running' tasks, not 'pending' or 'done'."""
        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True)

        q = TaskQueue(queue_dir)
        q.ensure_dirs()

        pending_task = Task.create("still pending")
        q.append(pending_task)

        done_task = Task.create("already done")
        q.append(done_task)
        q.update_status(done_task.id, "done", result="ok")

        running_task = Task.create("interrupted mid-run")
        q.append(running_task)
        q.update_status(running_task.id, "running")

        recovered = q.recover_interrupted()
        assert recovered == 1

        tasks = {t.id: t for t in q._read_all()}
        assert tasks[pending_task.id].status == "pending"
        assert tasks[done_task.id].status == "done"
        assert tasks[running_task.id].status == "failed"
        assert "interrupted" in (tasks[running_task.id].error or "")


# ---------- EventBus wiring ----------


def test_queue_runner_emits_started(queue: TaskQueue, mock_orch: MagicMock):
    """TASK_STARTED is published via the event bus when a task transitions to running."""
    from praxis.event_bus import TASK_STARTED
    task = Task.create("emit started")
    queue.append(task)

    mock_bus = MagicMock()
    with patch("praxis.event_bus.get_event_bus", return_value=mock_bus):
        _run_atomic_task(task, mock_orch, queue)

    mock_bus.publish_sync.assert_any_call(TASK_STARTED, {"task_id": task.id})


def test_queue_runner_emits_completed(queue: TaskQueue, mock_orch: MagicMock):
    """TASK_COMPLETED is published via the event bus when a task finishes successfully."""
    from praxis.event_bus import TASK_COMPLETED
    task = Task.create("emit completed")
    queue.append(task)

    mock_bus = MagicMock()
    with patch("praxis.event_bus.get_event_bus", return_value=mock_bus):
        _run_atomic_task(task, mock_orch, queue)

    mock_bus.publish_sync.assert_any_call(TASK_COMPLETED, {"task_id": task.id})


def test_queue_runner_emits_failed(queue: TaskQueue, mock_orch: MagicMock):
    """TASK_FAILED is published via the event bus after all retries are exhausted."""
    from praxis.event_bus import TASK_FAILED
    mock_orch.run.side_effect = RuntimeError("always fails")
    task = Task.create("emit failed")
    queue.append(task)

    mock_bus = MagicMock()
    with (
        patch("praxis.event_bus.get_event_bus", return_value=mock_bus),
        patch("praxis.queue_runner._TASK_MAX_RETRIES", 1),
        patch("praxis.queue_runner.time.sleep"),
    ):
        _run_atomic_task(task, mock_orch, queue)

    mock_bus.publish_sync.assert_any_call(TASK_FAILED, {"task_id": task.id})
