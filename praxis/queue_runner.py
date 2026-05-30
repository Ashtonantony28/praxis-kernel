"""Queue processing loop — polls tasks.jsonl, runs each through the orchestrator."""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

from .checkpoint import Checkpoint, CheckpointStore
from .config import Config
from .convergence import ConvergenceConfig, detect_task_type
from .orchestrator import Orchestrator
from .queue import Task, TaskQueue
from .runtime import ClaudeCodeRuntime, LocalRuntime, OpenAICloudRuntime
from .runtime.base import Runtime


_shutdown_requested = False


def _handle_sigterm(signum: int, frame: object) -> None:
    global _shutdown_requested
    _shutdown_requested = True
    sys.stderr.write("[praxis] shutdown requested, finishing current task…\n")


def _create_runtimes_for_queue(
    conv: ConvergenceConfig,
) -> tuple[Runtime, dict[str, Runtime], dict[str, Runtime]]:
    """Create runtimes — same logic as __main__._create_runtimes.

    Returns (default_runtime, subagent_overrides, all_runtimes_by_name).
    all_runtimes_by_name is keyed by runtime name string (e.g. "claude", "cloud").
    """
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
    return default, overrides, runtimes


def _run_single_task(
    task: Task,
    orch: Orchestrator,
    queue: TaskQueue,
    cp_store: CheckpointStore,
    *,
    conv: "ConvergenceConfig | None" = None,
    all_runtimes: "dict[str, Runtime] | None" = None,
    config: "Config | None" = None,
) -> None:
    """Execute a single task, with checkpoint support for multi-stage tasks.

    When conv, all_runtimes, and config are provided, detects the task type
    from the prompt and routes to the appropriate runtime if configured.
    """
    # Task-type routing: select runtime based on detected task type
    task_orch = orch
    if conv is not None and all_runtimes is not None and config is not None:
        task_type = detect_task_type(task.prompt)
        runtime_name = conv.runtime_for_task_type(task_type)
        if runtime_name != conv.default_runtime and runtime_name in all_runtimes:
            sys.stderr.write(
                f"[praxis] task {task.id}: type={task_type!r}, "
                f"routing to runtime={runtime_name!r}\n"
            )
            task_orch = Orchestrator(all_runtimes[runtime_name], config)

    if task.stages:
        _run_staged_task(task, task_orch, queue, cp_store)
    else:
        _run_atomic_task(task, task_orch, queue)


def _run_atomic_task(task: Task, orch: Orchestrator, queue: TaskQueue) -> None:
    """Run a task as a single orchestrator call.

    Atomic tasks are not interrupted by SIGTERM; they run to completion.
    This means that if a SIGTERM is received during an atomic task, the task
    will still attempt to complete. The `recover_interrupted` function
    marks tasks as 'failed' if they were 'running' at the time of interruption.
    It cannot distinguish between an atomic task that crashed and one that
    completed successfully after SIGTERM but before the process exited.
    This is by design for atomic tasks to ensure they finish their work.
    """
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


def _start_scheduler_thread(queue: TaskQueue, workspace_root: Path) -> None:
    """Start a background daemon thread that runs CronScheduler.tick() and
    check_heartbeat() on a configurable interval.

    - Reads PRAXIS_SCHEDULER_POLL_INTERVAL env var (default 60 seconds) for
      cron tick frequency.
    - Reads PRAXIS_HEARTBEAT_INTERVAL_MINUTES env var (default 30) for how
      often check_heartbeat() actually fires (enforced inside check_heartbeat).
    - Creates CronScheduler with schedule_file and log_file under workspace_root.
    - Calls scheduler.load() before starting the thread.
    - Thread is daemon=True — dies automatically when the main process exits.
    - Thread loop: while True: scheduler.tick(); check_heartbeat(); sleep(poll_interval)
    - If croniter is not installed (ImportError), the cron scheduler is skipped but
      heartbeat checking still runs (does NOT crash the queue runner).
    """
    from praxis.scheduler import check_heartbeat

    heartbeat_interval = int(os.environ.get("PRAXIS_HEARTBEAT_INTERVAL_MINUTES", "30"))

    try:
        from praxis.scheduler import CronScheduler
        poll_interval = int(os.environ.get("PRAXIS_SCHEDULER_POLL_INTERVAL", "60"))

        schedule_file = workspace_root / ".praxis" / "schedule" / "tasks.json"
        log_file = workspace_root / ".praxis" / "logs" / "scheduler.log"

        scheduler: "CronScheduler | None" = CronScheduler(
            queue=queue, schedule_file=schedule_file, log_file=log_file
        )
        scheduler.load()
        sys.stderr.write(f"[praxis] scheduler: polling every {poll_interval}s\n")
    except ImportError:
        sys.stderr.write(
            "[praxis] scheduler: croniter not installed — cron triggers disabled."
            " Run: pip install praxis[scheduler]\n"
        )
        scheduler = None
        poll_interval = int(os.environ.get("PRAXIS_SCHEDULER_POLL_INTERVAL", "60"))

    sys.stderr.write(
        f"[praxis] heartbeat: checking every {heartbeat_interval} min\n"
    )

    # Use a threading.Event for sleep so tests that mock time.sleep don't interfere.
    _stop_event = threading.Event()

    def _scheduler_loop() -> None:
        while True:
            if scheduler is not None:
                try:
                    scheduler.tick()
                except Exception as exc:
                    sys.stderr.write(
                        f"[praxis] scheduler: tick() raised unexpected error: {exc}\n"
                    )
            try:
                check_heartbeat(
                    queue,
                    workspace_root,
                    heartbeat_interval_minutes=heartbeat_interval,
                )
            except Exception as exc:
                sys.stderr.write(
                    f"[praxis] heartbeat: check raised unexpected error: {exc}\n"
                )
            _stop_event.wait(timeout=poll_interval)

    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="praxis-scheduler")
    thread.start()


def run_queue_loop(workspace: Path) -> None:
    """Main queue processing loop — polls for tasks and runs them."""
    signal.signal(signal.SIGTERM, _handle_sigterm)

    poll_interval = int(os.environ.get("PRAXIS_QUEUE_POLL_INTERVAL", "2"))
    config = Config.from_env()
    conv = ConvergenceConfig.load(config.workspace_root)
    default_runtime, runtime_overrides, all_runtimes = _create_runtimes_for_queue(conv)
    orch = Orchestrator(default_runtime, config, runtime_overrides=runtime_overrides)

    queue_dir = workspace / ".praxis" / "queue"
    queue = TaskQueue(queue_dir)
    queue.ensure_dirs()
    cp_store = CheckpointStore(queue_dir)

    # Recover any tasks interrupted by a previous crash.
    # For atomic tasks, this means tasks that were 'running' when the queue runner
    # was interrupted will be marked 'failed'. This is by design, as atomic tasks
    # are expected to run to completion even if a SIGTERM is received.
    # `recover_interrupted` cannot distinguish between a crashed atomic task and
    # one that completed successfully after SIGTERM but before the process exited.
    recovered = queue.recover_interrupted()
    if recovered:
        sys.stderr.write(f"[praxis] recovered {recovered} interrupted task(s)\n")

    _start_scheduler_thread(queue, workspace)

    max_concurrent = int(os.environ.get("PRAXIS_MAX_CONCURRENT_TASKS", "3"))
    sys.stderr.write(f"[praxis] queue loop started, polling every {poll_interval}s\n")
    sys.stderr.write(f"[praxis] queue loop: max_concurrent={max_concurrent}\n")

    while not _shutdown_requested:
        # Rate limit: do not start a new task if too many are already running
        running = queue.stats().get("running", 0)
        if running >= max_concurrent:
            time.sleep(poll_interval)
            continue

        task = queue.next_pending()
        if task is None:
            time.sleep(poll_interval)
            continue

        _run_single_task(
            task, orch, queue, cp_store,
            conv=conv,
            all_runtimes=all_runtimes,
            config=config,
        )

    sys.stderr.write("[praxis] queue loop stopped\n")
