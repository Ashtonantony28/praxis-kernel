"""OpenAIBaseRuntime — shared logic for any OpenAI-compatible endpoint.

Both LocalRuntime (Ollama/vLLM) and OpenAICloudRuntime (OpenAI/Gemini/
OpenRouter/Groq) inherit from this class. It implements the full agent
loop, tool execution, and context management against the OpenAI
chat completions API. Subclasses override _call_api() for provider-
specific error handling and _resolve_model() for model ID mapping.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .base import Runtime, ToolExecutor
from .cost import CostCircuitBreaker

if TYPE_CHECKING:
    from praxis.modes.base import Mode

MAX_TURNS = 50
MAX_CONTEXT_MESSAGES = 40   # trigger compaction above this
CONTEXT_KEEP_RECENT = 10    # keep last N messages verbatim


class OpenAIBaseRuntime(Runtime):
    """Base runtime for any endpoint that speaks the OpenAI chat completions API.

    Subclasses must implement:
      from_env()        — class method, create from environment variables
      _call_api()       — single API call with provider-specific error handling
      _resolve_model()  — map model IDs (e.g., replace claude-* for local)
    """

    def __init__(
        self,
        client: Any,
        *,
        default_model: str,
        base_url: str,
    ) -> None:
        self.client = client
        self.default_model = default_model
        self.base_url = base_url
        self._cost_breaker = CostCircuitBreaker.from_env()
        self._current_mode: "Mode | None" = None

    def run_loop(
        self,
        *,
        model: str,
        system: str,
        user_message: str,
        tool_schemas: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        max_turns: int = MAX_TURNS,
        mode: "Mode | None" = None,
    ) -> str:
        # Apply mode: filter tools and inject prompt suffix
        self._current_mode = mode
        effective_system = system
        effective_tool_schemas = tool_schemas
        if mode is not None:
            effective_tool_schemas = self.apply_mode(mode, tool_schemas)
            if mode.prompt_suffix:
                effective_system = system + mode.prompt_suffix

        resolved_model = self._resolve_model(model)
        openai_tools = self._convert_tools(effective_tool_schemas)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": user_message},
        ]

        last_content = ""
        for _ in range(max_turns):
            kwargs: dict[str, Any] = {
                "model": resolved_model,
                "messages": messages,
            }
            if openai_tools:
                kwargs["tools"] = openai_tools

            response = self._call_api(**kwargs)

            # Record estimated cost; trip breaker if session cap exceeded
            usage = getattr(response, "usage", None)
            if usage is not None:
                try:
                    self._cost_breaker.record_call(
                        resolved_model,
                        int(getattr(usage, "prompt_tokens", 0) or 0),
                        int(getattr(usage, "completion_tokens", 0) or 0),
                    )
                except (TypeError, ValueError):
                    pass

            if not response.choices:
                raise SystemExit(
                    "[praxis] fatal: model server returned an empty response."
                )
            choice = response.choices[0]
            msg = choice.message

            # Build assistant message for conversation history
            assistant_entry: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
            }
            if msg.tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages = self.manage_context(messages, "assistant", assistant_entry)

            last_content = msg.content or ""

            if choice.finish_reason == "stop" or not msg.tool_calls:
                return last_content

            # Execute tool calls and append results
            tool_results = self.execute_tool(msg.tool_calls, tool_executor)
            for result in tool_results:
                messages = self.manage_context(messages, "tool", result)

        return last_content

    def spawn_subagent(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        tool_schemas: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        max_turns: int = MAX_TURNS,
        mode: "Mode | None" = None,
    ) -> str:
        return self.run_loop(
            model=model,
            system=system,
            user_message=prompt,
            tool_schemas=tool_schemas,
            tool_executor=tool_executor,
            max_turns=max_turns,
            mode=mode,
        )

    def execute_tool(
        self,
        response_content: list[Any],
        tool_executor: ToolExecutor,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for tc in response_content:
            # Support both object (SDK) and dict (test) forms
            if isinstance(tc, dict):
                tc_id = tc["id"]
                name = tc["function"]["name"]
                args_raw = tc["function"]["arguments"]
            else:
                tc_id = tc.id
                name = tc.function.name
                args_raw = tc.function.arguments

            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                output = f"Error: malformed tool arguments from model: {args_raw!r}"
                results.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": output,
                    }
                )
                continue

            # Layer 1 enforcement — raises EnforcementError if blocked
            try:
                from .enforcement import enforce, EnforcementError
                enforce(name, args, mode=self._current_mode)
            except EnforcementError as _e:
                output = f"BLOCKED by §5 escalation boundary: {_e}"
                results.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": output,
                })
                continue

            import time as _time
            _t0 = _time.monotonic()
            output = tool_executor(name, args)
            _latency_ms = (_time.monotonic() - _t0) * 1000

            # Record telemetry — never let this break the main loop
            try:
                from datetime import datetime, timezone
                from .telemetry import TelemetryEvent, TelemetryStore
                _hook_result = "blocked" if str(output).startswith("BLOCKED by §5") else "allowed"
                TelemetryStore.get_global().record(TelemetryEvent(
                    tool_name=name,
                    latency_ms=_latency_ms,
                    hook_result=_hook_result,
                    caller="OpenAIBaseRuntime",
                    token_count=None,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ))
            except Exception:
                pass

            results.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": output,
                }
            )
        return results

    def manage_context(
        self,
        messages: list[dict[str, Any]],
        role: str,
        content: Any,
    ) -> list[dict[str, Any]]:
        if isinstance(content, dict) and "role" in content:
            # Full message dict (assistant with tool_calls, or tool result)
            messages.append(content)
        else:
            messages.append({"role": role, "content": content})
        if len(messages) > MAX_CONTEXT_MESSAGES:
            messages = self._compact_context(messages)
        return messages

    def _compact_context(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Sliding window — summarize older exchanges, keep recent ones.

        Keeps messages[0] (system) and messages[1] (user prompt) plus
        last CONTEXT_KEEP_RECENT messages. Middle is compressed into a
        summary appended to the user prompt. Split aligned to assistant
        boundary for valid message ordering.
        """
        split = len(messages) - CONTEXT_KEEP_RECENT
        while split < len(messages) and messages[split].get("role") != "assistant":
            split += 1
        if split >= len(messages) - 2:
            return messages

        older = messages[2:split]  # skip system + user prompt
        recent = messages[split:]

        summary_lines = ["[Compacted context — older exchanges summarized]"]
        for msg in older:
            line = self._summarize_message(msg)
            if line:
                summary_lines.append(line)
        summary_text = "\n".join(summary_lines)

        first_system = messages[0]
        first_user = dict(messages[1])
        original = first_user["content"]
        if isinstance(original, str):
            first_user["content"] = f"{original}\n\n{summary_text}"
        return [first_system, first_user] + recent

    @staticmethod
    def _summarize_message(msg: dict[str, Any]) -> str | None:
        """One-line summary of a single message for context compaction."""
        role = msg.get("role", "?")
        content = msg.get("content", "")

        if role == "assistant":
            parts: list[str] = []
            if content:
                parts.append(str(content)[:80])
            for tc in msg.get("tool_calls", []):
                name = (
                    tc.get("function", {}).get("name", "?")
                    if isinstance(tc, dict)
                    else tc.function.name
                )
                parts.append(f"called {name}")
            return "  Assistant: " + "; ".join(parts) if parts else None

        if role == "tool":
            return f"  Tool result: {str(content)[:60]}"

        if isinstance(content, str):
            return f"  {role.capitalize()}: {content[:100]}"
        return None

    def _resolve_model(self, model: str) -> str:
        """Map model IDs. Default: pass through unchanged.

        Subclasses override (e.g., LocalRuntime replaces claude-* IDs).
        """
        return model

    def _call_api(self, **kwargs: Any) -> Any:
        """Make a single chat completions API call.

        Subclasses override to add provider-specific error handling
        and retry logic.
        """
        return self.client.chat.completions.create(**kwargs)

    @staticmethod
    def _convert_tools(
        tool_schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert Anthropic tool schemas to OpenAI function calling format.

        Anthropic: {"name": ..., "description": ..., "input_schema": {...}}
        OpenAI:    {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
        """
        openai_tools: list[dict[str, Any]] = []
        for schema in tool_schemas:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": schema["name"],
                        "description": schema.get("description", ""),
                        "parameters": schema.get(
                            "input_schema",
                            {"type": "object", "properties": {}},
                        ),
                    },
                }
            )
        return openai_tools
