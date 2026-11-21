"""SG2 parity: the §5 hook and enforce() give the same verdict on every corpus and bypass case.

The SG2 hook ships as scripts/hooks/escalation-boundary.py; the owner installs it at
.claude/hooks/ (STATUS.md, NEEDS HUMAN). test_installed_hook_is_sg2 skips until then.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from praxis.runtime.enforcement import EnforcementError, enforce

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "scripts" / "hooks" / "escalation-boundary.py"
INSTALLED = REPO / ".claude" / "hooks" / "escalation-boundary.py"
FIXTURES = REPO / "tests" / "fixtures" / "policy"
CORPUS = [json.loads(line) for f in sorted(FIXTURES.glob("*.jsonl")) for line in f.read_text().splitlines()
          if line.strip()]
BYPASS = [
    ("Bash", {"command": "rm -rf ../x"}), ("Bash", {"command": "cat .env"}), ("Bash", {"command": "git push"}),
    ("Bash", {"command": "python3 -c \"open('/etc/x','w')\""}), ("Bash", {"command": "echo x > convergence.yaml"}),
    ("Read", {"file_path": "/etc/passwd"}), ("Read", {"file_path": ".praxis/security/credentials.json"}),
    ("Write", {"path": "x"}), ("Write", {"file_path": "praxis/policy.py", "content": ""}),
    ("WebFetch", {"url": "https://docs.python.org/3/"}), ("WebFetch", {"url": "https://evil.example/"}),
    ("WebSearch", {"query": "x"}), ("Glob", {"pattern": "../**"}), ("Bash", {"command": "ls -la"}),
]

_spec = importlib.util.spec_from_file_location("sg2_hook", HOOK)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


def _sub(value, root: str):
    if isinstance(value, str):
        return value.replace("{workspace}", root)
    if isinstance(value, dict):
        return {k: _sub(v, root) for k, v in value.items()}
    return value


def run_hook(monkeypatch, capsys, event) -> tuple[bool, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(event if isinstance(event, str) else json.dumps(event)))
    with pytest.raises(SystemExit) as exc:
        hook.main()
    return exc.value.code == 0, capsys.readouterr().err


def run_enforce(name, args) -> tuple[bool, str]:
    try:
        enforce(name, args)
        return True, ""
    except EnforcementError as exc:
        return False, str(exc)


def _cases():
    for c in CORPUS:
        yield pytest.param(c["call"]["name"], c["call"]["input"], c["ctx"]["allowed_domains"], id=c["id"])
    for i, (name, args) in enumerate(BYPASS):
        yield pytest.param(name, args, ["docs.python.org"], id=f"bypass-{i}-{name}")


@pytest.mark.parametrize("name,args,domains", list(_cases()))
def test_hook_matches_enforce(name, args, domains, tmp_path, monkeypatch, capsys):
    root = str(tmp_path.resolve())
    monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", ",".join(domains))
    args = _sub(args, root)
    allowed, err = run_hook(monkeypatch, capsys, {"tool_name": name, "tool_input": args})
    want, reason = run_enforce(name, args)
    assert allowed == want, (err, reason)
    assert reason in err


@pytest.mark.parametrize("event", ["not json", "[]", '{"tool_input": {}}', '{"tool_name": "Bash", "tool_input": "ls"}'])
def test_hook_malformed_input_blocks(event, monkeypatch, capsys):
    allowed, err = run_hook(monkeypatch, capsys, event)
    assert not allowed and "malformed" in err


def test_hook_unset_root_blocks(monkeypatch, capsys):
    monkeypatch.delenv("PRAXIS_WORKSPACE_ROOT")
    allowed, err = run_hook(monkeypatch, capsys, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert not allowed and "unset" in err


def test_hook_engine_import_failure_blocks(monkeypatch, capsys):
    def boom():
        raise ImportError("no praxis")

    monkeypatch.setattr(hook, "_load_engine", boom)
    allowed, err = run_hook(monkeypatch, capsys, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert not allowed and "policy engine unavailable" in err


def test_hook_subprocess(tmp_path):
    """The hook file as Claude Code runs it: a fresh interpreter, JSON on stdin, exit 0 or 2."""
    env = {"PRAXIS_WORKSPACE_ROOT": str(tmp_path), "PATH": "/usr/bin:/bin"}

    def run(event):
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True,
                              text=True, env=env, timeout=30)

    ok = run({"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "a.txt"), "content": ""}})
    bad = run({"tool_name": "Write", "tool_input": {"file_path": "/etc/x", "content": ""}})
    assert ok.returncode == 0, ok.stderr
    assert bad.returncode == 2 and "write.outside_workspace" in bad.stderr


def test_installed_hook_is_sg2():
    if INSTALLED.read_bytes() != HOOK.read_bytes():
        pytest.skip("SG2 hook not installed yet: see STATUS.md, NEEDS HUMAN")
