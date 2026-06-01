"""Tests for praxis/memory/conversation_log.py."""

from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from praxis.memory.conversation_log import ConversationLog


def _conv_dir(log: ConversationLog) -> Path:
    return log._conv_dir


def _make_log(tmp_path: Path, log_days: int = 30, monkeypatch=None) -> ConversationLog:
    if monkeypatch is not None:
        monkeypatch.setenv("PRAXIS_CONVERSATION_LOG_DAYS", str(log_days))
    return ConversationLog(tmp_path)


# 1. append() creates the JSONL file for today
def test_append_creates_file(tmp_path: Path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch=monkeypatch)
    log.append("do something", "it worked", "success", "queue_task")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    expected = _conv_dir(log) / f"{today}.jsonl"
    assert expected.exists(), "Today's JSONL file should exist after append"
    line = json.loads(expected.read_text().strip())
    assert line["prompt"] == "do something"
    assert line["outcome"] == "success"


# 2. append() with bad path must not raise
def test_append_never_raises(tmp_path: Path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch=monkeypatch)
    # First write to create the dir
    log.append("seed", "seed summary", "success", "queue_task")
    # Now make the dir read-only to cause a write failure
    conv_dir = _conv_dir(log)
    conv_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        # Must not raise
        log.append("will fail", "n/a", "success", "queue_task")
    finally:
        conv_dir.chmod(stat.S_IRWXU)


# 3. recent() returns newest first
def test_recent_returns_newest_first(tmp_path: Path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch=monkeypatch)
    log.append("alpha", "sum-a", "success", "queue_task")
    log.append("beta", "sum-b", "success", "queue_task")
    log.append("gamma", "sum-c", "success", "queue_task")
    results = log.recent(10)
    assert len(results) == 3
    # newest first → gamma, beta, alpha
    assert results[0]["prompt"] == "gamma"
    assert results[1]["prompt"] == "beta"
    assert results[2]["prompt"] == "alpha"


# 4. recent() honors n
def test_recent_honors_n(tmp_path: Path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch=monkeypatch)
    for i in range(5):
        log.append(f"task-{i}", f"sum-{i}", "success", "queue_task")
    results = log.recent(2)
    assert len(results) == 2


# 5. recent() skips files outside log-days window
def test_recent_skips_old_files(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_CONVERSATION_LOG_DAYS", "30")
    log = ConversationLog(tmp_path)
    conv_dir = _conv_dir(log)
    conv_dir.mkdir(parents=True, exist_ok=True)
    # Write a file dated 60 days ago
    old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    old_file = conv_dir / f"{old_date}.jsonl"
    entry = {
        "ts": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
        "prompt": "old prompt",
        "summary": "old",
        "outcome": "success",
        "task_type": "queue_task",
    }
    old_file.write_text(json.dumps(entry) + "\n")
    results = log.recent(10)
    prompts = [r["prompt"] for r in results]
    assert "old prompt" not in prompts


# 6. search() returns entries with highest token overlap first
def test_search_token_overlap(tmp_path: Path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch=monkeypatch)
    log.append("deploy prod release", "deployed to production", "success", "queue_task")
    log.append("run tests", "all tests passed", "success", "queue_task")
    log.append("deploy staging", "staging deployed", "success", "queue_task")
    results = log.search("deploy prod")
    assert len(results) >= 1
    # First result should contain both "deploy" and "prod"
    top = results[0]
    text = (top.get("prompt", "") + " " + top.get("summary", "")).lower()
    assert "deploy" in text
    assert "prod" in text


# 7. search() returns empty list on no match
def test_search_returns_empty_on_no_match(tmp_path: Path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch=monkeypatch)
    log.append("deploy prod", "done", "success", "queue_task")
    results = log.search("zzznomatch")
    assert results == []


# 8. Corrupt lines are skipped; valid entries still returned
def test_corrupt_line_skipped(tmp_path: Path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch=monkeypatch)
    conv_dir = _conv_dir(log)
    conv_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_file = conv_dir / f"{today}.jsonl"
    valid = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "prompt": "valid prompt",
        "summary": "valid",
        "outcome": "success",
        "task_type": "queue_task",
    })
    today_file.write_text("NOT_VALID_JSON\n" + valid + "\n")
    results = log.recent(10)
    prompts = [r["prompt"] for r in results]
    assert "valid prompt" in prompts
