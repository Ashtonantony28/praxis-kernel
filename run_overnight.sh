#!/usr/bin/env bash
# Runs run_session.py in a loop. On rate limit, waits 6 hours then retries.
# Run this before sleeping. Kill it in the morning with Ctrl+C or kill <pid>.

set -uo pipefail

PROJECT_DIR="${1:-.}"
LOG="overnight.log"
RETRY_WAIT=21600  # 6 hours in seconds — safely past the 5-hour window reset

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: ANTHROPIC_API_KEY is set. Run: unset ANTHROPIC_API_KEY"
  exit 1
fi

echo "=== Overnight run started $(date) ===" | tee -a "$LOG"
echo "Logs: $LOG — kill this terminal or Ctrl+C to stop" | tee -a "$LOG"

while true; do
  REMAINING=$(/usr/bin/python -c "
import json
f = json.load(open('feature_list.json'))
print(len([x for x in f if not x['passes']]))
" 2>/dev/null || echo "unknown")

  if [ "$REMAINING" = "0" ]; then
    echo "=== All features complete $(date) ===" | tee -a "$LOG"
    exit 0
  fi

  echo "" | tee -a "$LOG"
  echo "--- Session start $(date) — $REMAINING features remaining ---" | tee -a "$LOG"

  /usr/bin/python run_session.py "$PROJECT_DIR" 2>&1 | tee -a "$LOG"
  EXIT_CODE=${PIPESTATUS[0]}

  # Check if rate limit was hit (run_session.py writes INTERRUPTED to progress)
  if grep -q "INTERRUPTED" claude-progress.txt 2>/dev/null; then
    LAST=$(tail -5 claude-progress.txt)
    if echo "$LAST" | grep -q "INTERRUPTED"; then
      echo "" | tee -a "$LOG"
      echo "=== Rate limit hit $(date). Waiting 6 hours before retry... ===" | tee -a "$LOG"
      sleep $RETRY_WAIT
      echo "=== Retrying $(date) ===" | tee -a "$LOG"
      continue
    fi
  fi

  if [ "$EXIT_CODE" -ne 0 ]; then
    echo "=== run_session.py exited with code $EXIT_CODE. Waiting 60s then retrying... ===" | tee -a "$LOG"
    sleep 60
    continue
  fi

  # Clean exit — brief pause then continue
  sleep 10
done
