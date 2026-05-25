"""Tests for praxis.hooks — §5 escalation boundary enforcement."""

from __future__ import annotations

import shutil
from pathlib import Path

from praxis.config import Config
from praxis.hooks import run_pretool_hook

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_hook_allows_workspace_write(config: Config, workspace: Path):
    result = run_pretool_hook(
        config, "Write", {"file_path": str(workspace / "new.txt"), "content": "hi"}
    )
    assert result.allowed


def test_hook_blocks_outside_workspace_write(config: Config):
    result = run_pretool_hook(
        config, "Write", {"file_path": "/tmp/evil.txt", "content": "bad"}
    )
    assert not result.allowed
    assert "outside WORKSPACE_ROOT" in (result.reason or "")


def test_hook_blocks_control_plane_write(config: Config, workspace: Path):
    result = run_pretool_hook(
        config,
        "Edit",
        {
            "file_path": str(workspace / ".claude" / "settings.json"),
            "old_string": "x",
            "new_string": "y",
        },
    )
    assert not result.allowed
    assert "control plane" in (result.reason or "")


def test_hook_blocks_webfetch(config: Config):
    result = run_pretool_hook(
        config, "WebFetch", {"url": "https://example.com"}
    )
    assert not result.allowed
    assert "egress" in (result.reason or "").lower() or "ALLOWED_DOMAINS" in (result.reason or "")


def test_hook_blocks_websearch(config: Config):
    result = run_pretool_hook(config, "WebSearch", {"query": "test"})
    assert not result.allowed


def test_hook_blocks_bash_curl(config: Config):
    result = run_pretool_hook(
        config, "Bash", {"command": "curl https://example.com"}
    )
    assert not result.allowed
    assert "egress" in (result.reason or "").lower() or "network" in (result.reason or "").lower()


def test_hook_allows_bash_echo(config: Config):
    result = run_pretool_hook(config, "Bash", {"command": "echo hello"})
    assert result.allowed


def test_hook_allows_read_anywhere(config: Config):
    """Read is not a mutating tool — the hook does not check it."""
    result = run_pretool_hook(
        config, "Read", {"file_path": "/etc/passwd"}
    )
    assert result.allowed


def test_hook_missing_file_allows(tmp_path: Path):
    cfg = Config(
        workspace_root=tmp_path,
        memory_root=tmp_path / ".praxis" / "memory",
        hook_path=tmp_path / "nonexistent.py",
        allowed_domains=frozenset(),
    )
    result = run_pretool_hook(cfg, "Write", {"file_path": "/tmp/x", "content": ""})
    assert result.allowed


# -- Space-in-path and relative-path regression tests ------------------------


def test_hook_allows_bash_rm_relative_path(config: Config):
    """Bug fix: rm with relative path must not false-positive on mid-slash."""
    result = run_pretool_hook(
        config, "Bash", {"command": "rm tests/file.py"}
    )
    assert result.allowed


def test_hook_blocks_bash_rm_outside_workspace(config: Config):
    """rm with absolute path outside workspace is still blocked."""
    result = run_pretool_hook(
        config, "Bash", {"command": "rm /etc/important"}
    )
    assert not result.allowed
    assert "outside WORKSPACE_ROOT" in (result.reason or "")


def test_hook_allows_bash_rm_quoted_workspace_path(tmp_path: Path):
    """Bug fix: rm with quoted path containing spaces is allowed inside workspace."""
    ws = tmp_path / "My Workspace"
    ws.mkdir()
    hooks_dir = ws / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / ".claude" / "hooks" / "escalation-boundary.py",
        hooks_dir / "escalation-boundary.py",
    )
    (ws / ".praxis" / "memory").mkdir(parents=True)

    cfg = Config(
        workspace_root=ws,
        memory_root=ws / ".praxis" / "memory",
        hook_path=hooks_dir / "escalation-boundary.py",
        allowed_domains=frozenset(),
    )

    target = str(ws / "some file.txt")
    result = run_pretool_hook(
        cfg, "Bash", {"command": f'rm "{target}"'}
    )
    assert result.allowed


def test_hook_blocks_bash_redirect_outside(config: Config):
    """Redirect to absolute path outside workspace is blocked."""
    result = run_pretool_hook(
        config, "Bash", {"command": "echo hi > /tmp/evil.txt"}
    )
    assert not result.allowed
    assert "outside WORKSPACE_ROOT" in (result.reason or "")
