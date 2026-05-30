"""Tests for HEARTBEAT.md scheduler in praxis/scheduler.py (TASK-H04)."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

import praxis.scheduler as sched_module
from praxis.queue import Task, TaskQueue
from praxis.scheduler import check_heartbeat, _heartbeat_fired


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_queue() -> MagicMock:
    mock_queue = MagicMock(spec=TaskQueue)
    mock_queue._read_all.return_value = []
    mock_queue.ensure_dirs.return_value = None
    mock_queue.append.return_value = None
    return mock_queue


def _write_heartbeat(path: Path, content: str) -> None:
    praxis_dir = path / ".praxis"
    praxis_dir.mkdir(parents=True, exist_ok=True)
    (praxis_dir / "HEARTBEAT.md").write_text(content, encoding="utf-8")


WEEKDAY_CONTENT = """\
## Morning Check
when: weekdays 09:00-10:00
Review morning tasks and priorities.
"""

WEEKEND_CONTENT = """\
## Weekend Review
when: weekends 10:00-12:00
Review the week.
"""

DAILY_CONTENT = """\
## Daily Standup
when: daily 08:00-09:00
Quick status check.
"""

OUTSIDE_WINDOW_CONTENT = """\
## Afternoon Check
when: weekdays 14:00-15:00
Only in the afternoon.
"""


def _reset_heartbeat_state():
    """Clear in-process dedup state and last-run timestamp between tests."""
    sched_module._heartbeat_fired.clear()
    sched_module._heartbeat_last_run = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHeartbeatWeekdayFires:
    def test_parse_weekday_section_fires(self, tmp_path: Path):
        """Section tagged 'weekdays' fires when mocked time is a weekday within the window."""
        _reset_heartbeat_state()
        _write_heartbeat(tmp_path, WEEKDAY_CONTENT)

        mock_queue = _make_mock_queue()

        # Monday (weekday=0) at 09:30 — within 09:00-10:00
        fixed_now = datetime(2026, 5, 25, 9, 30)  # Monday
        assert fixed_now.weekday() == 0

        with patch("praxis.scheduler.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz=None: (
                datetime(2026, 5, 25, 9, 30, tzinfo=tz) if tz else fixed_now
            )
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = check_heartbeat(mock_queue, tmp_path, heartbeat_interval_minutes=0)

        assert "Morning Check" in result
        mock_queue.append.assert_called_once()
        task_arg = mock_queue.append.call_args[0][0]
        assert isinstance(task_arg, Task)
        assert "Morning Check" in task_arg.prompt

    def test_parse_section_no_fire_outside_window(self, tmp_path: Path):
        """Section does not fire when current time is outside the when: window."""
        _reset_heartbeat_state()
        _write_heartbeat(tmp_path, OUTSIDE_WINDOW_CONTENT)

        mock_queue = _make_mock_queue()

        # Monday at 09:00 — outside 14:00-15:00
        fixed_now = datetime(2026, 5, 25, 9, 0)

        with patch("praxis.scheduler.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz=None: (
                datetime(2026, 5, 25, 9, 0, tzinfo=tz) if tz else fixed_now
            )
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = check_heartbeat(mock_queue, tmp_path, heartbeat_interval_minutes=0)

        assert result == []
        mock_queue.append.assert_not_called()

    def test_weekend_section_does_not_fire_on_weekday(self, tmp_path: Path):
        """Section tagged 'weekends' does not fire on a weekday."""
        _reset_heartbeat_state()
        _write_heartbeat(tmp_path, WEEKEND_CONTENT)

        mock_queue = _make_mock_queue()

        # Monday (weekday=0) at 10:30 — inside time window but wrong day type
        fixed_now = datetime(2026, 5, 25, 10, 30)  # Monday
        assert fixed_now.weekday() == 0

        with patch("praxis.scheduler.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz=None: (
                datetime(2026, 5, 25, 10, 30, tzinfo=tz) if tz else fixed_now
            )
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = check_heartbeat(mock_queue, tmp_path, heartbeat_interval_minutes=0)

        assert result == []
        mock_queue.append.assert_not_called()

    def test_dedup_prevents_double_fire(self, tmp_path: Path):
        """check_heartbeat() called twice on same date enqueues only once."""
        _reset_heartbeat_state()
        _write_heartbeat(tmp_path, WEEKDAY_CONTENT)

        mock_queue = _make_mock_queue()

        fixed_now = datetime(2026, 5, 25, 9, 30)  # Monday

        with patch("praxis.scheduler.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz=None: (
                datetime(2026, 5, 25, 9, 30, tzinfo=tz) if tz else fixed_now
            )
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result1 = check_heartbeat(mock_queue, tmp_path, heartbeat_interval_minutes=0)
            result2 = check_heartbeat(mock_queue, tmp_path, heartbeat_interval_minutes=0)

        assert "Morning Check" in result1
        assert result2 == []
        # append called exactly once across both check_heartbeat() calls
        assert mock_queue.append.call_count == 1

    def test_missing_heartbeat_file_no_error(self, tmp_path: Path):
        """check_heartbeat() with no HEARTBEAT.md present returns [] without raising."""
        _reset_heartbeat_state()
        # Do NOT create any HEARTBEAT.md
        mock_queue = _make_mock_queue()

        result = check_heartbeat(mock_queue, tmp_path, heartbeat_interval_minutes=0)

        assert result == []
        mock_queue.append.assert_not_called()

    def test_daily_section_fires_every_day(self, tmp_path: Path):
        """Section tagged 'daily' fires on both a weekday and a weekend date."""
        _reset_heartbeat_state()
        _write_heartbeat(tmp_path, DAILY_CONTENT)

        mock_queue = _make_mock_queue()

        # First call: Monday 2026-05-25 at 08:30
        monday = datetime(2026, 5, 25, 8, 30)
        with patch("praxis.scheduler.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz=None: (
                datetime(2026, 5, 25, 8, 30, tzinfo=tz) if tz else monday
            )
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result1 = check_heartbeat(mock_queue, tmp_path, heartbeat_interval_minutes=0)

        assert "Daily Standup" in result1

        # Second call on Saturday 2026-05-30 at 08:30 (different date → dedup key differs)
        saturday = datetime(2026, 5, 30, 8, 30)
        with patch("praxis.scheduler.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz=None: (
                datetime(2026, 5, 30, 8, 30, tzinfo=tz) if tz else saturday
            )
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result2 = check_heartbeat(mock_queue, tmp_path, heartbeat_interval_minutes=0)

        assert "Daily Standup" in result2
        assert mock_queue.append.call_count == 2
