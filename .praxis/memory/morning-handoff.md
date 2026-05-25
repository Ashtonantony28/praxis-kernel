# Morning handoff — Phase I complete

**Date:** 2026-05-25
**Status:** Phase I complete. Model-agnostic cloud runtime implemented. 144 tests pass.

---

## 1. Exact repo state

**Repo:** `Ashtonantony28/Praxis_AgenticOSKernel`
**Branch:** `claude/blissful-franklin-VIMiH`
**Main branch:** `claude/plan-execute-mode-switch-zCz38`

```
praxis-system-prompt.md              # the spec (§0–§11)
CLAUDE.md                            # project conventions — updated Phase I
pyproject.toml                       # deps: anthropic, pyyaml, openai[local], pytest

praxis/                              # the orchestrator
  __init__.py                        #   package marker, version
  __main__.py                        #   `python -m praxis` — convergence config + runtime creation
  config.py                          #   Config.from_env() — workspace/memory/hook from env
  convergence.py                     #   ConvergenceConfig.load() — multi-runtime routing (Phase D+I)
  subagents.py                       #   parse .claude/agents/*.md → SubagentDef
  hooks.py                           #   run_pretool_hook() — §5 enforcement
  tools.py                           #   7 tool schemas + implementations + secret filtering (Phase E)
  orchestrator.py                    #   Orchestrator — PRAXIS_MODEL support (Phase G)
  runtime/                           #   Provider abstraction (Phase A + C + D + H + I)
    __init__.py                      #     exports Runtime, ClaudeCodeRuntime, LocalRuntime, OpenAICloudRuntime
    base.py                          #     Abstract Runtime (4 abstract methods)
    openai_base.py                   #     OpenAIBaseRuntime — shared OpenAI-compatible logic (NEW)
    claude_code.py                   #     _create_with_retry() + sliding window (Phase H)
    local.py                         #     Ollama/vLLM/llama.cpp — inherits OpenAIBaseRuntime (REFACTORED)
    cloud.py                         #     OpenAICloudRuntime — cloud OpenAI-compat APIs (NEW)

tests/                               # 144 tests, all pass, all mocked
  conftest.py                        #   FakeClient, FakeResponse, workspace fixtures
  test_config.py                     #   6 tests
  test_convergence.py                #   16 tests
  test_subagents.py                  #   8 tests
  test_hooks.py                      #   17 tests
  test_tools.py                      #   20 tests
  test_orchestrator.py               #   8 tests
  test_runtime.py                    #   14 tests — retry + context management (Phase H)
  test_local_runtime.py              #   25 tests — context management (Phase H)
  test_cloud_runtime.py              #   18 tests — cloud runtime + convergence routing (Phase I)
  test_main.py                       #   11 tests

.claude/agents/                      # 5 subagent definitions (builder, planner, scout, scribe, verifier)
.claude/hooks/escalation-boundary.py # §5 hook
.claude/settings.json                # hook wiring

.praxis/memory/
  morning-handoff.md                 # this file
  model-agnostic-plan.md             # Phase I design plan (NEW)
  h3-pipeline-report.md             # Phase H pipeline test results
  first-live-run.md                  # Phase G milestone
  phase-g-plan.md                    # Phase G design plan
```

---

## 2. What Phase I built

### I-1: OpenAIBaseRuntime (shared base class)
- Extracted all shared OpenAI-compatible logic from LocalRuntime into `openai_base.py`
- Implements: `run_loop`, `spawn_subagent`, `execute_tool`, `manage_context`
- Also: `_compact_context`, `_summarize_message`, `_convert_tools`
- Subclasses override only 3 hooks: `from_env()`, `_call_api()`, `_resolve_model()`

### I-2: OpenAICloudRuntime
- New provider in `cloud.py` — works against any cloud OpenAI-compatible API
- Requires `PRAXIS_CLOUD_API_KEY` (exits with clear error if missing)
- Configurable via `PRAXIS_CLOUD_BASE_URL` and `PRAXIS_CLOUD_MODEL`
- Exponential backoff retry on 429 (same pattern as ClaudeCodeRuntime)
- Tested endpoints: OpenAI, OpenRouter, Groq, Gemini compatibility layer

### I-3: LocalRuntime refactored
- Now inherits from `OpenAIBaseRuntime` instead of `Runtime` directly
- Only overrides: `__init__` (defaults), `from_env`, `_resolve_model`, `_call_api`
- All existing behavior preserved — 25 tests pass unchanged

### I-4: Convergence routing updated
- `VALID_RUNTIMES` now includes `"cloud"` alongside `"claude"` and `"local"`
- `ConvergenceConfig` has `cloud_base_url`, `cloud_model` fields
- `needs_cloud()` method added
- `__main__.py` creates `OpenAICloudRuntime` when convergence config requires it
- Per-subagent routing: e.g., `scout: cloud` in convergence.yaml

### Provider matrix

| Runtime | Class | API Protocol | Auth | Retry |
|---------|-------|-------------|------|-------|
| claude | ClaudeCodeRuntime | Anthropic Messages | OAuth/API key | 429 backoff |
| local | LocalRuntime | OpenAI chat completions | dummy "ollama" | none |
| cloud | OpenAICloudRuntime | OpenAI chat completions | real API key | 429 backoff |

---

## 3. What stayed the same

- ClaudeCodeRuntime untouched — still primary tested runtime
- All 126 pre-existing tests pass unmodified (one import path adjusted)
- §5 hook enforcement unchanged
- Token propagation and secret filtering unchanged

---

## 4. How to verify

```bash
export PRAXIS_WORKSPACE_ROOT=$(pwd)

# Full test suite (144 tests):
python -m pytest tests/ -v

# Cloud runtime tests only:
python -m pytest tests/test_cloud_runtime.py -v

# Verify refactored LocalRuntime still works:
python -m pytest tests/test_local_runtime.py -v

# Live test with cloud provider (needs API key):
export PRAXIS_RUNTIME=cloud
export PRAXIS_CLOUD_API_KEY=sk-...
export PRAXIS_CLOUD_BASE_URL=https://api.openai.com/v1
export PRAXIS_CLOUD_MODEL=gpt-4o
python -m praxis "Hello, what model are you?"

# Live test with OpenRouter:
export PRAXIS_CLOUD_BASE_URL=https://openrouter.ai/api/v1
export PRAXIS_CLOUD_MODEL=anthropic/claude-3.5-sonnet
python -m praxis "Hello"
```

---

## 5. Next session: unattended operation

**Recommended next phase:** Resolve the 3 gaps from Phase H that block unattended runs.

### Gap 1: Rate limit budget (critical, blocking)
OAuth session tokens cannot support 5-agent pipelines (~10-15 API calls).
- **Fix:** Dedicated `ANTHROPIC_API_KEY` with production rate limits, or
  use cloud runtime (OpenRouter/Groq) for high-throughput subagents via convergence routing

### Gap 2: Retry budget too short for sustained limits
35s total (5+10+20) insufficient when limits persist for minutes.
- **Fix:** Extend to 5 retries (5/10/20/40/60 = 135s total) and/or
  respect Retry-After headers from the API

### Gap 3: Context management unproven under real load
Passes 9+ unit tests but has never triggered during a live multi-agent run.
- **Fix:** Run a sustained test session once rate limit gap is resolved

### New opportunity from Phase I
Cloud runtime enables a cost-optimization strategy: route cheap subagents
(scout, verifier) to fast/cheap cloud endpoints (Groq, GPT-4o-mini) while
keeping builder/planner on Claude. This could solve Gap 1 by reducing
Claude API pressure.

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
| G | Fix OAuth auth_token= bug. First live run confirmed. | 116 |
| H-1 | Retry on rate limit (exponential backoff) | 117 |
| H-2 | Context window management (sliding window) | 126 |
| H-3 | Pipeline test: Scout passed, 2-5 rate-limited | 126 |
| **I-1** | **OpenAIBaseRuntime — shared OpenAI-compatible base** | **126** |
| **I-2** | **OpenAICloudRuntime — cloud provider** | **144** |
| **I-3** | **LocalRuntime refactored to inherit base** | **144** |
| **I-4** | **Convergence routing for cloud runtime** | **144** |
