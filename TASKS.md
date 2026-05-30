# Tasks

<!-- Scenario A: all baseline work marked [x] from audit. Phase H tasks are [ ]. -->
<!-- Auditor must verify [x] items against real code before any implementation. -->

## Baseline — believed complete (auditor verifies on first run)

- [x] TASK-B01: Python orchestrator + five subagents — praxis/orchestrator.py, praxis/subagents.py
- [x] TASK-B02: Runtime abstraction — praxis/runtime/{base,claude_code,cloud,local,openai_base}.py
- [x] TASK-B03: §5 hook + enforcement — .claude/hooks/escalation-boundary.py, praxis/runtime/enforcement.py
- [x] TASK-B04: Seven integrations (github, codebase, testrunner, deps, web, files, email, calendar) — praxis/integrations/
- [x] TASK-B05: Extended integrations (playwright, notion, linear, slack, wiki, wiki_sync) — praxis/integrations/
- [x] TASK-B06: Unattended operation (queue, checkpoint, queue_runner, daemon, scheduler) — praxis/queue.py etc.
- [x] TASK-B07: MCP gateway HTTP/SSE + /metrics + /dashboard — praxis/mcp_server.py
- [x] TASK-B08: Docker + systemd deployment — Dockerfile, docker-compose.yml, systemd/
- [x] TASK-B09: Bitemporal wiki (ingest/query/lint/graph export/multi-source merge) — praxis/wiki.py, wiki/
- [x] TASK-B10: v2.0 model-agnostic architecture (modes, native agents, cross-runtime enforcement) — praxis/modes/, praxis/agents/
- [x] TASK-B11: Setup wizard + config wizard — praxis/setup_wizard.py, praxis/config_wizard.py
- [x] TASK-B12: Open-source prep (README, install.sh, demo, LICENSE, CI, templates) — README.md etc.
- [x] TASK-B13: Plan approval flow (--plan, --approve-plan) — praxis/**main**.py
- [x] TASK-B14: Auth rotation hardening — praxis/runtime/auth.py
- [x] TASK-B15: Financial circuit breaker — praxis/runtime/cost.py
- [x] TASK-B16: Telemetry + Prometheus metrics — praxis/runtime/telemetry.py, praxis/mcp_server.py
- [x] TASK-B17: Personal data gitignore hardening — .gitignore, .gitkeep stubs

## Phase H — new work ([ ] = not yet started)

- [x] TASK-H01: SOUL.md persona layer — files: praxis/orchestrator.py, wiki/SOUL.md (template), .gitignore — deps: none
- [x] TASK-H02: HEARTBEAT.md proactive trigger — files: praxis/queue_runner.py, praxis/scheduler.py, .praxis/HEARTBEAT.md (template), .gitignore — deps: none (independent of H01)
- [x] TASK-H03: Telegram adapter — files: praxis/integrations/telegram.py, praxis/slack_listener.py (reference), pyproject.toml, convergence.yaml, .env.example — deps: none (independent of H01, H02)
- [x] TASK-H04: Tests for H01/H02/H03 — files: tests/test_soul.py, tests/test_heartbeat.py, tests/test_telegram.py — deps: TASK-H01, TASK-H02, TASK-H03
- [x] TASK-H05: Scribe pass — files: CLAUDE.md, README.md, .gitignore, praxis/setup_wizard.py, .env.example, .praxis/memory/morning-handoff.md — deps: TASK-H04
