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

RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_DELAY = 5   # seconds — 5+10+20+40+60 = 135s total budget
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

    def _resolve_model(self, model: str) -> str:
        """Replace Claude model IDs with the configured cloud model.

        Subagent definitions hardcode Claude model IDs (e.g. claude-haiku-*).
        When routing through a non-Anthropic cloud endpoint those IDs are
        meaningless — substitute the configured default instead.
        """
        if model.startswith("claude-"):
            return self.default_model
        return model

    def _call_api(self, **kwargs: Any) -> Any:
        """Call the cloud API with exponential backoff on rate limits and overload.

        Retries up to RATE_LIMIT_MAX_RETRIES times on 429 (rate limit) or
        503 (service unavailable / transient overload), with delays of
        5s, 10s, 20s, 40s, 60s (doubling, capped at 60s). Logs each retry
        to stderr. Clean SystemExit after exhaustion.

        Gemini free tier returns 503 on transient overload, not 429 — both
        are treated identically as retriable transient errors.
        """
        import openai

        for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except openai.RateLimitError as exc:
                label = "rate limited"
                retriable = True
            except openai.APIStatusError as exc:
                if exc.status_code == 503:
                    label = "service unavailable (503)"
                    retriable = True
                else:
                    raise SystemExit(
                        f"[praxis] fatal: cloud API error (HTTP {exc.status_code}) — {exc.message}"
                    )
            except openai.AuthenticationError:
                from .auth import graceful_auth_error_message
                raise SystemExit(graceful_auth_error_message("cloud"))
            except openai.APIConnectionError:
                raise SystemExit(
                    f"[praxis] fatal: cannot connect to cloud API at {self.base_url}.\n"
                    "Check your PRAXIS_CLOUD_BASE_URL."
                )
            else:
                retriable = False

            if not retriable:
                break

            if attempt >= RATE_LIMIT_MAX_RETRIES:
                raise SystemExit(
                    f"[praxis] fatal: cloud API {label} after "
                    f"{RATE_LIMIT_MAX_RETRIES} retries. Try again later."
                )
            delay = min(
                RATE_LIMIT_BASE_DELAY * (2 ** attempt),
                RATE_LIMIT_MAX_DELAY,
            )
            print(
                f"[praxis] cloud {label} — retrying in {delay}s "
                f"(attempt {attempt + 1}/{RATE_LIMIT_MAX_RETRIES})",
                file=sys.stderr,
            )
            time.sleep(delay)
