# H-3 Pipeline Report — Five-Subagent Live Test

**Date:** 2026-05-25
**Branch:** `claude/blissful-franklin-VIMiH`
**Auth:** OAuth session token (`CLAUDE_CODE_OAUTH_TOKEN`)
**Model:** `claude-haiku-4-5-20251001`

---

## What was tested

Full Scout → Planner → Builder → Verifier → Scribe pipeline on a real
task (review the H-1 retry logic), using live API calls via
`python -m praxis`.

## Results by stage

### Stage 1: Scout — PASSED

Orchestrator spawned Scout via Agent tool. Scout read
`praxis/runtime/claude_code.py` and returned a correct analysis:

> `_create_with_retry` wraps `messages.create()` with exponential
> backoff retry logic — up to 4 tries (3 retries + 1 initial),
> triggered on 429 rate-limit errors, with delays starting at 5s
> doubling each time, capped at 60s. Raises SystemExit after
> exhausting retries.

Full subagent path confirmed: orchestrator → Agent tool → §5 hook →
Scout spawned → Scout uses Read tool → result bubbles back.

### Stage 2: Planner — BLOCKED (rate limit)

Three attempts, all rate-limited after 35s of backoff (5+10+20s).
The orchestrator's initial API call consumed the rate limit budget;
the subagent's API call (which must follow immediately) found no
remaining budget.

### Stages 3–5: Builder, Verifier, Scribe — NOT REACHED

Blocked by the same rate limit constraint.

---

## Root cause analysis

**The pipeline architecture works. The rate limit budget does not.**

OAuth session tokens (shared with the active Claude Code session)
have an effective budget of ~1 API request per rate-limit window.
Every subagent call requires a minimum of 2 rapid API calls:

1. Orchestrator call → model decides to use Agent tool
2. Subagent call → scout/planner/etc does its work

These happen within milliseconds of each other. Even with H-1's
exponential backoff (35s total wait), the budget does not recover
fast enough.

Key evidence:
- Simple one-shot calls (no tool use) succeed consistently
- The first subagent call (Scout) succeeded because it ran cold
  (no recent API calls)
- All subsequent subagent calls failed, even with 60+ second gaps

The H-1 retry logic itself performed correctly:
- Timings: 5s → 10s → 20s as designed
- Clean error messages
- Correct exit code (1)
- But the retries themselves consume additional budget, compounding
  the problem (4 requests per failed attempt)

---

## What H-1 and H-2 delivered

### H-1: Retry on rate limit — VERIFIED WORKING
- `_create_with_retry()` method on ClaudeCodeRuntime
- Exponential backoff: 5s, 10s, 20s (3 retries)
- Clean SystemExit after exhaustion
- Logged to stderr at each retry
- Tests: 2 new tests (retries-then-exits, retry-then-succeed)

### H-2: Context window management — VERIFIED IN TESTS, UNTESTED LIVE
- Sliding window compaction at 40 messages
- Keeps first message + last 10 verbatim
- Older exchanges summarized into compact header
- No information silently lost (summary includes tool names)
- Tests: 5 new tests (ClaudeCodeRuntime) + 4 new tests (LocalRuntime)
- Not exercised under real load — pipeline never ran long enough

---

## Honest assessment: readiness for unattended overnight operation

**NOT READY.** Three gaps remain:

### Gap 1: Rate limit budget (critical)
The OAuth session token cannot support multi-subagent pipelines.
A 5-agent pipeline needs ~10-15 API calls in a few minutes.
The current budget supports ~1 per window.

**Fix options:**
- Dedicated API key (`ANTHROPIC_API_KEY`) with higher rate limits
- Add configurable inter-subagent delay (e.g., 30s between stages)
- Implement request queuing in the runtime

### Gap 2: Retry budget too small for sustained rate limits
35 seconds (5+10+20) is insufficient when rate limits persist for
minutes. For unattended operation, the backoff should extend further
(e.g., 5/10/20/40/60 over 5 retries = 135 seconds total).

### Gap 3: H-2 context management unproven under load
The compaction logic passes 9 unit tests covering both runtimes,
but has never fired during a real multi-agent run. First real
trigger may surface edge cases with Anthropic's message format
requirements (strict user/assistant alternation).

---

## Test count

| Phase | Tests |
|-------|-------|
| Pre-H | 116 |
| H-1 (retry) | +1 |
| H-2 (context) | +9 |
| **Total** | **126** |

All 126 tests pass.
