"""Tests for Option D — task-type-based convergence routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.convergence import (
    ConvergenceConfig,
    TaskTypeRule,
    detect_task_type,
)
from praxis.queue import Task


# ---------- TestDetectTaskType ----------


class TestDetectTaskType:
    def test_audit_keyword(self):
        assert detect_task_type("audit the codebase") == "audit"

    def test_implement_keyword(self):
        assert detect_task_type("implement a new feature") == "implement"

    def test_review_keyword(self):
        assert detect_task_type("review this pull request") == "review"

    def test_scribe_keyword(self):
        assert detect_task_type("update claude.md with new conventions") == "scribe"

    def test_default_on_no_match(self):
        assert detect_task_type("do something vague") == "default"

    def test_case_insensitive(self):
        assert detect_task_type("AUDIT all files") == "audit"

    def test_highest_score_wins(self):
        # "audit" gets 2 hits (audit + scan), "review" gets 1 (review)
        result = detect_task_type("audit and scan the codebase and review it")
        assert result == "audit"

    def test_empty_prompt_returns_default(self):
        assert detect_task_type("") == "default"

    def test_multiple_audit_keywords(self):
        result = detect_task_type("verify and inspect and check")
        assert result == "audit"


# ---------- TestTaskTypeRule ----------


class TestTaskTypeRule:
    def test_basic_creation(self):
        rule = TaskTypeRule(runtime="claude")
        assert rule.runtime == "claude"

    def test_model_default_none(self):
        assert TaskTypeRule(runtime="cloud").model is None

    def test_model_set(self):
        rule = TaskTypeRule(runtime="claude", model="haiku")
        assert rule.model == "haiku"

    def test_frozen(self):
        rule = TaskTypeRule(runtime="claude")
        with pytest.raises((AttributeError, TypeError)):
            rule.runtime = "local"  # type: ignore[misc]


# ---------- TestConvergenceConfigTaskTypes ----------


class TestConvergenceConfigTaskTypes:
    def test_no_task_types_section(self, tmp_path: Path):
        """ConvergenceConfig.load() with no YAML → task_type_rules == {}."""
        cfg = ConvergenceConfig.load(tmp_path)
        assert cfg.task_type_rules == {}

    def test_runtime_for_task_type_no_rules(self):
        """No rules → returns default_runtime."""
        cfg = ConvergenceConfig(default_runtime="claude")
        assert cfg.runtime_for_task_type("audit") == "claude"
        assert cfg.runtime_for_task_type("default") == "claude"

    def test_runtime_for_task_type_matching(self):
        """Rules configured → returns rule's runtime."""
        cfg = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={"audit": TaskTypeRule(runtime="cloud")},
        )
        assert cfg.runtime_for_task_type("audit") == "cloud"

    def test_runtime_for_task_type_default_rule_fallback(self):
        """No exact match but 'default' rule → uses it."""
        cfg = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={"default": TaskTypeRule(runtime="local")},
        )
        assert cfg.runtime_for_task_type("review") == "local"

    def test_runtime_for_task_type_exact_beats_default_rule(self):
        """Exact match beats fallback 'default' rule."""
        cfg = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={
                "audit": TaskTypeRule(runtime="cloud"),
                "default": TaskTypeRule(runtime="local"),
            },
        )
        assert cfg.runtime_for_task_type("audit") == "cloud"

    def test_load_task_types_from_yaml(self, tmp_path: Path):
        (tmp_path / "convergence.yaml").write_text(
            "task_types:\n"
            "  audit:\n"
            "    runtime: cloud\n"
        )
        cfg = ConvergenceConfig.load(tmp_path)
        assert "audit" in cfg.task_type_rules
        assert cfg.task_type_rules["audit"].runtime == "cloud"

    def test_invalid_task_type_runtime_raises(self, tmp_path: Path):
        (tmp_path / "convergence.yaml").write_text(
            "task_types:\n"
            "  audit:\n"
            "    runtime: badruntime\n"
        )
        with pytest.raises(SystemExit) as exc_info:
            ConvergenceConfig.load(tmp_path)
        assert "badruntime" in str(exc_info.value)

    def test_needs_local_via_task_type(self):
        cfg = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={"audit": TaskTypeRule(runtime="local")},
        )
        assert cfg.needs_local() is True

    def test_needs_cloud_via_task_type(self):
        cfg = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={"review": TaskTypeRule(runtime="cloud")},
        )
        assert cfg.needs_cloud() is True

    def test_needs_claude_via_task_type(self):
        cfg = ConvergenceConfig(
            default_runtime="local",
            task_type_rules={"implement": TaskTypeRule(runtime="claude")},
        )
        assert cfg.needs_claude() is True

    def test_model_for_task_type_returns_model(self):
        cfg = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={"audit": TaskTypeRule(runtime="claude", model="haiku")},
        )
        assert cfg.model_for_task_type("audit") == "haiku"

    def test_model_for_task_type_none_when_unset(self):
        cfg = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={"audit": TaskTypeRule(runtime="claude")},
        )
        assert cfg.model_for_task_type("audit") is None

    def test_model_for_task_type_default_rule_fallback(self):
        cfg = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={"default": TaskTypeRule(runtime="claude", model="haiku")},
        )
        assert cfg.model_for_task_type("review") == "haiku"

    def test_model_for_task_type_no_rules_returns_none(self):
        cfg = ConvergenceConfig(default_runtime="claude")
        assert cfg.model_for_task_type("audit") is None

    def test_load_task_types_with_model(self, tmp_path: Path):
        (tmp_path / "convergence.yaml").write_text(
            "task_types:\n"
            "  scribe:\n"
            "    runtime: claude\n"
            "    model: haiku\n"
        )
        cfg = ConvergenceConfig.load(tmp_path)
        assert cfg.task_type_rules["scribe"].model == "haiku"

    def test_task_type_non_dict_rule_skipped(self, tmp_path: Path):
        """Non-dict rule data is silently skipped."""
        (tmp_path / "convergence.yaml").write_text(
            "task_types:\n"
            "  audit: null\n"
        )
        cfg = ConvergenceConfig.load(tmp_path)
        # null rule is skipped, so audit not in task_type_rules
        assert "audit" not in cfg.task_type_rules


# ---------- TestQueueRunnerTaskTypeRouting ----------


class TestQueueRunnerTaskTypeRouting:
    def test_run_single_task_uses_default_when_no_conv(
        self, tmp_path: Path
    ):
        """With conv=None, _run_single_task uses the passed orch, no new Orchestrator created."""
        from praxis.checkpoint import CheckpointStore
        from praxis.queue import TaskQueue
        from praxis.queue_runner import _run_single_task

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True)
        queue = TaskQueue(queue_dir)
        queue.ensure_dirs()
        cp_store = CheckpointStore(queue_dir)

        mock_orch = MagicMock()
        mock_orch.run.return_value = "result"
        task = Task.create("do something vague")
        queue.append(task)

        with patch("praxis.queue_runner.Orchestrator") as MockOrch:
            _run_single_task(task, mock_orch, queue, cp_store, conv=None)
            # No new Orchestrator should be created
            MockOrch.assert_not_called()
        mock_orch.run.assert_called_once()

    def test_run_single_task_routes_to_different_runtime(self, tmp_path: Path):
        """Conv with audit→cloud rule routes audit task to cloud runtime."""
        from praxis.checkpoint import CheckpointStore
        from praxis.config import Config
        from praxis.queue import TaskQueue
        from praxis.queue_runner import _run_single_task

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True)
        (tmp_path / ".praxis" / "memory").mkdir(parents=True)
        queue = TaskQueue(queue_dir)
        queue.ensure_dirs()
        cp_store = CheckpointStore(queue_dir)

        default_rt = MagicMock()
        cloud_rt = MagicMock()
        all_runtimes = {"claude": default_rt, "cloud": cloud_rt}

        conv = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={"audit": TaskTypeRule(runtime="cloud")},
        )
        config = Config(
            workspace_root=tmp_path,
            memory_root=tmp_path / ".praxis" / "memory",
            hook_path=tmp_path / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )

        mock_orch = MagicMock()
        mock_orch.run.return_value = "default result"
        task = Task.create("audit the codebase")
        queue.append(task)

        new_orch = MagicMock()
        new_orch.run.return_value = "cloud result"

        with patch("praxis.queue_runner.Orchestrator", return_value=new_orch) as MockOrch:
            _run_single_task(
                task, mock_orch, queue, cp_store,
                conv=conv,
                all_runtimes=all_runtimes,
                config=config,
            )
            # New Orchestrator created with cloud_rt
            MockOrch.assert_called_once_with(cloud_rt, config)
        # The new orchestrator's run was used
        new_orch.run.assert_called_once()
        mock_orch.run.assert_not_called()

    def test_run_single_task_fallback_when_runtime_not_in_map(self, tmp_path: Path):
        """Rule says 'cloud' but all_runtimes doesn't have 'cloud' → uses default orch."""
        from praxis.checkpoint import CheckpointStore
        from praxis.config import Config
        from praxis.queue import TaskQueue
        from praxis.queue_runner import _run_single_task

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True)
        (tmp_path / ".praxis" / "memory").mkdir(parents=True)
        queue = TaskQueue(queue_dir)
        queue.ensure_dirs()
        cp_store = CheckpointStore(queue_dir)

        default_rt = MagicMock()
        # Notice: no "cloud" in all_runtimes
        all_runtimes = {"claude": default_rt}

        conv = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={"audit": TaskTypeRule(runtime="cloud")},
        )
        config = Config(
            workspace_root=tmp_path,
            memory_root=tmp_path / ".praxis" / "memory",
            hook_path=tmp_path / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )

        mock_orch = MagicMock()
        mock_orch.run.return_value = "default result"
        task = Task.create("audit the codebase")
        queue.append(task)

        with patch("praxis.queue_runner.Orchestrator") as MockOrch:
            _run_single_task(
                task, mock_orch, queue, cp_store,
                conv=conv,
                all_runtimes=all_runtimes,
                config=config,
            )
            # No new Orchestrator — cloud not in all_runtimes
            MockOrch.assert_not_called()
        mock_orch.run.assert_called_once()

    def test_create_runtimes_returns_three_values(self):
        """_create_runtimes_for_queue returns a 3-tuple."""
        from praxis.queue_runner import _create_runtimes_for_queue

        mock_rt = MagicMock()
        mock_rt.auth_method = "oauth"

        conv = MagicMock()
        conv.needs_claude.return_value = True
        conv.needs_local.return_value = False
        conv.needs_cloud.return_value = False
        conv.default_runtime = "claude"
        conv.overrides = {}

        with patch("praxis.queue_runner.ClaudeCodeRuntime") as MockClaude:
            MockClaude.from_env.return_value = mock_rt
            result = _create_runtimes_for_queue(conv)

        assert len(result) == 3
        default, overrides, all_runtimes = result
        assert default is mock_rt
        assert isinstance(overrides, dict)
        assert isinstance(all_runtimes, dict)
        assert all_runtimes["claude"] is mock_rt

    def test_run_single_task_no_routing_when_same_runtime(self, tmp_path: Path):
        """When task-type runtime matches default, no new Orchestrator is created."""
        from praxis.checkpoint import CheckpointStore
        from praxis.config import Config
        from praxis.queue import TaskQueue
        from praxis.queue_runner import _run_single_task

        queue_dir = tmp_path / ".praxis" / "queue"
        queue_dir.mkdir(parents=True)
        (tmp_path / ".praxis" / "memory").mkdir(parents=True)
        queue = TaskQueue(queue_dir)
        queue.ensure_dirs()
        cp_store = CheckpointStore(queue_dir)

        default_rt = MagicMock()
        all_runtimes = {"claude": default_rt}

        # audit → claude, which is also the default_runtime → no new orch
        conv = ConvergenceConfig(
            default_runtime="claude",
            task_type_rules={"audit": TaskTypeRule(runtime="claude")},
        )
        config = Config(
            workspace_root=tmp_path,
            memory_root=tmp_path / ".praxis" / "memory",
            hook_path=tmp_path / ".claude" / "hooks" / "escalation-boundary.py",
            allowed_domains=frozenset(),
        )

        mock_orch = MagicMock()
        mock_orch.run.return_value = "result"
        task = Task.create("audit the codebase")
        queue.append(task)

        with patch("praxis.queue_runner.Orchestrator") as MockOrch:
            _run_single_task(
                task, mock_orch, queue, cp_store,
                conv=conv,
                all_runtimes=all_runtimes,
                config=config,
            )
            MockOrch.assert_not_called()
        mock_orch.run.assert_called_once()
