"""Generate allow.jsonl (must-allow policy cases). Run: python tests/fixtures/policy/gen_allow.py

"{workspace}" in any input string is replaced with ctx.workspace_root at test time.
"""

from __future__ import annotations

import json
from pathlib import Path

WS = "{workspace}"
DOMAINS = ["docs.python.org", "github.com"]

# (prefix, tool, input, mode, origin)
TABLE: list[tuple[str, str, dict, str | None, str]] = [
    ("read", "Read", {"file_path": "README.md"}, None, "cli"),
    ("read", "Read", {"file_path": "praxis/policy.py"}, "build", "cli"),
    ("read", "Read", {"file_path": "./tests/conftest.py"}, "plan", "cli"),
    ("read", "Read", {"file_path": f"{WS}/README.md"}, None, "cli"),
    ("read", "Read", {"file_path": f"{WS}/praxis/queue.py", "offset": 10, "limit": 40}, "build", "queue"),
    ("read", "Read", {"file_path": f"{WS}/docs/notes.md"}, "plan", "cli"),
    ("read", "Read", {"file_path": "PLAN.md"}, "plan", "queue"),
    ("write", "Write", {"file_path": "notes.md", "content": "hello\n"}, None, "cli"),
    ("write", "Write", {"file_path": "src/app.py", "content": "print('hi')\n"}, "build", "cli"),
    ("write", "Write", {"file_path": "./out/report.txt", "content": "ok"}, "build", "queue"),
    ("write", "Write", {"file_path": f"{WS}/notes.md", "content": "hello\n"}, None, "cli"),
    ("write", "Write", {"file_path": f"{WS}/tests/test_new.py", "content": "def test_x():\n    pass\n"}, "build", "cli"),
    ("write", "Write", {"file_path": f"{WS}/deep/nested/dir/file.json", "content": "{}"}, "build", "queue"),
    ("write", "Write", {"file_path": "docs/guide.md", "content": "# Guide\n"}, "build", "cli"),
    ("edit", "Edit", {"file_path": "praxis/policy.py", "old_string": "a", "new_string": "b"}, None, "cli"),
    ("edit", "Edit", {"file_path": "README.md", "old_string": "x", "new_string": "y"}, "build", "cli"),
    ("edit", "Edit", {"file_path": f"{WS}/praxis/queue.py", "old_string": "a", "new_string": "b"}, "build", "queue"),
    ("edit", "Edit", {"file_path": f"{WS}/pyproject.toml", "old_string": "a", "new_string": "b"}, None, "cli"),
    ("edit", "Edit", {"file_path": "./src/app.py", "old_string": "a", "new_string": "b", "replace_all": True}, "build", "cli"),
    ("edit", "NotebookEdit", {"notebook_path": "analysis.ipynb", "new_source": "x = 1"}, "build", "cli"),
    ("edit", "NotebookEdit", {"notebook_path": f"{WS}/nb/explore.ipynb", "new_source": "x = 1"}, None, "cli"),
    ("grep", "Grep", {"pattern": "def evaluate", "path": "praxis"}, None, "cli"),
    ("grep", "Grep", {"pattern": "TODO", "path": f"{WS}/tests"}, "plan", "cli"),
    ("grep", "Grep", {"pattern": "import os", "glob": "*.py"}, "build", "queue"),
    ("grep", "Grep", {"pattern": "class Mode", "path": "./praxis/modes", "output_mode": "content"}, "plan", "queue"),
    ("grep", "Grep", {"pattern": "Verdict", "path": f"{WS}/praxis/policy.py"}, "build", "cli"),
    ("glob", "Glob", {"pattern": "**/*.py"}, None, "cli"),
    ("glob", "Glob", {"pattern": "tests/test_*.py"}, "plan", "cli"),
    ("glob", "Glob", {"pattern": "*.md", "path": f"{WS}/docs"}, "build", "queue"),
    ("glob", "Glob", {"pattern": "praxis/**/*.yaml", "path": "."}, "plan", "cli"),
    ("bash", "Bash", {"command": "ls"}, None, "cli"),
    ("bash", "Bash", {"command": "ls -la praxis"}, "build", "cli"),
    ("bash", "Bash", {"command": "pytest"}, "build", "cli"),
    ("bash", "Bash", {"command": "python -m pytest tests/ -q"}, "build", "queue"),
    ("bash", "Bash", {"command": "pytest tests/test_policy_corpus.py -v"}, None, "cli"),
    ("bash", "Bash", {"command": "git status"}, "build", "cli"),
    ("bash", "Bash", {"command": "git diff --stat"}, "build", "cli"),
    ("bash", "Bash", {"command": "git log --oneline -5"}, None, "queue"),
    ("bash", "Bash", {"command": "python script.py"}, "build", "cli"),
    ("bash", "Bash", {"command": "python scripts/bench_policy.py"}, "build", "queue"),
    ("bash", "Bash", {"command": "cat README.md"}, None, "cli"),
    ("bash", "Bash", {"command": "grep -rn evaluate praxis"}, "build", "cli"),
    ("bash", "Bash", {"command": "wc -l praxis/policy.py"}, "build", "cli"),
    ("bash", "Bash", {"command": "echo done > out.txt"}, "build", "cli"),
    ("bash", "Bash", {"command": "mkdir -p build/tmp"}, None, "cli"),
    ("bash", "Bash", {"command": "pwd"}, "build", "queue"),
    ("bash", "Bash", {"command": f"ls {WS}/tests"}, "build", "cli"),
    ("web", "WebFetch", {"url": "https://docs.python.org/3/library/pathlib.html", "prompt": "summarise"}, None, "cli"),
    ("web", "WebFetch", {"url": "https://github.com/pytest-dev/pytest", "prompt": "readme"}, "build", "cli"),
    ("web", "WebFetch", {"url": "https://docs.python.org/3/", "prompt": "index"}, "plan", "queue"),
    ("web", "WebResearch", {"action": "fetch", "url": "https://docs.python.org/3/library/json.html"}, "build", "cli"),
    ("web", "WebResearch", {"action": "search", "query": "pytest xfail strict"}, "build", "cli"),
    ("integ", "notion", {"action": "search", "query": "roadmap"}, "build", "cli"),
    ("integ", "notion", {"action": "get_page", "page_id": "abc123"}, "build", "queue"),
    ("integ", "linear", {"action": "list_issues"}, "build", "cli"),
    ("integ", "linear", {"action": "get_issue", "issue_id": "PRX-1"}, "build", "cli"),
    ("integ", "Calendar", {"action": "list_events"}, "build", "cli"),
    ("integ", "Calendar", {"action": "today"}, "build", "queue"),
    ("integ", "Email", {"action": "list_emails"}, "build", "cli"),
    ("integ", "Email", {"action": "search_emails", "query": "invoice"}, "build", "cli"),
    ("integ", "GitHub", {"action": "pr_list", "state": "open"}, "build", "cli"),
    ("integ", "GitHub", {"action": "issue_view", "number": 4}, "build", "queue"),
    ("integ", "Wiki", {"action": "query", "query": "policy engine"}, "build", "cli"),
    ("integ", "Wiki", {"action": "lint"}, "build", "cli"),
]


def build() -> list[dict]:
    counts: dict[str, int] = {}
    cases = []
    for prefix, tool, inp, mode, origin in TABLE:
        counts[prefix] = counts.get(prefix, 0) + 1
        cases.append({
            "id": f"{prefix}-{counts[prefix]:03d}",
            "call": {"name": tool, "input": inp},
            "ctx": {"mode": mode, "origin": origin, "allowed_domains": DOMAINS},
            "expect": "allow",
            "rule": "allow",
        })
    return cases


if __name__ == "__main__":
    out = Path(__file__).with_name("allow.jsonl")
    out.write_text("".join(json.dumps(c) + "\n" for c in build()))
    print(f"wrote {len(TABLE)} cases to {out}")
