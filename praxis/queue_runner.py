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
from .runtime import ClaudeCodeCLIRuntime, ClaudeCodeRuntime, LocalRuntime, OpenAICloudRuntime
from .runtime.base import Runtime
from .modes import Mode
from .runtime.cost import CostCircuitBreaker
from .staging import task_scope


_shutdown_requested = False

_TASK_MAX_RETRIES = int(os.environ.get("PRAXIS_TASK_MAX_RETRIES", "3"))
_TASK_INITIAL_BACKOFF = float(os.environ.get("PRAXIS_TASK_INITIAL_BACKOFF_SECONDS", "5.0"))
_TASK_MAX_BACKOFF = float(os.environ.get("PRAXIS_TASK_MAX_BACKOFF_SECONDS", "60.0"))


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
    # ClaudeCodeCLIRuntime override: when PRAXIS_RUNTIME=claudecode (or auto-detected)
    _runtime_env = os.environ.get("PRAXIS_RUNTIME", "").lower().strip()
    _has_oauth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    _has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if _runtime_env == "claudecode" or (not _runtime_env and _has_oauth and not _has_api_key):
        _rt = ClaudeCodeCLIRuntime.from_env()
        sys.stderr.write("[praxis] runtime: claudecode (claude -p subprocess)\n")
        _runtimes: dict[str, Runtime] = {"claudecode": _rt}
        return _rt, {}, _runtimes

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
    notifier=None,
) -> None:
    """Execute a single task, with checkpoint support for multi-stage tasks.

    When conv, all_runtimes, and config are provided, detects the task type
    from the prompt and routes to the appropriate runtime and model if configured.
    Each task gets a fresh cost breaker (task.cost_budget_usd or the global default)
    and a staging scope keyed on its id, so retries do not re-stage actions.
    """
    task_orch = orch
    model = None
    if conv is not None and all_runtimes is not None and config is not None:
        task_type = detect_task_type(task.prompt)
        model = conv.model_for_task_type(task_type)
        runtime_name = conv.runtime_for_task_type(task_type)
        if runtime_name != conv.default_runtime and runtime_name in all_runtimes:
            sys.stderr.write(
                f"[praxis] task {task.id}: type={task_type!r}, "
                f"routing to runtime={runtime_name!r}\n"
            )
            task_orch = Orchestrator(
                all_runtimes[runtime_name],
                config,
                runtime_overrides=getattr(orch, "runtime_overrides", None),
                agent_modes=getattr(orch, "agent_modes", None),
            )

    _reset_cost_breakers(task_orch, all_runtimes, task.cost_budget_usd)
    with task_scope(task.id):
        if task.stages:
            _run_staged_task(task, task_orch, queue, cp_store)
        else:
            _run_atomic_task(task, task_orch, queue, notifier=notifier, model=model)


def _reset_cost_breakers(orch, all_runtimes: "dict[str, Runtime] | None", budget: float | None) -> None:
    runtimes = [getattr(orch, "runtime", None), *dict(getattr(orch, "runtime_overrides", None) or {}).values(),
                *(all_runtimes or {}).values()]
    for rt in {id(r): r for r in runtimes if r is not None}.values():
        if hasattr(rt, "_cost_breaker"):
            rt._cost_breaker = (
                CostCircuitBreaker(budget) if budget is not None else CostCircuitBreaker.from_env()
            )


def _run_task_guarded(task: Task, orch: Orchestrator, queue: TaskQueue, cp_store: CheckpointStore, **kw) -> None:
    """Task boundary: any BaseException fails this task only; KeyboardInterrupt still propagates."""
    try:
        _run_single_task(task, orch, queue, cp_store, **kw)
    except BaseException as exc:
        _fail_unfinished(queue, task.id, exc)
        if isinstance(exc, KeyboardInterrupt):
            raise


def _describe(exc: BaseException) -> str:
    return str(exc) if isinstance(exc, Exception) else f"{type(exc).__name__}: {exc}"


def _fail_unfinished(queue: TaskQueue, task_id: str, exc: BaseException) -> None:
    sys.stderr.write(f"[praxis] task {task_id} aborted: {_describe(exc)}\n")
    try:
        current = next((t for t in queue._read_all() if t.id == task_id), None)
        if current is None or current.status not in ("done", "failed"):
            queue.update(task_id, status="failed", error=_describe(exc))
    except Exception as inner:
        sys.stderr.write(f"[praxis] could not mark task {task_id} failed: {inner}\n")


def _run_kwargs(task: Task) -> dict:
    """orch.run kwargs for the task's mode; an unknown mode raises (fail closed)."""
    return {"mode": Mode.load(task.mode)} if task.mode else {}


def _run_atomic_task(
    task: Task, orch: Orchestrator, queue: TaskQueue, *, notifier=None, model: str | None = None
) -> None:
    """Run a task as a single orchestrator call, with exponential backoff on failure.

    Retries up to _TASK_MAX_RETRIES times (env: PRAXIS_TASK_MAX_RETRIES, default 3).
    Backoff: initial 5 s (PRAXIS_TASK_INITIAL_BACKOFF_SECONDS), doubles each attempt,
    capped at 60 s (PRAXIS_TASK_MAX_BACKOFF_SECONDS).
    After all retries exhausted, task is dead-lettered to queue_dir/dead_letter.jsonl.
    A BaseException (SystemExit, cost trip, KeyboardInterrupt) is not retried: the task
    is dead-lettered at once, and KeyboardInterrupt is re-raised afterwards.
    """
    if task.status != "running":
        queue.update(task.id, status="running")
    try:
        from .event_bus import TASK_STARTED, get_event_bus
        get_event_bus().publish_sync(TASK_STARTED, {"task_id": task.id})
    except Exception:
        pass
    sys.stderr.write(f"[praxis] running task {task.id}: {task.prompt[:80]}\n")
    run_kw = _run_kwargs(task)
    if model:
        run_kw["model"] = model

    last_exc: BaseException | None = None
    attempts = 0
    for attempt in range(_TASK_MAX_RETRIES):
        attempts = attempt + 1
        try:
            result = orch.run(task.prompt, **run_kw)
            queue.update(task.id, status="done", result=result)
            try:
                from .event_bus import TASK_COMPLETED, get_event_bus
                get_event_bus().publish_sync(TASK_COMPLETED, {"task_id": task.id})
            except Exception:
                pass
            queue.write_result(task.id, result)
            sys.stderr.write(f"[praxis] task {task.id} done\n")
            # Append to conversation log — best-effort
            try:
                from .memory.conversation_log import ConversationLog
                from .config import Config as _Config
                _cfg = _Config.from_env()
                _log = ConversationLog(_cfg.workspace_root)
                _log.append(
                    prompt=task.prompt[:500],
                    summary=result[:300] if result else "",
                    outcome="success",
                    task_type="queue_task",
                )
            except Exception:
                pass
            if notifier is not None:
                try:
                    notifier.notify_task_complete(task.prompt, "success", result)
                except Exception:
                    pass
            return
        except Exception as exc:
            last_exc = exc
            if attempt < _TASK_MAX_RETRIES - 1:
                backoff = min(_TASK_INITIAL_BACKOFF * (2 ** attempt), _TASK_MAX_BACKOFF)
                sys.stderr.write(
                    f"[praxis] task {task.id} attempt {attempt + 1}/{_TASK_MAX_RETRIES} failed: {exc}; "
                    f"retrying in {backoff:.1f}s\n"
                )
                time.sleep(backoff)
        except BaseException as exc:
            last_exc = exc
            break

    error_str = _describe(last_exc) if last_exc else "unknown error"
    sys.stderr.write(
        f"[praxis] task {task.id} failed after {attempts} attempt(s); moving to dead letter\n"
    )
    queue.move_to_dead_letter(task, error_str, attempts)
    try:
        from .event_bus import TASK_FAILED, get_event_bus
        get_event_bus().publish_sync(TASK_FAILED, {"task_id": task.id})
    except Exception:
        pass
    try:
        from .memory.conversation_log import ConversationLog
        from .config import Config as _Config
        _cfg = _Config.from_env()
        _log = ConversationLog(_cfg.workspace_root)
        _log.append(
            prompt=task.prompt[:500],
            summary=error_str[:300],
            outcome="failed",
            task_type="queue_task",
        )
    except Exception:
        pass
    if notifier is not None:
        try:
            notifier.notify_task_complete(task.prompt, "failed", error_str)
        except Exception:
            pass
    if isinstance(last_exc, KeyboardInterrupt):
        raise last_exc


def _run_staged_task(task: Task, orch: Orchestrator, queue: TaskQueue, cp_store: CheckpointStore) -> None:
    """Run a multi-stage task with checkpointing between stages."""
    cp = cp_store.load(task.id)
    if cp is None:
        cp = Checkpoint(task_id=task.id, stages=task.stages)

    if task.status != "running":
        queue.update(task.id, status="running")
    try:
        from .event_bus import TASK_STARTED, get_event_bus
        get_event_bus().publish_sync(TASK_STARTED, {"task_id": task.id})
    except Exception:
        pass
    sys.stderr.write(f"[praxis] running staged task {task.id} ({len(task.stages)} stages)\n")

    try:
        run_kw = _run_kwargs(task)
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

            result = orch.run(stage_prompt, **run_kw)
            cp.mark_stage_done(idx, result)
            cp_store.save(cp)

        final = cp.final_result()
        queue.update_status(task.id, "done", result=final)
        try:
            from .event_bus import TASK_COMPLETED, get_event_bus
            get_event_bus().publish_sync(TASK_COMPLETED, {"task_id": task.id})
        except Exception:
            pass
        queue.write_result(task.id, final)
        cp_store.remove(task.id)
        sys.stderr.write(f"[praxis] task {task.id} done (all stages)\n")

    except BaseException as exc:
        cp_store.save(cp)
        queue.update(task.id, status="failed", error=_describe(exc))
        try:
            from .event_bus import TASK_FAILED, get_event_bus
            get_event_bus().publish_sync(TASK_FAILED, {"task_id": task.id})
        except Exception:
            pass
        sys.stderr.write(f"[praxis] task {task.id} failed at stage: {_describe(exc)}\n")
        if isinstance(exc, KeyboardInterrupt):
            raise


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

    # Import check_heartbeat independently — it does not require croniter.
    # Provide a no-op fallback so _scheduler_loop never hits NameError even when
    # praxis.scheduler itself cannot be imported (e.g. during tests that patch
    # builtins.__import__ to simulate croniter being absent).
    try:
        from praxis.scheduler import check_heartbeat
    except ImportError:
        def check_heartbeat(*args, **kwargs):  # type: ignore[misc]
            """No-op: praxis.scheduler unavailable."""

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


def _start_ambient_monitor(queue: TaskQueue, workspace_root: Path) -> None:
    """Start ambient event monitor if PRAXIS_AMBIENT_ENABLED=true (default false).

    Enabled by setting PRAXIS_AMBIENT_ENABLED=true in .env.
    Polls email, calendar, Linear, GitHub in a background daemon thread.
    """
    if os.environ.get("PRAXIS_AMBIENT_ENABLED", "").lower() != "true":
        return
    try:
        from .ambient import AmbientMonitor
        monitor = AmbientMonitor(queue, workspace_root)
        monitor.start()
    except Exception as exc:
        sys.stderr.write(f"[praxis] ambient monitor failed to start: {exc}\n")


def _start_file_watcher(queue: TaskQueue) -> None:
    """Start FileWatcher if PRAXIS_WATCH_PATHS is set.

    Requires: pip install praxis[hooks]  (installs watchdog>=3.0).
    Silently skips if PRAXIS_WATCH_PATHS is not configured or watchdog is absent.
    """
    if not os.environ.get("PRAXIS_WATCH_PATHS", "").strip():
        return
    try:
        from .hooks_engine import FileWatcher
        watcher = FileWatcher(queue)
        watcher.start()
        sys.stderr.write("[praxis] file watcher started\n")
    except Exception as exc:
        sys.stderr.write(f"[praxis] file watcher failed to start: {exc}\n")


# TODO: TASK_QUEUED emitted from api.py POST /api/queue
def run_queue_loop(workspace: Path) -> None:
    """Main queue processing loop — polls for tasks and runs them."""
    signal.signal(signal.SIGTERM, _handle_sigterm)

    poll_interval = int(os.environ.get("PRAXIS_QUEUE_POLL_INTERVAL", "2"))
    config = Config.from_env()
    conv = ConvergenceConfig.load(config.workspace_root)
    default_runtime, runtime_overrides, all_runtimes = _create_runtimes_for_queue(conv)
    orch = Orchestrator(
        default_runtime, config, runtime_overrides=runtime_overrides, agent_modes=conv.agent_modes
    )

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

    from .notifier import Notifier as _Notifier
    _notifier = _Notifier(workspace)

    _start_scheduler_thread(queue, workspace)
    _start_ambient_monitor(queue, workspace)
    _start_file_watcher(queue)

    import os as _os
    if _os.environ.get("PRAXIS_MORNING_NOTIFY", "").lower() == "true":
        _handoff = workspace / ".praxis" / "memory" / "morning-handoff.md"
        try:
            _notifier.notify_morning_handoff(_handoff)
        except Exception:
            pass

    from .event_bus import enable_event_log, get_event_bus
    enable_event_log(workspace)
    sys.stderr.write(f"[praxis] queue loop started, polling every {poll_interval}s\n")
    try:
        # Tasks run one at a time: runtimes and their per-task cost breakers are shared.
        while not _shutdown_requested:
            try:
                task = queue.claim()
            except Exception as exc:
                sys.stderr.write(f"[praxis] queue claim failed: {exc}\n")
                task = None
            if task is None:
                time.sleep(poll_interval)
                continue

            _run_task_guarded(
                task, orch, queue, cp_store,
                conv=conv,
                all_runtimes=all_runtimes,
                config=config,
                notifier=_notifier,
            )

        # Send shutdown notification
        try:
            _notifier.notify(
                f"[Praxis] Shutting down. "
                f"Session complete."
            )
        except Exception:
            pass

        sys.stderr.write("[praxis] queue loop stopped\n")
    finally:
        get_event_bus().attach_log(None)
