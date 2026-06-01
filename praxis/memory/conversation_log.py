"""Persistent conversation log — one JSONL file per day under
.praxis/memory/conversations/YYYY-MM-DD.jsonl.

Schema per line:
  {"ts": <ISO8601>, "prompt": str, "summary": str,
   "outcome": str, "task_type": str}

PRAXIS_CONVERSATION_LOG_DAYS (default 30): entries older than this
are excluded from recent() / search() but files are NOT deleted.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ConversationLog:
    """Persistent, per-day JSONL conversation log."""

    def __init__(self, workspace_root: Path) -> None:
        self._conv_dir = workspace_root / ".praxis" / "memory" / "conversations"
        self._log_days = int(os.environ.get("PRAXIS_CONVERSATION_LOG_DAYS", "30"))

    def _today_file(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._conv_dir / f"{today}.jsonl"

    def _window_start(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=self._log_days)

    def _files_in_window(self) -> list[Path]:
        """Return JSONL files within the log-days window, newest first."""
        if not self._conv_dir.exists():
            return []
        window_start = self._window_start()
        files = []
        for f in self._conv_dir.glob("*.jsonl"):
            try:
                file_date = datetime.strptime(f.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date >= window_start:
                    files.append((file_date, f))
            except ValueError:
                continue
        files.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in files]

    def append(
        self,
        prompt: str,
        summary: str,
        outcome: str,
        task_type: str,
    ) -> None:
        """Append one entry to today's JSONL file. Never raises."""
        try:
            self._conv_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "prompt": prompt,
                "summary": summary,
                "outcome": outcome,
                "task_type": task_type,
            }
            today_file = self._today_file()
            with today_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[praxis-memlog] append failed: {exc}\n")

    def recent(self, n: int = 10) -> list[dict]:
        """Return the last n entries across files in window, newest first."""
        try:
            entries: list[dict] = []
            for file_path in self._files_in_window():
                try:
                    lines = file_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    continue
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except Exception:
                        continue
                    if len(entries) >= n:
                        break
                if len(entries) >= n:
                    break
            return entries[:n]
        except Exception:
            return []

    def search(self, query: str, n: int = 5) -> list[dict]:
        """Token-overlap search across entries in window. Returns top n, newest-first on ties."""
        try:
            tokens = set(query.lower().split())
            if not tokens:
                return []

            scored: list[tuple[int, int, dict]] = []  # (score, file_index, entry)
            file_index = 0
            for file_path in self._files_in_window():
                try:
                    lines = file_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    file_index += 1
                    continue
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    text = (
                        (entry.get("prompt", "") or "") + " " +
                        (entry.get("summary", "") or "")
                    ).lower()
                    score = sum(1 for t in tokens if t in text)
                    if score > 0:
                        scored.append((score, file_index, entry))
                file_index += 1

            # Sort: highest score first; for equal scores, lower file_index = newer file
            scored.sort(key=lambda x: (-x[0], x[1]))
            return [entry for _, _, entry in scored[:n]]
        except Exception:
            return []
