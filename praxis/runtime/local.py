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

        last_content = ""
        for _ in range(max_turns):
            kwargs: dict[str, Any] = {
                "model": resolved_model,
                "messages": messages,
            }
            if openai_tools:
                kwargs["tools"] = openai_tools

            response = self.client.chat.completions.create(**kwargs)
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

            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw

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
        return messages

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
