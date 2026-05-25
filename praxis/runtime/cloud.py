"""OpenAICloudRuntime — cloud OpenAI-compatible endpoint.

Works against any cloud API that speaks the OpenAI chat completions
protocol: OpenAI, Gemini (compatibility layer), OpenRouter, Groq,
Together, Fireworks, etc. Selected by base_url and model string.

Thin subclass of OpenAIBaseRuntime. Adds:
  - from_env() with real API key auth (required, not dummy)
  - _call_api() with exponential backoff retry on 429
  - No model ID remapping (passes model string through unchanged)
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from .openai_base import OpenAIBaseRuntime

RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BASE_DELAY = 5   # seconds
RATE_LIMIT_MAX_DELAY = 60   # seconds


class OpenAICloudRuntime(OpenAIBaseRuntime):
    """Runtime backed by a cloud OpenAI-compatible API."""

    @classmethod
    def from_env(cls) -> "OpenAICloudRuntime":
        """Create runtime from environment variables.

        Env vars:
            PRAXIS_CLOUD_API_KEY   — API key (required)
            PRAXIS_CLOUD_BASE_URL  — full base URL (default: https://api.openai.com/v1)
            PRAXIS_CLOUD_MODEL     — default model (default: gpt-4o)
        """
        try:
            import openai
        except ImportError:
            raise SystemExit(
                "[praxis] fatal: 'openai' package required for cloud runtime.\n"
                "Install it: pip install praxis[local]"
            )

        api_key = os.environ.get("PRAXIS_CLOUD_API_KEY")
        if not api_key:
            raise SystemExit(
                "[praxis] fatal: PRAXIS_CLOUD_API_KEY is required for cloud runtime.\n"
                "Set it to your OpenAI / OpenRouter / Groq / etc. API key."
            )

        base_url = os.environ.get(
            "PRAXIS_CLOUD_BASE_URL", "https://api.openai.com/v1"
        )
        model = os.environ.get("PRAXIS_CLOUD_MODEL", "gpt-4o")

        client = openai.OpenAI(base_url=base_url, api_key=api_key)
        return cls(client, default_model=model, base_url=base_url)

    def _call_api(self, **kwargs: Any) -> Any:
        """Call the cloud API with exponential backoff on rate limits.

        Retries up to RATE_LIMIT_MAX_RETRIES times on 429, with delays
        of 5s, 10s, 20s (doubling, capped at 60s). Logs each retry to
        stderr. Clean SystemExit after exhaustion.
        """
        import openai

        last_exc: Exception | None = None
        for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except openai.RateLimitError as exc:
                last_exc = exc
                if attempt >= RATE_LIMIT_MAX_RETRIES:
                    raise SystemExit(
                        "[praxis] fatal: rate limited by cloud API after "
                        f"{RATE_LIMIT_MAX_RETRIES} retries. Try again later."
                    )
                delay = min(
                    RATE_LIMIT_BASE_DELAY * (2 ** attempt),
                    RATE_LIMIT_MAX_DELAY,
                )
                print(
                    f"[praxis] cloud rate limited — retrying in {delay}s "
                    f"(attempt {attempt + 1}/{RATE_LIMIT_MAX_RETRIES})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            except openai.AuthenticationError:
                raise SystemExit(
                    "[praxis] fatal: cloud API rejected authentication.\n"
                    "Check your PRAXIS_CLOUD_API_KEY."
                )
            except openai.APIConnectionError:
                raise SystemExit(
                    f"[praxis] fatal: cannot connect to cloud API at {self.base_url}.\n"
                    "Check your PRAXIS_CLOUD_BASE_URL."
                )
            except openai.APIStatusError as exc:
                raise SystemExit(
                    f"[praxis] fatal: cloud API error (HTTP {exc.status_code}) — {exc.message}"
                )
