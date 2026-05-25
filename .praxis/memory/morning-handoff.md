# Morning handoff — Phase F complete

**Date:** 2026-05-25 (night session)
**Status:** Phase F complete (F-1 through F-4).

---

## 1. Exact repo state

**Repo:** `Ashtonantony28/Praxis_AgenticOSKernel`
**Branch:** `claude/blissful-franklin-VIMiH`
**Main branch:** `claude/plan-execute-mode-switch-zCz38`

```
praxis-system-prompt.md              # the spec (§0–§11)
CLAUDE.md                            # project conventions — read this first
pyproject.toml                       # deps: anthropic, pyyaml, openai[local], pytest

praxis/                              # the orchestrator
  __init__.py                        #   package marker, version
  __main__.py                        #   `python -m praxis` — convergence config + runtime creation
  config.py                          #   Config.from_env() — workspace/memory/hook from env
  convergence.py                     #   ConvergenceConfig.load() — multi-runtime routing (Phase D)
  subagents.py                       #   parse .claude/agents/*.md → SubagentDef
  hooks.py                           #   run_pretool_hook() — §5 enforcement
  tools.py                           #   7 tool schemas + implementations + secret filtering (Phase E)
  orchestrator.py                    #   Orchestrator — runtime_overrides for per-subagent routing
  runtime/                           #   Provider abstraction (Phase A + C + D)
    __init__.py                      #     exports Runtime, ClaudeCodeRuntime, LocalRuntime
    base.py                          #     Abstract Runtime (4 abstract methods)
    claude_code.py                   #     ClaudeCodeRuntime — hardened error handling (Phase D)
    local.py                         #     LocalRuntime — hardened error handling (Phase D)

tests/                               # 116 tests, all pass, all mocked
  conftest.py                        #   FakeClient, FakeResponse, workspace fixtures
  test_config.py                     #   6 tests — env resolution, restrictive fallback
  test_convergence.py                #   16 tests — YAML parsing, routing, env override, validation
  test_subagents.py                  #   8 tests — YAML parsing, model mapping
  test_hooks.py                      #   17 tests — allow/block + space-in-path + /dev/null (Phase F)
  test_tools.py                      #   20 tests — tools + env propagation + secret filtering
  test_orchestrator.py               #   8 tests — runtime delegation + subagent routing override
  test_runtime.py                    #   9 tests — OAuth/API key + import guard + error handling
  test_local_runtime.py              #   21 tests — from_env, run_loop, tools, error handling
  test_main.py                       #   11 tests — _create_runtimes() + main() entry point (Phase F)

.claude/agents/                      # 5 subagent definitions (unchanged)
.claude/hooks/escalation-boundary.py # §5 hook — /dev/null fix applied (Phase F)
.claude/settings.json                # hook wiring (unchanged)

.praxis/memory/
  morning-handoff.md                 # this file
  f1-selforch-test.md               # Phase F-1 live test results + honest assessment
  phase-e1-plan.md                   # Phase E-1 design plan (archived)
  coverage-report.md                 # E-2 coverage analysis (archived)
  e2-assessment.md                   # E-2 pipeline assessment (archived)
  phase-d1-plan.md                   # Phase D-1 design plan (archived)
  phase-d2-plan.md                   # Phase D-2 design plan (archived)
  workload-test-d3.md               # Phase D-3 integration test report (archived)
  phase-c-plan.md                    # Phase C design plan (archived)
  phase-b-plan.md                    # Phase B design plan (archived)
  runtime-abstraction-plan.md        # Phase A design plan (archived)
  phase0-plan.md                     # Phase 0 design plan (archived)
  .gitkeep
```

---

## 2. What Phase F built

### F-1: Live self-orchestration test

**Result: structurally unvalidated.** All orchestrator init paths work against
real files (config, convergence, subagents, tools, system prompt). The API call
itself cannot be made from within a Claude Code session — auth tokens are
correctly sandboxed from child processes.

Full findings in `.praxis/memory/f1-selforch-test.md`.

### F-2: `__main__.py` test coverage

11 new tests covering `_create_runtimes()` (5 tests) and `main()` (6 tests).
Coverage: **98%** (only the `if __name__ == "__main__"` guard line uncovered).

### F-3: §5 hook `/dev/null` fix

**Bug:** Redirecting to `/dev/null`, `/dev/stdout`, `/dev/stderr` in Bash commands
was blocked because these paths are outside WORKSPACE_ROOT.

**Fix:** Added `_SAFE_DEVICE_PATHS` frozenset to `escalation-boundary.py`. The
`check_bash()` loop now skips resolved paths matching these device files.
4 new regression tests confirm the fix.

### F-4: Self-orchestration readiness assessment (this section)

See section 5 below.

---

## 3. What stayed the same

- `praxis/` — all source files unchanged (only .claude/hooks was patched)
- `runtime/`, `orchestrator.py`, `tools.py`, `config.py`, `convergence.py` — unchanged
- All 101 pre-F tests — unchanged and passing

---

## 4. How to verify

```bash
export PRAXIS_WORKSPACE_ROOT=$(pwd)
python -m pytest tests/ -v                    # 116 tests, all should pass

# Coverage check:
pip install pytest-cov
python -m pytest tests/test_main.py --cov=praxis.__main__ --cov-report=term-missing
# Should show 98% coverage

# /dev/null fix verification:
python -m pytest tests/test_hooks.py -k dev -v
# Should show 4 tests passing
```

---

## 5. Is Praxis ready for unattended autonomous operation?

**No.** The honest answer is no, and here is exactly why.

### What works (validated)

- **Init pipeline**: Config → Convergence → Runtime creation → Orchestrator init — all
  validated against real files, not just mocks. (F-1)
- **Tool dispatch**: All 7 tools work correctly with §5 enforcement. 116 tests confirm. (F-2)
- **§5 hook**: Correctly blocks out-of-workspace writes, network egress, control plane
  modification. Device paths now handled correctly. (F-3)
- **Multi-runtime routing**: Convergence config, per-subagent overrides, runtime selection —
  all covered by 16 tests. (Phase D)
- **Secret filtering**: Auth tokens never leak into tool results. (Phase E)

### What has NEVER been validated

1. **Zero live API round-trips have ever been completed.** The `run_loop` method in
   `ClaudeCodeRuntime` has only been tested with `FakeClient`. No one has ever seen
   a real response from Claude come back through the orchestrator.

2. **Self-orchestration (the core claim) is untested.** Does the model actually invoke
   the Agent tool to chain Scout → Planner → Builder → Verifier → Scribe? Unknown.
   The system prompt tells it to, but whether the model follows that instruction in
   practice has never been observed.

3. **Context management is append-only.** `manage_context()` just appends messages.
   Any multi-subagent task will hit the context window limit. A real overnight run
   would fail silently after the first few subagent exchanges.

4. **Error recovery under real load is untested.** Rate limiting, partial failures,
   malformed model responses — the error handling exists but has never been exercised
   with real API responses.

### What must be true before the first real unattended session

1. **One successful live API call.** Run `python -m praxis "describe this repo"` from
   a normal terminal with `ANTHROPIC_API_KEY` exported. Observe: auth works, tool calls
   execute, §5 hook fires, response returns. This takes 60 seconds and proves the core
   path works.

2. **Context window management.** Implement summarization in `manage_context()` — either
   a sliding window or a summary-and-compact strategy. Without this, any multi-turn task
   will fail.

3. **Graceful degradation on API errors.** The current error handling exits on any API
   failure. An unattended session needs retry logic with exponential backoff for transient
   errors (rate limits, connection drops).

4. **Output persistence.** The orchestrator currently prints to stdout. An unattended
   session needs to write results to a durable file, not a terminal nobody is watching.

### Bottom line

Praxis is a well-tested blueprint. The plumbing is solid — 116 tests, 98% coverage on
the entry point, hardened error handling, secret filtering. But it has never pumped
real water. The gap between "all mocked tests pass" and "runs autonomously overnight"
is the gap between a blueprint and a building. Items 1-2 above are the minimum viable
path to closing that gap.

---

## 6. Build history

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
