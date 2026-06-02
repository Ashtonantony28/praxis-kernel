#!/usr/bin/env python3
# .claude/hooks/pre-compact.py
# Runs before context compaction. Archives the full transcript so nothing is lost.
# IMPORTANT: This file must be ADDED to .claude/settings.json hooks section.
# Do NOT overwrite settings.json — merge this with the existing file to preserve
# the §5 escalation-boundary hook registration.
import json
import sys
import os
import datetime

data = json.load(sys.stdin)
transcript_path = data.get("transcript_path", "")
session_id = data.get("session_id", "unknown")

if transcript_path and os.path.exists(transcript_path):
    archive_dir = ".claude/compaction-archive"
    os.makedirs(archive_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = f"{archive_dir}/{timestamp}-{session_id[:8]}.jsonl"
    with open(transcript_path) as f:
        content = f.read()
    with open(dest, "w") as f:
        f.write(content)
    print(f"Archived transcript to {dest}", file=sys.stderr)

sys.exit(0)  # Always exit 0 — compaction must proceed
