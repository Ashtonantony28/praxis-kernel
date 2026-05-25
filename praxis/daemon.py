"""Daemon management — start/stop/status for background queue processing."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path


def _pid_file(workspace: Path) -> Path:
    return workspace / ".praxis" / "praxis.pid"


def _log_file(workspace: Path) -> Path:
    return workspace / ".praxis" / "logs" / "praxis.log"


def read_pid(workspace: Path) -> int | None:
    """Read PID from file, return None if missing or stale."""
    pf = _pid_file(workspace)
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
    except (ValueError, OSError):
        return None
    # Check if process is actually running
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        # Process not running — stale PID file
        pf.unlink(missing_ok=True)
        return None


def start_daemon(workspace: Path) -> None:
    """Start the queue processor as a background process."""
    pid = read_pid(workspace)
    if pid is not None:
        sys.stderr.write(f"[praxis] daemon already running (pid {pid})\n")
        raise SystemExit(1)

    log_path = _log_file(workspace)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Fork to background
    child_pid = os.fork()
    if child_pid > 0:
        # Parent — report and exit
        sys.stderr.write(
            f"[praxis] daemon started (pid {child_pid})\n"
            f"[praxis] log: {log_path}\n"
        )
        return

    # Child — detach and run
    os.setsid()

    # Write PID file
    pf = _pid_file(workspace)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(os.getpid()))

    # Redirect stdout/stderr to log file
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(log_fd, sys.stdout.fileno())
    os.dup2(log_fd, sys.stderr.fileno())
    os.close(log_fd)

    # Redirect stdin from /dev/null
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, sys.stdin.fileno())
    os.close(devnull)

    # Now run the queue loop — import here to avoid circular imports
    from .queue_runner import run_queue_loop

    run_queue_loop(workspace)


def stop_daemon(workspace: Path) -> None:
    """Stop the running daemon."""
    pid = read_pid(workspace)
    if pid is None:
        sys.stderr.write("[praxis] no daemon running\n")
        raise SystemExit(1)

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        sys.stderr.write(f"[praxis] failed to stop daemon: {exc}\n")
        raise SystemExit(1)

    # Clean up PID file
    _pid_file(workspace).unlink(missing_ok=True)
    sys.stderr.write(f"[praxis] daemon stopped (pid {pid})\n")


def report_status(workspace: Path) -> None:
    """Report daemon status and queue stats."""
    from .queue import TaskQueue

    pid = read_pid(workspace)
    queue_dir = workspace / ".praxis" / "queue"
    q = TaskQueue(queue_dir)
    stats = q.stats()

    if pid is not None:
        sys.stderr.write(f"[praxis] daemon running (pid {pid})\n")
    else:
        sys.stderr.write("[praxis] daemon not running\n")

    sys.stderr.write(
        f"[praxis] queue: {stats['pending']} pending, "
        f"{stats['running']} running, "
        f"{stats['done']} done, "
        f"{stats['failed']} failed\n"
    )
