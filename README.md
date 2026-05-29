# Praxis-Kernel

A governed agentic OS kernel that works overnight and stages 
external writes for your morning approval.

**Run it free** — works on local Ollama, free Gemini 2.5 Flash, 
or any OpenAI-compatible endpoint. No per-token billing required.

**Trust it** — every write to Notion, Linear, email, or calendar 
is staged locally. You run `python -m praxis --approve` in the 
morning to review and execute. There is no code path that acts 
on your behalf without you.

**Verify it** — 767 tests, a §5 security hook that intercepts 
every tool call before execution, and a financial circuit breaker 
that halts the system if session costs exceed your configured cap.

## Why Praxis

**Security-first.** The `§5 escalation boundary` is not a suggestion — it's a PreToolUse hook that inspects every tool call before execution. The model cannot write outside the workspace, cannot modify its own control plane, and cannot reach the network without explicit domain allowlisting. When it tries, the hook blocks the call and the model escalates to you instead of proceeding.

**Model-agnostic.** Three runtime backends — Anthropic Claude API (subscription or pay-per-token), any local model via Ollama/vLLM/llama.cpp, or any cloud API that speaks the OpenAI protocol (OpenAI, Gemini, OpenRouter, Groq). Switch with one environment variable. Route different subagents to different providers with `convergence.yaml`.

**Flat subscription cost.** When using `CLAUDE_CODE_OAUTH_TOKEN`, you run on your Claude subscription — no per-token billing surprises.

## The Security Gate in Action

```
$ python -m praxis "Write a config file to /etc/myapp/config.yaml"

[praxis] Planning...
[praxis] Calling tool: Write { file_path: "/etc/myapp/config.yaml", ... }

BLOCKED by §5 escalation boundary: Write would write outside
WORKSPACE_ROOT (/home/user/project): /etc/myapp/config.yaml
Escalate to the human per §5 — do not retry without approval.

[praxis] The write was blocked by the security boundary.
         /etc/myapp/config.yaml is outside the workspace.
         I'll save it inside the project instead — does
         config/myapp.yaml work for you?
```

The hook didn't just warn — it exited with code 2, which killed the tool call. The model received the block message and changed its approach instead of retrying. This is the core contract: **the model adapts to its boundaries rather than working around them.**

## Quick Start

```bash
git clone https://github.com/Ashtonantony28/Praxis_AgenticOSKernel.git
cd Praxis_AgenticOSKernel
bash install.sh
```

The installer checks Python 3.10+, creates a virtual environment, installs the package, and prints a setup checklist.

```bash
# Run the setup wizard (no manual .env editing required)
python -m praxis --setup
```

The wizard takes you through runtime selection, credential entry (all hidden with getpass), and optional integrations (Slack, GitHub, web search, email). It writes your `.env` file and can schedule a morning briefing in under 5 minutes.

```bash
# Change model/effort settings at any time (no file editing required)
python -m praxis --config
```

The config wizard lets you adjust model assignments per subagent (orchestrator, builder, reviewer, scout, scribe), max turns, cost cap, and runtime. It includes named effort presets (Minimal → Max) that set a coordinated combination in one step. Changes are written to `.env` and `convergence.yaml` — no manual file editing required.

```bash
# Plan mode — presents a plan instead of executing writes
python -m praxis --plan "refactor the auth module"
# or equivalently:
python -m praxis --mode plan "refactor the auth module"

# Set plan mode as your default
export PRAXIS_DEFAULT_MODE=plan

# Custom modes — define in praxis/modes.yaml
python -m praxis --mode my-review-mode "review the PR"
```

After setup:
```bash
source .venv/bin/activate
python -m praxis --daemon    # start background operation
python -m praxis --status    # verify it's running
```

Or run interactively:

```bash
source .venv/bin/activate
export PRAXIS_WORKSPACE_ROOT=$(pwd)
export PRAXIS_MEMORY_ROOT=$PRAXIS_WORKSPACE_ROOT/.praxis/memory
python -m praxis "hello, what can you do?"
```

### Use a local model instead

```bash
pip install praxis[local]
export PRAXIS_RUNTIME=local
export PRAXIS_LOCAL_BASE_URL=http://localhost:11434   # Ollama
export PRAXIS_LOCAL_MODEL=llama3.1:8b
python -m praxis "hello"
```

### Use a cloud provider

```bash
pip install praxis[cloud]
export PRAXIS_RUNTIME=cloud
export PRAXIS_CLOUD_API_KEY=sk-...
export PRAXIS_CLOUD_MODEL=gpt-4o
python -m praxis "hello"
```

## Architecture

```
                    ┌─────────────────────────┐
                    │     Human Operator       │
                    └────────────┬─────────────┘
                                 │ prompt / approval
                    ┌────────────▼─────────────┐
                    │      Orchestrator         │
                    │  (tool dispatch + §5 hook)│
                    └────────────┬─────────────┘
                                 │
              ┌──────────┬───────┼───────┬──────────┐
              ▼          ▼       ▼       ▼          ▼
          ┌───────┐ ┌────────┐ ┌─────┐ ┌────────┐ ┌───────┐
          │ Scout │ │Planner │ │Build│ │Verifier│ │Scribe │
          │ (read)│ │ (plan) │ │(act)│ │(check) │ │(memo) │
          └───────┘ └────────┘ └─────┘ └────────┘ └───────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
     ┌────────────────┐ ┌───────────────┐ ┌────────────────┐
     │ ClaudeCode     │ │ Local         │ │ OpenAICloud    │
     │ Runtime        │ │ Runtime       │ │ Runtime        │
     │ (Anthropic API)│ │ (Ollama/vLLM) │ │ (OpenAI/Gemini)│
     └────────────────┘ └───────────────┘ └────────────────┘
```

### Five Subagents

| Agent | Role | Tools |
|-------|------|-------|
| **Scout** | Read-only investigation. Finds files, greps symbols, summarizes. | Read, Grep, Glob |
| **Planner** | Designs implementation plans. Read-only — never executes. | Read, Grep, Glob |
| **Builder** | Executes approved plans. Edits files, runs commands. | Read, Edit, Write, Bash, Grep, Glob |
| **Verifier** | Independently checks Builder output. Runs tests, probes health. | Read, Grep, Glob, Bash |
| **Scribe** | Maintains durable memory. Updates records across sessions. | Read, Edit, Write, Grep, Glob |

### Three Runtimes

| Runtime | Backend | Config |
|---------|---------|--------|
| `claude` (default) | Anthropic Messages API | `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` |
| `local` | Ollama, vLLM, llama.cpp | `PRAXIS_LOCAL_BASE_URL` + `PRAXIS_LOCAL_MODEL` |
| `cloud` | OpenAI, Gemini, OpenRouter, Groq | `PRAXIS_CLOUD_API_KEY` + `PRAXIS_CLOUD_BASE_URL` |

Set `PRAXIS_RUNTIME=local` or `PRAXIS_RUNTIME=cloud` to switch. Use `convergence.yaml` to route different subagents to different runtimes.

### Three Operating Modes

- **Assistant** — personal computing: documents, notes, schedules
- **Workstation** — software development: code, tests, version control
- **Operator** — infrastructure: services, deployments, live systems

The orchestrator infers the mode from workspace contents. Each mode adjusts defaults for caution level and verification strategy.

## Plan & Build Modes

Praxis supports runtime-agnostic permission modes that work identically across all three runtimes (Claude OAuth, Cloud, Local Ollama):

- **build** (default): Full tool access. Praxis acts immediately.
- **plan**: Read-only mode. Write, Edit, Bash, and all integration write actions are denied. Praxis presents a numbered plan for human approval before anything executes.

Use `--plan` or `--mode plan` for a single run; set `PRAXIS_DEFAULT_MODE=plan` to make plan mode the default. Define custom modes in `praxis/modes.yaml`.

Plan mode is enforced at two layers:
1. Tools denied by the mode are removed from the tool list before the model sees them.
2. `enforcement.py` blocks any denied tool call as defense-in-depth, even if the model somehow invokes one.

## Integrations

Eight built-in tools for workstation tasks:

| Tool | What it does | Requires |
|------|-------------|----------|
| **GitHub** | PRs, issues, diffs | `gh` CLI + `GITHUB_TOKEN` |
| **Analyze** | Coverage, complexity, lint | `coverage`, `radon`, `pylint` |
| **TestRunner** | pytest with parsed output | `pytest` |
| **Dependencies** | Outdated packages, vulnerability scan | `pip`, `pip-audit` |
| **WebResearch** | Search + page fetch | `PRAXIS_WEB_SEARCH_API_KEY` + domain allowlist |
| **FileManager** | Search, summarize, git status, disk usage | (stdlib — no extra deps) |
| **Email** | IMAP inbox read + local draft staging | `PRAXIS_EMAIL_*` vars |
| **Calendar** | iCal feed read + event proposal staging | `PRAXIS_CALENDAR_URL` |

Email and Calendar follow the **read-safe / write-escalate** pattern: read operations run autonomously, but there is no send or create action. `draft_email` and `propose_event` save files locally for human review.

Install analysis tools: `pip install praxis[analyze]`

See `.env.example` for full configuration reference.

## Phase S — Slack Bridge

Praxis can receive commands from Slack and send structured notifications back.

### Outbound notifications (autonomous)

```bash
export PRAXIS_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
export PRAXIS_ALLOWED_DOMAINS=...,hooks.slack.com
pip install praxis[slack]
python -m praxis "notify the team that the nightly run completed"
```

Praxis calls the `Slack` tool with `action: notify` — this POSTs to your incoming webhook autonomously. It is Praxis-to-user communication, not user-attributed, so the §5 boundary permits it.

### User-attributed messages (write-escalate)

`stage_message` saves the composed message to `.praxis/staging/slack/messages/{id}.json` for your review. There is no `send` action — the escalation is structural.

### Approval workflows

`post_approval_request` stages an approval record and sends a notification with a Slack button. When you click Approve or Reject in Slack, the listener updates `.praxis/staging/slack/approvals/{id}.json`. Praxis polls with `get_approval` to see the result.

### Inbound commands (socket mode)

```bash
export PRAXIS_SLACK_BOT_TOKEN=xoxb-...
export PRAXIS_SLACK_APP_TOKEN=xapp-...
python -m praxis --slack-listen
```

The socket listener receives DMs and slash commands, wraps them as tasks in `.praxis/queue/`, and processes them through the queue runner. SIGTERM-safe — finishes the current event before exiting.

## Phase M — MCP Gateway

Praxis exposes all its tools to any MCP (Model Context Protocol) client over HTTP/SSE.

```bash
pip install praxis[mcp]
python -m praxis --mcp          # starts on http://127.0.0.1:8765/sse
```

**What's exposed:**
- All core tools (Bash, Read, Edit, Write, Grep, Glob, Agent) as MCP `tools/list` entries
- All integration tools (GitHub, Analyze, TestRunner, Dependencies, WebResearch, FileManager, Email, Calendar, Wiki, Slack, Playwright, Notion, Linear) as MCP tools
- `wiki/pages/*.md` as MCP Resources at `wiki://pages/{slug}`

**§5 boundary at MCP:** Every tool call passes through the same `escalation-boundary.py` hook as all other Praxis tool calls. Out-of-workspace writes, network egress to unlisted domains, and other §5 violations are blocked before the tool implementation runs.

**Transport:** HTTP/SSE. Compatible with Claude Desktop and any remote MCP client. Port configurable via `PRAXIS_MCP_PORT` (default 8765).

## Phase T — Telemetry

Every tool call in `ClaudeCodeRuntime` and `OpenAIBaseRuntime` is logged with:
- Tool name, latency (ms), hook result (allowed/blocked), caller runtime
- Events written to `.praxis/logs/telemetry.jsonl` (append-only JSONL, ring buffer of 1000 events in memory)

**Prometheus metrics** are available on the MCP server at `GET /metrics`:

```
praxis_tool_calls_total              # total tool calls
praxis_hook_blocks_total             # §5 blocks
praxis_circuit_breaker_trips_total   # cost breaker trips
praxis_tool_latency_seconds          # summary (p50/p95/p99)
```

Access: `http://127.0.0.1:8765/metrics` (when `--mcp` is running). Falls back gracefully if telemetry is unavailable.

## Phase X — External Integrations

### Browser automation (Playwright)

```bash
pip install praxis[playwright]
playwright install chromium

# Add target domain to allowlist
export PRAXIS_ALLOWED_DOMAINS=example.com

# Playwright fetch and screenshot are available as Praxis tools
python -m praxis "fetch https://example.com and summarize"
```

Playwright runs in an **isolated subprocess** — no local browser profile, no stored session cookies, no credentials exposed to the browser process.

### Notion + Linear (write-escalate pattern)

```bash
# Set credentials
export PRAXIS_NOTION_TOKEN=secret_...
export PRAXIS_LINEAR_API_KEY=lin_api_...

# Add API domains
export PRAXIS_ALLOWED_DOMAINS=api.notion.com,api.linear.app

# Read operations happen autonomously
python -m praxis "list my Linear issues"

# Write operations are STAGED — never executed autonomously
python -m praxis "create a Linear issue for the Playwright tests"
# → Staged to .praxis/staging/external_actions.jsonl

# Review and approve staged actions
python -m praxis --approve

# Show all pending staged items without interactive prompts
python -m praxis --list-staged
```

Write actions (create_page, create_issue, update_issue, etc.) follow the same **read-safe / write-escalate** pattern as email and calendar: they are staged to `.praxis/staging/external_actions.jsonl` for human review, never executed autonomously.

### Cost circuit breaker

```bash
# Cap per-session estimated API cost (default $2.00)
export PRAXIS_MAX_SESSION_COST=5.00

# On breach: dumps trace to .praxis/logs/cost-circuit-break-{timestamp}.json and exits 3
```

## Scheduled Triggers (Option I)

Make Praxis autonomous without manual session kicks. The daemon automatically runs a cron-style scheduler in a background thread.

```bash
# Install scheduler dep
pip install praxis[scheduler]

# Add a schedule (cron syntax)
python -m praxis --schedule-add 'morning-briefing' '0 7 * * *' 'wiki query: what are my priorities today?'
python -m praxis --schedule-add 'linear-sync' '0 9-18 * * 1-5' 'linear list_issues: assigned to me'
python -m praxis --schedule-add 'weekly-lint' '0 9 * * 0' 'wiki lint: report stale facts'

# Manage schedules
python -m praxis --schedule-list
python -m praxis --schedule-enable  <id>
python -m praxis --schedule-disable <id>
python -m praxis --schedule-remove  <id>

# Daemon auto-starts the scheduler (no extra flags needed)
python -m praxis --daemon
```

Scheduler state: `.praxis/schedule/tasks.json`. Log: `.praxis/logs/scheduler.log`.
Poll interval: `PRAXIS_SCHEDULER_POLL_INTERVAL=60` (seconds, default 60).

Dedup: if a scheduled task's prompt is already pending or running in the queue, it is skipped rather than queued a second time.

## Wiki → Notion / Linear Sync (Option J)

Praxis's bitemporal wiki can be synced to Notion pages or Linear issues. All
external writes are **staged** — they never execute automatically, only after
`python -m praxis --approve`.

### Export and sync

```bash
# Stage wiki page 'alice' as a Notion page under parent page/database ID
python -m praxis --wiki-sync-notion alice your-notion-parent-id

# Stage wiki page 'alice' as a Linear issue in team 'team-xyz'
python -m praxis --wiki-sync-linear alice team-xyz

# Link wiki page 'alice' to existing Linear issue LIN-42 (local frontmatter update)
python -m praxis --wiki-link-issue alice LIN-42

# Pull current status of all Linear-linked wiki pages and stage update proposals
python -m praxis --queue  # or start interactively and call pull_linear_updates
```

### Reverse sync

`pull_linear_updates()` reads the Linear API for all wiki pages with a
`linear_issue_id` frontmatter field and stages update proposals to
`.praxis/staging/wiki_updates.jsonl`. Review them with `--list-staged`.

### Required setup

- Notion sync: `PRAXIS_NOTION_TOKEN` + `api.notion.com` in `PRAXIS_ALLOWED_DOMAINS`
- Linear sync: `PRAXIS_LINEAR_API_KEY` + `api.linear.app` in `PRAXIS_ALLOWED_DOMAINS`

### §5 compliance

- `sync_to_notion` and `sync_to_linear` never call external APIs — they stage to
  `.praxis/staging/external_actions.jsonl`. Run `python -m praxis --approve` to review.
- `link_linear_issue` writes only to `wiki/pages/` (inside `WORKSPACE_ROOT`).
- `pull_linear_updates` reads the Linear API (already-allowlisted domain) and
  stages results locally — no autonomous external writes.

## Queue and Daemon

Praxis can run unattended, processing tasks from a queue:

```bash
# Add tasks to .praxis/queue/tasks.jsonl, then:
python -m praxis --queue            # process queue, exit when empty
python -m praxis --daemon           # run in background
python -m praxis --status           # check daemon + queue stats
python -m praxis --stop             # graceful shutdown
```

Multi-stage tasks are checkpointed — if the daemon crashes, it resumes from the last completed stage.

## Running Tests

```bash
pip install praxis[dev]
python -m pytest tests/ -v          # 767 tests, all mocked
```

No real API calls. All tests use a mock client from `tests/conftest.py`.

## Contributing

1. Fork and create a feature branch
2. Write tests for new functionality (no real API calls — use `FakeClient`)
3. Run the full test suite: `python -m pytest tests/ -v`
4. Ensure the §5 hook still passes for all tool calls
5. Open a PR against `main`

The `§5 escalation boundary` is the one thing you should never bypass, weaken, or work around — in code or in tests.

## License

MIT
