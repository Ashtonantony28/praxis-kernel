"""Entry point: python -m praxis"""

from __future__ import annotations

import sys

from .config import Config
from .orchestrator import Orchestrator
from .runtime import ClaudeCodeRuntime


def main() -> None:
    config = Config.from_env()
    runtime = ClaudeCodeRuntime.from_env()

    sys.stderr.write(f"[praxis] auth: {runtime.auth_method}\n")

    orch = Orchestrator(runtime, config)

    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
    else:
        message = sys.stdin.read()

    result = orch.run(message)
    print(result)


if __name__ == "__main__":
    main()
