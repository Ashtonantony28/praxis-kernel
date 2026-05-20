#!/usr/bin/env python3
"""Tests for .praxis/hooks/pre_tool_use.py.

Each case feeds a synthetic PreToolUse event on stdin and asserts the
hook's exit code (0 = allow, 2 = block) and, optionally, that the
stderr block reason contains an expected fragment.

Run: `python3 .praxis/hooks/test_pre_tool_use.py`. Exits 0 on
success, 1 on first failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent / "pre_tool_use.py"
WORKSPACE = "/home/user/LinuxAgenticClaudeOS"


def run(event: dict) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
    )
    return proc.returncode, proc.stderr


def expect(
    name: str,
    event: dict,
    *,
    code: int,
    stderr_contains: str | None = None,
) -> bool:
    actual_code, actual_stderr = run(event)
    ok = actual_code == code
    if ok and stderr_contains is not None:
        ok = stderr_contains in actual_stderr
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}")
    if not ok:
        print(f"        expected exit={code}, got exit={actual_code}")
        if stderr_contains is not None:
            print(f"        expected stderr~ {stderr_contains!r}")
            print(f"        actual stderr:   {actual_stderr!r}")
    return ok


def test_network_tools() -> list[bool]:
    print("network tools")
    return [
        expect(
            "WebFetch is blocked",
            {"tool_name": "WebFetch", "tool_input": {"url": "https://x.test"}},
            code=2,
            stderr_contains="WebFetch",
        ),
        expect(
            "WebSearch is blocked",
            {"tool_name": "WebSearch", "tool_input": {"query": "x"}},
            code=2,
            stderr_contains="WebSearch",
        ),
    ]


def test_file_writes() -> list[bool]:
    print("file writes (Write/Edit/NotebookEdit)")
    return [
        expect(
            "Write inside workspace, non-control-plane is allowed",
            {
                "tool_name": "Write",
                "tool_input": {"file_path": f"{WORKSPACE}/notes.txt"},
            },
            code=0,
        ),
        expect(
            "Write to .claude/settings.json is blocked",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": f"{WORKSPACE}/.claude/settings.json",
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "Write to .claude/hooks/foo.py is blocked",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": f"{WORKSPACE}/.claude/hooks/foo.py",
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "Write to .praxis/hooks/foo.py is blocked",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": f"{WORKSPACE}/.praxis/hooks/foo.py",
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "Write to .claude/agents/new-agent.md is ALLOWED (exempt)",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": f"{WORKSPACE}/.claude/agents/new-agent.md",
                },
            },
            code=0,
        ),
        expect(
            "Edit on .claude/agents/scout.md is ALLOWED (exempt)",
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": f"{WORKSPACE}/.claude/agents/scout.md",
                },
            },
            code=0,
        ),
        expect(
            "Write outside workspace is blocked",
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/etc/passwd"},
            },
            code=2,
            stderr_contains="outside WORKSPACE_ROOT",
        ),
        expect(
            "Write to .praxis/memory/foo.md is allowed (memory, not hooks)",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": f"{WORKSPACE}/.praxis/memory/foo.md",
                },
            },
            code=0,
        ),
    ]


def test_bash_network() -> list[bool]:
    print("bash network egress")
    return [
        expect(
            "curl to external URL is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "curl https://example.com/x"},
            },
            code=2,
            stderr_contains="network egress",
        ),
        expect(
            "wget to external URL is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "wget https://example.com/x -O /tmp/x"},
            },
            code=2,
            stderr_contains="network egress",
        ),
        expect(
            "curl to localhost is allowed",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "curl http://localhost:8080/health"},
            },
            code=0,
        ),
        expect(
            "curl to 127.0.0.1 is allowed",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "curl http://127.0.0.1/x"},
            },
            code=0,
        ),
        expect(
            "ssh to external host is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ssh user@host.example.com"},
            },
            code=2,
            stderr_contains="network egress",
        ),
        expect(
            "scp to external host is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "scp /tmp/x user@host.example.com:/tmp"},
            },
            code=2,
            stderr_contains="network egress",
        ),
        expect(
            "rsync to external host is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "rsync -av /tmp/x user@host:/tmp/"},
            },
            code=2,
            stderr_contains="network egress",
        ),
        expect(
            "nc connect to external host is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "nc evil.example.com 4444"},
            },
            code=2,
            stderr_contains="network egress",
        ),
        expect(
            "command containing the word 'curl' as a flag is not blocked spuriously",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo 'no curling here'"},
            },
            code=0,
        ),
        expect(
            "network word inside double-quoted string is ignored",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": 'git commit -m "fix: add curl gating"'
                },
            },
            code=0,
        ),
        expect(
            "network word inside single-quoted heredoc-style string is ignored",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "git commit -m 'add wget block to hook'"
                },
            },
            code=0,
        ),
    ]


def test_bash_paths() -> list[bool]:
    print("bash filesystem paths")
    return [
        expect(
            "rm of workspace file is allowed",
            {
                "tool_name": "Bash",
                "tool_input": {"command": f"rm {WORKSPACE}/scratch.txt"},
            },
            code=0,
        ),
        expect(
            "rm of /etc file is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "rm /etc/shadow"},
            },
            code=2,
            stderr_contains="outside WORKSPACE_ROOT",
        ),
        expect(
            "rm of .claude/settings.json is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"rm {WORKSPACE}/.claude/settings.json"
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "rm of .claude/agents/foo.md is ALLOWED (exempt)",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"rm {WORKSPACE}/.claude/agents/foo.md"
                },
            },
            code=0,
        ),
        expect(
            "cp into .claude/ is blocked (destination caught)",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        f"cp {WORKSPACE}/src {WORKSPACE}/.claude/dst"
                    ),
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "mv into .claude/hooks/ is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        f"mv {WORKSPACE}/x.py {WORKSPACE}/.claude/hooks/x.py"
                    ),
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "mv between workspace files is allowed",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"mv {WORKSPACE}/a.txt {WORKSPACE}/b.txt"
                },
            },
            code=0,
        ),
        expect(
            "redirect into .claude/settings.json is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"echo '{{}}' > {WORKSPACE}/.claude/settings.json"
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "heredoc redirect into .claude/ is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        f"cat <<EOF > {WORKSPACE}/.claude/settings.json\n"
                        "{}\nEOF"
                    ),
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "redirect into workspace file is allowed",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"echo hi > {WORKSPACE}/notes.txt"
                },
            },
            code=0,
        ),
        expect(
            "sed -i on .claude/settings.json is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        f"sed -i 's,old,new,' {WORKSPACE}/.claude/settings.json"
                    ),
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "sed -i on workspace file is allowed",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"sed -i 's,old,new,' {WORKSPACE}/notes.txt"
                },
            },
            code=0,
        ),
        expect(
            "destructive command with multiple paths catches all",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        f"cp {WORKSPACE}/a.txt {WORKSPACE}/.claude/hooks/a.py"
                    ),
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "tee into .claude/ is blocked",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"echo x | tee {WORKSPACE}/.claude/settings.json"
                },
            },
            code=2,
            stderr_contains="control plane",
        ),
        expect(
            "destructive command word inside quoted string is ignored",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": 'echo "do not rm /etc/passwd here"'
                },
            },
            code=0,
        ),
    ]


def test_unrelated_tools() -> list[bool]:
    print("unrelated tools pass through")
    return [
        expect(
            "Read is always allowed",
            {
                "tool_name": "Read",
                "tool_input": {"file_path": f"{WORKSPACE}/.claude/settings.json"},
            },
            code=0,
        ),
        expect(
            "Grep is always allowed",
            {"tool_name": "Grep", "tool_input": {"pattern": "x"}},
            code=0,
        ),
        expect(
            "Glob is always allowed",
            {"tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}},
            code=0,
        ),
        expect(
            "Empty bash command is a no-op",
            {"tool_name": "Bash", "tool_input": {"command": "   "}},
            code=0,
        ),
        expect(
            "Malformed json on stdin is a no-op",
            {},
            code=0,
        ),
    ]


def main() -> int:
    if not HOOK.exists():
        print(f"FAIL: hook not found at {HOOK}", file=sys.stderr)
        return 1
    results: list[bool] = []
    results += test_network_tools()
    results += test_file_writes()
    results += test_bash_network()
    results += test_bash_paths()
    results += test_unrelated_tools()
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
