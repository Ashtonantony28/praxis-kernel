import asyncio
import os
import sys
import random
from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AgentDefinition,
    AssistantMessage,
    TextBlock,
    ResultMessage,
)

# ── PROFILE: Governed (hard regulations found — §5, control-plane, OAuth) ────
PERMISSION_MODE        = "acceptEdits"   # Governed: edit but gate risky actions.
AUTO_LOOP              = False           # Governed: one cycle then stop for review.
MAX_TURNS              = 40             # Hard cap per orchestrator cycle.
ALLOW_FANOUT           = False          # Governed: Task subagents only — claude -p
                                        # fan-out is harder to gate per-action.
FANOUT_CONCURRENCY     = 3             # Max parallel claude -p processes (unused when
                                        # ALLOW_FANOUT=False but kept for future).
FANOUT_PER_INVOC_TURNS = 30            # --max-turns for each fan-out worker.
# ─────────────────────────────────────────────────────────────────────────────

# ── RETRY: survive transient rate-limit hits without losing state ─────────────
RETRY_ON = (
    "rate", "quota", "limit", "429", "exceeded",
    "overloaded", "503", "529", "timeout", "connection", "unavailable", "turns",
)
RETRY_BASE_DELAY  = 60    # seconds — first wait
RETRY_MAX_DELAY   = 600   # seconds — cap at 10 minutes
RETRY_JITTER      = 30    # seconds — random jitter (thundering-herd avoidance)
# ─────────────────────────────────────────────────────────────────────────────


def check_auth() -> None:
    """Refuse to run on a metered API key — applies to both SDK calls and any
    claude -p invocations the orchestrator launches."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ANTHROPIC_API_KEY is set and would override your Claude subscription,\n"
            "running this loop on uncapped per-token billing.\n"
            "Run:  unset ANTHROPIC_API_KEY   then re-run.\n"
            "Authenticate the subscription with:  claude setup-token\n"
            "(sets CLAUDE_CODE_OAUTH_TOKEN, used by both SDK and claude -p)"
        )
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print(
            "Note: CLAUDE_CODE_OAUTH_TOKEN not in env. If you haven't run\n"
            "`claude setup-token`, do so before running unattended.\n"
        )


# ── Orchestrator system prompt — KEEP LEAN ───────────────────────────────────
# 5-minute cache TTL means this gets re-billed on most cycle gaps.
# Worker-applicable rules live in CLAUDE.md (loaded via setting_sources).
_FANOUT_STATE = "ENABLED" if ALLOW_FANOUT else "DISABLED (Governed profile — use Task subagents)"

ORCHESTRATOR_SYSTEM = f"""
You orchestrate; you do NOT implement. Optimize for correctness first, then for
minimum rate-limit pool consumption. Default model: Sonnet. Escalate to Opus ONLY
for genuinely ambiguous architectural decisions or Scenario A reconciliation.

Each cycle:
1. Read PLAN.md, TASKS.md, STATUS.md. Read nothing else unless a decision requires it.
2. Evaluate the latest STATUS.md entries against PLAN.md's definition of done.
3. Select next task(s) whose dependencies are complete.
4. Choose dispatch mode and dispatch (see below).
5. Reconcile results into STATUS.md and TASKS.md.

DISPATCH MODE — pick by shape of work:

(A) `Task` SUBAGENT — for one task, or 2-4 heterogeneous tasks needing coordination.
    Use `implementer` (Sonnet), `reviewer` (Haiku), or `auditor` (Haiku).
    Tight feedback loop; respects permission_mode. This is the default.

(B) FAN-OUT `claude -p` SCRIPT — fan-out is {_FANOUT_STATE}.
    (Governed profile: prefer Task subagents. Fan-out is harder to gate per-action.)

(C) SEQUENTIAL `claude -p` PIPELINE — for multi-stage work where stage K's JSON
    output feeds stage K+1. Use sparingly; each stage pays its own ~20k startup.

BRIEFING RULE (critical for pool efficiency):
- Brief workers by EMBEDDING the relevant PLAN.md slice as a quoted block in the
  worker prompt, plus exact file paths the worker needs.
- NEVER tell a worker to "read PLAN.md" — that forces it to load the entire doc
  just to find one section, paid in full per worker.

PARALLELISM RULES:
- NEVER dispatch the same task or same slice to two workers in parallel.
- NEVER parallelize two workers editing the same file.
- BATCH small sequential tasks that touch overlapping files into ONE worker.

ROUTING:
- Sonnet: implementer (default for all building work)
- Haiku: auditor, reviewer (read-only passes)
- Opus: ONLY for ambiguous architecture decisions or Scenario A reconciliation

AFTER WORKERS RETURN:
- Confirm TASKS.md was updated (flip [ ] to [x] for any fan-out items).
- Write a concise one-line evaluation per task to STATUS.md.
- Every 5 completed tasks: dispatch `reviewer` to verify STATUS claims match code.
- When STATUS.md exceeds ~3000 tokens (~12 KB): COMPACT — move entries older than
  the last 5 tasks to STATUS_archive.md.

GOVERNED PROFILE RULES:
- This project has hard regulations in PLAN.md. Honor every constraint.
- §5 boundary is inviolable. Workers PAUSE for: writes outside WORKSPACE_ROOT;
  egress to non-allowlisted domain; spending beyond cap; sends/publishes as user;
  secrets movement; control-plane modifications; shared/production state.
- CONTROL-PLANE EDITS ARE HUMAN-APPLIED. No agent edits .claude/hooks/,
  .claude/settings.json, or permission rules. If needed, write the exact patch
  to STATUS.md under "NEEDS HUMAN: control-plane change" and stop.
- Never auto-run actions that are both irreversible AND destructive.
- Never print, log, or commit any credential.

SCENARIO A — first run:
The auditor must verify the baseline [x] items in TASKS.md against the actual repo
before any Phase H implementation begins. Dispatch the auditor first if STATUS.md
still shows the provisional baseline note.
"""

WORKER_PROMPT = """
You implement ONE task (or one batched spec). The orchestrator has briefed you with
everything you need. Do not read PLAN/TASKS/STATUS — they will not give you anything
the orchestrator didn't already include. Implement, then follow the finishing
contract in CLAUDE.md. Be terse: no step narration, one sentence of chat confirming
completion.
"""

REVIEWER_PROMPT = """
Read-only verification. Read the most recent STATUS.md entries and the actual code
they describe. Produce a concise structured assessment: what is correct, what is
missing or wrong, what correction tasks are needed. Do not modify code.
"""

AUDITOR_PROMPT = """
Read-only inventory of the existing codebase. Produce a factual snapshot appended to
STATUS.md: what exists, what works, which TASKS.md [x] items are actually present.
Use Grep/Glob to navigate; do not read whole directories. Do not modify code other
than appending to STATUS.md. Report any [x] items not found in the real code.
"""

AGENTS = {
    "implementer": AgentDefinition(
        description="Implements one development task or a batched spec: writes code, edits files, runs tests.",
        prompt=WORKER_PROMPT,
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        model="sonnet",
    ),
    "auditor": AgentDefinition(
        description="Read-only. Inventories the existing codebase and appends a factual baseline to STATUS.md.",
        prompt=AUDITOR_PROMPT,
        tools=["Read", "Glob", "Grep", "Edit"],  # Edit only to append STATUS.md
        model="haiku",
    ),
    "reviewer": AgentDefinition(
        description="Read-only. Verifies STATUS.md claims match the real code.",
        prompt=REVIEWER_PROMPT,
        tools=["Read", "Glob", "Grep"],
        model="haiku",
    ),
}


def is_retriable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(keyword in msg for keyword in RETRY_ON)


async def run_cycle(prompt: str) -> bool:
    """Run one orchestrator cycle. Returns True on clean completion, False if
    the session window was exhausted (resume marker written to STATUS.md)."""
    options = ClaudeAgentOptions(
        system_prompt=ORCHESTRATOR_SYSTEM,
        model="claude-sonnet-4-6",          # Sonnet default — 5× more pool-efficient
                                            # than Opus. Escalate per-prompt only when
                                            # architectural reasoning genuinely needs it.
        allowed_tools=["Read", "Edit", "Write", "Bash", "Task"],
        agents=AGENTS,
        permission_mode=PERMISSION_MODE,
        setting_sources=["project"],        # loads CLAUDE.md into orchestrator + workers
        max_turns=MAX_TURNS,
        cwd=".",
    )
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text)
            elif isinstance(message, ResultMessage):
                print("\n── cycle complete ──")
        return True
    except Exception as e:
        msg = str(e).lower()
        if any(s in msg for s in ("rate", "quota", "limit", "429", "exceeded",
                                   "overloaded", "503", "529")):
            from datetime import datetime
            marker = (
                f"\n\n## RESUME MARKER ({datetime.utcnow().isoformat()}Z)\n"
                f"- Session window or rate limit hit mid-cycle\n"
                f"- Error: {e!r}\n"
                f"- Re-run `python orchestrate.py` after your 5-hour window resets.\n"
                f"  The orchestrator reads TASKS.md and STATUS.md fresh each run.\n"
            )
            try:
                with open("STATUS.md", "a") as f:
                    f.write(marker)
            except OSError:
                pass
            print(
                "\n── session limit hit. Resume marker written to STATUS.md.\n"
                "   Re-run after your 5-hour window resets. ──"
            )
            return False
        raise


async def run_cycle_with_retry(prompt: str) -> bool:
    """Retry indefinitely on transient errors (overload, network, turns cap).
    Only session-pool exhaustion (rate/quota/429) writes a resume marker and stops.
    Ctrl+C always propagates cleanly."""
    delay = RETRY_BASE_DELAY
    attempt = 0
    while True:
        try:
            return await run_cycle(prompt)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if not is_retriable(exc):
                raise
            attempt += 1
            jitter = random.uniform(0, RETRY_JITTER)
            wait = min(delay, RETRY_MAX_DELAY) + jitter
            print(
                f"\n── retriable error (attempt {attempt}): {exc}\n"
                f"── waiting {wait:.0f}s before retry (Ctrl+C to stop) ──",
                flush=True,
            )
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                raise
            delay = min(delay * 2, RETRY_MAX_DELAY)


def has_open_tasks() -> bool:
    try:
        with open("TASKS.md") as f:
            return "- [ ]" in f.read()
    except FileNotFoundError:
        return False


async def main(goal: str | None):
    check_auth()

    first = goal or (
        "Read PLAN.md, TASKS.md, STATUS.md. "
        "If STATUS.md still shows the provisional baseline note, dispatch the "
        "'auditor' to verify the baseline [x] items against the actual repo before "
        "beginning any Phase H work. "
        "Otherwise evaluate progress and dispatch the next appropriate task(s). "
        "Brief workers with embedded PLAN.md slices, not file pointers."
    )

    try:
        ok = await run_cycle_with_retry(first)
        if not ok:
            return

        if AUTO_LOOP:
            while has_open_tasks():
                await asyncio.sleep(3)
                ok = await run_cycle_with_retry(
                    "Read PLAN.md, TASKS.md, STATUS.md. Evaluate latest results and "
                    "dispatch the next task(s). Compact STATUS.md if it has grown past "
                    "~3000 tokens (~12 KB). Brief workers with embedded slices."
                )
                if not ok:
                    return
    except KeyboardInterrupt:
        print("\n── interrupted by user — stopping cleanly ──")
        sys.exit(0)


if __name__ == "__main__":
    goal = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    asyncio.run(main(goal))
