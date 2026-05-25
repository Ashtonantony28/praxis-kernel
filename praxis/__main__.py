"""Entry point: python -m praxis"""

from __future__ import annotations

import os
import sys

from .config import Config
from .orchestrator import Orchestrator
from .runtime import ClaudeCodeRuntime, LocalRuntime


def _create_runtime():
    """Select runtime based on PRAXIS_RUNTIME env var.

    Values: "claude" (default), "local" (OpenAI-compatible endpoint).
    """
    choice = os.environ.get("PRAXIS_RUNTIME", "claude").lower()

    if choice == "local":
        runtime = LocalRuntime.from_env()
        sys.stderr.write(
            f"[praxis] runtime: local ({runtime.base_url}, "
            f"model: {runtime.default_model})\n"
        )
        return runtime
    elif choice == "claude":
        runtime = ClaudeCodeRuntime.from_env()
        sys.stderr.write(f"[praxis] auth: {runtime.auth_method}\n")
        return runtime
    else:
        raise SystemExit(
            f"[praxis] fatal: unknown PRAXIS_RUNTIME={choice!r}.\n"
            "Valid values: claude, local"
        )


def main() -> None:
    config = Config.from_env()
    runtime = _create_runtime()

    orch = Orchestrator(runtime, config)

    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
    else:
        message = sys.stdin.read()

    result = orch.run(message)
    print(result)


if __name__ == "__main__":
    main()
