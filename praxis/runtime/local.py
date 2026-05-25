"""LocalRuntime — local model server (Ollama, vLLM, llama.cpp).

Thin subclass of OpenAIBaseRuntime. Adds:
  - from_env() with Ollama-oriented defaults (dummy API key, /v1 suffix)
  - _resolve_model() to replace Claude model IDs with the local default
  - _call_api() with connection-oriented error messages
"""

from __future__ import annotations

import os
from typing import Any

from .openai_base import OpenAIBaseRuntime


class LocalRuntime(OpenAIBaseRuntime):
    """Runtime backed by a local model server via OpenAI-compatible API."""

    def __init__(
        self,
        client: Any,
        *,
        default_model: str = "llama3.1:8b",
        base_url: str = "http://localhost:11434",
    ) -> None:
        super().__init__(client, default_model=default_model, base_url=base_url)

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

    def _resolve_model(self, model: str) -> str:
        """Replace Claude model IDs with the configured local model."""
        if model.startswith("claude-"):
            return self.default_model
        return model

    def _call_api(self, **kwargs: Any) -> Any:
        """Call the local server with connection-oriented error handling."""
        import openai

        try:
            return self.client.chat.completions.create(**kwargs)
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
