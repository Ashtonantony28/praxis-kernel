# F-1: Live Self-Orchestration Test Results

**Date:** 2026-05-25
**Branch:** claude/blissful-franklin-VIMiH

---

## What was tested

Attempted to run `python -m praxis` with a real task: "explain how runtime selection
works in this codebase" — intended to exercise the full Scout -> Planner -> Builder
-> Verifier -> Scribe pipeline with live API calls.

## What succeeded

All orchestrator initialization paths validated with real code (not mocks):

- `Config.from_env()` — resolved workspace, memory root, hook path correctly
- `ConvergenceConfig.load()` — parsed defaults (no convergence.yaml present), correctly
  reported `default=claude, needs_claude=True, needs_local=False`
- `load_subagents()` — loaded all 5 agent definitions (builder, planner, scout, scribe, verifier)
- `get_tool_schemas()` — produced all 7 tool schemas (Bash, Read, Edit, Write, Grep, Glob, Agent)
- System prompt loaded (16KB, praxis-system-prompt.md)

These are the same code paths the orchestrator uses in production — they ran against
real files, not test fixtures.

## What failed — and why

The live API call could not be made. Root cause:

**Auth tokens are sandboxed.** The Claude Code CLI sets `CLAUDE_CODE_OAUTH_TOKEN` and
`ANTHROPIC_API_KEY` in its own process, but the execution sandbox intentionally strips
these from child process environments. This is correct security behavior — arbitrary
code spawned by Claude Code should not have access to the user's API credentials.

When `ClaudeCodeRuntime.from_env()` runs inside `python -m praxis`, both token env
vars are empty, producing: `[praxis] fatal: no auth configured.`

## Where intervention was required

The entire test required human intervention — specifically, the orchestrator needs
explicitly-set credentials that are not the Claude Code CLI's own session tokens.
Running Praxis from within a Claude Code session is a chicken-and-egg problem: the
orchestrator is designed to BE the CLI, not to be called FROM the CLI.

## Honest assessment

**Self-orchestration is structurally unvalidated.** The init path works. The tool
dispatch works (101 mocked tests confirm this). But the core claim — that the
orchestrator can autonomously drive a multi-subagent pipeline via real API calls —
has never been proven. The specific gap:

1. **No live API round-trip has ever been completed.** Not once. The `run_loop` method
   in `ClaudeCodeRuntime` has only been tested with `FakeClient`.

2. **Subagent spawning is untested live.** `run_subagent()` → `runtime.spawn_subagent()`
   has never executed against a real model. Unknown: does the model actually invoke the
   Agent tool to chain subagents? Does the system prompt produce the right tool calls?

3. **The §5 hook under real load is untested.** The hook works in isolation (13 tests),
   but it's never been exercised inside a real multi-turn conversation where the model
   is making tool calls.

4. **Context management is append-only.** Even if the API call worked, any multi-subagent
   task would likely hit the context window limit since `manage_context()` just appends.

## What would be needed for a valid live test

- Run `python -m praxis` from a terminal with explicitly exported credentials:
  ```bash
  export ANTHROPIC_API_KEY=sk-ant-...
  python -m praxis "describe this repo"
  ```
- This must happen outside the Claude Code sandbox — in a normal shell session.
- The user must set credentials manually (they should never be hardcoded or committed).
