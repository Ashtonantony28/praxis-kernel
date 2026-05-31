"""Task queue for unattended operation — reads/writes .praxis/queue/tasks.jsonl."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def create(cls, prompt: str, priority: int = 0, stages: list[str] | None = None) -> Task:
        return cls(
            id=uuid.uuid4().hex[:12],
            prompt=prompt,
            priority=priority,
            stages=stages,
        )


class TaskQueue:
    """Persistent task queue backed by a JSONL file."""

    def __init__(self, queue_dir: Path) -> None:
        self.queue_dir = queue_dir
        self.tasks_file = queue_dir / "tasks.jsonl"
        self.results_dir = queue_dir / "results"

    def ensure_dirs(self) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> list[Task]:
        if not self.tasks_file.exists():
            return []
        tasks = []
        for line in self.tasks_file.read_text().splitlines():
            line = line.strip()
            if line:
                tasks.append(Task.from_dict(json.loads(line)))
        return tasks

    def _write_all(self, tasks: list[Task]) -> None:
        self.ensure_dirs()
        lines = [json.dumps(t.to_dict()) for t in tasks]
        self.tasks_file.write_text("\n".join(lines) + "\n" if lines else "")

    def append(self, task: Task) -> None:
        self.ensure_dirs()
        with self.tasks_file.open("a") as f:
            f.write(json.dumps(task.to_dict()) + "\n")

    def next_pending(self) -> Task | None:
        """Return the highest-priority pending task (lowest number, then oldest)."""
        tasks = self._read_all()
        pending = [t for t in tasks if t.status == "pending"]
        if not pending:
            return None
        pending.sort(key=lambda t: (t.priority, t.created_at))
        return pending[0]

    def update_status(
        self,
        task_id: str,
        status: str,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        tasks = self._read_all()
        now = datetime.now(timezone.utc).isoformat()
        for t in tasks:
            if t.id == task_id:
                t.status = status
                if status == "running":
                    t.started_at = now
                if status in ("done", "failed"):
                    t.completed_at = now
                if result is not None:
                    t.result = result
                if error is not None:
                    t.error = error
                break
        self._write_all(tasks)

    def write_result(self, task_id: str, content: str) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        (self.results_dir / f"{task_id}.txt").write_text(content)

    def recover_interrupted(self) -> int:
        """Mark any 'running' tasks as 'failed' — they were interrupted by a crash."""
        tasks = self._read_all()
        recovered = 0
        for t in tasks:
            if t.status == "running":
                t.status = "failed"
                t.error = "interrupted — process exited before completion"
                t.completed_at = datetime.now(timezone.utc).isoformat()
                recovered += 1
        if recovered:
            self._write_all(tasks)
        return recovered

    def stats(self) -> dict[str, int]:
        tasks = self._read_all()
        counts: dict[str, int] = {"pending": 0, "running": 0, "done": 0, "failed": 0}
        for t in tasks:
            counts[t.status] = counts.get(t.status, 0) + 1
        return counts

    def move_to_dead_letter(self, task: "Task", error: str, max_retries: int) -> None:
        """Mark task failed and append a copy to dead_letter.jsonl for human review."""
        from datetime import datetime, timezone
        self.update_status(task.id, "failed", error=f"dead-lettered after {max_retries} retries: {error}")
        dl_file = self.queue_dir / "dead_letter.jsonl"
        entry = task.to_dict()
        entry["dead_lettered_at"] = datetime.now(timezone.utc).isoformat()
        entry["final_error"] = error
        entry["retries_exhausted"] = max_retries
        with dl_file.open("a") as f:
            f.write(json.dumps(entry) + "\n")
