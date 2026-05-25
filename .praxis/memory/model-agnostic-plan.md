# Model-Agnostic Cloud Runtime — Design Plan

**Date:** 2026-05-25
**Goal:** Single `OpenAICloudRuntime` that works against any OpenAI-compatible cloud endpoint (OpenAI, Gemini compatibility layer, OpenRouter, Groq, Together, etc.)

---

## Architecture

```
Runtime (ABC — base.py)
  ├── ClaudeCodeRuntime (claude_code.py) — Anthropic Messages API
  └── OpenAIBaseRuntime (openai_base.py) — shared OpenAI-compatible logic
        ├── LocalRuntime (local.py) — local servers (Ollama/vLLM/llama.cpp)
        └── OpenAICloudRuntime (cloud.py) — cloud OpenAI-compatible APIs
```

### Shared logic in OpenAIBaseRuntime
- `run_loop()` — full agent loop with OpenAI chat completions
- `spawn_subagent()` — delegates to run_loop
- `execute_tool()` — parse OpenAI tool_calls, invoke executor
- `manage_context()` — append + compact
- `_compact_context()` — sliding window
- `_summarize_message()` — one-line summary
- `_convert_tools()` — Anthropic schema → OpenAI function format
- `_call_api()` — abstract hook for the actual API call (subclass overrides for retry/error handling)

### LocalRuntime specifics (stays in local.py)
- `from_env()` — reads PRAXIS_LOCAL_BASE_URL/MODEL, appends /v1, uses "ollama" key
- `_resolve_model()` — replaces claude-* with default local model
- `_call_api()` — simple call, connection-oriented error messages

### OpenAICloudRuntime specifics (new cloud.py)
- `from_env()` — reads PRAXIS_CLOUD_BASE_URL, PRAXIS_CLOUD_MODEL, PRAXIS_CLOUD_API_KEY
- `_resolve_model()` — no-op (pass through)
- `_call_api()` — call with exponential backoff retry on 429 (same pattern as ClaudeCodeRuntime)
- Rate limit constants: 3 retries, 5/10/20s delays, cap 60s

---

## Env vars

| Var | Purpose | Default |
|-----|---------|---------|
| `PRAXIS_CLOUD_API_KEY` | API key for the cloud endpoint | (required) |
| `PRAXIS_CLOUD_BASE_URL` | Full base URL | `https://api.openai.com/v1` |
| `PRAXIS_CLOUD_MODEL` | Default model | `gpt-4o` |

---

## Convergence routing

- Add `"cloud"` to `VALID_RUNTIMES` in convergence.py
- Add `needs_cloud()` method to ConvergenceConfig
- Add cloud config fields: `cloud_base_url`, `cloud_model`
- In `__main__.py`, create `OpenAICloudRuntime` when `needs_cloud()` is True

Example convergence.yaml:
```yaml
runtimes:
  default: claude
  overrides:
    scout: cloud     # use GPT-4o for cheap scouting
    builder: claude  # keep builder on Claude

cloud:
  base_url: https://api.openai.com/v1
  model: gpt-4o
```

---

## Key decisions

1. **No model remapping for cloud** — user explicitly chooses the model string for their endpoint
2. **Retry on cloud** — cloud APIs rate-limit; reuse the same exponential backoff pattern from ClaudeCodeRuntime
3. **API key required** — unlike local (dummy key), cloud exits if PRAXIS_CLOUD_API_KEY is unset
4. **Base URL includes path** — cloud URLs like `https://api.openai.com/v1` are used as-is (no /v1 appended)
5. **openai package shared** — both local and cloud use the `openai` Python package; dependency stays in `[local]` extra (rename to `[openai]` or keep as-is since it's the same package)

---

## Files to create/modify

| File | Action |
|------|--------|
| `praxis/runtime/openai_base.py` | NEW — shared base class |
| `praxis/runtime/cloud.py` | NEW — OpenAICloudRuntime |
| `praxis/runtime/local.py` | MODIFY — inherit from OpenAIBaseRuntime |
| `praxis/runtime/__init__.py` | MODIFY — export new classes |
| `praxis/convergence.py` | MODIFY — add "cloud" to valid runtimes |
| `praxis/__main__.py` | MODIFY — create cloud runtime when needed |
| `tests/test_cloud_runtime.py` | NEW — tests for OpenAICloudRuntime |
| `tests/test_local_runtime.py` | VERIFY — must still pass after refactor |
| `pyproject.toml` | MODIFY — rename extra or add alias |
