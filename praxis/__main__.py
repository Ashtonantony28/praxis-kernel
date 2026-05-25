"""Entry point: python -m praxis"""

from __future__ import annotations

import sys

from .config import Config
from .convergence import ConvergenceConfig
from .orchestrator import Orchestrator
from .runtime import ClaudeCodeRuntime, LocalRuntime, OpenAICloudRuntime
from .runtime.base import Runtime


def _create_runtimes(conv: ConvergenceConfig):
    """Create all runtimes needed by the convergence config.

    Returns (default_runtime, overrides_dict).
    """
    runtimes: dict[str, Runtime] = {}

    if conv.needs_claude():
        rt = ClaudeCodeRuntime.from_env()
        sys.stderr.write(f"[praxis] runtime claude: auth={rt.auth_method}\n")
        runtimes["claude"] = rt

    if conv.needs_local():
        rt = LocalRuntime.from_env()
        sys.stderr.write(
            f"[praxis] runtime local: {rt.base_url}, "
            f"model={rt.default_model}\n"
        )
        runtimes["local"] = rt

    if conv.needs_cloud():
        rt = OpenAICloudRuntime.from_env()
        sys.stderr.write(
            f"[praxis] runtime cloud: {rt.base_url}, "
            f"model={rt.default_model}\n"
        )
        runtimes["cloud"] = rt

    default = runtimes[conv.default_runtime]
    overrides = {
        name: runtimes[rt_name]
        for name, rt_name in conv.overrides.items()
        if rt_name != conv.default_runtime
    }

    return default, overrides


def main() -> None:
    try:
        config = Config.from_env()
        conv = ConvergenceConfig.load(config.workspace_root)
        default_runtime, runtime_overrides = _create_runtimes(conv)

        orch = Orchestrator(default_runtime, config, runtime_overrides=runtime_overrides)

        if len(sys.argv) > 1:
            message = " ".join(sys.argv[1:])
        else:
            message = sys.stdin.read()

        result = orch.run(message)
        print(result)
    except KeyboardInterrupt:
        sys.stderr.write("\n[praxis] interrupted.\n")
        raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"[praxis] fatal: {exc}")


if __name__ == "__main__":
    main()
