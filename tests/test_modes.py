"""tests/test_modes.py — Tests for Mode dataclass + Mode.load()."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from praxis.modes.base import Mode
from praxis.modes.plan import MODE as plan_mode
from praxis.modes.build import MODE as build_mode


# ---------------------------------------------------------------------------
# TestModeDataclass
# ---------------------------------------------------------------------------

class TestModeDataclass:
    """Tests for Mode field defaults, immutability, and equality."""

    def test_default_fields(self):
        """Mode fields have correct defaults."""
        m = Mode(name="x")
        assert m.name == "x"
        assert m.allowed_tools == frozenset()
        assert m.denied_tools == frozenset()
        assert m.prompt_suffix == ""
        assert m.requires_confirmation is False
        assert m.model_override is None

    def test_frozen_cannot_assign(self):
        """Mode is frozen — assigning to a field must raise."""
        m = Mode(name="frozen_test")
        with pytest.raises((AttributeError, TypeError)):
            m.name = "changed"  # type: ignore[misc]

    def test_allowed_and_denied_are_frozensets(self):
        """allowed_tools and denied_tools are frozensets, not plain sets."""
        m = Mode(
            name="fs_test",
            allowed_tools=frozenset({"Read", "Grep"}),
            denied_tools=frozenset({"Write", "Bash"}),
        )
        assert isinstance(m.allowed_tools, frozenset)
        assert isinstance(m.denied_tools, frozenset)

    def test_equality_same_fields(self):
        """Two Mode instances with identical fields are equal."""
        m1 = Mode(name="eq", denied_tools=frozenset({"Write"}), requires_confirmation=True)
        m2 = Mode(name="eq", denied_tools=frozenset({"Write"}), requires_confirmation=True)
        assert m1 == m2


# ---------------------------------------------------------------------------
# TestModeLoadBuiltin
# ---------------------------------------------------------------------------

class TestModeLoadBuiltin:
    """Tests for Mode.load() against built-in plan and build modes."""

    def test_load_plan_returns_mode(self):
        """Mode.load('plan') returns a Mode instance."""
        mode = Mode.load("plan")
        assert isinstance(mode, Mode)
        assert mode.name == "plan"

    def test_load_plan_requires_confirmation(self):
        """plan mode has requires_confirmation=True."""
        assert Mode.load("plan").requires_confirmation is True

    def test_load_plan_denies_write(self):
        "'Write' is in plan mode denied_tools."
        assert "Write" in Mode.load("plan").denied_tools

    def test_load_plan_denies_bash(self):
        "'Bash' is in plan mode denied_tools."
        assert "Bash" in Mode.load("plan").denied_tools

    def test_load_build_returns_mode(self):
        """Mode.load('build') returns a Mode with name='build'."""
        mode = Mode.load("build")
        assert isinstance(mode, Mode)
        assert mode.name == "build"

    def test_load_build_no_denied_tools(self):
        """build mode has empty denied_tools."""
        assert len(Mode.load("build").denied_tools) == 0


# ---------------------------------------------------------------------------
# TestModeLoadYAML
# ---------------------------------------------------------------------------

class TestModeLoadYAML:
    """Tests for Mode.load() reading from user-supplied praxis/modes.yaml."""

    def _write_modes_yaml(self, tmp_path: Path, content: str) -> None:
        modes_dir = tmp_path / "praxis"
        modes_dir.mkdir(parents=True, exist_ok=True)
        (modes_dir / "modes.yaml").write_text(textwrap.dedent(content))

    def test_yaml_custom_mode_denied_tools(self, tmp_path, monkeypatch):
        """YAML custom mode with denied_tools=['Write'] is loaded correctly."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        self._write_modes_yaml(tmp_path, """
            modes:
              custom:
                denied_tools:
                  - Write
        """)
        mode = Mode.load("custom")
        assert "Write" in mode.denied_tools

    def test_yaml_overrides_builtin(self, tmp_path, monkeypatch):
        """User YAML override of 'plan' takes precedence over the built-in."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        self._write_modes_yaml(tmp_path, """
            modes:
              plan:
                denied_tools:
                  - Read
        """)
        mode = Mode.load("plan")
        assert mode.denied_tools == frozenset({"Read"})
        # Built-in has Write/Edit/Bash, but override only has Read
        assert "Write" not in mode.denied_tools

    def test_yaml_allowed_tools(self, tmp_path, monkeypatch):
        """YAML allowed_tools list is loaded as a frozenset."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        self._write_modes_yaml(tmp_path, """
            modes:
              readonly:
                allowed_tools:
                  - Read
                  - Grep
        """)
        mode = Mode.load("readonly")
        assert mode.allowed_tools == frozenset({"Read", "Grep"})

    def test_yaml_requires_confirmation_true(self, tmp_path, monkeypatch):
        """YAML requires_confirmation: true is respected."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        self._write_modes_yaml(tmp_path, """
            modes:
              careful:
                requires_confirmation: true
        """)
        mode = Mode.load("careful")
        assert mode.requires_confirmation is True

    def test_yaml_model_override(self, tmp_path, monkeypatch):
        """YAML model_override is set on the returned Mode."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        self._write_modes_yaml(tmp_path, """
            modes:
              haiku_mode:
                model_override: "claude-haiku-3"
        """)
        mode = Mode.load("haiku_mode")
        assert mode.model_override == "claude-haiku-3"

    def test_yaml_empty_modes_falls_through_to_builtin(self, tmp_path, monkeypatch):
        """YAML with modes: {} does not match any name; built-in is used instead."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        self._write_modes_yaml(tmp_path, """
            modes: {}
        """)
        # 'build' is not in the YAML override — falls through to built-in
        mode = Mode.load("build")
        assert mode.name == "build"
        assert len(mode.denied_tools) == 0


# ---------------------------------------------------------------------------
# TestModeLoadErrors
# ---------------------------------------------------------------------------

class TestModeLoadErrors:
    """Tests for Mode.load() error paths."""

    def test_nonexistent_mode_raises(self, tmp_path, monkeypatch):
        """Loading an unknown mode name raises ValueError."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        # No YAML present, so it falls through to built-in lookup then raises
        with pytest.raises(ValueError):
            Mode.load("nonexistent_mode_xyz")

    def test_error_mentions_mode_name(self, tmp_path, monkeypatch):
        """ValueError message includes the requested mode name."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        with pytest.raises(ValueError, match="nonexistent"):
            Mode.load("nonexistent")

    def test_error_mentions_available_builtins(self, tmp_path, monkeypatch):
        """ValueError message lists 'plan' and 'build' as available options."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        with pytest.raises(ValueError) as exc_info:
            Mode.load("no_such_mode")
        msg = str(exc_info.value)
        assert "plan" in msg
        assert "build" in msg

    def test_yaml_with_unknown_keys_does_not_crash(self, tmp_path, monkeypatch):
        """Unrecognised YAML keys in a mode entry are silently ignored."""
        monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
        modes_dir = tmp_path / "praxis"
        modes_dir.mkdir(parents=True, exist_ok=True)
        (modes_dir / "modes.yaml").write_text(textwrap.dedent("""
            modes:
              weird:
                denied_tools:
                  - Bash
                unknown_key_future_feature: "some value"
                another_unknown: 42
        """))
        # Should not raise; unknown keys are ignored
        mode = Mode.load("weird")
        assert "Bash" in mode.denied_tools
