"""SG7: trust tiers drive channel intake and task mode."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from praxis.checkpoint import CheckpointStore
from praxis.queue import Task, TaskQueue
from praxis.queue_runner import _run_atomic_task, _run_staged_task
from praxis.trust import Tier, mode_for, tier_for


@pytest.fixture(autouse=True)
def _no_env_ids(monkeypatch):
    monkeypatch.delenv("PRAXIS_OWNER_IDS", raising=False)
    monkeypatch.delenv("PRAXIS_ADMIN_IDS", raising=False)


def test_mode_for_owner_is_unrestricted():
    assert mode_for(Tier.OWNER) is None


@pytest.mark.parametrize("tier", [Tier.ADMIN, Tier.USER, Tier.BLOCKED])
def test_mode_for_non_owner_is_plan(tier):
    assert mode_for(tier) == "plan"


def test_env_beats_yaml(tmp_path, monkeypatch):
    (tmp_path / "convergence.yaml").write_text("channels:\n  slack:\n    trusted_contacts: [U1]\n")
    assert tier_for("slack", "U1") is Tier.USER
    monkeypatch.setenv("PRAXIS_OWNER_IDS", "slack:U1")
    assert tier_for("slack", "U1") is Tier.OWNER


def test_email_sender_case_insensitive(tmp_path, monkeypatch):
    (tmp_path / "convergence.yaml").write_text(
        "channels:\n  email:\n    trusted_contacts: [Alice@Example.com]\n"
    )
    assert tier_for("email", "alice@example.COM") is Tier.USER
    monkeypatch.setenv("PRAXIS_OWNER_IDS", "email:Bob@Example.com")
    assert tier_for("email", "bob@example.com") is Tier.OWNER


def test_unconfigured_is_blocked():
    assert tier_for("slack", "U1") is Tier.BLOCKED
    assert tier_for("webhook", "github") is Tier.BLOCKED


# ---------- runner passes task.mode through ----------


@pytest.fixture
def queue(tmp_path: Path) -> TaskQueue:
    q = TaskQueue(tmp_path / ".praxis" / "queue")
    q.ensure_dirs()
    return q


def _orch() -> MagicMock:
    orch = MagicMock()
    orch.run.return_value = "ok"
    return orch


def test_runner_passes_plan_mode(queue):
    task = Task.create("hi", origin="webhook", mode="plan")
    queue.append(task)
    orch = _orch()
    _run_atomic_task(task, orch, queue)
    mode = orch.run.call_args.kwargs["mode"]
    assert mode.name == "plan"


def test_runner_no_mode_for_owner_task(queue):
    task = Task.create("hi")
    queue.append(task)
    orch = _orch()
    _run_atomic_task(task, orch, queue)
    orch.run.assert_called_once_with("hi")


def test_staged_runner_passes_plan_mode(queue, tmp_path):
    task = Task.create("x", stages=["a", "b"], origin="slack", mode="plan")
    queue.append(task)
    orch = _orch()
    _run_staged_task(task, orch, queue, CheckpointStore(tmp_path / ".praxis" / "queue"))
    assert [c.kwargs["mode"].name for c in orch.run.call_args_list] == ["plan", "plan"]


def test_unknown_mode_fails_closed(queue):
    task = Task.create("hi", mode="no-such-mode")
    queue.append(task)
    orch = _orch()
    with pytest.raises(Exception):
        _run_atomic_task(task, orch, queue)
    orch.run.assert_not_called()
