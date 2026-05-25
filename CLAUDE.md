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
  __main__.py                    # python -m praxis entrypoint (convergence config + runtime creation)
  runtime/                       # Provider abstraction layer (Phase A+C+D)
    __init__.py                  #   exports Runtime, ClaudeCodeRuntime, LocalRuntime
    base.py                      #   Abstract Runtime interface (4 methods)
    claude_code.py               #   ClaudeCodeRuntime — Anthropic Messages API (hardened error handling)
    local.py                     #   LocalRuntime — OpenAI-compatible endpoint (hardened error handling)
.claude/agents/                  # Subagent definitions (builder, planner, scout, scribe, verifier)
.claude/hooks/escalation-boundary.py  # §5 hook — blocks out-of-workspace writes, network egress
.claude/settings.json            # Claude Code hook wiring
tests/                           # pytest suite (126 tests, all mocked — no real API calls)
.praxis/memory/                  # Durable memory across sessions
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
- **Runtime interface.** `Orchestrator` takes a `Runtime` (not a raw client). `ClaudeCodeRuntime` wraps the Anthropic SDK. `LocalRuntime` wraps any OpenAI-compatible endpoint. To add a new provider, subclass `Runtime` in `praxis/runtime/` and implement 4 methods: `run_loop`, `spawn_subagent`, `execute_tool`, `manage_context`.
- **Runtime selection.** `PRAXIS_RUNTIME=claude` (default) or `PRAXIS_RUNTIME=local`. Local runtime uses `PRAXIS_LOCAL_BASE_URL` and `PRAXIS_LOCAL_MODEL` env vars. Claude model IDs are automatically replaced with the configured local model.
- **Convergence config.** Optional `convergence.yaml` at workspace root enables per-subagent runtime routing (e.g., scout → local, builder → claude). Env var `PRAXIS_RUNTIME` overrides the file's default. If no file exists, behavior is identical to env-var-only mode. See `praxis/convergence.py`.
- **Rate limit retry.** `ClaudeCodeRuntime._create_with_retry()` wraps `messages.create()` with exponential backoff on 429: 5s → 10s → 20s (3 retries, capped at 60s). Clean `SystemExit` after exhaustion. Each retry logged to stderr.
- **Context window management.** `manage_context()` compacts messages when they exceed 40: keeps first message + last 10 verbatim, summarizes older exchanges into a compact header. Prevents token limit crashes on long runs. Both runtimes implement this.
- **Error handling.** All import errors, auth failures, connection errors, and API errors produce clean `[praxis] fatal:` messages — no raw tracebacks reach the user. Top-level handler in `__main__.py` catches anything a runtime misses.
- **Token propagation.** All subprocesses (Bash tool, Grep tool, hooks) receive an explicit `env=` dict that includes auth tokens. Never rely on implicit inheritance. Subprocess output is filtered through `_redact_secrets()` before returning to the model — tokens never leak into tool results (§5.8).
