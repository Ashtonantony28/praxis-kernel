# Phase D-1 Plan: Harden Failure Paths

**Date:** 2026-05-25
**Prerequisite for:** D-2 (convergence.yaml), D-3 (live integration test)

---

## Problem

Workload testing found 5 HIGH-severity failure paths where raw SDK
tracebacks surface to the user instead of clean error messages.

## Strategy

Three layers of defense, consistent across both runtimes:

### Layer 1: Import guards

**ClaudeCodeRuntime.from_env()** — wrap `import anthropic` in try/except
matching the pattern already used by LocalRuntime:

```python
try:
    import anthropic
except ImportError:
    raise SystemExit(
        "[praxis] fatal: 'anthropic' package required for claude runtime.\n"
        "Install it: pip install anthropic"
    )
```

### Layer 2: API call error handling in run_loop()

Both runtimes wrap the API call in a try/except that catches provider-specific
errors and surfaces a one-line user message. Pattern:

```python
try:
    response = self.client.<api_call>(...)
except (<ConnectionError>, <AuthError>, <APIError>) as exc:
    raise SystemExit(f"[praxis] fatal: {_friendly_message(exc)}")
```

**ClaudeCodeRuntime** catches:
- `anthropic.AuthenticationError` — bad credentials
- `anthropic.APIConnectionError` — network/DNS failure
- `anthropic.RateLimitError` — rate limited
- `anthropic.APIStatusError` — catch-all for other API errors

**LocalRuntime** catches:
- `openai.APIConnectionError` — connection refused (Ollama not running)
- `openai.AuthenticationError` — (unlikely but possible with vLLM)
- `openai.APIStatusError` — catch-all for HTTP errors

### Layer 3: Top-level handler in __main__.py

Wrap `main()` body in a try/except that catches:
- `SystemExit` — re-raise (already user-friendly)
- `KeyboardInterrupt` — clean exit
- `Exception` — one-line error with hint to use --debug (future)

This ensures nothing leaks even if a runtime misses an edge case.

### Minor hardening (MEDIUM/LOW issues)

- **local.py line 91:** Guard `response.choices[0]` with empty-check.
- **local.py line 160:** Wrap `json.loads()` in try/except for malformed tool args.
- **orchestrator.py line 29:** Not in scope (system prompt is a hard requirement;
  FileNotFoundError message is clear enough).

## Files changed

1. `praxis/runtime/claude_code.py` — import guard + run_loop error handling
2. `praxis/runtime/local.py` — run_loop error handling + JSON/choices guards
3. `praxis/__main__.py` — top-level error handler
4. `tests/test_runtime.py` — test import guard, test API error → clean message
5. `tests/test_local_runtime.py` — test connection error → clean message

## What stays the same

- `runtime/base.py` — unchanged
- `orchestrator.py` — unchanged (tool execution already has try/except)
- `tools.py`, `hooks.py`, `config.py`, `subagents.py` — unchanged
- All 69 existing tests — unchanged and still passing

## Error message format

All user-facing errors follow the existing pattern:
```
[praxis] fatal: <one-line description>
```

No raw tracebacks. No stack traces. No SDK internals.
