"""Tests for praxis/ambient.py — ambient event monitoring (TASK-I2F3)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.ambient import (
    AmbientMonitor,
    CalendarMonitor,
    EmailMonitor,
    GitHubMonitor,
    LinearMonitor,
    SeenStore,
    _parse_ical_events,
)
from praxis.queue import Task, TaskQueue


class TestSeenStore:
    def test_new_id_not_seen(self, tmp_path):
        store = SeenStore("test", tmp_path)
        assert not store.is_seen("abc123")

    def test_mark_seen_persists(self, tmp_path):
        store = SeenStore("test", tmp_path)
        store.mark_seen("abc123")
        assert store.is_seen("abc123")
        # Re-load from disk
        store2 = SeenStore("test", tmp_path)
        assert store2.is_seen("abc123")

    def test_atomic_write_creates_valid_json(self, tmp_path):
        store = SeenStore("email", tmp_path)
        store.mark_seen("uid1")
        path = tmp_path / ".praxis" / "ambient" / "email_seen.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "uid1" in data

    def test_dedup_returns_true_for_existing(self, tmp_path):
        store = SeenStore("test", tmp_path)
        store.mark_seen("x")
        store.mark_seen("x")  # double mark is idempotent
        assert store.is_seen("x")


class TestEmailMonitor:
    def test_skipped_when_no_env_vars(self, tmp_path):
        # No PRAXIS_EMAIL_* vars set
        monitor = EmailMonitor(tmp_path)
        mock_queue = MagicMock(spec=TaskQueue)
        result = monitor.poll(mock_queue)
        assert result == []
        mock_queue.append.assert_not_called()

    def test_creates_task_for_known_contact(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.test.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@test.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "secret")

        # Create a known contact page
        pages = tmp_path / "wiki" / "pages"
        pages.mkdir(parents=True)
        (pages / "alice.md").write_text("Contact: alice@example.com")

        mock_queue = MagicMock(spec=TaskQueue)

        # Build a fake email header bytes
        raw_hdr = b"From: Alice <alice@example.com>\r\nSubject: Hello\r\n\r\n"

        with patch("praxis.ambient.imaplib.IMAP4_SSL") as mock_imap_cls:
            mock_conn = MagicMock()
            mock_imap_cls.return_value = mock_conn
            mock_conn.search.return_value = (None, [b"1"])
            mock_conn.fetch.return_value = (None, [(None, raw_hdr)])

            monitor = EmailMonitor(tmp_path)
            result = monitor.poll(mock_queue)

        assert "1" in result
        mock_queue.append.assert_called_once()
        task_arg = mock_queue.append.call_args[0][0]
        assert isinstance(task_arg, Task)
        assert "alice@example.com" in task_arg.prompt.lower() or "alice" in task_arg.prompt.lower()
        assert task_arg.priority == 10

    def test_dedup_prevents_double_task(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_EMAIL_IMAP_HOST", "imap.test.com")
        monkeypatch.setenv("PRAXIS_EMAIL_USER", "user@test.com")
        monkeypatch.setenv("PRAXIS_EMAIL_PASSWORD", "secret")

        pages = tmp_path / "wiki" / "pages"
        pages.mkdir(parents=True)
        (pages / "bob.md").write_text("Email: bob@example.com")

        mock_queue = MagicMock(spec=TaskQueue)
        raw_hdr = b"From: Bob <bob@example.com>\r\nSubject: Re: stuff\r\n\r\n"

        with patch("praxis.ambient.imaplib.IMAP4_SSL") as mock_imap_cls:
            mock_conn = MagicMock()
            mock_imap_cls.return_value = mock_conn
            mock_conn.search.return_value = (None, [b"42"])
            mock_conn.fetch.return_value = (None, [(None, raw_hdr)])

            monitor = EmailMonitor(tmp_path)
            monitor.poll(mock_queue)   # first call — should enqueue
            mock_conn.search.return_value = (None, [b"42"])
            mock_conn.fetch.return_value = (None, [(None, raw_hdr)])
            monitor.poll(mock_queue)   # second call — dedup, no enqueue

        assert mock_queue.append.call_count == 1


class TestCalendarMonitor:
    def test_skipped_when_no_url(self, tmp_path):
        monitor = CalendarMonitor(tmp_path)
        mock_queue = MagicMock(spec=TaskQueue)
        result = monitor.poll(mock_queue)
        assert result == []
        mock_queue.append.assert_not_called()

    def test_creates_task_for_upcoming_event(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone, timedelta
        monkeypatch.setenv("PRAXIS_CALENDAR_URL", "https://cal.example.com/feed.ics")
        monkeypatch.setenv("PRAXIS_AMBIENT_CAL_MINUTES", "30")

        # Event starts in 15 minutes from "now"
        now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        start = now + timedelta(minutes=15)
        dtstart_str = start.strftime("%Y%m%dT%H%M%SZ")
        ical = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:event-001\r\n"
            f"SUMMARY:Team standup\r\n"
            f"DTSTART:{dtstart_str}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        mock_queue = MagicMock(spec=TaskQueue)

        # Patch _get_now on the instance rather than the datetime class,
        # so _parse_ical_dt keeps using the real datetime.strptime.
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = ical.encode("utf-8")

        with patch("praxis.ambient.urllib.request.urlopen", return_value=mock_resp):
            monitor = CalendarMonitor(tmp_path)
            monkeypatch.setattr(monitor, "_get_now", lambda: now)
            result = monitor.poll(mock_queue)

        assert len(result) == 1
        mock_queue.append.assert_called_once()
        task_arg = mock_queue.append.call_args[0][0]
        assert "Team standup" in task_arg.prompt
        assert task_arg.priority == 10

    def test_ical_parser_extracts_events(self):
        ical = (
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:abc-123\r\n"
            "SUMMARY:My Meeting\r\n"
            "DTSTART:20260601T090000Z\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        events = _parse_ical_events(ical)
        assert len(events) == 1
        assert events[0]["uid"] == "abc-123"
        assert events[0]["summary"] == "My Meeting"
        assert events[0]["dtstart"] is not None


class TestLinearMonitor:
    def test_skipped_when_no_api_key(self, tmp_path):
        monitor = LinearMonitor(tmp_path)
        mock_queue = MagicMock(spec=TaskQueue)
        result = monitor.poll(mock_queue)
        assert result == []
        mock_queue.append.assert_not_called()

    def test_creates_task_for_new_issue(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_LINEAR_API_KEY", "lin_api_test_key")
        mock_queue = MagicMock(spec=TaskQueue)

        api_response = json.dumps({
            "data": {"issues": {"nodes": [
                {"id": "ISSUE-1", "title": "Fix the bug"}
            ]}}
        }).encode("utf-8")

        with patch("praxis.ambient.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = api_response
            mock_urlopen.return_value = mock_resp

            monitor = LinearMonitor(tmp_path)
            result = monitor.poll(mock_queue)

        assert "ISSUE-1" in result
        task_arg = mock_queue.append.call_args[0][0]
        assert "Fix the bug" in task_arg.prompt
        assert task_arg.priority == 10

    def test_dedup_skips_seen_issue(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRAXIS_LINEAR_API_KEY", "lin_api_test_key")
        mock_queue = MagicMock(spec=TaskQueue)

        api_response = json.dumps({
            "data": {"issues": {"nodes": [{"id": "ISSUE-2", "title": "Known bug"}]}}
        }).encode("utf-8")

        with patch("praxis.ambient.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = api_response
            mock_urlopen.return_value = mock_resp

            monitor = LinearMonitor(tmp_path)
            monitor.poll(mock_queue)  # first: enqueue
            mock_resp.read.return_value = api_response
            monitor.poll(mock_queue)  # second: dedup

        assert mock_queue.append.call_count == 1


class TestGitHubMonitor:
    def test_skipped_when_no_token(self, tmp_path):
        monitor = GitHubMonitor(tmp_path)
        mock_queue = MagicMock(spec=TaskQueue)
        result = monitor.poll(mock_queue)
        assert result == []
        mock_queue.append.assert_not_called()

    def test_creates_task_for_pr_review_request(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
        mock_queue = MagicMock(spec=TaskQueue)

        api_response = json.dumps([
            {"id": "notif-99", "subject": {"title": "Add feature X"}}
        ]).encode("utf-8")

        with patch("praxis.ambient.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = api_response
            mock_urlopen.return_value = mock_resp

            monitor = GitHubMonitor(tmp_path)
            result = monitor.poll(mock_queue)

        assert "notif-99" in result
        task_arg = mock_queue.append.call_args[0][0]
        assert "Add feature X" in task_arg.prompt
        assert task_arg.priority == 10


class TestAmbientMonitor:
    def test_start_stop_thread(self, tmp_path):
        mock_queue = MagicMock(spec=TaskQueue)
        monitor = AmbientMonitor(mock_queue, tmp_path, poll_seconds=60)
        monitor.start()
        assert monitor._thread is not None
        assert monitor._thread.is_alive()
        monitor.stop()
        # Thread is daemon — just verify stop_event was set
        assert monitor._stop_event.is_set()

    def test_start_is_idempotent(self, tmp_path):
        mock_queue = MagicMock(spec=TaskQueue)
        monitor = AmbientMonitor(mock_queue, tmp_path, poll_seconds=60)
        monitor.start()
        thread_id = id(monitor._thread)
        monitor.start()  # second call — should not create a new thread
        assert id(monitor._thread) == thread_id
        monitor.stop()

    def test_sources_polled_on_run_all(self, tmp_path):
        mock_queue = MagicMock(spec=TaskQueue)
        monitor = AmbientMonitor(mock_queue, tmp_path, poll_seconds=60)

        mock_source = MagicMock()
        mock_source.poll.return_value = []
        monitor._sources = [mock_source]

        monitor._run_all_sources()
        mock_source.poll.assert_called_once_with(mock_queue)
