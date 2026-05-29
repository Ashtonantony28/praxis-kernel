"""Tests for praxis/config_wizard.py.

All tests mock I/O — no real keyboard input, no real file writes except to tmp_path.
No network access required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from praxis.config_wizard import (
    DEFAULTS,
    PRESETS,
    ROLES,
    _load_current_config,
    _read_env,
    _update_env,
    _write_convergence_agents,
    run_config_wizard,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_input(*answers):
    """Return a callable that returns answers in order (for visible prompts)."""
    it = iter(answers)

    def _input(prompt=""):
        return next(it, "")

    return _input


# ===========================================================================
# Tests 1–2: _read_env
# ===========================================================================

def test_read_env_empty(tmp_path):
    """_read_env returns empty dict for nonexistent file."""
    env_file = tmp_path / ".env"
    result = _read_env(env_file)
    assert result == {}


def test_read_env_basic(tmp_path):
    """_read_env parses KEY=VALUE pairs, ignores comments and blanks."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\n\nFOO=bar\nBAZ=qux\n# another comment\n\n",
        encoding="utf-8",
    )
    result = _read_env(env_file)
    assert result == {"FOO": "bar", "BAZ": "qux"}


# ===========================================================================
# Tests 3–5: _update_env
# ===========================================================================

def test_update_env_adds_new_key(tmp_path):
    """_update_env adds a key that doesn't exist yet."""
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=yes\n", encoding="utf-8")
    _update_env(env_file, {"NEW_KEY": "hello"})
    result = _read_env(env_file)
    assert result["NEW_KEY"] == "hello"
    assert result["EXISTING"] == "yes"


def test_update_env_replaces_existing_key(tmp_path):
    """_update_env replaces an existing key in place, doesn't create duplicates."""
    env_file = tmp_path / ".env"
    env_file.write_text("PRAXIS_RUNTIME=claude\n", encoding="utf-8")
    _update_env(env_file, {"PRAXIS_RUNTIME": "local"})
    content = env_file.read_text(encoding="utf-8")
    # Only one occurrence
    assert content.count("PRAXIS_RUNTIME=") == 1
    result = _read_env(env_file)
    assert result["PRAXIS_RUNTIME"] == "local"


def test_update_env_preserves_unrelated_keys(tmp_path):
    """_update_env never removes keys it wasn't asked to change."""
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=keep_me\nBAR=also_keep\n", encoding="utf-8")
    _update_env(env_file, {"BAZ": "added"})
    result = _read_env(env_file)
    assert result["FOO"] == "keep_me"
    assert result["BAR"] == "also_keep"
    assert result["BAZ"] == "added"


# ===========================================================================
# Tests 6–8: _load_current_config
# ===========================================================================

def test_load_current_config_defaults(tmp_path):
    """When .env and yaml don't exist, _load_current_config returns DEFAULTS values."""
    env_file = tmp_path / ".env"
    config = _load_current_config(tmp_path, env_file)
    for key, val in DEFAULTS.items():
        assert config[key] == val, f"Expected DEFAULTS[{key!r}]={val!r}, got {config[key]!r}"


def test_load_current_config_from_env(tmp_path):
    """PRAXIS_RUNTIME, PRAXIS_MAX_SESSION_COST, PRAXIS_MAX_TURNS, PRAXIS_EFFORT_PRESET in .env are picked up."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PRAXIS_RUNTIME=local\n"
        "PRAXIS_MAX_SESSION_COST=9.99\n"
        "PRAXIS_MAX_TURNS=150\n"
        "PRAXIS_EFFORT_PRESET=high\n",
        encoding="utf-8",
    )
    config = _load_current_config(tmp_path, env_file)
    assert config["runtime"] == "local"
    assert config["cost_cap"] == "9.99"
    assert config["max_turns"] == "150"
    assert config["effort_preset"] == "high"


def test_load_current_config_from_yaml(tmp_path):
    """agents: section in convergence.yaml is picked up."""
    yaml_file = tmp_path / "convergence.yaml"
    yaml_file.write_text(
        "default_runtime: claude\n"
        "agents:\n"
        "  orchestrator: claude-opus-4-7\n"
        "  builder: claude-haiku-4-5\n"
        "  reviewer: claude-haiku-4-5\n"
        "  scout: claude-haiku-4-5\n"
        "  scribe: claude-haiku-4-5\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    config = _load_current_config(tmp_path, env_file)
    assert config["orchestrator"] == "claude-opus-4-7"
    assert config["builder"] == "claude-haiku-4-5"


# ===========================================================================
# Tests 9–10: _write_convergence_agents
# ===========================================================================

def test_write_convergence_agents_creates_section(tmp_path):
    """When no agents: section exists, appends one."""
    yaml_file = tmp_path / "convergence.yaml"
    agents = {role: "claude-sonnet-4-6" for role in ROLES}
    _write_convergence_agents(yaml_file, agents)
    content = yaml_file.read_text(encoding="utf-8")
    assert "agents:" in content
    for role in ROLES:
        assert f"  {role}:" in content


def test_write_convergence_agents_replaces_section(tmp_path):
    """When agents: section already exists, replaces it without touching runtimes: or task_types:."""
    yaml_file = tmp_path / "convergence.yaml"
    yaml_file.write_text(
        "runtimes:\n"
        "  cloud:\n"
        "    model: gpt-4o\n"
        "agents:\n"
        "  orchestrator: claude-opus-4-7\n"
        "  builder: claude-opus-4-7\n"
        "  reviewer: claude-haiku-4-5\n"
        "  scout: claude-haiku-4-5\n"
        "  scribe: claude-haiku-4-5\n"
        "task_types:\n"
        "  implement: claude\n",
        encoding="utf-8",
    )
    new_agents = {role: "claude-sonnet-4-6" for role in ROLES}
    _write_convergence_agents(yaml_file, new_agents)
    content = yaml_file.read_text(encoding="utf-8")
    # Original sections preserved
    assert "runtimes:" in content
    assert "task_types:" in content
    # New agents values are there
    for role in ROLES:
        assert f"  {role}: claude-sonnet-4-6" in content
    # Old opus values replaced
    assert "claude-opus-4-7" not in content


# ===========================================================================
# Tests 11–15: run_config_wizard — menu interactions
# ===========================================================================

def test_model_selection_updates_config(tmp_path):
    """Simulate user picking [1] orchestrator, then model 2 (sonnet), then [11] done."""
    # Model 2 is claude-sonnet-4-6
    inp = make_input("1", "2", "11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    yaml_file = tmp_path / "convergence.yaml"
    assert yaml_file.exists()
    content = yaml_file.read_text(encoding="utf-8")
    assert "orchestrator: claude-sonnet-4-6" in content


def test_max_turns_selection(tmp_path):
    """Simulate [6], enter '120', then [11]. Verify PRAXIS_MAX_TURNS=120 in .env."""
    inp = make_input("6", "120", "11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    env = _read_env(tmp_path / ".env")
    assert env["PRAXIS_MAX_TURNS"] == "120"


def test_max_turns_invalid_then_valid(tmp_path):
    """Simulate [6], enter '999' (too large), then '50', then [11]. Verify 50 written."""
    inp = make_input("6", "999", "50", "11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    env = _read_env(tmp_path / ".env")
    assert env["PRAXIS_MAX_TURNS"] == "50"


def test_cost_cap_selection(tmp_path):
    """Simulate [7], enter '5.50', then [11]. Verify PRAXIS_MAX_SESSION_COST=5.50 in .env."""
    inp = make_input("7", "5.50", "11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    env = _read_env(tmp_path / ".env")
    assert env["PRAXIS_MAX_SESSION_COST"] == "5.50"


def test_runtime_selection(tmp_path):
    """Simulate [8], select '2' (cloud), then [11]. Verify PRAXIS_RUNTIME=cloud in .env."""
    inp = make_input("8", "2", "11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    env = _read_env(tmp_path / ".env")
    assert env["PRAXIS_RUNTIME"] == "cloud"


# ===========================================================================
# Tests 16–19: Effort presets
# ===========================================================================

def test_effort_preset_medium_applies_all_fields(tmp_path):
    """Simulate [9], select '3' (medium), confirm with 'y', then [10].

    Verify all models set to sonnet, max_turns=80, cost_cap=5.00 in output files.
    """
    # Preset order: 1=minimal, 2=low, 3=medium
    inp = make_input("9", "3", "y", "11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    env = _read_env(tmp_path / ".env")
    assert env["PRAXIS_MAX_TURNS"] == "80"
    assert env["PRAXIS_MAX_SESSION_COST"] == "5.00"
    assert env["PRAXIS_EFFORT_PRESET"] == "medium"

    yaml_file = tmp_path / "convergence.yaml"
    content = yaml_file.read_text(encoding="utf-8")
    for role in ROLES:
        assert f"  {role}: claude-sonnet-4-6" in content


def test_effort_preset_high_diff_shows_changes(tmp_path, capsys):
    """Preset 'high' should change orchestrator and builder to opus.

    Confirm the confirmation actually ran (check file contents).
    """
    # Preset order: 4=high
    inp = make_input("9", "4", "y", "11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    env = _read_env(tmp_path / ".env")
    assert env["PRAXIS_EFFORT_PRESET"] == "high"
    assert env["PRAXIS_MAX_TURNS"] == "120"
    assert env["PRAXIS_MAX_SESSION_COST"] == "10.00"

    yaml_file = tmp_path / "convergence.yaml"
    content = yaml_file.read_text(encoding="utf-8")
    assert "orchestrator: claude-opus-4-7" in content
    assert "builder: claude-opus-4-7" in content

    # The diff output should have been printed
    out = capsys.readouterr().out
    assert "Applying" in out or "High" in out


def test_effort_preset_abort_with_n(tmp_path):
    """Simulate [9], select '1' (minimal), then 'n' (don't confirm), then [10].

    Verify the original settings are NOT replaced (model is still the default, not haiku).
    """
    # Preset order: 1=minimal (all haiku)
    inp = make_input("9", "1", "n", "11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    yaml_file = tmp_path / "convergence.yaml"
    content = yaml_file.read_text(encoding="utf-8")
    # Default orchestrator model should be opus, NOT haiku
    assert "orchestrator: claude-haiku-4-5" not in content
    # Defaults: orchestrator is claude-opus-4-7
    assert "orchestrator: claude-opus-4-7" in content


def test_effort_preset_custom_noop(tmp_path):
    """Simulate [9], select '6' (custom), then [10]. Verify nothing changed from defaults."""
    # Preset order has 5 named presets; index 6 = custom (no change)
    inp = make_input("9", "6", "11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    env = _read_env(tmp_path / ".env")
    # Effort preset should remain the default value
    assert env.get("PRAXIS_EFFORT_PRESET") == DEFAULTS["effort_preset"]

    yaml_file = tmp_path / "convergence.yaml"
    content = yaml_file.read_text(encoding="utf-8")
    # Orchestrator should be default (opus)
    assert f"orchestrator: {DEFAULTS['orchestrator']}" in content


# ===========================================================================
# Test 20: Merge mode safety
# ===========================================================================

def test_merge_mode_never_overwrites_unrelated_keys(tmp_path):
    """Write a .env with FOO=bar. Run wizard saving PRAXIS_RUNTIME=cloud.
    Confirm FOO=bar is still present.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\n", encoding="utf-8")

    inp = make_input("8", "2", "11")  # choose cloud runtime, then done
    run_config_wizard(tmp_path, env_file=env_file, _input=inp)

    result = _read_env(env_file)
    assert result["FOO"] == "bar"
    assert result["PRAXIS_RUNTIME"] == "cloud"


# ===========================================================================
# Test 21: convergence.yaml agents section format
# ===========================================================================

def test_convergence_yaml_agents_section_written_correctly(tmp_path):
    """After [11] done, read convergence.yaml and verify agents: section formatting."""
    inp = make_input("11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    yaml_file = tmp_path / "convergence.yaml"
    assert yaml_file.exists()
    content = yaml_file.read_text(encoding="utf-8")

    # Must have agents: at column 0
    assert "\nagents:\n" in content or content.startswith("agents:\n")

    # Each role must be indented with exactly 2 spaces
    for role in ROLES:
        assert f"  {role}:" in content

    # All 5 roles must be present
    for role in ROLES:
        assert role in content


# ===========================================================================
# Test 22: Invalid main menu option
# ===========================================================================

def test_invalid_main_menu_option(tmp_path):
    """Enter '99' (invalid), then '11' (done). Verify it doesn't crash."""
    inp = make_input("99", "11")
    # Should not raise
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    # The wizard should have saved defaults
    env_file = tmp_path / ".env"
    assert env_file.exists()


# ===========================================================================
# Test 23: PRESETS structure validation
# ===========================================================================

def test_presets_all_valid_keys():
    """For each preset in PRESETS.values(), verify it contains all ROLES plus max_turns and cost_cap."""
    required_keys = set(ROLES) | {"max_turns", "cost_cap"}
    for preset_name, preset in PRESETS.items():
        missing = required_keys - set(preset.keys())
        assert not missing, f"Preset {preset_name!r} is missing keys: {missing}"


# ===========================================================================
# Test 24: Summary output after Done
# ===========================================================================

def test_print_summary_after_done(tmp_path, capsys):
    """After selecting [11], captured output contains 'Configuration saved.' and 'Effective configuration:'."""
    inp = make_input("11")
    run_config_wizard(tmp_path, env_file=tmp_path / ".env", _input=inp)

    out = capsys.readouterr().out
    assert "Configuration saved." in out
    assert "Effective configuration:" in out
