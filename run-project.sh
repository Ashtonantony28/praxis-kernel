#!/usr/bin/env bash
set -uo pipefail

PROJECT_DIR="${1:-.}"
PROMPT_FILE="${PROJECT_DIR}/prompts/coding-session.md"
LOG_FILE="${PROJECT_DIR}/agent-run.log"
MAX_SESSIONS="${MAX_SESSIONS:-500}"
SESSION_COUNT=0

cd "$PROJECT_DIR"

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: ANTHROPIC_API_KEY is set. Run: unset ANTHROPIC_API_KEY"
  exit 1
fi

echo "Starting session loop. Log: $LOG_FILE"

while [ "$SESSION_COUNT" -lt "$MAX_SESSIONS" ]; do
  SESSION_COUNT=$((SESSION_COUNT + 1))

  REMAINING=$(/usr/bin/python -c "
import json
features = json.load(open('feature_list.json'))
print(len([f for f in features if not f.get('passes', False)]))" 2>/dev/null || echo "unknown")

  if [ "$REMAINING" = "0" ]; then
    echo "All features complete after $SESSION_COUNT sessions."
    break
  fi

  echo ""
  echo "=========================================="
  echo "Session $SESSION_COUNT — $(date) — $REMAINING remaining"
  echo "=========================================="

  RESULT=$(claude -p "$(cat "$PROMPT_FILE")" \
    --allowedTools "Read,Write,Edit,Bash,Glob,Task" \
    --max-turns 80 \
    --dangerously-skip-permissions \
    --output-format json \
    2>>"$LOG_FILE") || true

  SUBTYPE=$(/usr/bin/python -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('subtype', ''))
except:
    print('')
" << JSON_EOF
$RESULT
JSON_EOF
  )

  case "$SUBTYPE" in
    error_during_execution)
      echo "Session $SESSION_COUNT failed. Retrying in 30s..."
      echo "$(date) | FAIL | Session $SESSION_COUNT" >> "$LOG_FILE"
      sleep 30
      ;;
    error_max_turns)
      echo "Session $SESSION_COUNT hit max_turns — partial. Continuing..."
      echo "$(date) | MAX_TURNS | Session $SESSION_COUNT" >> "$LOG_FILE"
      sleep 5
      ;;
    *)
      if echo "$RESULT" | grep -qi "rate.limit\|session limit\|resets [0-9]"; then
        echo "Rate limit hit. Re-run after your window resets."
        echo "$(date) | RATE_LIMIT | Session $SESSION_COUNT" >> "$LOG_FILE"
        exit 0
      fi
      echo "Session $SESSION_COUNT complete."
      echo "$(date) | OK | Session $SESSION_COUNT" >> "$LOG_FILE"
      sleep 5
      ;;
  esac
done
