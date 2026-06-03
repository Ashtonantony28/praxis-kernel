#!/usr/bin/env bash
# Runs run_session.py in a loop. On rate limit, waits 6 hours then retries.

set -uo pipefail

PROJECT_DIR="${1:-.}"
LOG="overnight.log"
RETRY_WAIT=21600  # 6 hours — safely past the 5-hour window reset

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

  if [ "$EXIT_CODE" -ne 0 ]; then
    # Check if log mentions session/rate limit
    if tail -20 "$LOG" | grep -qiE "session limit|rate.limit|resets|quota|429|exceeded|overloaded"; then
      echo "" | tee -a "$LOG"
      echo "=== Rate limit detected $(date). Waiting 6 hours... ===" | tee -a "$LOG"
      sleep $RETRY_WAIT
      echo "=== Retrying $(date) ===" | tee -a "$LOG"
    else
      echo "=== Exited with code $EXIT_CODE $(date). Waiting 60s then retrying... ===" | tee -a "$LOG"
      sleep 60
    fi
    continue
  fi

  # Clean exit — brief pause then continue
  sleep 10
done