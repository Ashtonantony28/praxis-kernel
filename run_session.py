"""Praxis V2 — autonomous session loop.

Each call to run_coding_session() launches one claude -p session that:
  1. Orients from files (claude-progress.txt, ARCHITECTURE.md, CONSTRAINTS.md)
  2. Picks the next feature from feature_list.json
  3. Implements it, runs tests, updates feature_list.json, commits
  4. Exits cleanly

run_project_loop() runs sessions back-to-back until all features pass.

Usage:
    python run_session.py .                 # run until done or rate limit
    python run_session.py . --once          # one session then stop (for review)
    python phase_orchestrator.py .          # multi-phase with handoffs
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ResultMessage,
    query,
)

# ── Profile ───────────────────────────────────────────────────────────────────
# Governed: acceptEdits + AUTO_LOOP=False (human reviews between sessions)
# Autonomous: bypassPermissions + AUTO_LOOP=True (overnight unattended)
# §5 hook fires regardless of permission_mode.
PERMISSION_MODE = "bypassPermissions"   # Safe: §5 hook enforces boundaries
AUTO_LOOP       = True                  # Set False for single-session review
MAX_TURNS       = 80                    # Per-session cap; enough for full-feature work
# ─────────────────────────────────────────────────────────────────────────────

RATE_LIMIT_KEYWORDS = (
    "rate", "quota", "limit", "429", "exceeded",
    "overloaded", "503", "529", "timeout",
)


def check_auth() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ANTHROPIC_API_KEY is set and overrides your Claude subscription.\n"
            "Run: unset ANTHROPIC_API_KEY\n"
            "Auth via: claude setup-token (sets CLAUDE_CODE_OAUTH_TOKEN)"
        )
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print(
            "Note: CLAUDE_CODE_OAUTH_TOKEN not in env.\n"
            "Run `claude setup-token` before unattended use.\n"
        )


def features_remaining(project_dir: str) -> int:
    """Return count of features where passes=false. -1 if file not found."""
    try:
        features = json.loads(Path(project_dir, "feature_list.json").read_text())
        return len([f for f in features if not f.get("passes", False)])
    except FileNotFoundError:
        return -1


def next_feature_id(project_dir: str) -> str | None:
    """Return the ID of the next feature to implement, or None if all done."""
    try:
        features = json.loads(Path(project_dir, "feature_list.json").read_text())
        done = {f["id"] for f in features if f.get("passes", False)}
        candidates = [
            f for f in features
            if not f.get("passes", False)
            and all(dep in done for dep in f.get("depends_on", []))
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda f: f["priority"])["id"]
    except (FileNotFoundError, KeyError):
        return None


def append_resume_marker(project_dir: str, session_num: int, reason: str) -> None:
    marker = (
        f"\n--- INTERRUPTED ({datetime.utcnow().isoformat()}Z) ---\n"
        f"Session {session_num} stopped: {reason}\n"
        f"State is on disk. Re-run `python run_session.py .` after window resets.\n"
        f"---\n"
    )
    try:
        with open(Path(project_dir, "claude-progress.txt"), "a") as f:
            f.write(marker)
    except OSError:
        pass


async def run_coding_session(project_dir: str) -> dict:
    """Run one coding session. Returns {is_error, subtype, result}."""
    prompt = Path(project_dir, "prompts", "coding-session.md").read_text()
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Task"],
        permission_mode=PERMISSION_MODE,
        max_turns=MAX_TURNS,
        setting_sources=["project"],   # auto-loads CLAUDE.md
        model="claude-sonnet-4-6",     # Sonnet for implementation; Opus only for synthesis
        cwd=project_dir,
    )
    result_text: list[str] = []
    is_error = False
    subtype = ""

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        print(block.text, end="", flush=True)
                        result_text.append(block.text)
            elif isinstance(message, ResultMessage):
                is_error = getattr(message, "is_error", False)
                subtype = getattr(message, "subtype", "")
    except Exception as exc:
        msg = str(exc).lower()
        if any(kw in msg for kw in RATE_LIMIT_KEYWORDS):
            return {"is_error": True, "subtype": "rate_limit", "result": str(exc)}
        raise

    return {"is_error": is_error, "subtype": subtype, "result": "\n".join(result_text)}


async def run_project_loop(project_dir: str, max_sessions: int = 500) -> None:
    """Run sessions in a loop until all features pass or rate limit hit."""
    for session_num in range(1, max_sessions + 1):
        remaining = features_remaining(project_dir)
        if remaining == 0:
            print(f"\n✓ All features complete after {session_num - 1} sessions.")
            return
        if remaining < 0:
            print("feature_list.json not found. Run the initializer first.")
            return

        next_id = next_feature_id(project_dir)
        if next_id is None:
            print(
                f"\nNo eligible features (all remaining have unmet dependencies).\n"
                f"Remaining: {remaining}. Check feature_list.json depends_on fields."
            )
            return

        print(f"\n{'='*55}")
        print(f"Session {session_num} — next: {next_id} — {remaining} remaining — {datetime.now().strftime('%H:%M')}")
        print(f"{'='*55}\n")

        result = await run_coding_session(project_dir)
        subtype = result.get("subtype", "")

        if subtype == "rate_limit":
            append_resume_marker(project_dir, session_num, "rate limit / session window exhausted")
            print(
                "\n── Rate limit hit. State is safe on disk. ──\n"
                "   Re-run after your 5-hour pool window resets:\n"
                "   python run_session.py .\n"
            )
            return
        elif subtype == "error_during_execution":
            print(f"\nSession {session_num} errored. Retrying in 30s...")
            await asyncio.sleep(30)
            continue
        elif subtype == "error_max_turns":
            print(f"\nSession {session_num} hit turn cap — partial work. Continuing to next session.")
        else:
            print(f"\n✓ Session {session_num} complete.")

        if not AUTO_LOOP:
            print("AUTO_LOOP=False — stopping after one session.")
            return

        await asyncio.sleep(5)  # Brief pause between sessions


if __name__ == "__main__":
    check_auth()
    import argparse
    parser = argparse.ArgumentParser(description="Praxis V2 session loop")
    parser.add_argument("project_dir", nargs="?", default=".", help="Project root directory")
    parser.add_argument("--once", action="store_true", help="Run one session then stop")
    args = parser.parse_args()

    if args.once:
        # Override AUTO_LOOP for single-session review
        AUTO_LOOP = False  # noqa: F841 — intentional module-level override

    try:
        asyncio.run(run_project_loop(args.project_dir))
    except KeyboardInterrupt:
        print("\n── Interrupted. State is safe on disk. Re-run to continue. ──")
        sys.exit(0)
