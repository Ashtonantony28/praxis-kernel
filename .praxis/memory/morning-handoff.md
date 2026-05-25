# Morning handoff — Phase B complete, ready for Phase C

**Date:** 2026-05-25
**Status:** Phase B **done**. Subscription OAuth is the primary auth path,
API key is fallback. 52 tests green, §5 hook fixed for space-in-path.
Phase C (LocalRuntime stub for open-source models) is next.

---

## 1. Exact repo state

**Repo:** `Ashtonantony28/Praxis_AgenticOSKernel`
**Branch:** `claude/blissful-franklin-VIMiH`
**Main branch:** `claude/plan-execute-mode-switch-zCz38`

```
praxis-system-prompt.md              # the spec (§0–§11)
CLAUDE.md                            # project conventions — read this first
pyproject.toml                       # deps: anthropic, pytest

praxis/                              # the orchestrator
  __init__.py                        #   package marker, version
  __main__.py                        #   `python -m praxis` — uses from_env(), logs auth
  config.py                          #   Config.from_env() — workspace/memory/hook from env
  subagents.py                       #   parse .claude/agents/*.md → SubagentDef
  hooks.py                           #   run_pretool_hook() — §5 enforcement
  tools.py                           #   7 tool schemas + implementations
  orchestrator.py                    #   Orchestrator — delegates to Runtime, owns tools/hooks
  runtime/                           #   Provider abstraction (Phase A)
    __init__.py                      #     exports Runtime, ClaudeCodeRuntime
    base.py                          #     Abstract Runtime (4 abstract methods)
    claude_code.py                   #     ClaudeCodeRuntime — from_env() with OAuth/API key

tests/                               # 52 tests, all pass, all mocked
  conftest.py                        #   FakeClient, FakeResponse, workspace fixtures
  test_config.py                     #   6 tests — env resolution, restrictive fallback
  test_subagents.py                  #   8 tests — YAML parsing, model mapping
  test_hooks.py                      #   13 tests — allow/block + space-in-path regression
  test_tools.py                      #   13 tests — Bash, Read, Edit, Write, Grep, Glob, schemas
  test_orchestrator.py               #   7 tests — uses ClaudeCodeRuntime(FakeClient) now
  test_runtime.py                    #   5 tests — OAuth/API key priority, env scrubbing

.claude/agents/                      # 5 subagent definitions (unchanged)
.claude/hooks/escalation-boundary.py # §5 hook (fixed: space-in-path regex bugs)
.claude/settings.json                # hook wiring (unchanged)

.praxis/memory/
  morning-handoff.md                 # this file
  phase-b-plan.md                    # Phase B design plan
  runtime-abstraction-plan.md        # Phase A design plan (archived)
  phase0-plan.md                     # Phase 0 design plan (archived)
  .gitkeep
```

---

## 2. What Phase B built

### Auth resolution (`ClaudeCodeRuntime.from_env()`)

1. **Priority:** `CLAUDE_CODE_OAUTH_TOKEN` first (subscription, flat cost),
   `ANTHROPIC_API_KEY` second (pay-per-token), hard exit if neither.
2. **Env scrubbing:** When OAuth is active, `ANTHROPIC_API_KEY` is popped from
   `os.environ` so the SDK and subprocesses can't silently use it.
3. **Startup logging:** `[praxis] auth: oauth` or `[praxis] auth: api_key`
   logged to stderr on every session start.
4. **Backwards compatible:** `ClaudeCodeRuntime(client)` still works for tests
   and manual construction. `auth_method` defaults to `"api_key"`.

### Hook fix (escalation-boundary.py)

Fixed two bugs in Bash command path extraction:
- **Bug 1 (space truncation):** Paths with spaces (like the workspace root
  "/mnt/c/Users/Aiden Antony/...") were truncated at the first space,
  causing false blocks on legitimate operations.
- **Bug 2 (relative path mid-slash):** `rm tests/file.py` captured
  `/file.py` as the path, which resolved to root-level and got blocked.

New approach: `_PATH_TOKEN_RE` handles double-quoted, single-quoted, and
unquoted absolute paths. `_segment_after` + `_extract_paths` replace the
old `.findall()` pattern. 4 new regression tests added.

### Cleanup

- Deleted `tests/verify_hook.py` (empty file from space-in-path bug).

---

## 3. What stayed the same

- `runtime/base.py` — Runtime interface unchanged
- `orchestrator.py` — unchanged
- `tools.py`, `hooks.py`, `config.py`, `subagents.py` — unchanged
- §5 hook enforcement — verified: curl blocked, workspace write allowed
- All 47 pre-existing test assertions — unchanged

---

## 4. How to verify

```bash
export PRAXIS_WORKSPACE_ROOT=$(pwd)
python -m pytest tests/ -v          # 52 tests, all should pass
python -c "from praxis.runtime import Runtime, ClaudeCodeRuntime"
```

---

## 5. What Phase C should do

**Goal:** LocalRuntime stub for open-source models.

1. Create `praxis/runtime/local.py` with a `LocalRuntime(Runtime)` class
2. Implement the 4 abstract methods to call a local model server
   (e.g., ollama, vLLM, llama.cpp) via its HTTP API
3. Start as a stub — raise `NotImplementedError` with clear messages,
   then implement `run_loop` first
4. Add a `--runtime local` flag or `PRAXIS_RUNTIME=local` env var to
   `__main__.py` for runtime selection
5. Do not change §5 hook, tools, or the Runtime interface contract
6. Test with FakeClient pattern (mock the HTTP calls)

---

## 6. Recommended next-session prompt

> Begin Phase C: add LocalRuntime stub for open-source models. Read
> `CLAUDE.md` and `.praxis/memory/morning-handoff.md` for current
> state. The Runtime interface is stable — subclass it in
> `praxis/runtime/local.py`. 52 tests green. Use the full pipeline:
> Scout → Plan → Build → Verify → Scribe. Do not expand scope beyond
> the LocalRuntime stub.
