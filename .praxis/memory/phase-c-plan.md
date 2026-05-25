# Phase C Plan — LocalRuntime for open-source models

**Date:** 2026-05-25
**Goal:** Add a second Runtime provider that talks to OpenAI-compatible
endpoints (Ollama first, vLLM / llama.cpp later).

---

## 1. How `run_loop` reimplements the agent loop

Ollama exposes `/v1/chat/completions` (OpenAI-compatible) — not an agent
SDK. LocalRuntime must own the full loop:

```
messages = [system, user_message]
loop (max_turns):
    response = client.chat.completions.create(model, messages, tools)
    append assistant message to history
    if finish_reason == "stop" or no tool_calls → return content
    execute each tool_call via tool_executor callback
    append tool results (role: "tool") to history
```

Key differences from ClaudeCodeRuntime:
- System prompt goes in `messages[0]` as `{"role": "system", ...}`,
  not a separate `system=` parameter.
- Tool calls arrive as `message.tool_calls` list with JSON-string
  `arguments`, not Anthropic content blocks with dict `input`.
- Tool results are separate messages with `role: "tool"` and
  `tool_call_id`, not a single user message with `tool_result` blocks.
- Tool schemas need conversion: Anthropic `input_schema` → OpenAI
  `parameters`, wrapped in `{"type": "function", "function": {...}}`.

## 2. How `spawn_subagent` works

Delegates to `run_loop` — same as ClaudeCodeRuntime. No native subagent
isolation exists in OpenAI-compatible APIs. Each subagent call is just a
fresh conversation with a different system prompt and tool set.

## 3. How `execute_tool` maps tool calls

OpenAI tool_call format:
```python
tool_call.id           → "call_abc123"
tool_call.function.name → "Bash"
tool_call.function.arguments → '{"command": "ls"}'  # JSON string!
```

Steps:
1. Iterate `response_content` (the tool_calls list)
2. Parse `arguments` from JSON string to dict
3. Call `tool_executor(name, args)` — same callback as ClaudeCodeRuntime
4. Return `{"role": "tool", "tool_call_id": id, "content": output}`

The `tool_executor` callback comes from the Orchestrator and already
handles §5 hook enforcement and Agent-as-subagent routing. No changes
needed there.

## 4. Which subagent roles are safe for local models (v1)

**Safe:** Scout (read-only search), Scribe (memory writes)
**Unsafe:** Builder (multi-step edits), Planner (complex reasoning),
Verifier (judgment calls)

Rationale: smaller local models (7B–13B) can follow simple instructions
but cannot reliably orchestrate multi-tool workflows. v1 does not
enforce this at the runtime level — it's a usage guideline. Future
versions may add a role whitelist in convergence.yaml.

## 5. Model resolution

Claude model IDs (`claude-sonnet-4-6`) won't exist on a local server.
Strategy: LocalRuntime stores a `default_model` (from env var
`PRAXIS_LOCAL_MODEL`). All `claude-*` model IDs are replaced with
`default_model`. Non-Claude IDs pass through unchanged.

## 6. Auth and configuration

| Env var                 | Default                    | Purpose                |
|------------------------|----------------------------|------------------------|
| `PRAXIS_RUNTIME`       | `claude`                   | Runtime selection      |
| `PRAXIS_LOCAL_BASE_URL`| `http://localhost:11434`   | Ollama server URL      |
| `PRAXIS_LOCAL_MODEL`   | `llama3.1:8b`              | Default local model    |

`from_env()` creates an `openai.OpenAI` client with
`base_url="{base_url}/v1"` and `api_key="ollama"` (dummy — Ollama
doesn't require auth).

## 7. What this phase does NOT do

- No `convergence.yaml` config file (future: multi-runtime routing)
- No per-subagent runtime selection (all calls go through one runtime)
- No streaming support
- No model capability checking
- No retry/fallback to Claude on local model failure
