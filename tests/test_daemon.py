"""Tests for praxis/daemon.py — daemon start/stop/status."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.daemon import read_pid, report_status, stop_daemon


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / ".praxis").mkdir()
    (tmp_path / ".praxis" / "queue").mkdir()
    return tmp_path


# ---------- read_pid ----------


def test_read_pid_no_file(ws: Path):
    assert read_pid(ws) is None


def test_read_pid_stale(ws: Path):
    """PID file exists but process is not running → returns None and cleans up."""
    pid_file = ws / ".praxis" / "praxis.pid"
    pid_file.write_text("999999999")  # almost certainly not running

    assert read_pid(ws) is None
    assert not pid_file.exists()


def test_read_pid_valid(ws: Path):
    """PID file with current process PID → returns it."""
    pid_file = ws / ".praxis" / "praxis.pid"
    pid_file.write_text(str(os.getpid()))

    assert read_pid(ws) == os.getpid()


def test_read_pid_invalid_content(ws: Path):
    """PID file with non-numeric content → returns None."""
    pid_file = ws / ".praxis" / "praxis.pid"
    pid_file.write_text("not-a-pid")

    assert read_pid(ws) is None


# ---------- stop_daemon ----------


def test_stop_daemon_no_process(ws: Path):
    """stop_daemon with no running daemon → SystemExit."""
    with pytest.raises(SystemExit):
        stop_daemon(ws)


def test_stop_daemon_sends_sigterm(ws: Path):
    """stop_daemon sends SIGTERM to the PID and removes the file."""
    pid_file = ws / ".praxis" / "praxis.pid"
    fake_pid = 12345

    with (
        patch("praxis.daemon.read_pid", return_value=fake_pid),
        patch("os.kill") as mock_kill,
    ):
        stop_daemon(ws)
        mock_kill.assert_called_once_with(fake_pid, __import__("signal").SIGTERM)


def test_stop_daemon_kill_fails(ws: Path):
    """stop_daemon raises SystemExit if kill fails."""
    with (
        patch("praxis.daemon.read_pid", return_value=12345),
        patch("os.kill", side_effect=OSError("no such process")),
        pytest.raises(SystemExit),
    ):
        stop_daemon(ws)


# ---------- report_status ----------


def test_report_status_not_running(ws: Path, capsys):
    """Reports not running + queue stats."""
    report_status(ws)
    captured = capsys.readouterr()
    assert "not running" in captured.err
    assert "pending" in captured.err


def test_report_status_running(ws: Path, capsys):
    """Reports running when PID is valid."""
    pid_file = ws / ".praxis" / "praxis.pid"
    pid_file.write_text(str(os.getpid()))

    report_status(ws)
    captured = capsys.readouterr()
    assert "running" in captured.err
    assert str(os.getpid()) in captured.err


def test_report_status_with_tasks(ws: Path, capsys):
    """Reports accurate queue stats."""
    from praxis.queue import Task, TaskQueue

    q = TaskQueue(ws / ".praxis" / "queue")
    q.ensure_dirs()
    q.append(Task.create("task1"))
    t2 = Task.create("task2")
    t2.status = "done"
    q.append(t2)

    report_status(ws)
    captured = capsys.readouterr()
    assert "1 pending" in captured.err
    assert "1 done" in captured.err
