"""Tests for praxis/runtime/telemetry.py — TelemetryEvent and TelemetryStore."""

from __future__ import annotations

import json
import re
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

import pytest

from praxis.runtime.telemetry import TelemetryEvent, TelemetryStore
from praxis.runtime.claude_code import ClaudeCodeRuntime
from praxis.runtime.openai_base import OpenAIBaseRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    tool_name: str = "Bash",
    latency_ms: float = 12.5,
    hook_result: str = "allowed",
    caller: str = "ClaudeCodeRuntime",
    token_count: int | None = 42,
    timestamp: str | None = None,
) -> TelemetryEvent:
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    return TelemetryEvent(
        tool_name=tool_name,
        latency_ms=latency_ms,
        hook_result=hook_result,
        caller=caller,
        token_count=token_count,
        timestamp=timestamp,
    )


@pytest.fixture(autouse=True)
def reset_global():
    """Reset the global singleton before (and after) every test."""
    TelemetryStore.reset_global()
    yield
    TelemetryStore.reset_global()


# ---------------------------------------------------------------------------
# TestTelemetryEvent
# ---------------------------------------------------------------------------

class TestTelemetryEvent:
    def test_to_dict_roundtrip(self):
        """asdict then reconstruct — all fields preserved."""
        ts = "2026-05-27T10:00:00+00:00"
        event = TelemetryEvent(
            tool_name="Read",
            latency_ms=5.0,
            hook_result="allowed",
            caller="MCPServer",
            token_count=100,
            timestamp=ts,
        )
        d = event.to_dict()
        assert d["tool_name"] == "Read"
        assert d["latency_ms"] == 5.0
        assert d["hook_result"] == "allowed"
        assert d["caller"] == "MCPServer"
        assert d["token_count"] == 100
        assert d["timestamp"] == ts
        # Reconstruct
        restored = TelemetryEvent(**d)
        assert restored == event

    def test_timestamp_is_iso8601(self):
        """Timestamp field matches ISO 8601 pattern."""
        ts = datetime.now(timezone.utc).isoformat()
        event = _make_event(timestamp=ts)
        # ISO 8601: YYYY-MM-DDTHH:MM:SS...
        iso_pattern = re.compile(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
        )
        assert iso_pattern.match(event.timestamp), (
            f"Timestamp '{event.timestamp}' does not match ISO 8601"
        )

    def test_token_count_can_be_none(self):
        """token_count is allowed to be None."""
        event = _make_event(token_count=None)
        d = event.to_dict()
        assert d["token_count"] is None

    def test_to_dict_is_json_serializable(self):
        """to_dict() output must be JSON-serialisable."""
        event = _make_event()
        d = event.to_dict()
        # Should not raise
        serialised = json.dumps(d)
        assert isinstance(serialised, str)


# ---------------------------------------------------------------------------
# TestTelemetryStoreRecord
# ---------------------------------------------------------------------------

class TestTelemetryStoreRecord:
    def test_record_increments_tool_call_count(self):
        store = TelemetryStore()
        store.record(_make_event(hook_result="allowed"))
        store.record(_make_event(hook_result="n/a"))
        assert store.tool_call_count == 2

    def test_record_blocked_increments_hook_block_count(self):
        store = TelemetryStore()
        store.record(_make_event(hook_result="blocked"))
        store.record(_make_event(hook_result="blocked"))
        assert store.hook_block_count == 2
        assert store.tool_call_count == 2

    def test_record_allowed_does_not_increment_hook_block_count(self):
        store = TelemetryStore()
        store.record(_make_event(hook_result="allowed"))
        assert store.hook_block_count == 0

    def test_record_na_does_not_increment_hook_block_count(self):
        store = TelemetryStore()
        store.record(_make_event(hook_result="n/a"))
        assert store.hook_block_count == 0

    def test_circuit_breaker_trip_increments_counter(self):
        store = TelemetryStore()
        store.record_circuit_breaker_trip()
        store.record_circuit_breaker_trip()
        assert store.circuit_breaker_trips == 2
        # tool_call_count should NOT be incremented by circuit-breaker trips
        assert store.tool_call_count == 0

    def test_get_recent_returns_last_n(self):
        """Record 5 events, get_recent(3) returns the last 3."""
        store = TelemetryStore()
        events = [_make_event(tool_name=f"Tool{i}") for i in range(5)]
        for e in events:
            store.record(e)
        recent = store.get_recent(3)
        assert len(recent) == 3
        assert [e.tool_name for e in recent] == ["Tool2", "Tool3", "Tool4"]

    def test_get_recent_all_when_fewer_than_n(self):
        """When store has fewer than n events, all are returned."""
        store = TelemetryStore()
        for i in range(3):
            store.record(_make_event(tool_name=f"T{i}"))
        recent = store.get_recent(100)
        assert len(recent) == 3

    def test_get_recent_default_n_is_100(self):
        """get_recent() default returns at most 100 events."""
        store = TelemetryStore()
        for i in range(150):
            store.record(_make_event(tool_name=f"T{i}"))
        recent = store.get_recent()
        assert len(recent) == 100

    def test_get_counts_returns_dict_with_three_keys(self):
        store = TelemetryStore()
        counts = store.get_counts()
        assert set(counts.keys()) == {
            "tool_call_count",
            "hook_block_count",
            "circuit_breaker_trips",
        }
        assert all(isinstance(v, int) for v in counts.values())

    def test_get_counts_reflects_recorded_events(self):
        store = TelemetryStore()
        store.record(_make_event(hook_result="allowed"))
        store.record(_make_event(hook_result="blocked"))
        store.record_circuit_breaker_trip()
        counts = store.get_counts()
        assert counts["tool_call_count"] == 2
        assert counts["hook_block_count"] == 1
        assert counts["circuit_breaker_trips"] == 1


# ---------------------------------------------------------------------------
# TestTelemetryStoreSingleton
# ---------------------------------------------------------------------------

class TestTelemetryStoreSingleton:
    def test_get_global_returns_same_instance(self):
        """Two calls to get_global() return the same object."""
        a = TelemetryStore.get_global()
        b = TelemetryStore.get_global()
        assert a is b

    def test_reset_global_creates_fresh_instance(self):
        """After reset_global(), get_global() returns a new object."""
        first = TelemetryStore.get_global()
        TelemetryStore.reset_global()
        second = TelemetryStore.get_global()
        assert first is not second

    def test_reset_clears_state(self):
        """record() events, then reset(); get_counts() should all be zero."""
        store = TelemetryStore()
        store.record(_make_event(hook_result="blocked"))
        store.record_circuit_breaker_trip()
        store.reset()
        counts = store.get_counts()
        assert counts["tool_call_count"] == 0
        assert counts["hook_block_count"] == 0
        assert counts["circuit_breaker_trips"] == 0
        assert store.get_recent() == []

    def test_get_global_no_workspace_env_gives_none_log_path(self):
        """When PRAXIS_WORKSPACE_ROOT is unset, log_path should be None."""
        with patch.dict("os.environ", {}, clear=True):
            # Ensure env var absent
            import os
            os.environ.pop("PRAXIS_WORKSPACE_ROOT", None)
            TelemetryStore.reset_global()
            store = TelemetryStore.get_global()
            assert store._log_path is None

    def test_get_global_with_workspace_env_sets_log_path(self, tmp_path):
        """When PRAXIS_WORKSPACE_ROOT is set, log_path is derived from it."""
        with patch.dict("os.environ", {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}):
            TelemetryStore.reset_global()
            store = TelemetryStore.get_global()
            expected = tmp_path / ".praxis" / "logs" / "telemetry.jsonl"
            assert store._log_path == expected


# ---------------------------------------------------------------------------
# TestTelemetryStoreLogFile
# ---------------------------------------------------------------------------

class TestTelemetryStoreLogFile:
    def test_log_file_written(self, tmp_path):
        """Record one event; file exists and contains valid JSON."""
        log_path = tmp_path / "tel.jsonl"
        store = TelemetryStore(log_path=log_path)
        event = _make_event(tool_name="Write", hook_result="allowed")
        store.record(event)
        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["tool_name"] == "Write"
        assert parsed["hook_result"] == "allowed"

    def test_log_file_multiple_events(self, tmp_path):
        """Multiple events produce multiple lines."""
        log_path = tmp_path / "multi.jsonl"
        store = TelemetryStore(log_path=log_path)
        for i in range(5):
            store.record(_make_event(tool_name=f"Tool{i}"))
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 5

    def test_log_file_dir_created(self, tmp_path):
        """Parent dir is created automatically even when nested."""
        log_path = tmp_path / "nested" / "deep" / "tel.jsonl"
        store = TelemetryStore(log_path=log_path)
        store.record(_make_event())
        assert log_path.exists()

    def test_log_file_io_error_silenced(self, tmp_path):
        """OSError during log write must NOT propagate to the caller."""
        log_path = tmp_path / "tel.jsonl"
        store = TelemetryStore(log_path=log_path)
        # Patch open to raise OSError
        with patch.object(Path, "open", side_effect=OSError("disk full")):
            # Should not raise
            store.record(_make_event())
        # Counter still incremented despite I/O error
        assert store.tool_call_count == 1

    def test_log_file_no_path_no_write(self, tmp_path):
        """When log_path is None, no file is created."""
        store = TelemetryStore(log_path=None)
        store.record(_make_event())
        # Nothing in tmp_path
        assert list(tmp_path.iterdir()) == []

    def test_log_file_json_fields_complete(self, tmp_path):
        """Every required field appears in the written JSON line."""
        log_path = tmp_path / "complete.jsonl"
        store = TelemetryStore(log_path=log_path)
        ts = "2026-05-27T12:00:00+00:00"
        event = TelemetryEvent(
            tool_name="Grep",
            latency_ms=7.3,
            hook_result="n/a",
            caller="TestCaller",
            token_count=None,
            timestamp=ts,
        )
        store.record(event)
        parsed = json.loads(log_path.read_text(encoding="utf-8").strip())
        for key in ("tool_name", "latency_ms", "hook_result", "caller", "token_count", "timestamp"):
            assert key in parsed


# ---------------------------------------------------------------------------
# TestTelemetryStoreConcurrency
# ---------------------------------------------------------------------------

class TestTelemetryStoreConcurrency:
    def test_concurrent_writes_no_corruption(self):
        """10 threads each record 100 events; tool_call_count == 1000."""
        store = TelemetryStore(log_path=None)
        threads = []
        for _ in range(10):
            t = threading.Thread(
                target=lambda: [store.record(_make_event()) for _ in range(100)]
            )
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert store.tool_call_count == 1000

    def test_concurrent_block_count_no_corruption(self):
        """5 threads each record 20 blocked events; hook_block_count == 100."""
        store = TelemetryStore(log_path=None)
        threads = []
        for _ in range(5):
            t = threading.Thread(
                target=lambda: [
                    store.record(_make_event(hook_result="blocked"))
                    for _ in range(20)
                ]
            )
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert store.hook_block_count == 100

    def test_concurrent_circuit_breaker_trips(self):
        """10 threads each trip the breaker 10 times; total == 100."""
        store = TelemetryStore(log_path=None)
        threads = []
        for _ in range(10):
            t = threading.Thread(
                target=lambda: [store.record_circuit_breaker_trip() for _ in range(10)]
            )
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert store.circuit_breaker_trips == 100


# ---------------------------------------------------------------------------
# TestTelemetryWiring — TASK-A02: ClaudeCodeRuntime and OpenAIBaseRuntime
# ---------------------------------------------------------------------------

class _FakeToolUseBlock:
    """Minimal stand-in for an Anthropic tool_use content block."""
    def __init__(self, name: str, input: dict, id: str = "tc1"):
        self.type = "tool_use"
        self.name = name
        self.input = input
        self.id = id


class TestTelemetryWiring:
    """Verify telemetry is recorded from execute_tool() in both runtimes."""

    def test_claude_execute_tool_records_event(self):
        """ClaudeCodeRuntime.execute_tool() records one event for a tool call."""
        TelemetryStore.reset_global()
        runtime = ClaudeCodeRuntime(MagicMock())
        block = _FakeToolUseBlock(name="Read", input={"path": "x"}, id="tc1")

        runtime.execute_tool([block], lambda name, args: "file content")

        store = TelemetryStore.get_global()
        assert store.tool_call_count == 1
        recent = store.get_recent(1)
        assert len(recent) == 1
        assert recent[0].tool_name == "Read"
        assert recent[0].caller == "ClaudeCodeRuntime"
        assert recent[0].hook_result == "allowed"
        assert recent[0].latency_ms >= 0.0

    def test_claude_execute_tool_records_blocked(self):
        """hook_result='blocked' when tool output starts with BLOCKED by §5."""
        TelemetryStore.reset_global()
        runtime = ClaudeCodeRuntime(MagicMock())
        block = _FakeToolUseBlock(name="Bash", input={"cmd": "curl example.com"}, id="tc2")

        runtime.execute_tool(
            [block],
            lambda name, args: "BLOCKED by §5 escalation boundary: network egress denied",
        )

        store = TelemetryStore.get_global()
        assert store.hook_block_count == 1
        assert store.get_recent(1)[0].hook_result == "blocked"

    def test_openai_execute_tool_records_event(self):
        """OpenAIBaseRuntime.execute_tool() records one event with correct caller."""
        TelemetryStore.reset_global()
        runtime = OpenAIBaseRuntime(
            client=MagicMock(),
            default_model="gpt-4o",
            base_url="http://localhost",
        )
        tc = {
            "id": "tc1",
            "function": {
                "name": "Bash",
                "arguments": '{"cmd": "ls"}',
            },
        }

        runtime.execute_tool([tc], lambda name, args: "file1\nfile2")

        store = TelemetryStore.get_global()
        assert store.tool_call_count == 1
        recent = store.get_recent(1)
        assert recent[0].tool_name == "Bash"
        assert recent[0].caller == "OpenAIBaseRuntime"
        assert recent[0].hook_result == "allowed"

    def test_telemetry_failure_does_not_break_tool_execution(self):
        """If TelemetryStore.get_global() raises, execute_tool() still returns results."""
        TelemetryStore.reset_global()
        runtime = ClaudeCodeRuntime(MagicMock())
        block = _FakeToolUseBlock(name="Write", input={"path": "f", "content": "x"}, id="tc3")

        with patch(
            "praxis.runtime.claude_code.TelemetryStore" if False else
            "praxis.runtime.telemetry.TelemetryStore.get_global",
            side_effect=RuntimeError("telemetry unavailable"),
        ):
            results = runtime.execute_tool([block], lambda name, args: "done")

        # Tool result still returned despite telemetry failure
        assert len(results) == 1
        assert results[0]["content"] == "done"
        assert results[0]["tool_use_id"] == "tc3"
