"""tests/test_plan_approval.py — Tests for V2B plan staging + approval CLI flow."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.config import Config
from praxis.modes import Mode
from praxis.orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orch(workspace: Path, return_value: str = "The plan: do X then Y") -> Orchestrator:
    """Build an Orchestrator whose runtime.run_loop() returns a fixed string."""
    runtime = MagicMock()
    runtime.run_loop.return_value = return_value
    config = Config(
        workspace_root=workspace,
        memory_root=workspace / ".praxis" / "memory",
        hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
        allowed_domains=frozenset(),
    )
    return Orchestrator(runtime, config)


def _write_pending_plan(plans_dir: Path, plan_id: str, task: str = "do the thing") -> Path:
    """Write a pending plan JSON file and return the path."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_entry = {
        "id": plan_id,
        "task": task,
        "plan_text": "Step 1: ...\nStep 2: ...",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    plan_file = plans_dir / f"{plan_id}.json"
    plan_file.write_text(json.dumps(plan_entry, indent=2), encoding="utf-8")
    return plan_file


# ---------------------------------------------------------------------------
# TestPlanStagingOnRun
# ---------------------------------------------------------------------------


class TestPlanStagingOnRun:
    """Orchestrator.run() writes a staging file when mode.requires_confirmation=True."""

    def test_plan_mode_writes_staging_file(self, workspace):
        """Running in plan mode produces exactly one plan JSON in .praxis/staging/plans/."""
        orch = _make_orch(workspace)
        plan_mode = Mode.load("plan")
        orch.run("list files in praxis/", mode=plan_mode)

        plans_dir = workspace / ".praxis" / "staging" / "plans"
        files = list(plans_dir.glob("*.json"))
        assert len(files) == 1

    def test_build_mode_does_not_write_staging_file(self, workspace):
        """Running in build mode (requires_confirmation=False) must NOT create any plan JSON."""
        orch = _make_orch(workspace)
        build_mode = Mode.load("build")
        orch.run("list files in praxis/", mode=build_mode)

        plans_dir = workspace / ".praxis" / "staging" / "plans"
        # Either the dir was never created, or it has no files
        if plans_dir.exists():
            files = list(plans_dir.glob("*.json"))
            assert len(files) == 0

    def test_plan_file_has_correct_format(self, workspace):
        """The staged plan JSON has all required keys with correct types."""
        task = "list files in praxis/"
        orch = _make_orch(workspace, return_value="The plan: step 1, step 2")
        plan_mode = Mode.load("plan")
        orch.run(task, mode=plan_mode)

        plans_dir = workspace / ".praxis" / "staging" / "plans"
        files = list(plans_dir.glob("*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text(encoding="utf-8"))
        # Required keys
        assert "id" in data
        assert "task" in data
        assert "plan_text" in data
        assert "created_at" in data
        assert "status" in data

        # Correct values
        assert data["status"] == "pending"
        assert data["task"] == task
        assert data["plan_text"] == "The plan: step 1, step 2"

        # ISO8601 parseable
        parsed_dt = datetime.fromisoformat(data["created_at"])
        assert parsed_dt is not None

    def test_plan_file_is_uuid_named(self, workspace):
        """Running plan mode twice produces two files with distinct UUID-based names."""
        orch = _make_orch(workspace)
        plan_mode = Mode.load("plan")
        orch.run("first task", mode=plan_mode)
        orch.run("second task", mode=plan_mode)

        plans_dir = workspace / ".praxis" / "staging" / "plans"
        files = list(plans_dir.glob("*.json"))
        assert len(files) == 2

        names = [f.stem for f in files]
        # Each name must be a valid UUID
        for name in names:
            parsed = uuid.UUID(name)  # raises ValueError if not a valid UUID
            assert str(parsed) == name

        # The two plan IDs are different
        assert names[0] != names[1]

    def test_plan_mode_none_does_not_write_staging_file(self, workspace):
        """Passing mode=None (default) must NOT create any plan JSON."""
        orch = _make_orch(workspace)
        orch.run("list files in praxis/")

        plans_dir = workspace / ".praxis" / "staging" / "plans"
        if plans_dir.exists():
            files = list(plans_dir.glob("*.json"))
            assert len(files) == 0


# ---------------------------------------------------------------------------
# TestListPlansCommand
# ---------------------------------------------------------------------------


class TestListPlansCommand:
    """--list-plans CLI command: _parse_mode detection and output logic."""

    def test_parse_mode_list_plans(self):
        """_parse_mode returns 'list_plans' for --list-plans flag."""
        from praxis.__main__ import _parse_mode

        assert _parse_mode(["--list-plans"]) == "list_plans"

    def test_list_plans_no_dir(self, tmp_path, capsys):
        """Workspace with no plans/ dir prints 'No pending plans.'."""
        from praxis.__main__ import main

        with (
            patch("sys.argv", ["praxis", "--list-plans"]),
            patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}),
        ):
            main()

        captured = capsys.readouterr()
        assert "No pending plans." in captured.out

    def test_list_plans_empty_dir(self, tmp_path, capsys):
        """Plans dir exists but contains no JSON → 'No pending plans.'."""
        from praxis.__main__ import main

        plans_dir = tmp_path / ".praxis" / "staging" / "plans"
        plans_dir.mkdir(parents=True)

        with (
            patch("sys.argv", ["praxis", "--list-plans"]),
            patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}),
        ):
            main()

        captured = capsys.readouterr()
        assert "No pending plans." in captured.out

    def test_list_plans_shows_pending(self, tmp_path, capsys):
        """Plans dir with a pending plan shows its ID and task summary."""
        from praxis.__main__ import main

        plans_dir = tmp_path / ".praxis" / "staging" / "plans"
        plan_id = str(uuid.uuid4())
        _write_pending_plan(plans_dir, plan_id, task="analyze the codebase")

        with (
            patch("sys.argv", ["praxis", "--list-plans"]),
            patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}),
        ):
            main()

        captured = capsys.readouterr()
        assert plan_id in captured.out
        assert "analyze the codebase" in captured.out

    def test_list_plans_skips_non_pending(self, tmp_path, capsys):
        """Already approved/rejected plans are NOT shown."""
        from praxis.__main__ import main

        plans_dir = tmp_path / ".praxis" / "staging" / "plans"
        plans_dir.mkdir(parents=True)

        for status in ("approved", "rejected"):
            pid = str(uuid.uuid4())
            entry = {
                "id": pid,
                "task": f"task for {status}",
                "plan_text": "...",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": status,
            }
            (plans_dir / f"{pid}.json").write_text(json.dumps(entry), encoding="utf-8")

        with (
            patch("sys.argv", ["praxis", "--list-plans"]),
            patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}),
        ):
            main()

        captured = capsys.readouterr()
        assert "No pending plans." in captured.out


# ---------------------------------------------------------------------------
# TestApprovePlanCommand
# ---------------------------------------------------------------------------


class TestApprovePlanCommand:
    """--approve-plan <id> CLI command."""

    def test_parse_mode_approve_plan(self):
        """_parse_mode returns 'approve_plan' for --approve-plan flag."""
        from praxis.__main__ import _parse_mode

        assert _parse_mode(["--approve-plan", "some-uuid"]) == "approve_plan"

    def test_approve_plan_marks_approved(self, tmp_path, workspace):
        """Approving a pending plan sets its status field to 'approved'."""
        from praxis.__main__ import main

        plans_dir = tmp_path / ".praxis" / "staging" / "plans"
        plan_id = str(uuid.uuid4())
        plan_file = _write_pending_plan(plans_dir, plan_id, task="refactor module")

        with (
            patch("sys.argv", ["praxis", "--approve-plan", plan_id]),
            patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}),
            patch("praxis.__main__.Config") as mock_config_cls,
            patch("praxis.__main__.ConvergenceConfig") as mock_conv_cls,
            patch("praxis.__main__.Orchestrator") as mock_orch_cls,
            patch("praxis.__main__._create_runtimes") as mock_create_rt,
            patch("praxis.__main__._run_credential_check"),
        ):
            mock_config = MagicMock()
            mock_config.workspace_root = tmp_path
            mock_config_cls.from_env.return_value = mock_config

            mock_conv = MagicMock()
            mock_conv_cls.load.return_value = mock_conv

            mock_create_rt.return_value = (MagicMock(), {})

            mock_orch = MagicMock()
            mock_orch.run.return_value = "executed plan"
            mock_orch_cls.return_value = mock_orch

            main()

        data = json.loads(plan_file.read_text(encoding="utf-8"))
        assert data["status"] == "approved"

    def test_approve_plan_reruns_in_build_mode(self, tmp_path, workspace):
        """Approving a plan re-runs the original task using build mode (requires_confirmation=False)."""
        from praxis.__main__ import main

        plans_dir = tmp_path / ".praxis" / "staging" / "plans"
        plan_id = str(uuid.uuid4())
        _write_pending_plan(plans_dir, plan_id, task="deploy service")

        with (
            patch("sys.argv", ["praxis", "--approve-plan", plan_id]),
            patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}),
            patch("praxis.__main__.Config") as mock_config_cls,
            patch("praxis.__main__.ConvergenceConfig") as mock_conv_cls,
            patch("praxis.__main__.Orchestrator") as mock_orch_cls,
            patch("praxis.__main__._create_runtimes") as mock_create_rt,
            patch("praxis.__main__._run_credential_check"),
        ):
            mock_config = MagicMock()
            mock_config.workspace_root = tmp_path
            mock_config_cls.from_env.return_value = mock_config

            mock_conv = MagicMock()
            mock_conv_cls.load.return_value = mock_conv

            mock_create_rt.return_value = (MagicMock(), {})

            mock_orch = MagicMock()
            mock_orch.run.return_value = "executed plan"
            mock_orch_cls.return_value = mock_orch

            main()

        # orch.run was called once
        mock_orch.run.assert_called_once()
        call_kwargs = mock_orch.run.call_args

        # The mode argument passed must be build mode (requires_confirmation=False)
        mode_arg = call_kwargs.kwargs.get("mode") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs.get("mode")
        assert mode_arg is not None
        assert mode_arg.requires_confirmation is False
        assert mode_arg.name == "build"

    def test_approve_plan_not_found_exits_1(self, tmp_path, capsys):
        """--approve-plan with a non-existent plan ID exits with code 1."""
        from praxis.__main__ import main

        nonexistent_id = str(uuid.uuid4())
        # Ensure plans dir exists but file does not
        (tmp_path / ".praxis" / "staging" / "plans").mkdir(parents=True, exist_ok=True)

        with (
            patch("sys.argv", ["praxis", "--approve-plan", nonexistent_id]),
            patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# TestRejectPlanCommand
# ---------------------------------------------------------------------------


class TestRejectPlanCommand:
    """--reject-plan <id> CLI command."""

    def test_parse_mode_reject_plan(self):
        """_parse_mode returns 'reject_plan' for --reject-plan flag."""
        from praxis.__main__ import _parse_mode

        assert _parse_mode(["--reject-plan", "some-uuid"]) == "reject_plan"

    def test_reject_plan_marks_rejected(self, tmp_path, workspace):
        """Rejecting a pending plan sets its status field to 'rejected'."""
        from praxis.__main__ import main

        plans_dir = tmp_path / ".praxis" / "staging" / "plans"
        plan_id = str(uuid.uuid4())
        plan_file = _write_pending_plan(plans_dir, plan_id, task="migrate database")

        with (
            patch("sys.argv", ["praxis", "--reject-plan", plan_id]),
            patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}),
        ):
            main()

        data = json.loads(plan_file.read_text(encoding="utf-8"))
        assert data["status"] == "rejected"

    def test_reject_plan_does_not_run_orchestrator(self, tmp_path, workspace):
        """Rejecting a plan must NOT invoke Orchestrator.run at all."""
        from praxis.__main__ import main

        plans_dir = tmp_path / ".praxis" / "staging" / "plans"
        plan_id = str(uuid.uuid4())
        _write_pending_plan(plans_dir, plan_id, task="dangerous operation")

        with (
            patch("sys.argv", ["praxis", "--reject-plan", plan_id]),
            patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}),
            patch("praxis.__main__.Orchestrator") as mock_orch_cls,
        ):
            main()

        mock_orch_cls.return_value.run.assert_not_called()

    def test_reject_plan_not_found_exits_1(self, tmp_path, capsys):
        """--reject-plan with a non-existent plan ID exits with code 1."""
        from praxis.__main__ import main

        nonexistent_id = str(uuid.uuid4())
        (tmp_path / ".praxis" / "staging" / "plans").mkdir(parents=True, exist_ok=True)

        with (
            patch("sys.argv", ["praxis", "--reject-plan", nonexistent_id]),
            patch.dict(os.environ, {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1

    def test_list_staged_includes_plans(self, tmp_path, capsys):
        """_run_list_staged() reports pending plan approvals section when plans exist."""
        from praxis.__main__ import _run_list_staged

        plans_dir = tmp_path / ".praxis" / "staging" / "plans"
        plan_id = str(uuid.uuid4())
        _write_pending_plan(plans_dir, plan_id, task="implement feature X")

        _run_list_staged(tmp_path)

        captured = capsys.readouterr()
        assert "Pending plan approvals" in captured.out
        # The output shows the first 8 chars of the plan id
        assert plan_id[:8] in captured.out
