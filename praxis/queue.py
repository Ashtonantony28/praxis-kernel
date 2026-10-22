"""Task queue for unattended operation — reads/writes .praxis/queue/tasks.jsonl.

tasks.jsonl is an append-only log: each line is a full task record and the last
line for an id wins. Every read and write holds an fcntl lock on tasks.jsonl.lock;
compaction rewrites one line per task via atomic rename.
"""

from __future__ import annotations

import copy
import fcntl
import heapq
import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Task:
    id: str
    prompt: str
    priority: int = 0
    status: str = "pending"
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    result: str | None = None
    error: str | None = None
    stages: list[str] | None = None
    origin: str = "queue"
    mode: str | None = None
    cost_budget_usd: float | None = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def create(
        cls,
        prompt: str,
        priority: int = 0,
        stages: list[str] | None = None,
        *,
        origin: str = "queue",
        mode: str | None = None,
        cost_budget_usd: float | None = None,
    ) -> Task:
        return cls(
            id=uuid.uuid4().hex[:12],
            prompt=prompt,
            priority=priority,
            stages=stages,
            origin=origin,
            mode=mode,
            cost_budget_usd=cost_budget_usd,
        )


class TaskStore(Protocol):
    def append(self, task: Task) -> None: ...
    def claim(self) -> Task | None: ...  # atomically pending -> running
    def update(self, task_id: str, **fields: Any) -> None: ...


_COMPACT_MIN_LINES = 1000


class TaskQueue:
    """Persistent task queue backed by a JSONL log; implements TaskStore."""

    def __init__(self, queue_dir: Path) -> None:
        self.queue_dir = queue_dir
        self.tasks_file = queue_dir / "tasks.jsonl"
        self.lock_file = queue_dir / "tasks.jsonl.lock"
        self.results_dir = queue_dir / "results"
        self._mutex = threading.RLock()
        self._depth = 0
        self._reset_cache()

    def ensure_dirs(self) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    # --- locking and log folding ---

    def _reset_cache(self, ino: int = 0) -> None:
        self._tasks: dict[str, Task] = {}
        self._heap: list[tuple[int, str, str]] = []
        self._ino = ino
        self._offset = 0
        self._lines = 0

    @contextmanager
    def _locked(self, exclusive: bool = True):
        with self._mutex:
            if self._depth:
                yield
                return
            if not self.queue_dir.exists():
                if not exclusive:
                    self._reset_cache()
                    yield
                    return
                self.ensure_dirs()
            with open(self.lock_file, "a") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
                self._depth += 1
                try:
                    self._sync()
                    yield
                finally:
                    self._depth -= 1
                    fcntl.flock(lf, fcntl.LOCK_UN)

    def _sync(self) -> None:
        """Fold any lines appended since the last read; reload if the file was replaced."""
        try:
            st = os.stat(self.tasks_file)
        except FileNotFoundError:
            self._reset_cache()
            return
        if st.st_ino != self._ino or st.st_size < self._offset:
            self._reset_cache(st.st_ino)
        if st.st_size == self._offset:
            return
        with open(self.tasks_file, "rb") as f:
            f.seek(self._offset)
            chunk = f.read()
        end = chunk.rfind(b"\n") + 1  # ignore a partial trailing line from an unlocked writer
        for line in chunk[:end].splitlines():
            if line.strip():
                self._fold(Task.from_dict(json.loads(line)))
        self._offset += end

    def _fold(self, task: Task) -> None:
        self._tasks[task.id] = task
        self._lines += 1
        if task.status == "pending":
            heapq.heappush(self._heap, (task.priority, task.created_at, task.id))

    def _write(self, task: Task) -> None:
        data = (json.dumps(task.to_dict()) + "\n").encode()
        with open(self.tasks_file, "ab") as f:
            f.write(data)
        if self._offset == 0 and not self._ino:
            self._ino = os.stat(self.tasks_file).st_ino
        self._offset += len(data)
        self._fold(copy.copy(task))
        if self._lines > max(_COMPACT_MIN_LINES, 2 * len(self._tasks)):
            self._compact()

    def _compact(self) -> None:
        tmp = self.tasks_file.with_suffix(".jsonl.tmp")
        tasks = list(self._tasks.values())
        tmp.write_text("".join(json.dumps(t.to_dict()) + "\n" for t in tasks))
        os.replace(tmp, self.tasks_file)
        self._reset_cache(os.stat(self.tasks_file).st_ino)
        self._offset = os.stat(self.tasks_file).st_size
        for t in tasks:
            self._fold(t)

    def _peek_pending(self) -> Task | None:
        while self._heap:
            prio, created, tid = self._heap[0]
            t = self._tasks.get(tid)
            if t is not None and t.status == "pending" and (t.priority, t.created_at) == (prio, created):
                return t
            heapq.heappop(self._heap)
        return None

    # --- TaskStore ---

    def append(self, task: Task) -> None:
        with self._locked():
            self._write(task)

    def claim(self) -> Task | None:
        """Atomically move the highest-priority pending task to running and return it."""
        with self._locked():
            t = self._peek_pending()
            if t is None:
                return None
            heapq.heappop(self._heap)
            t = copy.copy(t)
            t.status = "running"
            t.started_at = datetime.now(timezone.utc).isoformat()
            self._write(t)
            return copy.copy(t)

    def update(self, task_id: str, **fields: Any) -> None:
        unknown = set(fields) - set(Task.__dataclass_fields__) | ({"id"} & set(fields))
        if unknown:
            raise ValueError(f"unknown task field(s): {sorted(unknown)}")
        with self._locked():
            current = self._tasks.get(task_id)
            if current is None:
                return
            t = copy.copy(current)
            status = fields.get("status")
            now = datetime.now(timezone.utc).isoformat()
            if status == "running":
                t.started_at = now
            if status in ("done", "failed"):
                t.completed_at = now
            for k, v in fields.items():
                setattr(t, k, v)
            self._write(t)

    # --- legacy wrappers and helpers ---

    def get(self, task_id: str) -> Task | None:
        with self._locked(exclusive=False):
            t = self._tasks.get(task_id)
            return copy.copy(t) if t is not None else None

    def _read_all(self) -> list[Task]:
        with self._locked(exclusive=False):
            return [copy.copy(t) for t in self._tasks.values()]

    def _write_all(self, tasks: list[Task]) -> None:
        with self._locked():
            self._tasks = {t.id: copy.copy(t) for t in tasks}
            self._compact()

    def compact(self) -> None:
        with self._locked():
            if self.tasks_file.exists():
                self._compact()

    def next_pending(self) -> Task | None:
        """Return the highest-priority pending task (lowest number, then oldest) without claiming it."""
        with self._locked(exclusive=False):
            t = self._peek_pending()
            return copy.copy(t) if t else None

    def update_status(
        self,
        task_id: str,
        status: str,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {"status": status}
        if result is not None:
            fields["result"] = result
        if error is not None:
            fields["error"] = error
        self.update(task_id, **fields)

    def write_result(self, task_id: str, content: str) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        (self.results_dir / f"{task_id}.txt").write_text(content)

    def recover_interrupted(self) -> int:
        """Mark any 'running' tasks as 'failed' — they were interrupted by a crash."""
        with self._locked():
            running = [t.id for t in self._tasks.values() if t.status == "running"]
            for tid in running:
                self.update(tid, status="failed", error="interrupted — process exited before completion")
        return len(running)

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {"pending": 0, "running": 0, "done": 0, "failed": 0}
        with self._locked(exclusive=False):
            for t in self._tasks.values():
                counts[t.status] = counts.get(t.status, 0) + 1
        return counts

    def move_to_dead_letter(self, task: "Task", error: str, max_retries: int) -> None:
        """Mark task failed and append a copy to dead_letter.jsonl for human review."""
        self.update_status(task.id, "failed", error=f"dead-lettered after {max_retries} retries: {error}")
        dl_file = self.queue_dir / "dead_letter.jsonl"
        entry = task.to_dict()
        entry["dead_lettered_at"] = datetime.now(timezone.utc).isoformat()
        entry["final_error"] = error
        entry["retries_exhausted"] = max_retries
        with dl_file.open("a") as f:
            f.write(json.dumps(entry) + "\n")
