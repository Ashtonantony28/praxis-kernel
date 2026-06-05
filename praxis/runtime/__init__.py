"""Runtime abstraction — swap providers without changing orchestrator logic."""

from __future__ import annotations

import os

from .base import Runtime
from .claude_code import ClaudeCodeCLIRuntime, ClaudeCodeRuntime
from .cloud import OpenAICloudRuntime
from .local import LocalRuntime
from .openai_base import OpenAIBaseRuntime
from .telemetry import TelemetryEvent, TelemetryStore

__all__ = [
    "Runtime",
    "ClaudeCodeRuntime",
    "ClaudeCodeCLIRuntime",
    "LocalRuntime",
    "OpenAICloudRuntime",
    "OpenAIBaseRuntime",
    "TelemetryEvent",
    "TelemetryStore",
    "get_default_runtime",
]


def get_default_runtime() -> Runtime:
    """Select and instantiate the default runtime from env vars.

    PRAXIS_RUNTIME env var controls selection:
      "claudecode" -> ClaudeCodeCLIRuntime (subprocess claude -p)
      "claude"     -> ClaudeCodeRuntime (Anthropic SDK)
      "cloud"      -> OpenAICloudRuntime
      "local"      -> LocalRuntime
      unset        -> auto-detect: ClaudeCodeCLIRuntime when
                     CLAUDE_CODE_OAUTH_TOKEN is set and ANTHROPIC_API_KEY is absent;
                     otherwise ClaudeCodeRuntime
    """
    runtime_name = os.environ.get("PRAXIS_RUNTIME", "").lower().strip()

    if runtime_name == "claudecode":
        return ClaudeCodeCLIRuntime.from_env()
    elif runtime_name == "claude":
        return ClaudeCodeRuntime.from_env()
    elif runtime_name == "cloud":
        return OpenAICloudRuntime.from_env()
    elif runtime_name == "local":
        return LocalRuntime.from_env()
    else:
        # Auto-detect: prefer ClaudeCodeCLIRuntime when OAuth token present
        # and no API key (avoids SDK model-tier problems)
        has_oauth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
        has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if has_oauth and not has_api_key:
            return ClaudeCodeCLIRuntime.from_env()
        return ClaudeCodeRuntime.from_env()
