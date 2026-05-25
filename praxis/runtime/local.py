"""LocalRuntime — OpenAI-compatible endpoint implementation of Runtime.

Targets Ollama, vLLM, llama.cpp, or any server exposing the
OpenAI /v1/chat/completions API with tool-calling support.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import Runtime, ToolExecutor

MAX_TURNS = 50
MAX_CONTEXT_MESSAGES = 40   # trigger compaction above this
CONTEXT_KEEP_RECENT = 10    # keep last N messages verbatim


class LocalRuntime(Runtime):
    """Runtime backed by a local model server via OpenAI-compatible API.

    Implements the full agent loop client-side — local servers expose
    chat completions, not an agent SDK.
    """

    def __init__(
        self,
        client: Any,
        *,
        default_model: str = "llama3.1:8b",
        base_url: str = "http://localhost:11434",
    ) -> None:
        self.client = client
        self.default_model = default_model
        self.base_url = base_url

    @classmethod
    def from_env(cls) -> "LocalRuntime":
        """Create runtime from environment variables.

        Env vars:
            PRAXIS_LOCAL_BASE_URL  — server URL (default: http://localhost:11434)
            PRAXIS_LOCAL_MODEL     — default model name (default: llama3.1:8b)

        Ollama's OpenAI-compatible endpoint lives at {base_url}/v1.
        API key is set to "ollama" (dummy — Ollama doesn't require auth).
        """
        try:
            import openai
        except ImportError:
            raise SystemExit(
                "[praxis] fatal: 'openai' package required for local runtime.\n"
                "Install it: pip install praxis[local]"
            )

        base_url = os.environ.get("PRAXIS_LOCAL_BASE_URL", "http://localhost:11434")
        model = os.environ.get("PRAXIS_LOCAL_MODEL", "llama3.1:8b")
        api_url = f"{base_url}/v1"

        client = openai.OpenAI(base_url=api_url, api_key="ollama")
        return cls(client, default_model=model, base_url=base_url)

    def run_loop(
        self,
        *,
        model: str,
        system: str,
        user_message: str,
        tool_schemas: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        max_turns: int = MAX_TURNS,
    ) -> str:
        resolved_model = self._resolve_model(model)
        openai_tools = self._convert_tools(tool_schemas)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ]

        import openai

        last_content = ""
        for _ in range(max_turns):
            kwargs: dict[str, Any] = {
                "model": resolved_model,
                "messages": messages,
            }
            if openai_tools:
                kwargs["tools"] = openai_tools

            try:
                response = self.client.chat.completions.create(**kwargs)
            except openai.APIConnectionError:
                raise SystemExit(
                    f"[praxis] fatal: cannot connect to local model server at {self.base_url}.\n"
                    "Is Ollama / vLLM / llama.cpp running?"
                )
            except openai.AuthenticationError:
                raise SystemExit(
                    "[praxis] fatal: local model server rejected authentication."
                )
            except openai.APIStatusError as exc:
                raise SystemExit(
                    f"[praxis] fatal: local model server error (HTTP {exc.status_code}) — {exc.message}"
                )

            if not response.choices:
                raise SystemExit(
                    "[praxis] fatal: local model server returned an empty response."
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
    ) -> str:
        return self.run_loop(
            model=model,
            system=system,
            user_message=prompt,
            tool_schemas=tool_schemas,
            tool_executor=tool_executor,
            max_turns=max_turns,
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

            output = tool_executor(name, args)
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
        """Replace Claude model IDs with the configured local model."""
        if model.startswith("claude-"):
            return self.default_model
        return model

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
