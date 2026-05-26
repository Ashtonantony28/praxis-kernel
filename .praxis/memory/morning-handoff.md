# Morning handoff — Phase 4 Wave 1 complete

**Date:** 2026-05-25  
**Status:** Phase 4 Wave 1 (workstation integrations) complete. 261 tests pass.

---

## 1. Exact repo state

**Repo:** `Ashtonantony28/Praxis_AgenticOSKernel`  
**Branch:** `claude/blissful-franklin-VIMiH`  
**Main branch:** `claude/plan-execute-mode-switch-zCz38`

```
praxis-system-prompt.md              # the spec (§0–§11)
CLAUDE.md                            # project conventions — updated Phase 4
pyproject.toml                       # deps: anthropic, pyyaml, openai[local], pytest

praxis/                              # the orchestrator
  __init__.py                        #   package marker, version
  __main__.py                        #   `python -m praxis` — interactive, --queue, --daemon, --stop, --status
  config.py                          #   Config.from_env() — workspace/memory/hook from env
  convergence.py                     #   ConvergenceConfig.load() — multi-runtime routing (Phase D+I)
  subagents.py                       #   parse .claude/agents/*.md → SubagentDef
  hooks.py                           #   run_pretool_hook() — §5 enforcement
  tools.py                           #   7 tool schemas + implementations + secret filtering (Phase E, +GITHUB_TOKEN)
  orchestrator.py                    #   Orchestrator — merges integration tools into dispatch (Phase 4)
  queue.py                           #   TaskQueue — CRUD on tasks.jsonl (Phase J)
  checkpoint.py                      #   CheckpointStore — atomic writes via os.replace() (Phase J + fix)
  queue_runner.py                    #   Queue processing loop (Phase J)
  daemon.py                          #   Daemon start/stop/status (Phase J)
  integrations/                      #   Workstation integrations (Phase 4 Wave 1) — NEW
    __init__.py                      #     aggregates INTEGRATION_SCHEMAS + INTEGRATION_IMPLEMENTATIONS
    github.py                        #     GitHub via `gh` CLI — pr_list, pr_view, issue_list, issue_view, pr_diff
    codebase.py                      #     coverage report, radon complexity, pylint lint
    testrunner.py                    #     pytest run + run_failed
    dependencies.py                  #     pip outdated + pip-audit vulnerability scan
  runtime/                           #   Provider abstraction (Phase A + C + D + H + I)
    __init__.py                      #     exports Runtime, ClaudeCodeRuntime, LocalRuntime, OpenAICloudRuntime
    base.py                          #     Abstract Runtime (4 abstract methods)
    openai_base.py                   #     OpenAIBaseRuntime — shared OpenAI-compatible logic
    claude_code.py                   #     _create_with_retry() + sliding window (Phase H)
    local.py                         #     Ollama/vLLM/llama.cpp — inherits OpenAIBaseRuntime
    cloud.py                         #     OpenAICloudRuntime — _resolve_model() added, retry 5/135s

tests/                               # 261 tests, all pass, all mocked
  conftest.py
  test_config.py                     #   6 tests
  test_convergence.py                #   16 tests
  test_subagents.py                  #   8 tests
  test_hooks.py                      #   17 tests
  test_tools.py                      #   20 tests
  test_orchestrator.py               #   8 tests
  test_runtime.py                    #   15 tests
  test_local_runtime.py              #   25 tests
  test_cloud_runtime.py              #   21 tests
  test_main.py                       #   20 tests
  test_queue.py                      #   20 tests
  test_checkpoint.py                 #   12 tests
  test_queue_runner.py               #   8 tests
  test_daemon.py                     #   10 tests
  test_integrations.py               #   54 tests (NEW — Phase 4 Wave 1)

.claude/agents/                      # 5 subagent definitions (builder, planner, scout, scribe, verifier)
.claude/hooks/escalation-boundary.py # §5 hook
.claude/settings.json                # hook wiring

.praxis/memory/
  morning-handoff.md                 # this file
  phase4-mcp-survey.md              # MCP server assessment (NEW)
  phase4-wave1-plan.md              # integration layer design (NEW)
  pipeline-validation-report.md
  unattended-readiness.md
  phase-j-plan.md
  model-agnostic-plan.md
  h3-pipeline-report.md
  first-live-run.md
  phase-g-plan.md

.praxis/queue/                       # Task queue directory (Phase J)
  tasks.jsonl
  results/
  checkpoints/
```

---

## 2. What Phase 4 Wave 1 built

### Design decisions (from MCP survey)
- No MCP servers needed — all four integrations use subprocess to existing CLI tools
- Follows the same `subprocess.run` + `_subprocess_env()` + `_redact_secrets()` pattern as `tools.py`
- Each integration is a standalone module with `SCHEMAS` and `IMPLEMENTATIONS` dicts
- `praxis/integrations/__init__.py` aggregates all modules
- `orchestrator.py` merges integration tools into core tool dispatch

### Integration 1: GitHub (`github.py`)
- Tool: `GitHub` with actions: `pr_list`, `pr_view`, `issue_list`, `issue_view`, `pr_diff`
- Wraps `gh` CLI with `--json` output for structured data
- Auth via `GITHUB_TOKEN` env var (read by `gh` automatically)
- Clear errors for: `gh` not installed, not authenticated, repo not found, timeout

### Integration 2: Codebase Analysis (`codebase.py`)
- Tool: `Analyze` with actions: `coverage`, `complexity`, `lint`
- `coverage report --show-missing` for test coverage
- `radon cc <path> -s -a` for cyclomatic complexity
- `pylint <path> --disable=C,R --score=no` for errors/warnings only
- Each sub-tool checked independently — clear install instructions if missing

### Integration 3: Test Runner (`testrunner.py`)
- Tool: `TestRunner` with actions: `run`, `run_failed`
- `pytest -v --tb=short -q` with optional path, marker (-m), keyword (-k)
- `pytest --lf` for re-running last failures

### Integration 4: Dependencies (`dependencies.py`)
- Tool: `Dependencies` with actions: `outdated`, `audit`
- `pip list --outdated --format=json` for outdated packages
- `pip-audit --format=json` for vulnerability scanning

### Orchestrator changes
- `orchestrator.py`: imports `INTEGRATION_IMPLEMENTATIONS` and `get_integration_schemas()`
- `run()`: passes `get_tool_schemas() + get_integration_schemas()` as tool_schemas
- `run_subagent()`: includes integration schemas alongside core schemas for subagent tool list
- `_execute_with_hook()`: checks `INTEGRATION_IMPLEMENTATIONS` as fallback after `TOOL_IMPLEMENTATIONS`
- `tools.py`: added `GITHUB_TOKEN` to `_redact_secrets()`

---

## 3. What stayed the same

- All 207 pre-existing tests pass unmodified
- Interactive `python -m praxis "prompt"` works exactly as before
- §5 hook enforcement unchanged
- Runtime, convergence, token propagation unchanged
- Queue, checkpoint, daemon unchanged

---

## 4. How to verify

```bash
export PRAXIS_WORKSPACE_ROOT=$(pwd)

# Full test suite (261 tests):
python -m pytest tests/ -v

# Integration tests only:
python -m pytest tests/test_integrations.py -v

# Quick import check:
python -c "from praxis.integrations import INTEGRATION_SCHEMAS; print(list(INTEGRATION_SCHEMAS.keys()))"
```

---

## 5. Build history

| Phase | What | Tests |
|-------|------|-------|
| 0 | Minimal orchestrator (tools, hooks, subagents, config) | 43 |
| A | Extract Runtime interface from Orchestrator | 43 |
| B | Subscription OAuth as primary auth | 52 |
| C | LocalRuntime for open-source models | 69 |
| D-1 | Harden failure paths (error handling) | 77 |
| D-2 | convergence.yaml multi-runtime routing | 94 |
| D-3 | Integration test report | 94 |
| E-1 | Token propagation + secret filtering | 101 |
| E-2 | Coverage analysis + pipeline assessment | 101 |
| F-1 | Live self-orchestration test (sandbox-limited) | 101 |
| F-2 | `__main__.py` test coverage (98%) | 112 |
| F-3 | §5 hook `/dev/null` device path fix | 116 |
| F-4 | Self-orchestration readiness assessment | 116 |
| G | Fix OAuth auth_token= bug. First live run confirmed. | 116 |
| H-1 | Retry on rate limit (exponential backoff) | 117 |
| H-2 | Context window management (sliding window) | 126 |
| H-3 | Pipeline test: Scout passed, 2-5 rate-limited | 126 |
| I-1 | OpenAIBaseRuntime — shared OpenAI-compatible base | 126 |
| I-2 | OpenAICloudRuntime — cloud provider | 144 |
| I-3 | LocalRuntime refactored to inherit base | 144 |
| I-4 | Convergence routing for cloud runtime | 144 |
| J-1 | Task queue (TaskQueue, tasks.jsonl, crash recovery) | 174 |
| J-2 | Session continuity (CheckpointStore, staged resume) | 186 |
| J-3 | Daemon entry point (start/stop/status + queue mode) | 203 |
| pipeline-validation | Gemini 2.5 Flash e2e validation + fixes | 207 |
| **4-W1** | **Workstation integrations: GitHub, Analyze, TestRunner, Dependencies** | **261** |

---

## 6. Wave 2 plan

Phase 4 Wave 2 — deeper workstation awareness:
- **File watcher** — detect workspace changes and trigger re-analysis
- **Git integration** — branch state, uncommitted changes, recent commits as tool
- **Project context** — auto-detect project type (Python/Node/Rust), load conventions
- **Integration config** — optional `integrations:` section in `convergence.yaml` for per-tool enablement
