"""Tests for praxis/__main__.py — _create_runtimes() and main() entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.convergence import ConvergenceConfig


# ---------- _create_runtimes ----------


def _make_fake_runtime(name: str) -> MagicMock:
    rt = MagicMock()
    rt.auth_method = "oauth"
    rt.base_url = "http://localhost:11434"
    rt.default_model = "llama3.1:8b"
    rt._name = name
    return rt


@pytest.fixture
def fake_claude_rt():
    return _make_fake_runtime("claude")


@pytest.fixture
def fake_local_rt():
    return _make_fake_runtime("local")


def test_create_runtimes_claude_only(fake_claude_rt):
    """Claude-only config creates one runtime, no overrides."""
    from praxis.__main__ import _create_runtimes

    conv = ConvergenceConfig(default_runtime="claude")

    with patch("praxis.__main__.ClaudeCodeRuntime") as mock_cls:
        mock_cls.from_env.return_value = fake_claude_rt
        default, overrides = _create_runtimes(conv)

    assert default is fake_claude_rt
    assert overrides == {}
    mock_cls.from_env.assert_called_once()


def test_create_runtimes_local_only(fake_local_rt):
    """Local-only config creates one runtime, no overrides."""
    from praxis.__main__ import _create_runtimes

    conv = ConvergenceConfig(default_runtime="local")

    with patch("praxis.__main__.LocalRuntime") as mock_cls:
        mock_cls.from_env.return_value = fake_local_rt
        default, overrides = _create_runtimes(conv)

    assert default is fake_local_rt
    assert overrides == {}
    mock_cls.from_env.assert_called_once()


def test_create_runtimes_mixed(fake_claude_rt, fake_local_rt):
    """Mixed config creates both runtimes with overrides."""
    from praxis.__main__ import _create_runtimes

    conv = ConvergenceConfig(
        default_runtime="claude",
        overrides={"scout": "local"},
    )

    with (
        patch("praxis.__main__.ClaudeCodeRuntime") as mock_claude,
        patch("praxis.__main__.LocalRuntime") as mock_local,
    ):
        mock_claude.from_env.return_value = fake_claude_rt
        mock_local.from_env.return_value = fake_local_rt
        default, overrides = _create_runtimes(conv)

    assert default is fake_claude_rt
    assert overrides == {"scout": fake_local_rt}


def test_create_runtimes_override_same_as_default_excluded(fake_claude_rt):
    """Override matching default runtime is excluded from overrides dict."""
    from praxis.__main__ import _create_runtimes

    conv = ConvergenceConfig(
        default_runtime="claude",
        overrides={"builder": "claude"},
    )

    with patch("praxis.__main__.ClaudeCodeRuntime") as mock_cls:
        mock_cls.from_env.return_value = fake_claude_rt
        default, overrides = _create_runtimes(conv)

    assert default is fake_claude_rt
    assert overrides == {}


def test_create_runtimes_stderr_logging(fake_claude_rt, capsys):
    """Runtime creation logs to stderr."""
    from praxis.__main__ import _create_runtimes

    conv = ConvergenceConfig(default_runtime="claude")

    with patch("praxis.__main__.ClaudeCodeRuntime") as mock_cls:
        mock_cls.from_env.return_value = fake_claude_rt
        _create_runtimes(conv)

    captured = capsys.readouterr()
    assert "runtime claude" in captured.err


# ---------- main() ----------


@pytest.fixture
def _mock_main_deps(tmp_path):
    """Patch all main() dependencies so no real I/O or API calls happen."""
    config = MagicMock()
    config.workspace_root = tmp_path

    conv = ConvergenceConfig(default_runtime="claude")
    fake_rt = _make_fake_runtime("claude")

    orch = MagicMock()
    orch.run.return_value = "result text"

    patches = {
        "config": patch("praxis.__main__.Config.from_env", return_value=config),
        "conv": patch("praxis.__main__.ConvergenceConfig.load", return_value=conv),
        "create_rt": patch(
            "praxis.__main__._create_runtimes",
            return_value=(fake_rt, {}),
        ),
        "orch_cls": patch("praxis.__main__.Orchestrator", return_value=orch),
    }

    started = {k: p.start() for k, p in patches.items()}
    yield {"mocks": started, "orch": orch}
    for p in patches.values():
        p.stop()


def test_main_with_argv(_mock_main_deps):
    """main() joins sys.argv[1:] as the message and passes an active Mode."""
    from praxis.__main__ import main
    from unittest.mock import ANY

    with patch.object(sys, "argv", ["praxis", "hello", "world"]):
        main()

    _mock_main_deps["orch"].run.assert_called_once_with("hello world", mode=ANY)


def test_main_with_stdin(_mock_main_deps):
    """main() reads stdin when no args given and passes an active Mode."""
    from praxis.__main__ import main
    from unittest.mock import ANY

    with (
        patch.object(sys, "argv", ["praxis"]),
        patch.object(sys, "stdin") as mock_stdin,
    ):
        mock_stdin.read.return_value = "stdin message"
        main()

    _mock_main_deps["orch"].run.assert_called_once_with("stdin message", mode=ANY)


def test_main_keyboard_interrupt(_mock_main_deps):
    """KeyboardInterrupt → SystemExit(1)."""
    from praxis.__main__ import main

    _mock_main_deps["orch"].run.side_effect = KeyboardInterrupt()

    with (
        patch.object(sys, "argv", ["praxis", "hi"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code == 1


def test_main_systemexit_passthrough(_mock_main_deps):
    """SystemExit is re-raised, not wrapped."""
    from praxis.__main__ import main

    _mock_main_deps["orch"].run.side_effect = SystemExit("[praxis] fatal: test")

    with (
        patch.object(sys, "argv", ["praxis", "hi"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert "test" in str(exc_info.value)


def test_main_generic_exception(_mock_main_deps):
    """Unexpected exceptions → SystemExit with fatal message."""
    from praxis.__main__ import main

    _mock_main_deps["orch"].run.side_effect = RuntimeError("boom")

    with (
        patch.object(sys, "argv", ["praxis", "hi"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert "fatal" in str(exc_info.value)
    assert "boom" in str(exc_info.value)


def test_main_prints_result(_mock_main_deps, capsys):
    """main() prints the orchestrator result to stdout."""
    from praxis.__main__ import main

    with patch.object(sys, "argv", ["praxis", "hi"]):
        main()

    captured = capsys.readouterr()
    assert "result text" in captured.out


# ---------- _parse_mode ----------


def test_parse_mode_interactive():
    from praxis.__main__ import _parse_mode

    assert _parse_mode(["praxis", "hello"]) == "interactive"
    assert _parse_mode(["praxis"]) == "interactive"


def test_parse_mode_queue():
    from praxis.__main__ import _parse_mode

    assert _parse_mode(["praxis", "--queue"]) == "queue"


def test_parse_mode_daemon():
    from praxis.__main__ import _parse_mode

    assert _parse_mode(["praxis", "--daemon"]) == "daemon"


def test_parse_mode_stop():
    from praxis.__main__ import _parse_mode

    assert _parse_mode(["praxis", "--stop"]) == "stop"


def test_parse_mode_status():
    from praxis.__main__ import _parse_mode

    assert _parse_mode(["praxis", "--status"]) == "status"


# ---------- main() queue/daemon modes ----------


def test_main_queue_mode(tmp_path):
    """--queue invokes run_queue_loop."""
    from praxis.__main__ import main

    config = MagicMock()
    config.workspace_root = tmp_path

    with (
        patch("praxis.__main__.Config.from_env", return_value=config),
        patch("praxis.queue_runner.run_queue_loop") as mock_loop,
        patch.object(sys, "argv", ["praxis", "--queue"]),
    ):
        main()
        mock_loop.assert_called_once_with(tmp_path)


def test_main_daemon_mode(tmp_path):
    """--daemon invokes start_daemon."""
    from praxis.__main__ import main

    config = MagicMock()
    config.workspace_root = tmp_path

    with (
        patch("praxis.__main__.Config.from_env", return_value=config),
        patch("praxis.daemon.start_daemon") as mock_start,
        patch.object(sys, "argv", ["praxis", "--daemon"]),
    ):
        main()
        mock_start.assert_called_once_with(tmp_path)


def test_main_stop_mode(tmp_path):
    """--stop invokes stop_daemon."""
    from praxis.__main__ import main

    config = MagicMock()
    config.workspace_root = tmp_path

    with (
        patch("praxis.__main__.Config.from_env", return_value=config),
        patch("praxis.daemon.stop_daemon") as mock_stop,
        patch.object(sys, "argv", ["praxis", "--stop"]),
    ):
        main()
        mock_stop.assert_called_once_with(tmp_path)


def test_main_status_mode(tmp_path):
    """--status invokes report_status."""
    from praxis.__main__ import main

    config = MagicMock()
    config.workspace_root = tmp_path

    with (
        patch("praxis.__main__.Config.from_env", return_value=config),
        patch("praxis.daemon.report_status") as mock_status,
        patch.object(sys, "argv", ["praxis", "--status"]),
    ):
        main()
        mock_status.assert_called_once_with(tmp_path)


# ---------- _parse_mode --list-staged ----------


class TestListStaged:
    """Tests for --list-staged subcommand."""

    def test_parse_mode_list_staged(self):
        """--list-staged flag parses to 'list_staged' mode."""
        from praxis.__main__ import _parse_mode

        assert _parse_mode(["praxis", "--list-staged"]) == "list_staged"

    def test_list_staged_no_staging_dir(self, tmp_path, capsys):
        """No .praxis/staging/ dir → 'No staged items' message."""
        from praxis.__main__ import _run_list_staged

        _run_list_staged(tmp_path)
        captured = capsys.readouterr()
        assert "No staged items" in captured.out

    def test_list_staged_external_actions(self, tmp_path, capsys):
        """external_actions.jsonl with pending entry is displayed."""
        import json
        from praxis.__main__ import _run_list_staged

        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True)
        actions_file = staging / "external_actions.jsonl"
        entry = {
            "status": "pending",
            "provider": "notion",
            "action": "create_page",
            "queued_at": "2026-05-27T00:00:00",
            "params": {},
        }
        actions_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        _run_list_staged(tmp_path)
        captured = capsys.readouterr()
        assert "notion" in captured.out
        assert "create_page" in captured.out

    def test_list_staged_slack_messages(self, tmp_path, capsys):
        """Slack staged messages directory with files is reported."""
        from praxis.__main__ import _run_list_staged

        slack_msgs = tmp_path / ".praxis" / "staging" / "slack" / "messages"
        slack_msgs.mkdir(parents=True)
        (slack_msgs / "abc.json").write_text('{"text": "hi"}', encoding="utf-8")

        _run_list_staged(tmp_path)
        captured = capsys.readouterr()
        assert "Slack staged messages" in captured.out
        assert "1" in captured.out

    def test_list_staged_empty_staging_dir(self, tmp_path, capsys):
        """Staging dir exists but nothing in it → 'No staged items'."""
        from praxis.__main__ import _run_list_staged

        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True)

        _run_list_staged(tmp_path)
        captured = capsys.readouterr()
        assert "No staged items" in captured.out

    def test_list_staged_skips_non_pending_actions(self, tmp_path, capsys):
        """Only pending entries are counted; approved/rejected are ignored."""
        import json
        from praxis.__main__ import _run_list_staged

        staging = tmp_path / ".praxis" / "staging"
        staging.mkdir(parents=True)
        actions_file = staging / "external_actions.jsonl"
        lines = [
            json.dumps({"status": "approved", "provider": "linear", "action": "create_issue",
                        "queued_at": "2026-05-27T00:00:00", "params": {}}),
            json.dumps({"status": "rejected", "provider": "notion", "action": "update_page",
                        "queued_at": "2026-05-27T00:00:00", "params": {}}),
        ]
        actions_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        _run_list_staged(tmp_path)
        captured = capsys.readouterr()
        # No pending entries, and no other staging dirs, so nothing should show
        assert "No staged items" in captured.out
