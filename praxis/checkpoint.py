"""Checkpoint system for session continuity — resumes multi-stage tasks after restart."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Checkpoint:
    task_id: str
    stages: list[str]
    completed: list[int] = field(default_factory=list)
    results: dict[str, str] = field(default_factory=dict)
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def next_stage_index(self) -> int | None:
        """Return the index of the next stage to run, or None if all done."""
        for i in range(len(self.stages)):
            if i not in self.completed:
                return i
        return None

    def mark_stage_done(self, index: int, result: str) -> None:
        if index not in self.completed:
            self.completed.append(index)
        self.results[str(index)] = result
        self.last_updated = datetime.now(timezone.utc).isoformat()

    def is_complete(self) -> bool:
        return len(self.completed) >= len(self.stages)

    def final_result(self) -> str:
        """Return concatenated results of all completed stages."""
        parts = []
        for i in sorted(self.completed):
            r = self.results.get(str(i), "")
            if r:
                parts.append(f"[Stage {i}: {self.stages[i]}]\n{r}")
        return "\n\n".join(parts)


class CheckpointStore:
    """Reads/writes checkpoint files for multi-stage tasks."""

    def __init__(self, queue_dir: Path) -> None:
        self.checkpoints_dir = queue_dir / "checkpoints"

    def _path(self, task_id: str) -> Path:
        return self.checkpoints_dir / f"{task_id}.json"

    def exists(self, task_id: str) -> bool:
        return self._path(task_id).exists()

    def load(self, task_id: str) -> Checkpoint | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        return Checkpoint.from_dict(json.loads(path.read_text()))

    def save(self, checkpoint: Checkpoint) -> None:
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self._path(checkpoint.task_id).write_text(
            json.dumps(checkpoint.to_dict(), indent=2) + "\n"
        )

    def remove(self, task_id: str) -> None:
        path = self._path(task_id)
        if path.exists():
            path.unlink()
