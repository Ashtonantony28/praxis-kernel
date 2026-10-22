"""Shared fixtures for the Praxis test suite."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from praxis.config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------- Hermetic environment ----------


@pytest.fixture(autouse=True)
def _hermetic_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point workspace and memory roots at tmp_path so no test touches the real tree."""
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PRAXIS_MEMORY_ROOT", str(tmp_path / ".praxis" / "memory"))


# ---------- Temp workspace ----------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Temporary workspace with real agent files and hook."""
    # Copy real escalation-boundary hook
    hooks_dir = tmp_path / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / ".claude" / "hooks" / "escalation-boundary.py",
        hooks_dir / "escalation-boundary.py",
    )

    # Copy real agent definitions
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    for md in (REPO_ROOT / ".claude" / "agents").glob("*.md"):
        shutil.copy2(md, agents_dir / md.name)

    # Memory directory
    (tmp_path / ".praxis" / "memory").mkdir(parents=True)

    # Minimal system prompt for tests
    (tmp_path / "praxis-system-prompt.md").write_text("# Test System Prompt\n")

    # Sample file for tool tests
    (tmp_path / "sample.txt").write_text("line one\nline two\nline three\n")

    return tmp_path


@pytest.fixture
def config(workspace: Path) -> Config:
    return Config(
        workspace_root=workspace,
        memory_root=workspace / ".praxis" / "memory",
        hook_path=workspace / ".claude" / "hooks" / "escalation-boundary.py",
        allowed_domains=frozenset(),
    )


# ---------- Fake Anthropic client ----------


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeToolUseBlock:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list[Any] = field(default_factory=list)
    stop_reason: str = "end_turn"


class FakeMessages:
    """Records calls and returns scripted responses."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        snapshot = dict(kwargs)
        if "messages" in snapshot:
            snapshot["messages"] = list(snapshot["messages"])
        self.calls.append(snapshot)
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.messages = FakeMessages(responses)
