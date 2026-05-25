"""Queue processing loop — polls tasks.jsonl, runs each through the orchestrator."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from .checkpoint import Checkpoint, CheckpointStore
from .config import Config
from .convergence import ConvergenceConfig
from .orchestrator import Orchestrator
from .queue import Task, TaskQueue
from .runtime import ClaudeCodeRuntime, LocalRuntime, OpenAICloudRuntime
from .runtime.base import Runtime


_shutdown_requested = False


def _handle_sigterm(signum: int, frame: object) -> None:
    global _shutdown_requested
    _shutdown_requested = True
    sys.stderr.write("[praxis] shutdown requested, finishing current task…\n")


def _create_runtimes_for_queue(conv: ConvergenceConfig) -> tuple[Runtime, dict[str, Runtime]]:
    """Create runtimes — same logic as __main__._create_runtimes."""
    runtimes: dict[str, Runtime] = {}

    if conv.needs_claude():
        rt = ClaudeCodeRuntime.from_env()
        sys.stderr.write(f"[praxis] runtime claude: auth={rt.auth_method}\n")
        runtimes["claude"] = rt

    if conv.needs_local():
        rt = LocalRuntime.from_env()
        sys.stderr.write(f"[praxis] runtime local: {rt.base_url}, model={rt.default_model}\n")
        runtimes["local"] = rt

    if conv.needs_cloud():
        rt = OpenAICloudRuntime.from_env()
        sys.stderr.write(f"[praxis] runtime cloud: {rt.base_url}, model={rt.default_model}\n")
        runtimes["cloud"] = rt

    default = runtimes[conv.default_runtime]
    overrides = {
        name: runtimes[rt_name]
        for name, rt_name in conv.overrides.items()
        if rt_name != conv.default_runtime
    }
    return default, overrides


def _run_single_task(task: Task, orch: Orchestrator, queue: TaskQueue, cp_store: CheckpointStore) -> None:
    """Execute a single task, with checkpoint support for multi-stage tasks."""
    if task.stages:
        _run_staged_task(task, orch, queue, cp_store)
    else:
        _run_atomic_task(task, orch, queue)


def _run_atomic_task(task: Task, orch: Orchestrator, queue: TaskQueue) -> None:
    """Run a task as a single orchestrator call."""
    queue.update_status(task.id, "running")
    sys.stderr.write(f"[praxis] running task {task.id}: {task.prompt[:80]}\n")
    try:
        result = orch.run(task.prompt)
        queue.update_status(task.id, "done", result=result)
        queue.write_result(task.id, result)
        sys.stderr.write(f"[praxis] task {task.id} done\n")
    except Exception as exc:
        queue.update_status(task.id, "failed", error=str(exc))
        sys.stderr.write(f"[praxis] task {task.id} failed: {exc}\n")


def _run_staged_task(task: Task, orch: Orchestrator, queue: TaskQueue, cp_store: CheckpointStore) -> None:
    """Run a multi-stage task with checkpointing between stages."""
    cp = cp_store.load(task.id)
    if cp is None:
        cp = Checkpoint(task_id=task.id, stages=task.stages)

    queue.update_status(task.id, "running")
    sys.stderr.write(f"[praxis] running staged task {task.id} ({len(task.stages)} stages)\n")

    try:
        while True:
            idx = cp.next_stage_index()
            if idx is None:
                break

            if _shutdown_requested:
                sys.stderr.write(f"[praxis] shutdown — pausing task {task.id} at stage {idx}\n")
                cp_store.save(cp)
                queue.update_status(task.id, "pending")
                return

            stage_prompt = task.stages[idx]
            sys.stderr.write(f"[praxis] task {task.id} stage {idx}: {stage_prompt[:60]}\n")

            result = orch.run(stage_prompt)
            cp.mark_stage_done(idx, result)
            cp_store.save(cp)

        final = cp.final_result()
        queue.update_status(task.id, "done", result=final)
        queue.write_result(task.id, final)
        cp_store.remove(task.id)
        sys.stderr.write(f"[praxis] task {task.id} done (all stages)\n")

    except Exception as exc:
        cp_store.save(cp)
        queue.update_status(task.id, "failed", error=str(exc))
        sys.stderr.write(f"[praxis] task {task.id} failed at stage: {exc}\n")


def run_queue_loop(workspace: Path) -> None:
    """Main queue processing loop — polls for tasks and runs them."""
    signal.signal(signal.SIGTERM, _handle_sigterm)

    poll_interval = int(os.environ.get("PRAXIS_QUEUE_POLL_INTERVAL", "2"))
    config = Config.from_env()
    conv = ConvergenceConfig.load(config.workspace_root)
    default_runtime, runtime_overrides = _create_runtimes_for_queue(conv)
    orch = Orchestrator(default_runtime, config, runtime_overrides=runtime_overrides)

    queue_dir = workspace / ".praxis" / "queue"
    queue = TaskQueue(queue_dir)
    queue.ensure_dirs()
    cp_store = CheckpointStore(queue_dir)

    # Recover any tasks interrupted by a previous crash
    recovered = queue.recover_interrupted()
    if recovered:
        sys.stderr.write(f"[praxis] recovered {recovered} interrupted task(s)\n")

    sys.stderr.write(f"[praxis] queue loop started, polling every {poll_interval}s\n")

    while not _shutdown_requested:
        task = queue.next_pending()
        if task is None:
            time.sleep(poll_interval)
            continue

        _run_single_task(task, orch, queue, cp_store)

    sys.stderr.write("[praxis] queue loop stopped\n")
