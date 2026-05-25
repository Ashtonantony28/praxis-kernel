"""Tests for convergence.yaml multi-runtime routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from praxis.convergence import ConvergenceConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove runtime env vars between tests."""
    monkeypatch.delenv("PRAXIS_RUNTIME", raising=False)
    monkeypatch.delenv("PRAXIS_LOCAL_BASE_URL", raising=False)
    monkeypatch.delenv("PRAXIS_LOCAL_MODEL", raising=False)


# ---------- loading ----------


def test_load_no_file(tmp_path):
    """Missing convergence.yaml returns defaults."""
    cfg = ConvergenceConfig.load(tmp_path)
    assert cfg.default_runtime == "claude"
    assert cfg.overrides == {}
    assert cfg.local_base_url == "http://localhost:11434"
    assert cfg.local_model == "llama3.1:8b"


def test_load_basic_yaml(tmp_path):
    """Parses a valid convergence.yaml."""
    (tmp_path / "convergence.yaml").write_text(
        "runtimes:\n"
        "  default: local\n"
        "  overrides:\n"
        "    builder: claude\n"
        "local:\n"
        "  base_url: http://gpu:8080\n"
        "  model: qwen2:7b\n"
    )
    cfg = ConvergenceConfig.load(tmp_path)
    assert cfg.default_runtime == "local"
    assert cfg.overrides == {"builder": "claude"}
    assert cfg.local_base_url == "http://gpu:8080"
    assert cfg.local_model == "qwen2:7b"


def test_load_minimal_yaml(tmp_path):
    """YAML with only default runtime works."""
    (tmp_path / "convergence.yaml").write_text(
        "runtimes:\n  default: claude\n"
    )
    cfg = ConvergenceConfig.load(tmp_path)
    assert cfg.default_runtime == "claude"
    assert cfg.overrides == {}


def test_load_empty_yaml(tmp_path):
    """Empty YAML file returns defaults."""
    (tmp_path / "convergence.yaml").write_text("")
    cfg = ConvergenceConfig.load(tmp_path)
    assert cfg.default_runtime == "claude"


# ---------- env var override ----------


def test_env_var_overrides_file_default(tmp_path, monkeypatch):
    """PRAXIS_RUNTIME env var overrides file's runtimes.default."""
    (tmp_path / "convergence.yaml").write_text(
        "runtimes:\n  default: local\n"
    )
    monkeypatch.setenv("PRAXIS_RUNTIME", "claude")
    cfg = ConvergenceConfig.load(tmp_path)
    assert cfg.default_runtime == "claude"


def test_env_var_overrides_no_file(tmp_path, monkeypatch):
    """PRAXIS_RUNTIME works without convergence.yaml."""
    monkeypatch.setenv("PRAXIS_RUNTIME", "local")
    cfg = ConvergenceConfig.load(tmp_path)
    assert cfg.default_runtime == "local"


def test_local_env_vars_override_file(tmp_path, monkeypatch):
    """PRAXIS_LOCAL_* env vars override file's local section."""
    (tmp_path / "convergence.yaml").write_text(
        "local:\n  base_url: http://file:1234\n  model: file-model\n"
    )
    monkeypatch.setenv("PRAXIS_LOCAL_BASE_URL", "http://env:5678")
    monkeypatch.setenv("PRAXIS_LOCAL_MODEL", "env-model")
    cfg = ConvergenceConfig.load(tmp_path)
    assert cfg.local_base_url == "http://env:5678"
    assert cfg.local_model == "env-model"


# ---------- validation ----------


def test_invalid_default_runtime(tmp_path):
    """Invalid default runtime name raises SystemExit."""
    (tmp_path / "convergence.yaml").write_text(
        "runtimes:\n  default: openai\n"
    )
    with pytest.raises(SystemExit) as exc_info:
        ConvergenceConfig.load(tmp_path)
    assert "openai" in str(exc_info.value)


def test_invalid_override_runtime(tmp_path):
    """Invalid override runtime name raises SystemExit."""
    (tmp_path / "convergence.yaml").write_text(
        "runtimes:\n  overrides:\n    scout: gemini\n"
    )
    with pytest.raises(SystemExit) as exc_info:
        ConvergenceConfig.load(tmp_path)
    assert "gemini" in str(exc_info.value)
    assert "scout" in str(exc_info.value)


# ---------- routing ----------


def test_runtime_for_default():
    """runtime_for returns default when no override exists."""
    cfg = ConvergenceConfig(default_runtime="claude")
    assert cfg.runtime_for("builder") == "claude"
    assert cfg.runtime_for("scout") == "claude"


def test_runtime_for_override():
    """runtime_for returns override when configured."""
    cfg = ConvergenceConfig(
        default_runtime="claude",
        overrides={"scout": "local", "scribe": "local"},
    )
    assert cfg.runtime_for("scout") == "local"
    assert cfg.runtime_for("scribe") == "local"
    assert cfg.runtime_for("builder") == "claude"


# ---------- needs_* ----------


def test_needs_local_false():
    """needs_local is False when everything routes to claude."""
    cfg = ConvergenceConfig(default_runtime="claude")
    assert not cfg.needs_local()


def test_needs_local_true_default():
    cfg = ConvergenceConfig(default_runtime="local")
    assert cfg.needs_local()


def test_needs_local_true_override():
    cfg = ConvergenceConfig(default_runtime="claude", overrides={"scout": "local"})
    assert cfg.needs_local()


def test_needs_claude_false():
    cfg = ConvergenceConfig(default_runtime="local")
    assert not cfg.needs_claude()


def test_needs_claude_true_override():
    cfg = ConvergenceConfig(default_runtime="local", overrides={"builder": "claude"})
    assert cfg.needs_claude()
