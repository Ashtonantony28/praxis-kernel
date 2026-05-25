# Morning handoff — Phase C complete, ready for Phase D

**Date:** 2026-05-25
**Status:** Phase C **done**. LocalRuntime implemented and tested (69 tests
green). Runtime selection via `PRAXIS_RUNTIME=local` env var.
Phase D (convergence.yaml multi-runtime config switch) is next.

---

## 1. Exact repo state

**Repo:** `Ashtonantony28/Praxis_AgenticOSKernel`
**Branch:** `claude/blissful-franklin-VIMiH`
**Main branch:** `claude/plan-execute-mode-switch-zCz38`

```
praxis-system-prompt.md              # the spec (§0–§11)
CLAUDE.md                            # project conventions — read this first
pyproject.toml                       # deps: anthropic, openai[local], pytest

praxis/                              # the orchestrator
  __init__.py                        #   package marker, version
  __main__.py                        #   `python -m praxis` — PRAXIS_RUNTIME selection
  config.py                          #   Config.from_env() — workspace/memory/hook from env
  subagents.py                       #   parse .claude/agents/*.md → SubagentDef
  hooks.py                           #   run_pretool_hook() — §5 enforcement
  tools.py                           #   7 tool schemas + implementations
  orchestrator.py                    #   Orchestrator — delegates to Runtime, owns tools/hooks
  runtime/                           #   Provider abstraction (Phase A + C)
    __init__.py                      #     exports Runtime, ClaudeCodeRuntime, LocalRuntime
    base.py                          #     Abstract Runtime (4 abstract methods)
    claude_code.py                   #     ClaudeCodeRuntime — from_env() with OAuth/API key
    local.py                         #     LocalRuntime — OpenAI-compatible (Ollama, vLLM)

tests/                               # 69 tests, all pass, all mocked
  conftest.py                        #   FakeClient, FakeResponse, workspace fixtures
  test_config.py                     #   6 tests — env resolution, restrictive fallback
  test_subagents.py                  #   8 tests — YAML parsing, model mapping
  test_hooks.py                      #   13 tests — allow/block + space-in-path regression
  test_tools.py                      #   13 tests — Bash, Read, Edit, Write, Grep, Glob, schemas
  test_orchestrator.py               #   7 tests — uses ClaudeCodeRuntime(FakeClient) now
  test_runtime.py                    #   5 tests — OAuth/API key priority, env scrubbing
  test_local_runtime.py              #   17 tests — LocalRuntime: from_env, run_loop, tools

.claude/agents/                      # 5 subagent definitions (unchanged)
.claude/hooks/escalation-boundary.py # §5 hook (unchanged)
.claude/settings.json                # hook wiring (unchanged)

.praxis/memory/
  morning-handoff.md                 # this file
  phase-c-plan.md                    # Phase C design plan
  phase-b-plan.md                    # Phase B design plan (archived)
  runtime-abstraction-plan.md        # Phase A design plan (archived)
  phase0-plan.md                     # Phase 0 design plan (archived)
  .gitkeep
```

---

## 2. What Phase C built

### LocalRuntime (`praxis/runtime/local.py`)

Full `Runtime` implementation targeting any OpenAI-compatible endpoint:

1. **`run_loop`** — client-side agent loop via `chat.completions.create()`.
   Converts tool schemas (Anthropic → OpenAI format), handles tool_calls
   with JSON-string arguments, feeds results back as `role: "tool"` messages.
2. **`spawn_subagent`** — delegates to `run_loop` (no native subagent support).
3. **`execute_tool`** — parses OpenAI tool_call objects (or dicts), decodes
   JSON arguments, invokes the Orchestrator's tool_executor callback.
4. **`manage_context`** — appends messages in OpenAI format (handles both
   plain strings and full message dicts with `role` key).

### Runtime selection (`__main__.py`)

`PRAXIS_RUNTIME` env var: `"claude"` (default) or `"local"`.
Logs runtime info to stderr on startup.

### Configuration

| Env var                 | Default                  | Purpose              |
|------------------------|--------------------------|----------------------|
| `PRAXIS_RUNTIME`       | `claude`                 | Runtime selection    |
| `PRAXIS_LOCAL_BASE_URL`| `http://localhost:11434` | Server URL           |
| `PRAXIS_LOCAL_MODEL`   | `llama3.1:8b`            | Default local model  |

### Model resolution

Claude model IDs (`claude-*`) are automatically replaced with
`PRAXIS_LOCAL_MODEL`. Non-Claude model IDs pass through unchanged.

### Dependency

`openai>=1.0` added as optional dependency: `pip install praxis[local]`.
Clean SystemExit if openai package is missing and local runtime is selected.

---

## 3. What stayed the same

- `runtime/base.py` — Runtime interface unchanged (4 abstract methods)
- `orchestrator.py` — unchanged
- `tools.py`, `hooks.py`, `config.py`, `subagents.py` — unchanged
- §5 hook enforcement — verified: all 13 hook tests pass
- All 52 pre-existing tests — unchanged and passing

---

## 4. How to verify

```bash
export PRAXIS_WORKSPACE_ROOT=$(pwd)
python -m pytest tests/ -v          # 69 tests, all should pass
python -c "from praxis.runtime import Runtime, ClaudeCodeRuntime, LocalRuntime"

# With Ollama running:
export PRAXIS_RUNTIME=local
python -m praxis "hello"            # should get a response from llama3.1:8b
```

---

## 5. What Phase D should do

**Goal:** Multi-runtime config switch via `convergence.yaml`.

1. Define `convergence.yaml` schema — selects runtime per role:
   ```yaml
   runtimes:
     default: claude
     overrides:
       scout: local
       scribe: local
   local:
     base_url: http://localhost:11434
     model: llama3.1:8b
   ```
2. Update `Orchestrator` to hold multiple runtimes, route by subagent role
3. Add config parsing in `praxis/config.py` or new `praxis/convergence.py`
4. Tests: routing logic, fallback behavior, invalid config handling
5. Do not change §5 hook, tools, or the Runtime interface contract

---

## 6. Recommended next-session prompt

> Begin Phase D: add convergence.yaml multi-runtime config switch. Read
> `CLAUDE.md` and `.praxis/memory/morning-handoff.md` for current state.
> Both runtimes work (Claude + Local). Goal: route subagent roles to
> different runtimes based on a yaml config file. 69 tests green. Use
> the full pipeline: Scout → Plan → Build → Verify → Scribe. Do not
> expand scope beyond the routing config.
