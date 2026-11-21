#!/usr/bin/env python3
"""Run the policy corpus through evaluate() (p99 <= 1 ms) and the SG2 hook subprocess (p99 <= 80 ms)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from praxis.modes.base import Mode  # noqa: E402
from praxis.policy import PolicyContext, ToolCall, evaluate  # noqa: E402

BUDGET_P99_US = 1000.0
HOOK_BUDGET_P99_MS = 80.0
HOOK = REPO / "scripts" / "hooks" / "escalation-boundary.py"


def _sub(value, root: str):
    if isinstance(value, str):
        return value.replace("{workspace}", root)
    if isinstance(value, dict):
        return {k: _sub(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_sub(v, root) for v in value]
    return value


def load(root: Path) -> list[tuple[ToolCall, PolicyContext]]:
    os.environ["PRAXIS_WORKSPACE_ROOT"] = str(root)
    modes: dict[str, Mode] = {}
    pairs = []
    for f in sorted((REPO / "tests" / "fixtures" / "policy").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            ctx, name = case["ctx"], case["ctx"]["mode"]
            if name and name not in modes:
                modes[name] = Mode.load(name)
            pairs.append((
                ToolCall(case["call"]["name"], _sub(case["call"]["input"], str(root))),
                PolicyContext(root, frozenset(ctx["allowed_domains"]), modes.get(name), ctx["origin"]),
            ))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=200)
    parser.add_argument("--hook-runs", type=int, default=50)
    parser.add_argument("--assert-budget", action="store_true", help="exit 1 when p99 exceeds the budget")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        pairs = load(Path(tmp).resolve())
        for call, ctx in pairs:
            evaluate(call, ctx)
        samples = []
        for _ in range(args.rounds):
            for call, ctx in pairs:
                t0 = time.perf_counter_ns()
                evaluate(call, ctx)
                samples.append(time.perf_counter_ns() - t0)

    samples.sort()
    p50 = samples[len(samples) // 2] / 1000
    p99 = samples[min(len(samples) - 1, int(len(samples) * 0.99))] / 1000
    ok = p99 <= BUDGET_P99_US
    print(f"evaluate: n={len(samples)} cases={len(pairs)} p50={p50:.1f}us p99={p99:.1f}us "
          f"budget={BUDGET_P99_US:.0f}us {'PASS' if ok else 'FAIL'}")

    hook = []
    event = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "PRAXIS_WORKSPACE_ROOT": tmp}
        for _ in range(args.hook_runs):
            t0 = time.perf_counter()
            subprocess.run([sys.executable, str(HOOK)], input=event, text=True, capture_output=True, env=env)
            hook.append((time.perf_counter() - t0) * 1000)
    hook.sort()
    h50, h99 = hook[len(hook) // 2], hook[min(len(hook) - 1, int(len(hook) * 0.99))]
    hook_ok = h99 <= HOOK_BUDGET_P99_MS
    print(f"hook: n={len(hook)} p50={h50:.1f}ms p99={h99:.1f}ms budget={HOOK_BUDGET_P99_MS:.0f}ms "
          f"{'PASS' if hook_ok else 'FAIL'}")
    return 0 if (ok and hook_ok) or not args.assert_budget else 1


if __name__ == "__main__":
    sys.exit(main())
