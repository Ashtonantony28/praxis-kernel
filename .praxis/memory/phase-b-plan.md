# Phase B Plan — Subscription OAuth as Primary Runtime Auth

**Date:** 2026-05-25
**Status:** Planning
**Branch:** `claude/blissful-franklin-VIMiH`

---

## Goal

`ClaudeCodeRuntime` uses subscription OAuth (`CLAUDE_CODE_OAUTH_TOKEN`) as the
default auth path, with `ANTHROPIC_API_KEY` as an explicit fallback. Auth
misconfiguration is never silent.

---

## Design

### Auth priority order

1. `CLAUDE_CODE_OAUTH_TOKEN` — subscription model (flat cost)
2. `ANTHROPIC_API_KEY` — pay-per-token (development fallback)
3. Neither set → hard exit with clear error message

### Env scrubbing

When OAuth is active, `ANTHROPIC_API_KEY` is removed from `os.environ` at
process start. This prevents:
- The Anthropic SDK from silently using API key instead of OAuth token
- Hook subprocesses from inheriting a stale API key
- Any child process from accidentally using the wrong auth

Scrubbing from `os.environ` (not just a subprocess env dict) is correct because
the Anthropic SDK reads env vars internally — if both are present, behavior is
ambiguous.

### Startup logging

At session start, log to stderr (not stdout, which is reserved for output):
```
[praxis] auth: oauth (CLAUDE_CODE_OAUTH_TOKEN)
```
or:
```
[praxis] auth: api_key (ANTHROPIC_API_KEY)
```

This makes misconfiguration visible in any log capture.

---

## Changes

### 1. `praxis/runtime/claude_code.py`

Add `from_env()` class method and `auth_method` attribute:

```python
class ClaudeCodeRuntime(Runtime):
    def __init__(self, client: Any, *, auth_method: str = "api_key") -> None:
        self.client = client
        self.auth_method = auth_method

    @classmethod
    def from_env(cls) -> "ClaudeCodeRuntime":
        import os
        import anthropic

        oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        api_key = os.environ.get("ANTHROPIC_API_KEY")

        if oauth_token:
            # Scrub API key so it can't silently override OAuth
            os.environ.pop("ANTHROPIC_API_KEY", None)
            client = anthropic.Anthropic(api_key=oauth_token)
            return cls(client, auth_method="oauth")
        elif api_key:
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
            return cls(client, auth_method="api_key")
        else:
            raise SystemExit(
                "[praxis] fatal: no auth configured.\n"
                "Set CLAUDE_CODE_OAUTH_TOKEN (subscription, flat cost) "
                "or ANTHROPIC_API_KEY (pay-per-token)."
            )
```

### 2. `praxis/__main__.py`

Replace manual client creation with `from_env()` and add startup log:

```python
def main() -> None:
    import sys

    config = Config.from_env()
    runtime = ClaudeCodeRuntime.from_env()

    sys.stderr.write(f"[praxis] auth: {runtime.auth_method}\n")

    orch = Orchestrator(runtime, config)
    # ... rest unchanged ...
```

### 3. Tests (`tests/test_runtime.py` — new file)

- `test_from_env_oauth_token` — sets CLAUDE_CODE_OAUTH_TOKEN, verifies auth_method="oauth"
- `test_from_env_api_key` — sets ANTHROPIC_API_KEY only, verifies auth_method="api_key"
- `test_from_env_oauth_scrubs_api_key` — sets both, verifies ANTHROPIC_API_KEY removed from env
- `test_from_env_neither_exits` — neither set, verifies SystemExit
- `test_from_env_oauth_priority` — both set, verifies OAuth wins

### 4. No changes to

- `runtime/base.py` — interface unchanged
- `hooks.py` — already uses `{**os.environ}`, scrubbing at process start covers it
- `tools.py`, `config.py`, `subagents.py` — unaffected
- `escalation-boundary.py` — unaffected

---

## Backwards compatibility

- Existing users with only `ANTHROPIC_API_KEY` see no behavior change
- `ClaudeCodeRuntime(client)` still works (auth_method defaults to "api_key")
- Existing tests pass unchanged (they use `FakeClient`, not `from_env()`)

---

## What this does NOT do

- Token refresh/rotation (OAuth tokens are assumed long-lived or externally managed)
- Custom base URL for subscription API (same API endpoint, different auth)
- Changes to the Runtime interface contract
- Changes to §5 hook enforcement

---

## Verification criteria

1. 47 existing tests still pass (no regression)
2. New runtime tests pass (5 tests for auth resolution)
3. Hook still enforces (Bash curl blocked, workspace write allowed)
4. `python -m praxis` logs auth path to stderr on startup
5. With both env vars set, OAuth wins and API key is scrubbed from env
