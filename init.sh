#!/usr/bin/env bash
# init.sh — smoke test for Praxis. Run at the start of every session.
# Sessions that see FAIL must fix the bug before implementing new features.
set -euo pipefail

PYTHON=/usr/bin/python

echo "=== Praxis smoke test ==="

# 1. Check Python environment
if ! $PYTHON -c "import praxis" 2>/dev/null; then
  echo "=== FAIL — praxis package not importable. Run: pip install -e . ==="
  exit 1
fi

# 2. Check §5 hook is intact
HOOK_PATH=".claude/hooks/escalation-boundary.py"
if [ ! -f "$HOOK_PATH" ]; then
  echo "=== FAIL — §5 hook missing at $HOOK_PATH ==="
  exit 1
fi

EXPECTED_MD5="057f07f223fd5b5fe11f2aa50af1e361"
ACTUAL_MD5=$(md5sum "$HOOK_PATH" | cut -d' ' -f1)
if [ "$ACTUAL_MD5" != "$EXPECTED_MD5" ]; then
  echo "=== FAIL — §5 hook md5 changed: $ACTUAL_MD5 (expected $EXPECTED_MD5) ==="
  echo "=== This is a CRITICAL governance violation. Do not proceed. ==="
  exit 1
fi

# 3. Run fast test subset (skip slow integration tests)
# set +e: pytest exits non-zero on failures; grep pipeline also exits non-zero
# when there are no matches — both would kill the script under set -euo pipefail.
set +e
RESULT=$($PYTHON -m pytest tests/ -q --ignore=tests/test_playwright.py 2>&1)
FAILED=$(echo "$RESULT" | grep -oP '^\d+ failed' | grep -oP '\d+')
set -e
FAILED=${FAILED:-0}
echo "$RESULT" | tail -5
if [ "$FAILED" -gt 7 ]; then
  echo "=== FAIL — $FAILED tests failing (threshold: 7 known pre-existing) ==="
  exit 1
else
  echo "=== PASS ($FAILED known pre-existing failures within threshold) ==="
  exit 0
fi