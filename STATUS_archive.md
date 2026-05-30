# Praxis STATUS_archive.md — Archived entries

## Archive: Cycle C + Enforcement (2026-05-28 to 2026-05-29)

### C-01+C-03 (completed 2026-05-28)
- Created praxis/config_wizard.py — interactive terminal config manager with effort presets
- Public API: run_config_wizard(workspace_root, *, env_file, _input, _env_mode)
- Writes PRAXIS_RUNTIME/PRAXIS_MAX_SESSION_COST/PRAXIS_MAX_TURNS/PRAXIS_EFFORT_PRESET to .env
- Writes agents: section to convergence.yaml (never touches runtimes: or task_types:)
- Six effort presets (minimal/low/medium/high/max/custom) with diff confirmation

### C-02 (completed 2026-05-28)
- Wired --config flag into praxis/__main__.py
- Added "config" to _parse_mode() and the elif mode == "config": branch in main()
- Calls run_config_wizard(workspace_root, env_file=env_file)

### C-04 (completed 2026-05-28)
- Created tests/test_config_wizard.py — 24 tests covering all wizard behaviors
- All 24 new tests pass; full suite (767+24 = 791) passes
- Tests cover: env read/write, yaml agents section, model/runtime/preset selection, merge safety, invalid inputs

### C-05 (completed 2026-05-28)
- Updated README.md: added --config quickstart entry after --setup block
- Updated CLAUDE.md: added "Config wizard conventions (Cycle C)" section
- Updated .praxis/memory/morning-handoff.md: reflects 791 tests, --config delivery, updated checklist

### CYCLE-C CLOSE (2026-05-28) — all tasks complete ✓
- 791 tests pass (767 baseline + 24 new); hook md5 057f07f223fd5b5fe11f2aa50af1e361 unchanged
- python -m praxis --config importable and parseable; egress blocked confirmed

### TASK-E01 (completed 2026-05-29)
- Created praxis/runtime/enforcement.py — EnforcementError + enforce() public API
- Mirrors escalation-boundary.py logic: workspace boundary, egress allowlist, control-plane guard, Bash network/destructive/bypass checks, WebResearch domain check
- Wired enforce() into ClaudeCodeRuntime.execute_tool() (claude_code.py line ~161) and OpenAIBaseRuntime.execute_tool() (openai_base.py line ~172); LocalRuntime inherits from OpenAIBaseRuntime so is covered automatically
- All 791 existing tests still pass; .claude/hooks/escalation-boundary.py untouched

### TASK-E02 (completed 2026-05-29)
- Created tests/test_enforcement.py — 43 parametrized tests for §5 enforcement
- Part 1 (TestEnforceDirect, 14 tests): exercises enforce() in isolation — workspace boundary, domain allowlist, control-plane guard, Bash bypass, Read passthrough, unset workspace root
- Part 2 (TestCrossRuntimeEnforcement, 27+2 tests): same blocked/allowed scenarios run against ClaudeCodeRuntime, OpenAIBaseRuntime, and LocalRuntime; proves tool_executor is never called when blocked and is called exactly once when allowed; includes mixed-batch tests verifying one blocked + one allowed in the same call
- Full suite: 791 pre-existing + 43 new = 834 tests, all passing; no regressions

### TASK-E03 (completed 2026-05-29)
- Audited tests/test_hooks.py for wrong-key payloads ("tool": instead of "tool_name:"): found 0 occurrences — no fix needed; tests use the Python run_pretool_hook() API, not raw JSON
- tests/test_hooks.py: 17/17 pass with no changes required
- Full suite: 834 tests still pass, 0 regressions
- Hook enforcement confirmed live: echo '{"tool_name":"WebFetch",...}' | python3 .claude/hooks/escalation-boundary.py → exit code 2, "BLOCKED by §5" in stderr

## Archive: Phase v2-A (2026-05-29)

### V2A-03 (completed 2026-05-29)
- Created praxis/modes.yaml — user-overridable mode definitions
- Default: modes: {} (no overrides); examples in comments; schema matches Mode dataclass

### V2A-01 (completed 2026-05-29)
- Created praxis/modes/__init__.py and praxis/modes/base.py
- Mode dataclass: name, allowed_tools, denied_tools, prompt_suffix, requires_confirmation, model_override
- Mode.load(name): YAML user-override at <workspace>/praxis/modes.yaml → built-in praxis.modes.{name} → ValueError
- All 834 existing tests still pass; import verified with python -c "from praxis.modes import Mode; print(Mode)"

### V2A-02 (completed 2026-05-29)
- Created praxis/modes/plan.py — MODE with 15 denied tools (Write/Edit/Bash/NotebookEdit + 11 integration write actions); requires_confirmation=True; prompt_suffix explains plan mode
- Created praxis/modes/build.py — MODE with no restrictions (full access, default behavior)
- Verified: python -c "from praxis.modes.plan import MODE; from praxis.modes.build import MODE as B; print('plan denied:', len(MODE.denied_tools)); print('build denied:', len(B.denied_tools))" → plan denied: 15, build denied: 0

### V2A-06 (completed 2026-05-29)
- Updated praxis/__main__.py: --plan flag (alias for --mode plan), --mode <name> flag, PRAXIS_DEFAULT_MODE env var
- Mode detected before arg extraction; --mode <name> arg pair skipped from prompt text; --plan filtered out as a flag
- sys.stderr.write for non-build modes; mode passed to orch.run() with TypeError fallback for unpatched orchestrators
- 834 tests still pass; tests/test_main.py updated to assert_called_once_with(msg, mode=ANY)

### V2A-07 (completed 2026-05-29)
- Updated praxis/setup_wizard.py: steps 1-9 renumbered to X/11; new step 10/11 asks plan vs build; sets PRAXIS_DEFAULT_MODE in env_data; summary shows mode
- Updated praxis/config_wizard.py: option [10] = default mode, option [11] = Done; _menu_default_mode() helper; saves PRAXIS_DEFAULT_MODE to .env; _load_current_config picks up PRAXIS_DEFAULT_MODE from .env
- Updated tests/test_config_wizard.py: all 13 "Done" inputs changed from "10" to "11" to match new menu numbering
- 834 tests still pass

### V2A-04 (completed 2026-05-29)
- Updated praxis/runtime/base.py: added TYPE_CHECKING import for Mode; added apply_mode() concrete method (filters tool list by denied_tools); added mode: "Mode | None" = None param to run_loop() abstract signature
- Updated praxis/runtime/claude_code.py: TYPE_CHECKING import; _current_mode attribute on __init__; run_loop() and spawn_subagent() accept mode param; effective_system/effective_tool_schemas computed at top of run_loop(); _create_with_retry() called with effective args
- Updated praxis/runtime/openai_base.py: same pattern as claude_code.py; effective_tool_schemas fed into _convert_tools(); effective_system into messages initialization
- Updated praxis/orchestrator.py: TYPE_CHECKING import for Mode; run() accepts mode=None and passes it to runtime.run_loop()
- 834 tests still pass

### V2A-05 (completed 2026-05-29)
- Updated praxis/runtime/enforcement.py: enforce() gains mode=None param (TYPE_CHECKING import for Mode added); mode.denied_tools check added at end (after all §5 checks) — defense-in-depth layer 2
- Updated praxis/runtime/claude_code.py: enforce() call now passes mode=self._current_mode
- Updated praxis/runtime/openai_base.py: enforce() call now passes mode=self._current_mode
- 834 tests still pass

### V2A-08 (completed 2026-05-29)
- Created tests/test_modes.py — 20 tests: Mode dataclass fields/immutability/equality, load from built-ins (plan/build), load from YAML (custom/override/allowed/confirmation/model_override/empty), load errors (ValueError message content, unknown keys ignored)
- Created tests/test_mode_enforcement.py — 20 tests: enforce() mode param (8 unit tests: plan blocks Write/Bash, allows Read; build/None allows Write; error messages; empty denied_tools), cross-runtime parametrized (12 tests: 3 runtimes x 4 scenarios — plan blocks Write, plan blocks Bash, plan allows Read, build allows Write)
- Full suite: 834 + 40 = 874 tests, all passing

### V2A-09 (completed 2026-05-29)
- Updated CLAUDE.md: added "Mode conventions (Phase v2-A)" section after Config wizard conventions; added praxis/modes/ + praxis/modes.yaml entries to repo layout
- Updated README.md: added --plan/--mode quickstart block after --config section; added "Plan & Build Modes" section before Integrations
- Overwrote .praxis/memory/morning-handoff.md: v2-A completion summary with V2A-01 through V2A-08 details, updated audit checklist, and v2-B/C/D/E next milestone options

### PHASE v2-A CLOSE (2026-05-29) — all V2A-01 through V2A-09 complete ✓
- 874 tests pass (834 baseline + 40 new); hook md5 057f07f223fd5b5fe11f2aa50af1e361 unchanged
- New: praxis/modes/ package (Mode dataclass, plan/build built-ins), praxis/modes.yaml user config
- Runtime wiring: apply_mode() + _current_mode on all 3 runtimes; enforcement.py defense-in-depth; CLI --mode/--plan/PRAXIS_DEFAULT_MODE
- Wizard updates: setup step 10/11 (mode) + config option [10] (mode); 40 new tests pass

## Archive: Phase v2-C control-plane issue (2026-05-29)

### NEEDS HUMAN: V2C-01 control-plane change (2026-05-29)
The escalation-boundary.py hook (line 105-109) blocks ALL writes under WORKSPACE_ROOT/.claude/ — including .claude/agents/*.md.
V2C-01 requires adding a `mode:` field to each agent's frontmatter. This MUST be human-applied.

**Exact patches to apply manually:**

`.claude/agents/scout.md` — add `mode: plan` before the closing `---`:
```
---
name: scout
description: Read-only investigation and research...
tools: Read, Grep, Glob
model: haiku
mode: plan
---
```

`.claude/agents/planner.md` — add `mode: plan` before the closing `---`:
```
---
name: planner
description: Turns a goal into a concrete...
tools: Read, Grep, Glob
model: sonnet
mode: plan
---
```

`.claude/agents/verifier.md` — add `mode: plan` before the closing `---`:
```
---
name: verifier
description: Independently checks a Builder's output...
tools: Read, Grep, Glob, Bash
model: sonnet
mode: plan
---
```

`.claude/agents/builder.md` — add `mode: build` before the closing `---`:
```
---
name: builder
description: Executes an approved plan...
tools: Read, Edit, Write, NotebookEdit, Bash, Grep, Glob
model: sonnet
mode: build
---
```

`.claude/agents/scribe.md` — add `mode: plan` before the closing `---`:
```
---
name: scribe
description: Maintains durable memory across sessions...
tools: Read, Edit, Write, Grep, Glob
model: haiku
mode: plan
---
```

**Rationale:** V2C-01 is superseded by V2D-04 (ClaudeCodeRuntime will auto-generate .claude/agents/*.md from praxis/agents/*.yaml at session start, which will include mode:). Human may choose to apply these patches now OR wait for V2D-04 to regenerate. Until applied, SubagentDef.mode will be None (session mode inherited — safe default). V2C-01 is marked blocked; proceeding with V2C-02, V2C-03, V2D-01, V2D-02 in parallel.

V2C-01 status: BLOCKED (human must apply frontmatter patches OR V2D-04 will generate them)

## Archive: Phase v2-C implementation (2026-05-29)

### V2C-02+V2C-03 (completed 2026-05-29)
- convergence.yaml: added commented agents: section with per-agent mode override structure
- praxis/convergence.py: added agent_modes field + mode_for() method; parse agents: YAML section in load()
- praxis/subagents.py: SubagentDef gains mode: str | None = None field; parse_agent_file() reads mode from frontmatter
- praxis/orchestrator.py: constructor accepts agent_modes dict; run_subagent() applies convergence.yaml override > SubagentDef.mode > None; logs mode to stderr; passes effective_mode to spawn_subagent()
- praxis/__main__.py: both Orchestrator creation sites (interactive + approve_plan) pass conv.agent_modes
- All 893 tests still pass; SubagentDef.mode=None is graceful default (no mode override)

### V2C-04 (completed 2026-05-29)
- Created tests/test_subagent_mode_routing.py — 17 tests in 4 classes: TestSubagentDefModeField (4), TestConvergenceAgentModes (4), TestOrchestratorSubagentMode (5), TestAgentDefinitionModeRouting (4)
- Tests use direct SubagentDef construction + praxis/agents.loader.load mock — no dependency on V2C-01 .claude/agents/ frontmatter edits
- Covers: SubagentDef.mode field/defaults, parse_agent_file mode extraction, convergence agent_modes/mode_for, run_subagent plan/build/override/none/unknown routing, spawn_from_definition mode wiring, all 5 YAML agents have plan|build mode
- All 910 tests pass (893 pre-existing + 17 new); no regressions
