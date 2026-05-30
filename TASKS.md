# Praxis TASKS.md — Phase v2-B/C/D: Plan Approval + Per-agent Modes + Native Agents

## Phase v2-B: Plan approval flow

- [x] V2B-01: Plan staging — when Orchestrator.run() completes in plan mode (mode.requires_confirmation=True), write .praxis/staging/plans/{uuid4}.json with keys: id, task, plan_text, created_at (ISO8601), status="pending". Create dir if absent. — deps: V2A-04
- [x] V2B-02: CLI commands — add to praxis/__main__.py: --list-plans (show pending staged plans), --approve-plan <id> (re-run original task in build mode, mark approved), --reject-plan <id> (mark rejected, no execution); extend --list-staged section 7 to scan plans/. — deps: V2B-01
- [x] V2B-03: Tests — tests/test_plan_approval.py: plan mode writes staging file on completion, --list-plans shows pending, --approve-plan re-runs in build mode, --reject-plan marks rejected without executing; 874 existing tests must still pass. — deps: V2B-01, V2B-02

## Phase v2-C: Per-subagent mode routing

- [x] V2C-01: Add mode field to subagent definitions — superseded by V2D-04: shim generator writes .claude/agents/*.md from praxis/agents/*.yaml (which carry mode: fields) at session start. — deps: V2A-01
- [x] V2C-02: Add mode to convergence.yaml agents section — per-agent mode override key under agents:; lets users change subagent modes without editing .md files. — deps: V2C-01
- [x] V2C-03: Wire into orchestrator — when spawning a subagent, apply subagent's declared mode regardless of session mode; if session is build but Scout's def says plan, Scout runs plan. — deps: V2C-01, V2C-02, V2A-04
- [x] V2C-04: Tests — Scout spawned in build session uses plan mode; Builder spawned in plan session uses build mode (surface warning not silent fail); 874+v2B tests must still pass. — deps: V2C-01, V2C-02, V2C-03

## Phase v2-D: Native subagent definitions (cross-runtime)

- [x] V2D-01: praxis/agents/ YAML definitions — create praxis/agents/ dir with scout.yaml, planner.yaml, builder.yaml, verifier.yaml, scribe.yaml. Schema per file: name, model (role alias), mode (plan|build), prompt (full system prompt), tools (list[str]), background (bool). — deps: V2C-01
- [x] V2D-02: praxis/agents/loader.py — load(name)->AgentDefinition dataclass, load_all()->list[AgentDefinition]; reads from praxis/agents/*.yaml; validates schema, raises clearly on malformed. — deps: V2D-01
- [x] V2D-03: Update all three runtimes — each runtime's spawn_subagent(agent_def, prompt) accepts AgentDefinition loaded from praxis/agents/ rather than assuming .claude/agents/ discovery. — deps: V2D-02, V2C-03
- [x] V2D-04: Generated .claude/agents/ shim — on session start ClaudeCodeRuntime writes .claude/agents/*.md from praxis/agents/*.yaml (keeps SDK discovery working); add .claude/agents/ to .gitignore (it is now generated output). — deps: V2D-03
- [x] V2D-05: Cross-runtime parametrized tests — tests/test_subagent_agnostic.py parametrized over all 3 runtimes: AgentDefinition loads from YAML, spawn_subagent works on ClaudeCodeRuntime (via shim), CloudRuntime (direct), LocalRuntime (direct), all 5 agents discoverable on all 3 runtimes. — deps: V2D-01, V2D-02, V2D-03, V2D-04
- [x] V2D-06: Scribe pass — CLAUDE.md: praxis/agents/ is source of truth, .claude/agents/ is generated; README.md: subagent system works across all three runtimes; .praxis/memory/morning-handoff.md: v2 completion status. — deps: V2D-05

## Phase v2-A: Runtime-agnostic permission abstraction (complete)

- [x] V2A-01: praxis/modes/__init__.py + praxis/modes/base.py — Mode dataclass + Mode.load() — deps: none
- [x] V2A-02: praxis/modes/plan.py + praxis/modes/build.py — built-in mode defs — deps: V2A-01
- [x] V2A-03: praxis/modes.yaml at repo root — user-overridable YAML definitions — deps: none
- [x] V2A-04: Wire apply_mode() into all three runtimes (claude_code.py, openai_base.py) — deps: V2A-01, V2A-02, V2A-03
- [x] V2A-05: praxis/runtime/enforcement.py — mode-aware check layer — deps: V2A-01, V2A-04
- [x] V2A-06: CLI integration in praxis/__main__.py (--mode / --plan / PRAXIS_DEFAULT_MODE) — deps: V2A-01, V2A-02
- [x] V2A-07: Update praxis/setup_wizard.py + praxis/config_wizard.py — deps: V2A-01, V2A-02
- [x] V2A-08: tests/test_modes.py + tests/test_mode_enforcement.py (all 834 existing still pass) — deps: V2A-01 through V2A-07
- [x] V2A-09: Docs — CLAUDE.md + README.md + morning-handoff.md — deps: V2A-08

## Previous cycles — all complete

- [x] C-01+C-03: praxis/config_wizard.py — full wizard incl. effort presets (assigned)
- [x] C-02: praxis/__main__.py — wire --config flag (depends on C-01)
- [x] C-04: tests/test_config_wizard.py — full test suite (depends on C-01+C-03)
- [x] C-05: README.md + CLAUDE.md + morning-handoff.md scribe pass (depends on C-04 passing)

## Enforcement correctness tasks — all complete

- [x] TASK-E01: praxis/runtime/enforcement.py — runtime-agnostic §5 enforcement layer + wire into all three runtimes
- [x] TASK-E02: tests/test_enforcement.py — parametrized cross-runtime enforcement tests (depends on E01)
- [x] TASK-E03: Fix test_hooks.py "tool" → "tool_name" payload key if any exist (depends on E02)
