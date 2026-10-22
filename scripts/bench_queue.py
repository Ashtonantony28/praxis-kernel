#!/usr/bin/env python3
"""Measure TaskQueue claim()/update() latency with 10k tasks on disk against the 20 ms p99 budget."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from praxis.queue import Task, TaskQueue  # noqa: E402

BUDGET_P99_MS = 20.0


def _p(samples: list[float], q: float) -> float:
    s = sorted(samples)
    return s[min(len(s) - 1, int(q * len(s)))]


def _timed(fn, *args, **kw) -> float:
    t0 = time.perf_counter()
    fn(*args, **kw)
    return (time.perf_counter() - t0) * 1000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=10_000)
    parser.add_argument("--rounds", type=int, default=2_000)
    parser.add_argument("--assert-budget", action="store_true", help="exit 1 when p99 exceeds the budget")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        qdir = Path(tmp) / "queue"
        seed = TaskQueue(qdir)
        seed._write_all([Task.create(f"task {i}", priority=i % 10) for i in range(args.tasks)])

        q, producer = TaskQueue(qdir), TaskQueue(qdir)
        cold_ms = _timed(q.stats)
        claim_ms, update_ms = [], []
        for i in range(args.rounds):
            producer.append(Task.create(f"new {i}", priority=i % 10))  # another writer between ops
            t0 = time.perf_counter()
            task = q.claim()
            claim_ms.append((time.perf_counter() - t0) * 1000)
            update_ms.append(_timed(q.update, task.id, status="done", result="ok"))
        compact_ms = _timed(q.compact)

    worst = max(_p(claim_ms, 0.99), _p(update_ms, 0.99))
    print(f"tasks on disk: {args.tasks}  rounds: {args.rounds}")
    for name, s in (("claim", claim_ms), ("update", update_ms)):
        print(f"{name:>7}: p50 {_p(s, 0.50):.3f} ms  p99 {_p(s, 0.99):.3f} ms  max {max(s):.3f} ms")
    print(f"cold load: {cold_ms:.1f} ms  compaction: {compact_ms:.1f} ms (amortised, once per >=N writes)")
    print(f"budget p99 <= {BUDGET_P99_MS:.0f} ms: {'OK' if worst <= BUDGET_P99_MS else 'OVER'}")
    return 1 if args.assert_budget and worst > BUDGET_P99_MS else 0


if __name__ == "__main__":
    raise SystemExit(main())
