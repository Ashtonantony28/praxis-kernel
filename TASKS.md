# Praxis TASKS.md — Phase v2-A: Plan/Build Modes

## Phase v2-A: Runtime-agnostic permission abstraction (current)

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
