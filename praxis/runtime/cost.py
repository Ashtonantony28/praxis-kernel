"""Session cost tracking and circuit breaker for API usage."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Pricing in USD per 1M tokens: {model: (input_per_1M, output_per_1M)}
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-6": (15.00, 75.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o-2024-11-20": (2.50, 10.00),
    "gemini-2.0-flash-exp": (0.075, 0.30),
}
_DEFAULT_PRICING: tuple[float, float] = (1.00, 3.00)
_DEFAULT_MAX_COST: float = 2.00


class CostCircuitBreaker:
    """Tracks per-session API cost and trips sys.exit(3) if cap exceeded.

    The cap is read from PRAXIS_MAX_SESSION_COST env var (default 2.00 USD).
    For subscription/OAuth users, costs are estimated from published pricing —
    not billed — but still useful to cap runaway agent loops.

    On trip: dumps execution trace to .praxis/logs/cost-circuit-break-{ts}.json
    then calls sys.exit(3).
    """

    def __init__(self, max_cost_usd: float | None = None) -> None:
        self.max_cost: float = (
            max_cost_usd if max_cost_usd is not None else _DEFAULT_MAX_COST
        )
        self.session_cost: float = 0.0
        self._call_log: list[dict[str, Any]] = []

    @classmethod
    def from_env(cls) -> "CostCircuitBreaker":
        """Create circuit breaker from PRAXIS_MAX_SESSION_COST env var."""
        raw = os.environ.get("PRAXIS_MAX_SESSION_COST", str(_DEFAULT_MAX_COST))
        try:
            max_cost = float(raw)
        except ValueError:
            sys.stderr.write(
                f"[praxis] warning: invalid PRAXIS_MAX_SESSION_COST={raw!r}; "
                f"using default ${_DEFAULT_MAX_COST:.2f}\n"
            )
            max_cost = _DEFAULT_MAX_COST
        return cls(max_cost_usd=max_cost)

    def record_call(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        """Record one API call. Trips the breaker if session cost exceeds cap."""
        in_price, out_price = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
        call_cost = (input_tokens * in_price + output_tokens * out_price) / 1_000_000
        self.session_cost += call_cost
        self._call_log.append(
            {
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "call_cost_usd": round(call_cost, 6),
                "session_total_usd": round(self.session_cost, 6),
                "timestamp": time.time(),
            }
        )
        if self.session_cost >= self.max_cost:
            self._trip()

    def _trip(self) -> None:
        """Dump execution trace to disk and exit with code 3."""
        import datetime

        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        workspace = os.environ.get("PRAXIS_WORKSPACE_ROOT", ".")
        log_dir = Path(workspace) / ".praxis" / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            out_path = log_dir / f"cost-circuit-break-{ts}.json"
            dump: dict[str, Any] = {
                "event": "COST_CIRCUIT_BREAK",
                "timestamp": ts,
                "max_cost_usd": self.max_cost,
                "session_cost_usd": round(self.session_cost, 6),
                "call_count": len(self._call_log),
                "call_log": self._call_log,
            }
            out_path.write_text(json.dumps(dump, indent=2))
            trace_msg = str(out_path)
        except OSError as exc:
            trace_msg = f"(could not write trace: {exc})"

        sys.stderr.write(
            f"[praxis] fatal: session cost ${self.session_cost:.4f} exceeds "
            f"cap ${self.max_cost:.2f}. Execution trace: {trace_msg}\n"
            "Raise PRAXIS_MAX_SESSION_COST or investigate the trace to resume.\n"
        )
        sys.exit(3)
