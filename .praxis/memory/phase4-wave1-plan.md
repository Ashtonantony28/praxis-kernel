# Phase 4 Wave 1 — Integration Layer Plan

**Date:** 2026-05-25  
**Based on:** phase4-mcp-survey.md (subprocess for all four)

## Architecture

### Directory: `praxis/integrations/`

```
praxis/integrations/
  __init__.py        # Aggregates INTEGRATION_SCHEMAS + INTEGRATION_IMPLEMENTATIONS from all modules
  github.py          # GitHub via `gh` CLI subprocess
  codebase.py        # Coverage, complexity, lint via subprocess
  testrunner.py      # pytest via subprocess
  dependencies.py    # pip-audit, pip outdated via subprocess
```

### Tool registration

Each module exports:
- `SCHEMAS: dict[str, dict]` — tool JSON schemas (same format as tools.py)
- `IMPLEMENTATIONS: dict[str, Callable]` — tool functions with signature `(args: dict, config: Config) -> str`

`__init__.py` aggregates into `INTEGRATION_SCHEMAS` and `INTEGRATION_IMPLEMENTATIONS`.

`orchestrator.py` merges integration tools into tool dispatch:
- `run()` and `run_subagent()` include integration schemas in `tool_schemas`
- `_execute_with_hook()` checks `INTEGRATION_IMPLEMENTATIONS` after `TOOL_IMPLEMENTATIONS`

### Auth config (env vars only)

- `GITHUB_TOKEN` — used by `gh` CLI automatically (no Praxis plumbing needed, `gh` reads it from env)
- No auth needed for pylint, radon, coverage, pytest, pip-audit — all local tools
- Env propagation via existing `_subprocess_env()` pattern from tools.py
- Secret redaction: add `GITHUB_TOKEN` to `_redact_secrets()` in tools.py

### §5 sandbox compliance

All integrations use subprocess.run within WORKSPACE_ROOT. The §5 hook already validates Bash commands — integration tools are equivalent to Bash calls with structured output. No network egress (all CLI tools operate locally, `gh` uses GITHUB_TOKEN for API calls which is user-configured).

## Tool designs (minimal viable)

### 1. GitHub (`github.py`)

**Tool name:** `GitHub`

**Operations via `action` parameter:**
- `pr_list` — `gh pr list --json number,title,state,author,url --limit 20`
- `pr_view` — `gh pr view <number> --json number,title,body,state,reviews,comments`
- `issue_list` — `gh issue list --json number,title,state,labels,url --limit 20`
- `issue_view` — `gh issue view <number> --json number,title,body,state,comments`
- `pr_diff` — `gh pr diff <number>`

**Fail mode:** If `gh` not found → clear error: "GitHub CLI (gh) not installed. Install: https://cli.github.com"
If not authenticated → `gh` itself errors clearly.

### 2. Codebase Analysis (`codebase.py`)

**Tool name:** `Analyze`

**Operations via `action` parameter:**
- `coverage` — `coverage json -o -` (stdout) or `coverage report` → parsed
- `complexity` — `radon cc <path> -s -j` (JSON cyclomatic complexity)
- `lint` — `pylint <path> --output-format=json --disable=C,R` (errors + warnings only for MVP)

**Fail mode:** Each tool checked independently — "pylint not installed", "radon not installed", etc.

### 3. Test Runner (`testrunner.py`)

**Tool name:** `TestRunner`

**Operations via `action` parameter:**
- `run` — `pytest <path> -v --tb=short -q` with optional path/marker args
- `run_failed` — `pytest --lf -v --tb=short`

**Fail mode:** "pytest not installed" if not found.

### 4. Dependencies (`dependencies.py`)

**Tool name:** `Dependencies`

**Operations via `action` parameter:**
- `outdated` — `pip list --outdated --format=json`
- `audit` — `pip-audit --format=json`

**Fail mode:** "pip-audit not installed" for audit action. `pip` is always available.

## Changes to existing files

1. **`praxis/orchestrator.py`** — import `INTEGRATION_SCHEMAS`, `INTEGRATION_IMPLEMENTATIONS` from `praxis.integrations`; merge into tool_schemas and tool dispatch
2. **`praxis/tools.py`** — add `GITHUB_TOKEN` to `_redact_secrets()`
3. **No changes** to config.py, hooks.py, runtime/, subagents.py, or any existing test

## Test plan

- One test module: `tests/test_integrations.py`
- All subprocess calls mocked (no real `gh`, `pytest`, `pip-audit` needed)
- Test each action for each tool
- Test failure modes: tool not installed (FileNotFoundError), auth missing, subprocess errors
- Test that existing 207 tests still pass
