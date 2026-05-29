"""ClaudeCodeRuntime — Anthropic Messages API implementation of Runtime."""

from __future__ import annotations

import os
import sys
import time
from typing import TYPE_CHECKING, Any

from .base import Runtime, ToolExecutor
from .cost import CostCircuitBreaker

if TYPE_CHECKING:
    from praxis.modes.base import Mode

MAX_TURNS = 50
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BASE_DELAY = 5   # seconds
RATE_LIMIT_MAX_DELAY = 60   # seconds
MAX_CONTEXT_MESSAGES = 40   # trigger compaction above this
CONTEXT_KEEP_RECENT = 10    # keep last N messages verbatim


class ClaudeCodeRuntime(Runtime):
    """Runtime backed by the Anthropic Messages API.

    Thin wrapper that reproduces the exact behavior of the Phase 0
    orchestrator's _run_loop / _process_tool_calls / _extract_text.
    """

    def __init__(self, client: Any, *, auth_method: str = "api_key") -> None:
        self.client = client
        self.auth_method = auth_method
        self._cost_breaker = CostCircuitBreaker.from_env()
        self._current_mode: "Mode | None" = None

    @classmethod
    def from_env(cls) -> "ClaudeCodeRuntime":
        """Create runtime from environment variables.

        Priority: CLAUDE_CODE_OAUTH_TOKEN (subscription) first,
        ANTHROPIC_API_KEY (pay-per-token) second, error if neither.

        When OAuth is active, ANTHROPIC_API_KEY is scrubbed from the
        environment to prevent silent override by the SDK or subprocesses.
        """
        try:
            import anthropic
        except ImportError:
            raise SystemExit(
                "[praxis] fatal: 'anthropic' package required for claude runtime.\n"
                "Install it: pip install anthropic"
            )

        oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        api_key = os.environ.get("ANTHROPIC_API_KEY")

        if oauth_token:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            client = anthropic.Anthropic(auth_token=oauth_token)
            return cls(client, auth_method="oauth")
        elif api_key:
            client = anthropic.Anthropic()
            return cls(client, auth_method="api_key")
        else:
            raise SystemExit(
                "[praxis] fatal: no auth configured.\n"
                "Set CLAUDE_CODE_OAUTH_TOKEN (subscription, flat cost) "
                "or ANTHROPIC_API_KEY (pay-per-token)."
            )

    def run_loop(
        self,
        *,
        model: str,
        system: str,
        user_message: str,
        tool_schemas: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        max_turns: int = MAX_TURNS,
        mode: "Mode | None" = None,
    ) -> str:
        import anthropic

        # Apply mode: filter tools and inject prompt suffix
        self._current_mode = mode
        effective_system = system
        effective_tool_schemas = tool_schemas
        if mode is not None:
            effective_tool_schemas = self.apply_mode(mode, tool_schemas)
            if mode.prompt_suffix:
                effective_system = system + mode.prompt_suffix

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        response = None
        for _ in range(max_turns):
            try:
                response = self._create_with_retry(
                    model=model,
                    system=effective_system,
                    messages=messages,
                    tools=effective_tool_schemas,
                    max_tokens=4096,
                )
            except anthropic.AuthenticationError:
                from .auth import graceful_auth_error_message
                raise SystemExit(graceful_auth_error_message(self.auth_method))
            except anthropic.APIConnectionError as exc:
                raise SystemExit(
                    f"[praxis] fatal: cannot reach Anthropic API — {exc}"
                )
            except anthropic.APIStatusError as exc:
                raise SystemExit(
                    f"[praxis] fatal: Anthropic API error (HTTP {exc.status_code}) — {exc.message}"
                )

            # Record estimated cost; trip breaker if session cap exceeded
            usage = getattr(response, "usage", None)
            if usage is not None:
                try:
                    self._cost_breaker.record_call(
                        model,
                        int(getattr(usage, "input_tokens", 0) or 0),
                        int(getattr(usage, "output_tokens", 0) or 0),
                    )
                except (TypeError, ValueError):
                    pass

            messages = self.manage_context(messages, "assistant", response.content)

            if response.stop_reason == "end_turn":
                return self._extract_text(response)

            tool_results = self.execute_tool(response.content, tool_executor)
            if not tool_results:
                break

            messages = self.manage_context(messages, "user", tool_results)

        return self._extract_text(response) if response else ""

    def spawn_subagent(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        tool_schemas: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        max_turns: int = MAX_TURNS,
        mode: "Mode | None" = None,
    ) -> str:
        return self.run_loop(
            model=model,
            system=system,
            user_message=prompt,
            tool_schemas=tool_schemas,
            tool_executor=tool_executor,
            max_turns=max_turns,
            mode=mode,
        )

    def execute_tool(
        self,
        response_content: list[Any],
        tool_executor: ToolExecutor,
    ) -> list[dict[str, Any]]:
        import time as _time
        from datetime import datetime, timezone

        results: list[dict[str, Any]] = []
        for block in response_content:
            if getattr(block, "type", None) != "tool_use":
                continue

            # Layer 1 enforcement — raises EnforcementError if blocked
            try:
                from .enforcement import enforce, EnforcementError
                enforce(block.name, block.input, mode=self._current_mode)
            except EnforcementError as _e:
                output = f"BLOCKED by §5 escalation boundary: {_e}"
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
                continue

            _t0 = _time.monotonic()
            output = tool_executor(block.name, block.input)
            _latency_ms = (_time.monotonic() - _t0) * 1000

            # Record telemetry — never let this break the main loop
            try:
                from .telemetry import TelemetryEvent, TelemetryStore
                _hook_result = "blocked" if str(output).startswith("BLOCKED by §5") else "allowed"
                TelemetryStore.get_global().record(TelemetryEvent(
                    tool_name=block.name,
                    latency_ms=_latency_ms,
                    hook_result=_hook_result,
                    caller="ClaudeCodeRuntime",
                    token_count=None,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ))
            except Exception:
                pass

            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )
        return results

    def manage_context(
        self,
        messages: list[dict[str, Any]],
        role: str,
        content: Any,
    ) -> list[dict[str, Any]]:
        messages.append({"role": role, "content": content})
        if len(messages) > MAX_CONTEXT_MESSAGES:
            messages = self._compact_context(messages)
        return messages

    def _compact_context(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Sliding window — summarize older exchanges, keep recent ones.

        Keeps messages[0] (initial user prompt) and the last
        CONTEXT_KEEP_RECENT messages. Everything in between is
        compressed into a summary appended to the first message.
        Split point is aligned to an assistant message boundary
        so the alternating user/assistant pattern stays valid.
        """
        split = len(messages) - CONTEXT_KEEP_RECENT
        # Align to assistant boundary for valid alternation
        while split < len(messages) and messages[split].get("role") != "assistant":
            split += 1
        if split >= len(messages) - 2:
            return messages  # not enough to compact

        older = messages[1:split]
        recent = messages[split:]

        summary_lines = ["[Compacted context — older exchanges summarized]"]
        for msg in older:
            line = self._summarize_message(msg)
            if line:
                summary_lines.append(line)
        summary_text = "\n".join(summary_lines)

        first = dict(messages[0])
        original = first["content"]
        if isinstance(original, str):
            first["content"] = f"{original}\n\n{summary_text}"
        return [first] + recent

    @staticmethod
    def _summarize_message(msg: dict[str, Any]) -> str | None:
        """One-line summary of a single message for context compaction."""
        role = msg.get("role", "?")
        content = msg.get("content", "")

        if role == "assistant" and isinstance(content, list):
            parts: list[str] = []
            for block in content:
                btype = getattr(block, "type", None)
                if btype == "tool_use":
                    parts.append(f"called {block.name}")
                elif btype == "text":
                    parts.append(block.text[:80])
            return "  Assistant: " + "; ".join(parts) if parts else None

        if role == "user" and isinstance(content, list):
            results: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    results.append(str(item.get("content", ""))[:60])
            return "  Results: " + "; ".join(results) if results else None

        if isinstance(content, str):
            return f"  {role.capitalize()}: {content[:100]}"
        return None

    def _create_with_retry(self, **kwargs: Any) -> Any:
        """Call messages.create with exponential backoff on rate limits.

        Retries up to RATE_LIMIT_MAX_RETRIES times on 429, with delays
        of 5s, 10s, 20s (doubling, capped at 60s). Logs each retry to
        stderr. Re-raises RateLimitError as SystemExit after exhaustion.
        """
        import anthropic

        for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
            try:
                return self.client.messages.create(**kwargs)
            except anthropic.RateLimitError:
                if attempt >= RATE_LIMIT_MAX_RETRIES:
                    raise SystemExit(
                        "[praxis] fatal: rate limited by Anthropic API after "
                        f"{RATE_LIMIT_MAX_RETRIES} retries. Try again later."
                    )
                delay = min(
                    RATE_LIMIT_BASE_DELAY * (2 ** attempt),
                    RATE_LIMIT_MAX_DELAY,
                )
                print(
                    f"[praxis] rate limited — retrying in {delay}s "
                    f"(attempt {attempt + 1}/{RATE_LIMIT_MAX_RETRIES})",
                    file=sys.stderr,
                )
                time.sleep(delay)

    @staticmethod
    def _extract_text(response: Any) -> str:
        parts = [
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        ]
        return "\n".join(parts) if parts else ""
