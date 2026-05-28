"""Structured telemetry store for Praxis tool calls."""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TelemetryEvent:
    tool_name: str
    latency_ms: float
    hook_result: str          # "allowed" | "blocked" | "n/a"
    caller: str               # e.g. "ClaudeCodeRuntime" or "MCPServer"
    token_count: int | None   # None if unavailable
    timestamp: str            # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TelemetryStore:
    """Thread-safe in-memory ring buffer + append-only log for telemetry events."""

    _global_instance: TelemetryStore | None = None
    _global_lock: threading.Lock = threading.Lock()

    def __init__(self, log_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._events: deque[TelemetryEvent] = deque(maxlen=1000)
        self._log_path = log_path
        # Counters
        self.tool_call_count: int = 0
        self.hook_block_count: int = 0
        self.circuit_breaker_trips: int = 0

    @classmethod
    def get_global(cls) -> "TelemetryStore":
        """Return (or create) the process-level singleton store."""
        with cls._global_lock:
            if cls._global_instance is None:
                # Default log path: .praxis/logs/telemetry.jsonl relative to WORKSPACE_ROOT
                workspace = os.environ.get("PRAXIS_WORKSPACE_ROOT", "")
                if workspace:
                    log_path = Path(workspace) / ".praxis" / "logs" / "telemetry.jsonl"
                else:
                    log_path = None
                cls._global_instance = cls(log_path=log_path)
            return cls._global_instance

    @classmethod
    def reset_global(cls) -> None:
        """Reset the global singleton — for testing only."""
        with cls._global_lock:
            cls._global_instance = None

    def record(self, event: TelemetryEvent) -> None:
        """Record an event in the ring buffer and append to the log file."""
        with self._lock:
            self._events.append(event)
            self.tool_call_count += 1
            if event.hook_result == "blocked":
                self.hook_block_count += 1
            # Write to log file
            if self._log_path is not None:
                try:
                    self._log_path.parent.mkdir(parents=True, exist_ok=True)
                    with self._log_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(event.to_dict()) + "\n")
                except OSError:
                    pass  # Never let telemetry I/O break the caller

    def record_circuit_breaker_trip(self) -> None:
        """Increment the circuit-breaker trip counter."""
        with self._lock:
            self.circuit_breaker_trips += 1

    def get_recent(self, n: int = 100) -> list[TelemetryEvent]:
        """Return the most recent n events (oldest-first within the slice)."""
        with self._lock:
            events = list(self._events)
        return events[-n:] if len(events) > n else events

    def get_counts(self) -> dict[str, int]:
        """Return current counter snapshot."""
        with self._lock:
            return {
                "tool_call_count": self.tool_call_count,
                "hook_block_count": self.hook_block_count,
                "circuit_breaker_trips": self.circuit_breaker_trips,
            }

    def reset(self) -> None:
        """Clear all state — for testing only."""
        with self._lock:
            self._events.clear()
            self.tool_call_count = 0
            self.hook_block_count = 0
            self.circuit_breaker_trips = 0
