# Praxis — Project Conventions

## What is this

Praxis is a minimal Python orchestrator for an agentic OS built on the Claude API. The markdown spec (`praxis-system-prompt.md`) defines the system; the orchestrator makes it executable.

## Repository layout

```
praxis-system-prompt.md          # The spec (§0–§11)
convergence.yaml                 # Multi-runtime routing config (optional — Phase D)
praxis/                          # Python orchestrator package
  orchestrator.py                # Orchestrator: tool dispatch + §5 hook (delegates API to Runtime)
  config.py                      # WORKSPACE_ROOT, MEMORY_ROOT from env vars
  convergence.py                 # Parses convergence.yaml — multi-runtime routing (Phase D)
  subagents.py                   # Parses .claude/agents/*.md into SubagentDef
  hooks.py                       # Runs escalation-boundary.py as PreToolUse check
  tools.py                       # Tool schemas + implementations (Bash, Read, Edit, Write, Grep, Glob, Agent)
  integrations/                  # Workstation integrations (Phase 4)
    __init__.py                  #   Aggregates INTEGRATION_SCHEMAS + INTEGRATION_IMPLEMENTATIONS
    github.py                    #   GitHub via `gh` CLI — PRs, issues, diffs
    codebase.py                  #   Coverage, complexity, lint via subprocess
    testrunner.py                #   pytest runner with parsed output
    dependencies.py              #   pip outdated + pip-audit vulnerability check
  queue.py                       # TaskQueue — CRUD on .praxis/queue/tasks.jsonl (Phase J)
  checkpoint.py                  # CheckpointStore — multi-stage task resumption (Phase J)
  queue_runner.py                # Queue processing loop — polls tasks, runs through orchestrator (Phase J)
  daemon.py                      # Daemon start/stop/status for background operation (Phase J)
  __main__.py                    # python -m praxis entrypoint — interactive, --queue, --daemon, --stop, --status
  runtime/                       # Provider abstraction layer (Phase A+C+D+I)
    __init__.py                  #   exports Runtime, ClaudeCodeRuntime, LocalRuntime, OpenAICloudRuntime
    base.py                      #   Abstract Runtime interface (4 methods)
    openai_base.py               #   OpenAIBaseRuntime — shared OpenAI-compatible logic
    claude_code.py               #   ClaudeCodeRuntime — Anthropic Messages API (hardened error handling)
    local.py                     #   LocalRuntime — local servers (Ollama/vLLM/llama.cpp)
    cloud.py                     #   OpenAICloudRuntime — cloud OpenAI-compatible APIs (OpenAI/Gemini/OpenRouter/Groq)
.claude/agents/                  # Subagent definitions (builder, planner, scout, scribe, verifier)
.claude/hooks/escalation-boundary.py  # §5 hook — blocks out-of-workspace writes, network egress
.claude/settings.json            # Claude Code hook wiring
tests/                           # pytest suite (261 tests, all mocked — no real API calls)
.praxis/memory/                  # Durable memory across sessions
.praxis/queue/                   # Task queue directory (Phase J)
  tasks.jsonl                    #   One JSON task object per line
  results/                       #   Human-readable result files per task
  checkpoints/                   #   Multi-stage task checkpoints
```

## Running

```bash
# Set workspace root (defaults to cwd if unset)
export PRAXIS_WORKSPACE_ROOT=/path/to/repo
export PRAXIS_MEMORY_ROOT=$PRAXIS_WORKSPACE_ROOT/.praxis/memory

# Auth: subscription OAuth (preferred) or API key (fallback)
export CLAUDE_CODE_OAUTH_TOKEN=your-oauth-token   # subscription, flat cost
# OR
export ANTHROPIC_API_KEY=sk-ant-...               # pay-per-token fallback

# Run orchestrator (logs active auth/runtime path to stderr)
python -m praxis "your message"

# Use a local model instead (Ollama, vLLM, llama.cpp)
export PRAXIS_RUNTIME=local                       # select local runtime
export PRAXIS_LOCAL_BASE_URL=http://localhost:11434  # Ollama default
export PRAXIS_LOCAL_MODEL=llama3.1:8b             # any pulled model
pip install praxis[local]                         # installs openai package
python -m praxis "your message"

# Use any cloud OpenAI-compatible API (OpenAI, Gemini, OpenRouter, Groq, etc.)
export PRAXIS_RUNTIME=cloud                       # select cloud runtime
export PRAXIS_CLOUD_API_KEY=sk-...                # API key (required)
export PRAXIS_CLOUD_BASE_URL=https://api.openai.com/v1  # endpoint (default)
export PRAXIS_CLOUD_MODEL=gpt-4o                  # model (default)
pip install praxis[local]                         # installs openai package
python -m praxis "your message"

# Unattended queue mode — process tasks from .praxis/queue/tasks.jsonl
python -m praxis --queue

# Daemon mode — run queue processor in background
python -m praxis --daemon                         # start, writes PID to .praxis/praxis.pid
python -m praxis --status                         # check if running + queue stats
python -m praxis --stop                           # send SIGTERM, clean up PID file

# Queue poll interval (default 2s)
export PRAXIS_QUEUE_POLL_INTERVAL=5

# Run tests
python -m pytest tests/ -v
```

## Key conventions

- **§5 hook is sacred.** Every tool call passes through `escalation-boundary.py` — in both orchestrator and subagent sessions. Never bypass it.
- **Subagent definitions live in `.claude/agents/*.md`** with YAML frontmatter (name, description, tools, model). The orchestrator loads these at startup.
- **No real API calls in tests.** All tests use FakeClient from `tests/conftest.py`.
- **Config from env vars.** `PRAXIS_WORKSPACE_ROOT` and `PRAXIS_MEMORY_ROOT` — restrictive fallback per §0 if unset.
- **Model mapping:** `haiku` → `claude-haiku-4-5-20251001`, `sonnet` → `claude-sonnet-4-6`, `opus` → `claude-opus-4-6`.
- **Auth priority.** `CLAUDE_CODE_OAUTH_TOKEN` first (subscription), `ANTHROPIC_API_KEY` second (pay-per-token). When OAuth is active, `ANTHROPIC_API_KEY` is scrubbed from the environment. Auth path is logged to stderr at startup. Use `ClaudeCodeRuntime.from_env()` to create the runtime.
- **Runtime interface.** `Orchestrator` takes a `Runtime` (not a raw client). Three provider families:
  - `ClaudeCodeRuntime` — Anthropic Messages API (primary tested runtime)
  - `LocalRuntime` — local OpenAI-compatible servers (Ollama, vLLM, llama.cpp)
  - `OpenAICloudRuntime` — cloud OpenAI-compatible APIs (OpenAI, Gemini, OpenRouter, Groq, Together, etc.)
  
  `LocalRuntime` and `OpenAICloudRuntime` share a common base (`OpenAIBaseRuntime` in `openai_base.py`) that implements the full agent loop, tool execution, and context management. To add a new OpenAI-compatible provider, subclass `OpenAIBaseRuntime` and override `from_env()`, `_call_api()`, and optionally `_resolve_model()`.
- **Runtime selection.** `PRAXIS_RUNTIME=claude` (default), `PRAXIS_RUNTIME=local`, or `PRAXIS_RUNTIME=cloud`. Local runtime uses `PRAXIS_LOCAL_*` env vars; cloud runtime uses `PRAXIS_CLOUD_*` env vars. Local replaces Claude model IDs with the configured local model; cloud passes model strings through unchanged.
- **Convergence config.** Optional `convergence.yaml` at workspace root enables per-subagent runtime routing (e.g., scout → cloud, builder → claude). Env var `PRAXIS_RUNTIME` overrides the file's default. If no file exists, behavior is identical to env-var-only mode. See `praxis/convergence.py`.
- **Rate limit retry.** `ClaudeCodeRuntime._create_with_retry()` and `OpenAICloudRuntime._call_api()` both use exponential backoff on 429: 5s → 10s → 20s (3 retries, capped at 60s). Clean `SystemExit` after exhaustion. Each retry logged to stderr.
- **Context window management.** `manage_context()` compacts messages when they exceed 40: keeps first message + last 10 verbatim, summarizes older exchanges into a compact header. Prevents token limit crashes on long runs. All three runtimes implement this (OpenAI-compatible runtimes share the implementation via `OpenAIBaseRuntime`).
- **Error handling.** All import errors, auth failures, connection errors, and API errors produce clean `[praxis] fatal:` messages — no raw tracebacks reach the user. Top-level handler in `__main__.py` catches anything a runtime misses.
- **Token propagation.** All subprocesses (Bash tool, Grep tool, hooks) receive an explicit `env=` dict that includes auth tokens. Never rely on implicit inheritance. Subprocess output is filtered through `_redact_secrets()` before returning to the model — tokens never leak into tool results (§5.8).
- **Task queue.** `.praxis/queue/tasks.jsonl` — one JSON task per line with `id`, `prompt`, `priority`, `status` (pending/running/done/failed), timestamps, `result`/`error`, and optional `stages` list. `TaskQueue` handles CRUD. On queue startup, any "running" tasks are marked "failed" (crash recovery). Priority: lowest number first, then oldest.
- **Checkpoints.** Multi-stage tasks (those with `stages` list) get checkpointed to `.praxis/queue/checkpoints/{task-id}.json` after each stage completes. On restart, incomplete staged tasks resume from the last completed stage instead of restarting from scratch. Checkpoint is deleted after all stages complete.
- **Queue runner.** `run_queue_loop()` polls `tasks.jsonl` every 2s (configurable via `PRAXIS_QUEUE_POLL_INTERVAL`). Handles SIGTERM gracefully — finishes current task stage, then exits. Atomic tasks run as a single `orch.run()` call; staged tasks run each stage as a separate `orch.run()` call with checkpoint between.
- **Daemon.** `python -m praxis --daemon` forks to background via `os.fork()`, writes PID to `.praxis/praxis.pid`, logs to `.praxis/logs/praxis.log`. `--stop` sends SIGTERM. `--status` reports running state + queue stats. No log rotation (out of scope).
- **Workstation integrations.** Four subprocess-backed tools in `praxis/integrations/`:
  - `GitHub` — wraps `gh` CLI. Actions: `pr_list`, `pr_view`, `issue_list`, `issue_view`, `pr_diff`. Requires `gh` installed and authenticated. Auth via `GITHUB_TOKEN` env var (read by `gh` automatically).
  - `Analyze` — wraps `coverage`, `radon`, `pylint`. Actions: `coverage`, `complexity`, `lint`. Each tool checked independently — clear error if not installed.
  - `TestRunner` — wraps `pytest`. Actions: `run` (with optional path/marker/keyword), `run_failed` (re-run last failures).
  - `Dependencies` — wraps `pip` and `pip-audit`. Actions: `outdated` (JSON list of outdated packages), `audit` (vulnerability scan).
  
  All integrations use `subprocess.run` with `_subprocess_env()` for token propagation and `_redact_secrets()` for output filtering (including `GITHUB_TOKEN`). Each fails loudly with install instructions if the required CLI tool is missing. Integration tools are registered in the orchestrator alongside core tools — subagents can call them if their tool list includes the tool name. No credentials stored in code or logs.
