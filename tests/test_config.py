"""Tests for praxis.config — §0 restrictive fallback semantics."""

from __future__ import annotations

from pathlib import Path

from praxis.config import Config


def test_from_env_with_vars(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PRAXIS_MEMORY_ROOT", str(tmp_path / "mem"))
    cfg = Config.from_env()
    assert cfg.workspace_root == tmp_path.resolve()
    assert cfg.memory_root == (tmp_path / "mem").resolve()


def test_from_env_no_vars_falls_back_to_cwd(monkeypatch):
    monkeypatch.delenv("PRAXIS_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("PRAXIS_MEMORY_ROOT", raising=False)
    cfg = Config.from_env()
    assert cfg.workspace_root == Path.cwd().resolve()
    assert cfg.memory_root == Path.cwd().resolve() / ".praxis" / "memory"


def test_memory_root_defaults_under_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("PRAXIS_MEMORY_ROOT", raising=False)
    cfg = Config.from_env()
    assert cfg.memory_root == tmp_path.resolve() / ".praxis" / "memory"


def test_hook_path_derived_from_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("PRAXIS_MEMORY_ROOT", raising=False)
    cfg = Config.from_env()
    expected = tmp_path.resolve() / ".claude" / "hooks" / "escalation-boundary.py"
    assert cfg.hook_path == expected


def test_allowed_domains_empty_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("PRAXIS_ALLOWED_DOMAINS", raising=False)
    cfg = Config.from_env()
    assert cfg.allowed_domains == frozenset()


def test_allowed_domains_parsed_from_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "example.com, api.test.io")
    cfg = Config.from_env()
    assert cfg.allowed_domains == frozenset({"example.com", "api.test.io"})


# SG8: convergence.yaml allowed_domains is read alongside PRAXIS_ALLOWED_DOMAINS


def _conv(root: Path, text: str) -> None:
    (root / "convergence.yaml").write_text(text)


def test_allowed_domains_union_env_and_convergence(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "example.com")
    _conv(tmp_path, "allowed_domains:\n  - api.telegram.org\n  - ' docs.python.org '\n")
    expected = frozenset({"example.com", "api.telegram.org", "docs.python.org"})
    assert Config.from_env().allowed_domains == expected

    from praxis.policy import context_from_env
    from praxis.runtime.enforcement import _allowed_domains

    assert _allowed_domains() == expected
    assert context_from_env().allowed_domains == expected


def test_allowed_domains_convergence_reload_on_change(tmp_path: Path, monkeypatch):
    import os

    from praxis.config import resolve_allowed_domains

    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("PRAXIS_ALLOWED_DOMAINS", raising=False)
    _conv(tmp_path, "allowed_domains: [a.example]\n")
    assert resolve_allowed_domains() == frozenset({"a.example"})
    _conv(tmp_path, "allowed_domains: [b.example]\n")
    st = (tmp_path / "convergence.yaml").stat()
    os.utime(tmp_path / "convergence.yaml", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert resolve_allowed_domains() == frozenset({"b.example"})


def test_allowed_domains_bad_convergence_ignored(tmp_path: Path, monkeypatch):
    from praxis.config import resolve_allowed_domains

    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", "example.com")
    for text in ("allowed_domains: just-a-string\n", ": : not yaml [", "- a\n- b\n"):
        _conv(tmp_path, text)
        st = (tmp_path / "convergence.yaml").stat()
        __import__("os").utime(tmp_path / "convergence.yaml", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        assert resolve_allowed_domains() == frozenset({"example.com"})


def test_allowed_domains_convergence_needs_workspace_env(tmp_path: Path, monkeypatch):
    from praxis.config import resolve_allowed_domains

    monkeypatch.delenv("PRAXIS_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("PRAXIS_ALLOWED_DOMAINS", raising=False)
    monkeypatch.chdir(tmp_path)
    _conv(tmp_path, "allowed_domains: [a.example]\n")
    assert resolve_allowed_domains() == frozenset()
