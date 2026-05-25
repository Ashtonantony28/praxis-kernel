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


def _parse_mode(argv: list[str]) -> str:
    """Determine execution mode from argv flags."""
    if "--daemon" in argv:
        return "daemon"
    if "--stop" in argv:
        return "stop"
    if "--status" in argv:
        return "status"
    if "--queue" in argv:
        return "queue"
    return "interactive"


def main() -> None:
    try:
        mode = _parse_mode(sys.argv)

        if mode == "interactive":
            config = Config.from_env()
            conv = ConvergenceConfig.load(config.workspace_root)
            default_runtime, runtime_overrides = _create_runtimes(conv)
            orch = Orchestrator(default_runtime, config, runtime_overrides=runtime_overrides)

            # Filter out the program name, leave only the prompt args
            args = [a for a in sys.argv[1:] if not a.startswith("--")]
            if args:
                message = " ".join(args)
            else:
                message = sys.stdin.read()

            result = orch.run(message)
            print(result)

        elif mode == "queue":
            from .queue_runner import run_queue_loop

            config = Config.from_env()
            run_queue_loop(config.workspace_root)

        elif mode == "daemon":
            from .daemon import start_daemon

            config = Config.from_env()
            start_daemon(config.workspace_root)

        elif mode == "stop":
            from .daemon import stop_daemon

            config = Config.from_env()
            stop_daemon(config.workspace_root)

        elif mode == "status":
            from .daemon import report_status

            config = Config.from_env()
            report_status(config.workspace_root)

    except KeyboardInterrupt:
        sys.stderr.write("\n[praxis] interrupted.\n")
        raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"[praxis] fatal: {exc}")


if __name__ == "__main__":
    main()
