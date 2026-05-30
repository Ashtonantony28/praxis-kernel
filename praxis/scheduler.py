"""
Cron-style task scheduler for Praxis.

Optional dep: pip install praxis[scheduler]  (installs croniter>=1.0)
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from praxis.queue import Task, TaskQueue

# ---------------------------------------------------------------------------
# HEARTBEAT.md support
# ---------------------------------------------------------------------------

# In-memory dedup: set of (section_title, iso_date_str) tuples.
# Reset on process restart — intentional, prevents same section firing twice
# within a run but allows it to fire again after restart on a new day.
_heartbeat_fired: set[tuple[str, str]] = set()

# Tracks the last time check_heartbeat() actually ran, for interval enforcement.
_heartbeat_last_run: datetime | None = None

_DAY_SPECS: dict[str, set[int]] = {
    "daily":    {0, 1, 2, 3, 4, 5, 6},
    "weekdays": {0, 1, 2, 3, 4},
    "weekends": {5, 6},
    "monday":   {0},
    "tuesday":  {1},
    "wednesday":{2},
    "thursday": {3},
    "friday":   {4},
    "saturday": {5},
    "sunday":   {6},
}

_WHEN_RE = re.compile(
    r"^\s*when:\s*(\w+)\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$",
    re.IGNORECASE,
)


def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _parse_heartbeat(content: str) -> list[dict]:
    """
    Parse HEARTBEAT.md content into a list of section dicts:
      {"title": str, "day_spec": str, "start": time, "end": time, "body": str}

    Only H2 sections (## Title) with a valid `when:` line immediately below are included.
    Content body is collected (stripped) but never logged verbatim.
    """
    sections = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            title = line[3:].strip()
            # Look for when: on the very next non-blank line
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines):
                m = _WHEN_RE.match(lines[j])
                if m:
                    day_spec_raw, start_raw, end_raw = m.group(1), m.group(2), m.group(3)
                    day_spec = day_spec_raw.lower()
                    if day_spec in _DAY_SPECS:
                        try:
                            start_t = _parse_time(start_raw)
                            end_t = _parse_time(end_raw)
                        except ValueError:
                            i += 1
                            continue
                        # Collect body lines until the next H2 or EOF
                        body_lines = []
                        k = j + 1
                        while k < len(lines) and not lines[k].startswith("## "):
                            body_lines.append(lines[k])
                            k += 1
                        sections.append({
                            "title": title,
                            "day_spec": day_spec,
                            "start": start_t,
                            "end": end_t,
                            "body": "\n".join(body_lines).strip(),
                        })
                        i = k
                        continue
        i += 1
    return sections


def check_heartbeat(
    queue: TaskQueue,
    workspace_root: Path,
    *,
    heartbeat_interval_minutes: int = 30,
) -> list[str]:
    """
    Read .praxis/HEARTBEAT.md, find sections whose `when:` window matches the
    current local time and weekday, and enqueue a low-priority Task for each
    that hasn't already fired today (in-memory dedup).

    Returns a list of section titles that were enqueued this call.

    Never logs HEARTBEAT.md content verbatim.
    """
    global _heartbeat_last_run

    now_local = datetime.now()  # local time for user-facing schedule matching
    now_utc = datetime.now(timezone.utc)

    # Enforce interval — only run if enough time has elapsed since last run.
    if _heartbeat_last_run is not None:
        elapsed_minutes = (now_utc - _heartbeat_last_run.replace(tzinfo=timezone.utc)
                           if _heartbeat_last_run.tzinfo is None
                           else (now_utc - _heartbeat_last_run).total_seconds() / 60)
        if isinstance(elapsed_minutes, float) and elapsed_minutes < heartbeat_interval_minutes:
            return []

    _heartbeat_last_run = now_utc

    heartbeat_path = workspace_root / ".praxis" / "HEARTBEAT.md"
    if not heartbeat_path.exists():
        return []

    try:
        content = heartbeat_path.read_text(encoding="utf-8")
    except OSError:
        return []

    sections = _parse_heartbeat(content)

    today_str = now_local.date().isoformat()
    current_weekday = now_local.weekday()  # Monday=0, Sunday=6
    current_time = now_local.time().replace(second=0, microsecond=0)

    enqueued: list[str] = []

    for sec in sections:
        # Check weekday match
        if current_weekday not in _DAY_SPECS.get(sec["day_spec"], set()):
            continue

        # Check time window
        if not (sec["start"] <= current_time < sec["end"]):
            continue

        # Dedup: only fire once per (title, date) per process lifetime
        dedup_key = (sec["title"], today_str)
        if dedup_key in _heartbeat_fired:
            continue

        # Build a prompt that describes the section intent without dumping personal content
        prompt = (
            f"[HEARTBEAT] {sec['title']}: {sec['body']}"
            if sec["body"]
            else f"[HEARTBEAT] {sec['title']}"
        )

        # LOW priority = 10 (higher number = lower priority in TaskQueue.next_pending sort)
        queue.ensure_dirs()
        queue.append(Task.create(prompt=prompt, priority=10))

        _heartbeat_fired.add(dedup_key)
        enqueued.append(sec["title"])
        sys.stderr.write(f"[praxis] heartbeat: enqueued section '{sec['title']}'\n")

    return enqueued


def _now_utc() -> str:
    """Return current UTC time as ISO8601 string (no microseconds)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _require_croniter():
    """Lazily import croniter, raising ImportError with install instructions if absent."""
    try:
        import croniter as _croniter
        return _croniter
    except ImportError:
        raise ImportError(
            "[praxis] croniter not installed. Run: pip install praxis[scheduler]\n"
            "Or: pip install croniter>=1.0"
        )


def _compute_next_run(schedule: str) -> str:
    """Compute the next run time for a cron expression. Raises ValueError if invalid."""
    croniter_mod = _require_croniter()
    cron = croniter_mod.croniter(schedule, datetime.now(timezone.utc))
    return cron.get_next(datetime).isoformat()


@dataclass
class ScheduledTask:
    id: str
    name: str
    prompt: str
    schedule: str
    enabled: bool = True
    last_run: str | None = None
    next_run: str | None = None
    created_at: str = field(default_factory=_now_utc)

    def to_dict(self) -> dict:
        return {k: v for k, v in vars(self).items()}

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduledTask":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class CronScheduler:
    """Cron-style scheduler that dispatches tasks to a TaskQueue when they come due."""

    def __init__(self, queue: TaskQueue, schedule_file: Path, log_file: Path) -> None:
        self._queue = queue
        self._schedule_file = schedule_file
        self._log_file = log_file
        self._tasks: list[ScheduledTask] = []

    def load(self) -> None:
        """Load tasks from schedule_file. Creates empty [] if absent, creates parent dir."""
        self._schedule_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._schedule_file.exists():
            self._tasks = []
            return
        raw = self._schedule_file.read_text(encoding="utf-8").strip()
        if not raw:
            self._tasks = []
            return
        data = json.loads(raw)
        self._tasks = [ScheduledTask.from_dict(d) for d in data]

    def save(self) -> None:
        """Atomically write tasks back to schedule_file as JSON array."""
        self._schedule_file.parent.mkdir(parents=True, exist_ok=True)
        # Write to temp then rename for atomicity
        tmp = self._schedule_file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([t.to_dict() for t in self._tasks], indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._schedule_file)

    def add_task(self, name: str, schedule: str, prompt: str) -> ScheduledTask:
        """
        Create a ScheduledTask, compute next_run via croniter, append to self._tasks,
        return the new task. Does NOT call save() — caller must call save() after.
        Raises ImportError (with install instructions) if croniter not installed.
        Raises ValueError if cron expression is invalid.
        """
        next_run = _compute_next_run(schedule)  # raises ImportError or ValueError as needed
        task = ScheduledTask(
            id=str(uuid.uuid4()),
            name=name,
            prompt=prompt,
            schedule=schedule,
            enabled=True,
            last_run=None,
            next_run=next_run,
            created_at=_now_utc(),
        )
        self._tasks.append(task)
        return task

    def remove_task(self, task_id: str) -> None:
        """Remove task by id. Raises KeyError if not found. Does NOT call save()."""
        for i, t in enumerate(self._tasks):
            if t.id == task_id:
                del self._tasks[i]
                return
        raise KeyError(f"No scheduled task with id {task_id!r}")

    def enable_task(self, task_id: str) -> None:
        """Set enabled=True. Raises KeyError if not found. Does NOT call save()."""
        for t in self._tasks:
            if t.id == task_id:
                t.enabled = True
                return
        raise KeyError(f"No scheduled task with id {task_id!r}")

    def disable_task(self, task_id: str) -> None:
        """Set enabled=False. Raises KeyError if not found. Does NOT call save()."""
        for t in self._tasks:
            if t.id == task_id:
                t.enabled = False
                return
        raise KeyError(f"No scheduled task with id {task_id!r}")

    def list_tasks(self) -> list[ScheduledTask]:
        """Return a copy of the current task list."""
        return list(self._tasks)

    def tick(self) -> list[str]:
        """
        Check all enabled tasks. For each where next_run <= now():
          1. Check if a task with the same prompt is already pending OR running in the queue
             (dedup: check self._queue tasks). If already queued, skip.
          2. Otherwise: self._queue.append(Task.create(prompt=task.prompt, priority=5)).
          3. Update task.last_run = now_utc(), task.next_run = next croniter run after now.
          4. Append log line to self._log_file.
        Call self.save() once after all dispatches.
        Return list of dispatched task names.
        """
        now_str = _now_utc()
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)

        # Collect existing pending+running prompts for dedup
        existing_tasks = self._queue._read_all()
        active_prompts = {
            t.prompt for t in existing_tasks if t.status in ("pending", "running")
        }

        dispatched: list[str] = []
        any_dispatched = False

        for task in self._tasks:
            if not task.enabled:
                continue
            if task.next_run is None:
                continue
            # Parse next_run; handle both naive and aware ISO strings
            try:
                next_run_str = task.next_run
                # Handle timezone-aware and naive datetimes
                if next_run_str.endswith("Z"):
                    next_run_str = next_run_str[:-1] + "+00:00"
                next_run_dt = datetime.fromisoformat(next_run_str)
                # Make naive datetimes UTC-aware for comparison
                if next_run_dt.tzinfo is None:
                    next_run_dt = next_run_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            if next_run_dt > now_dt:
                continue

            # Dedup check
            if task.prompt in active_prompts:
                # Still update next_run so we don't keep re-checking this slot
                try:
                    task.next_run = _compute_next_run(task.schedule)
                except (ImportError, ValueError):
                    pass
                continue

            # Dispatch to queue
            self._queue.ensure_dirs()
            self._queue.append(Task.create(prompt=task.prompt, priority=5))
            active_prompts.add(task.prompt)

            # Update task metadata
            task.last_run = now_str
            try:
                task.next_run = _compute_next_run(task.schedule)
            except (ImportError, ValueError):
                task.next_run = None

            # Append to log
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            log_ts = now_str.replace("T", " ").replace("+00:00", "Z")
            with self._log_file.open("a", encoding="utf-8") as f:
                f.write(f"{log_ts} DISPATCH {task.name}\n")

            dispatched.append(task.name)
            any_dispatched = True

        if any_dispatched:
            self.save()

        return dispatched
