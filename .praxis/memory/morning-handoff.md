# Morning handoff — Phase 5 complete

**Date:** 2026-05-26  
**Status:** Phase 5 (open-source preparation) complete. 388 tests pass.

---

## 1. Exact repo state

**Repo:** `Ashtonantony28/Praxis_AgenticOSKernel`  
**Branch:** `claude/blissful-franklin-VIMiH`  
**Main branch:** `claude/plan-execute-mode-switch-zCz38`

```
praxis-system-prompt.md              # the spec (§0–§11)
CLAUDE.md                            # project conventions — updated Phase 5
README.md                            # NEW — project README
install.sh                           # NEW — one-command installer
.env.example                         # NEW — all env vars documented
pyproject.toml                       # updated — [cloud], [analyze], [all] extras

demo/
  demo.sh                            # NEW — §5 escalation boundary demo (7 scenarios)

praxis/                              # the orchestrator (unchanged from Phase 4)
  __init__.py, __main__.py, config.py, convergence.py,
  subagents.py, hooks.py, tools.py, orchestrator.py,
  queue.py, checkpoint.py, queue_runner.py, daemon.py
  integrations/                      #   8 tools: GitHub, Analyze, TestRunner, Dependencies,
                                     #   WebResearch, FileManager, Email, Calendar
  runtime/                           #   3 runtimes: ClaudeCode, Local, OpenAICloud

tests/                               # 388 tests, all pass, all mocked

.claude/agents/                      # 5 subagent definitions
.claude/hooks/escalation-boundary.py # §5 hook (unchanged)
.claude/settings.json                # hook wiring

.praxis/memory/
  morning-handoff.md                 # this file
  install-audit.md                   # NEW — full dependency audit
  p1-install-plan.md                 # NEW — install experience design
  phase4-wave4-survey.md
  phase4-wave4-plan.md
  phase4-wave3-plan.md
  phase4-wave2-survey.md
  phase4-wave2-plan.md
  phase4-mcp-survey.md
  phase4-wave1-plan.md
  pipeline-validation-report.md
  unattended-readiness.md
  phase-j-plan.md
  model-agnostic-plan.md
  h3-pipeline-report.md
  first-live-run.md
  phase-g-plan.md

.praxis/staging/
  escalation-boundary-patch.md
  drafts/
  events/

.praxis/queue/
  tasks.jsonl
  results/
  checkpoints/
```

---

## 2. What Phase 5 built

### P-1: One-command install

- **install.sh** — checks Python 3.10+, git, creates .venv, installs core package, creates workspace dirs, copies .env.example, checks optional tools with clear warnings
- **pyproject.toml** — added `[cloud]`, `[analyze]`, `[all]` extras. `pip install praxis[all]` installs everything.
- **.env.example** — all 18 env vars documented with comments explaining purpose and where to get credentials

### P-2: README

- **README.md** — covers: what Praxis is (2 sentences), security-first differentiator, demo of hook blocking, quick start (one command to first run), architecture (5 subagents, 3 runtimes, 3 modes), integration list, queue/daemon, contributing stub, MIT license

### P-3: Demo script

- **demo/demo.sh** — 7 scenarios showing the §5 hook in action. No API key needed — invokes the hook directly via stdin JSON. Demonstrates:
  1. Write outside workspace → BLOCKED
  2. Write inside workspace → ALLOWED
  3. Edit control plane → BLOCKED
  4. curl external host → BLOCKED
  5. curl localhost → ALLOWED
  6. rm outside workspace → BLOCKED
  7. Read (always allowed) → ALLOWED

### Verification

- All 388 tests pass
- install.sh syntax validated (`bash -n`)
- pyproject.toml TOML parsing verified
- .env.example covers all 18 env vars (cross-checked against codebase)
- Demo runs clean on WSL2 with correct BLOCKED/ALLOWED for all 7 scenarios

---

## 3. What stayed the same

- All 388 pre-existing tests pass unmodified
- No changes to orchestrator, runtime, tools, hooks, or integrations
- §5 hook enforcement unchanged
- All existing functionality preserved

---

## 4. How to verify

```bash
export PRAXIS_WORKSPACE_ROOT=$(pwd)

# Full test suite (388 tests):
python -m pytest tests/ -v

# Demo:
bash demo/demo.sh

# Install script syntax:
bash -n install.sh
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
| 4-W1 | Workstation integrations: GitHub, Analyze, TestRunner, Dependencies | 261 |
| 4-W2 | Web research: Brave Search API + fetch with domain allowlisting | 290 |
| 4-W3 | File management: search, summarize, git_status, disk_usage | 326 |
| 4-W4 | Email + Calendar: IMAP read, iCal feed, draft/propose staging | 388 |
| **5** | **Open-source prep: install.sh, README, .env.example, demo, pyproject.toml extras** | **388** |

---

## 6. What's next

Phase 5 complete. The project is ready for open-source release:
- One-command install works on Ubuntu 24 and WSL2
- README tells the full story to new developers
- Demo is reproducible with zero configuration
- All env vars documented
- 388 tests, all mocked, all green
