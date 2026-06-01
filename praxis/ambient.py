"""Ambient event monitor — background thread polling email, calendar, Linear, GitHub.

Four optional event sources, each skipped if its env vars are not set:
  EmailMonitor   — IMAP poll for new unread emails from known contacts (wiki/pages/)
  CalendarMonitor — iCal feed for events starting within PRAXIS_AMBIENT_CAL_MINUTES (default 30)
  LinearMonitor  — Linear GraphQL API for newly assigned issues
  GitHubMonitor  — GitHub notifications API for new PR review requests

All created Tasks use priority=10 (low, same as heartbeat) to avoid jumping the queue.
Dedup: each source tracks last-seen IDs in .praxis/ambient/{source}_seen.json (atomic write).
AmbientMonitor.start() / .stop() — starts/stops daemon thread; SIGTERM stops it cleanly.
Enabled by PRAXIS_AMBIENT_ENABLED=true (default false — opt-in).
"""

from __future__ import annotations

import imaplib
import json
import os
import re
import sys
import tempfile
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from email import message_from_bytes
from email.header import decode_header as _decode_header
from pathlib import Path
from typing import Any

from .queue import Task, TaskQueue


class SeenStore:
    """Persistent set of seen event IDs with atomic JSON writes."""

    def __init__(self, source_name: str, workspace_root: Path) -> None:
        self._path = workspace_root / ".praxis" / "ambient" / f"{source_name}_seen.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self) -> None:
        """Atomic write: write to tmp file then os.replace."""
        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=self._path.parent, prefix=".seen_tmp_")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            os.replace(tmp_path_str, self._path)
        except Exception:
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass

    def is_seen(self, event_id: str) -> bool:
        return event_id in self._data

    def mark_seen(self, event_id: str) -> None:
        self._data[event_id] = datetime.now(timezone.utc).isoformat()
        self._save()


def _decode_header_val(raw: str) -> str:
    """Decode RFC 2047 encoded email header to plain string."""
    parts = []
    for decoded, charset in _decode_header(raw):
        if isinstance(decoded, bytes):
            parts.append(decoded.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(decoded))
    return "".join(parts)


class EmailMonitor:
    """Polls IMAP INBOX for new unread emails from known contacts (wiki/pages/)."""

    def __init__(self, workspace_root: Path) -> None:
        self._workspace = workspace_root
        self._host = os.environ.get("PRAXIS_EMAIL_IMAP_HOST", "")
        self._user = os.environ.get("PRAXIS_EMAIL_USER", "")
        self._password = os.environ.get("PRAXIS_EMAIL_PASSWORD", "")
        self._seen = SeenStore("email", workspace_root)

    def _known_contacts(self) -> set[str]:
        contacts: set[str] = set()
        pages_dir = self._workspace / "wiki" / "pages"
        if not pages_dir.exists():
            return contacts
        email_re = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.]+")
        for md_file in pages_dir.glob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8", errors="ignore")
                for match in email_re.findall(text):
                    contacts.add(match.lower())
            except Exception:
                pass
        return contacts

    def poll(self, queue: TaskQueue) -> list[str]:
        """Poll IMAP for new unread emails. Returns list of UIDs enqueued."""
        if not (self._host and self._user and self._password):
            return []
        known = self._known_contacts()
        created: list[str] = []
        try:
            conn = imaplib.IMAP4_SSL(self._host)
            conn.login(self._user, self._password)
            conn.select("INBOX", readonly=True)
            _, data = conn.search(None, "UNSEEN")
            uids = data[0].split() if data and data[0] else []
            for uid_bytes in uids[-20:]:  # cap at 20
                uid_str = uid_bytes.decode()
                if self._seen.is_seen(uid_str):
                    continue
                _, msg_data = conn.fetch(uid_bytes, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                if not msg_data or not msg_data[0]:
                    continue
                raw_hdr = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
                msg = message_from_bytes(raw_hdr)
                from_raw = msg.get("From", "")
                subject_raw = msg.get("Subject", "(no subject)")
                sender = _decode_header_val(from_raw)
                subject = _decode_header_val(subject_raw)
                addr_match = re.search(r"<([^>]+)>", from_raw)
                sender_addr = addr_match.group(1).lower() if addr_match else from_raw.lower().strip()
                if not known or sender_addr in known:
                    self._seen.mark_seen(uid_str)
                    prompt = (
                        f"New email from {sender}: {subject} — "
                        "should I draft a reply or flag it?"
                    )
                    queue.append(Task.create(prompt, priority=10))
                    created.append(uid_str)
            try:
                conn.logout()
            except Exception:
                pass
        except Exception as exc:
            sys.stderr.write(f"[praxis/ambient] email poll error: {exc}\n")
        return created


def _parse_ical_dt(val: str) -> datetime | None:
    """Parse iCal DTSTART value to UTC-aware datetime.

    Uses a local import of the real datetime class so this function remains
    correct even when tests mock the module-level `datetime` name.
    """
    import datetime as _dt_mod
    try:
        val = val.rstrip("Z").replace("-", "").replace(":", "")
        if "T" in val:
            return _dt_mod.datetime.strptime(val[:15], "%Y%m%dT%H%M%S").replace(
                tzinfo=_dt_mod.timezone.utc
            )
        return _dt_mod.datetime.strptime(val[:8], "%Y%m%d").replace(
            tzinfo=_dt_mod.timezone.utc
        )
    except Exception:
        return None


def _parse_ical_events(ical_text: str) -> list[dict[str, Any]]:
    """Minimal iCal parser — extracts VEVENT blocks with uid, dtstart, summary."""
    events: list[dict[str, Any]] = []
    in_event = False
    current: dict[str, Any] = {}
    for line in ical_text.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            current = {}
        elif line == "END:VEVENT":
            if current:
                events.append(current)
            in_event = False
        elif in_event:
            if ":" in line:
                key, _, val = line.partition(":")
                key_base = key.split(";")[0].upper()
                if key_base == "UID":
                    current["uid"] = val.strip()
                elif key_base == "SUMMARY":
                    current["summary"] = val.strip()
                elif key_base == "DTSTART":
                    current["dtstart"] = _parse_ical_dt(val.strip())
    return events


class CalendarMonitor:
    """Checks iCal feed for events starting within the next N minutes."""

    def __init__(self, workspace_root: Path) -> None:
        self._url = os.environ.get("PRAXIS_CALENDAR_URL", "")
        self._minutes = int(os.environ.get("PRAXIS_AMBIENT_CAL_MINUTES", "30"))
        self._seen = SeenStore("calendar", workspace_root)

    def _get_now(self) -> datetime:
        """Return current UTC time — extracted for testability."""
        return datetime.now(timezone.utc)

    def poll(self, queue: TaskQueue) -> list[str]:
        if not self._url:
            return []
        created: list[str] = []
        try:
            with urllib.request.urlopen(
                urllib.request.Request(self._url), timeout=15
            ) as resp:
                ical_text = resp.read().decode("utf-8", errors="replace")
            events = _parse_ical_events(ical_text)
            now = self._get_now()
            cutoff = now + timedelta(minutes=self._minutes)
            for event in events:
                uid = event.get("uid", "")
                dtstart = event.get("dtstart")
                summary = event.get("summary", "Event")
                if not uid or dtstart is None:
                    continue
                dedup_key = f"{uid}:{dtstart.isoformat()}"
                if self._seen.is_seen(dedup_key):
                    continue
                if now <= dtstart <= cutoff:
                    minutes_away = max(1, int((dtstart - now).total_seconds() / 60))
                    self._seen.mark_seen(dedup_key)
                    prompt = (
                        f"You have '{summary}' in {minutes_away} minutes. "
                        "Any preparation needed?"
                    )
                    queue.append(Task.create(prompt, priority=10))
                    created.append(dedup_key)
        except Exception as exc:
            sys.stderr.write(f"[praxis/ambient] calendar poll error: {exc}\n")
        return created


class LinearMonitor:
    """Polls Linear GraphQL API for issues newly assigned to you."""

    _GRAPHQL_URL = "https://api.linear.app/graphql"

    def __init__(self, workspace_root: Path) -> None:
        self._api_key = os.environ.get("PRAXIS_LINEAR_API_KEY", "")
        self._seen = SeenStore("linear", workspace_root)

    def poll(self, queue: TaskQueue) -> list[str]:
        if not self._api_key:
            return []
        created: list[str] = []
        try:
            query = (
                "{ issues(filter: {assignee: {isMe: {eq: true}}}, first: 20)"
                " { nodes { id title } } }"
            )
            payload = json.dumps({"query": query}).encode("utf-8")
            req = urllib.request.Request(
                self._GRAPHQL_URL,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": self._api_key,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            nodes = data.get("data", {}).get("issues", {}).get("nodes", [])
            for issue in nodes:
                issue_id = str(issue.get("id", ""))
                title = issue.get("title", "Unknown issue")
                if not issue_id or self._seen.is_seen(issue_id):
                    continue
                self._seen.mark_seen(issue_id)
                prompt = (
                    f"New Linear issue assigned: {title}. "
                    "Want me to review and summarize it?"
                )
                queue.append(Task.create(prompt, priority=10))
                created.append(issue_id)
        except Exception as exc:
            sys.stderr.write(f"[praxis/ambient] linear poll error: {exc}\n")
        return created


class GitHubMonitor:
    """Polls GitHub notifications API for new PR review requests."""

    _API_URL = "https://api.github.com/notifications?reason=review_requested&all=false"

    def __init__(self, workspace_root: Path) -> None:
        self._token = os.environ.get("GITHUB_TOKEN", "")
        self._seen = SeenStore("github", workspace_root)

    def poll(self, queue: TaskQueue) -> list[str]:
        if not self._token:
            return []
        created: list[str] = []
        try:
            req = urllib.request.Request(
                self._API_URL,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                notifications = json.loads(resp.read().decode("utf-8"))
            for notif in notifications:
                notif_id = str(notif.get("id", ""))
                title = notif.get("subject", {}).get("title", "Unknown PR")
                if not notif_id or self._seen.is_seen(notif_id):
                    continue
                self._seen.mark_seen(notif_id)
                prompt = (
                    f"New PR review requested: {title}. "
                    "Want me to summarize the diff?"
                )
                queue.append(Task.create(prompt, priority=10))
                created.append(notif_id)
        except Exception as exc:
            sys.stderr.write(f"[praxis/ambient] github poll error: {exc}\n")
        return created


class AmbientMonitor:
    """Background daemon thread polling all configured event sources.

    Start with AmbientMonitor.start(); stop with .stop() (or SIGTERM stops it).
    Enabled by PRAXIS_AMBIENT_ENABLED=true (default false).
    """

    def __init__(
        self,
        queue: TaskQueue,
        workspace_root: Path,
        poll_seconds: int | None = None,
    ) -> None:
        self._queue = queue
        self._workspace = workspace_root
        self._poll_seconds = (
            poll_seconds
            if poll_seconds is not None
            else int(os.environ.get("PRAXIS_AMBIENT_POLL_SECONDS", "120"))
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sources: list[Any] = [
            EmailMonitor(workspace_root),
            CalendarMonitor(workspace_root),
            LinearMonitor(workspace_root),
            GitHubMonitor(workspace_root),
        ]

    def start(self) -> None:
        """Start background polling thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="praxis-ambient"
        )
        self._thread.start()
        sys.stderr.write(
            f"[praxis] ambient monitor started (poll every {self._poll_seconds}s)\n"
        )

    def stop(self) -> None:
        """Signal the polling thread to stop."""
        self._stop_event.set()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._run_all_sources()
            self._stop_event.wait(timeout=self._poll_seconds)

    def _run_all_sources(self) -> None:
        for source in self._sources:
            try:
                source.poll(self._queue)
            except Exception as exc:
                sys.stderr.write(
                    f"[praxis/ambient] {source.__class__.__name__} error: {exc}\n"
                )
