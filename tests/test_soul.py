"""Tests for SOUL.md persona layer in praxis/orchestrator.py (TASK-H04)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from praxis.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(workspace_root: Path) -> Config:
    return Config(
        workspace_root=workspace_root,
        memory_root=workspace_root / ".praxis" / "memory",
        hook_path=workspace_root / ".claude" / "hooks" / "escalation-boundary.py",
        allowed_domains=frozenset(),
    )


def _make_orchestrator(workspace_root: Path) -> "Orchestrator":
    """Create a minimal Orchestrator with mocked runtime and no real agents directory."""
    from praxis.orchestrator import Orchestrator

    config = _make_config(workspace_root)
    mock_runtime = MagicMock()

    with patch("praxis.orchestrator.load_subagents", return_value={}):
        orch = Orchestrator(runtime=mock_runtime, config=config)

    return orch


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSoulLoading:
    def test_soul_loads_when_present(self, tmp_path: Path):
        """SOUL.md content appears in the composed system prompt when file exists."""
        # Create the system prompt file
        system_prompt_file = tmp_path / "praxis-system-prompt.md"
        system_prompt_file.write_text("## §5 Governance\nGovernance text here.\n", encoding="utf-8")

        # Create .praxis/SOUL.md
        soul_dir = tmp_path / ".praxis"
        soul_dir.mkdir(parents=True, exist_ok=True)
        soul_path = soul_dir / "SOUL.md"
        soul_path.write_text("# Persona\nI am Praxis, a helpful assistant.\n", encoding="utf-8")

        orch = _make_orchestrator(tmp_path)

        assert "I am Praxis, a helpful assistant." in orch.system_prompt

    def test_soul_absent_no_error(self, tmp_path: Path):
        """No SOUL.md → _load_system_prompt() succeeds without raising."""
        system_prompt_file = tmp_path / "praxis-system-prompt.md"
        system_prompt_file.write_text("## §5 Governance\nGovernance text here.\n", encoding="utf-8")

        # Ensure .praxis dir does NOT have SOUL.md
        praxis_dir = tmp_path / ".praxis"
        praxis_dir.mkdir(parents=True, exist_ok=True)
        # No SOUL.md written here

        orch = _make_orchestrator(tmp_path)

        # Prompt is still formed with governance text
        assert "Governance text here." in orch.system_prompt

    def test_soul_not_logged(self, tmp_path: Path):
        """Logger is never called with SOUL.md content (privacy contract)."""
        system_prompt_file = tmp_path / "praxis-system-prompt.md"
        system_prompt_file.write_text("## §5 Governance\nGovernance text here.\n", encoding="utf-8")

        soul_dir = tmp_path / ".praxis"
        soul_dir.mkdir(parents=True, exist_ok=True)
        soul_secret = "MySecretPersonaString_XYZ"
        (soul_dir / "SOUL.md").write_text(f"# Persona\n{soul_secret}\n", encoding="utf-8")

        mock_logger = MagicMock()

        # Patch the logging module used in orchestrator
        with patch("praxis.orchestrator.load_subagents", return_value={}):
            with patch("logging.getLogger", return_value=mock_logger):
                from praxis.orchestrator import Orchestrator
                config = _make_config(tmp_path)
                mock_runtime = MagicMock()
                orch = Orchestrator(runtime=mock_runtime, config=config)

        # Collect all strings passed to any logger call
        all_logged = []
        for call_args in mock_logger.mock_calls:
            for arg in call_args.args:
                all_logged.append(str(arg))
            for val in call_args.kwargs.values():
                all_logged.append(str(val))

        logged_text = " ".join(all_logged)
        assert soul_secret not in logged_text, (
            f"SOUL.md content '{soul_secret}' was logged — privacy contract violated"
        )

    def test_soul_appended_after_governance(self, tmp_path: Path):
        """SOUL.md content appears AFTER the §5 governance text in the composed prompt."""
        governance_text = "## §5 Governance\nGovernance block content.\n"
        soul_text = "# Persona\nSoul block content.\n"

        system_prompt_file = tmp_path / "praxis-system-prompt.md"
        system_prompt_file.write_text(governance_text, encoding="utf-8")

        soul_dir = tmp_path / ".praxis"
        soul_dir.mkdir(parents=True, exist_ok=True)
        (soul_dir / "SOUL.md").write_text(soul_text, encoding="utf-8")

        orch = _make_orchestrator(tmp_path)

        prompt = orch.system_prompt
        governance_pos = prompt.find("Governance block content.")
        soul_pos = prompt.find("Soul block content.")

        assert governance_pos != -1, "Governance text not found in system prompt"
        assert soul_pos != -1, "SOUL.md content not found in system prompt"
        assert soul_pos > governance_pos, (
            "SOUL.md content appears before governance text — ordering contract violated"
        )
