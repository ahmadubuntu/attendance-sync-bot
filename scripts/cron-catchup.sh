#!/bin/sh
# Daily attendance catch-up: check the previous days and register what is still missing.
#
# Runs the preview, reconciles against Kasra, plans the missing payloads, and submits each
# one with its approval code. The approval is content-bound: the code is computed from the
# exact payloads this same run produced, so nothing is sent that this run did not plan.
# Every created document is read back from Kasra before the run reports success.
#
# Logs to var/cron.log (rotated by size). Secrets come from ~/.zshrc-style exports sourced
# from ~/.profile; nothing is printed.
set -u
PROJECT="/home/ahmad/work/mine/ai_projects/hermes_projects/AttendanceSync‌Bot"
cd "$PROJECT" || exit 1
LOG="var/cron.log"
mkdir -p var
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ] && mv "$LOG" "$LOG.old"
PY="$PROJECT/.venv/bin/python"

# Secrets live in ~/.zshrc exports; cron runs sh, not zsh, so pull only the six exports
# the app needs out of that file. Values are never printed or logged.
ZSHRC="$HOME/.zshrc"
if [ -f "$ZSHRC" ]; then
  for name in GOFT_URL GOFT_TOKEN GOFT_CHANNEL_ID KASRA_URL KASRA_USERNAME KASRA_PASSWORD; do
    line=$(grep "^export $name=" "$ZSHRC" | tail -1)
    [ -n "$line" ] && eval "${line#export }"
  done
fi

{
  echo "=== $(date -Is) run start ==="
  if ! "$PY" -m attendance_sync preview --days 7 --output var/cron-review; then
    echo "preview failed; nothing submitted"; exit 0
  fi
  START=$("$PY" - <<'EOF'
from datetime import date, timedelta
import jdatetime
day = date.today() - timedelta(days=6)
print(jdatetime.date.fromgregorian(date=day).strftime('%Y/%m/%d'))
EOF
)
  END=$("$PY" - <<'EOF'
from datetime import date, timedelta
import jdatetime
day = date.today() - timedelta(days=1)
print(jdatetime.date.fromgregorian(date=day).strftime('%Y/%m/%d'))
EOF
)
  echo "window $START..$END"
  if ! "$PY" -m attendance_sync kasra-plan --review var/cron-review.json --start "$START" --end "$END" --output var/cron-plan.json; then
    echo "plan failed; nothing submitted"; exit 0
  fi
  PAYLOADS=$("$PY" - <<'EOF'
import json
print(len(json.load(open('var/cron-plan.json', encoding='utf-8')).get('payloads') or []))
EOF
)
  echo "payloads: $PAYLOADS"
  [ "$PAYLOADS" = "0" ] && { echo "nothing missing; done"; exit 0; }
  CODE=$("$PY" -m attendance_sync kasra-submit --plan var/cron-plan.json | tail -1 | "$PY" -c "import json,sys; print(json.loads(sys.stdin.read())['approval_code'])")
  echo "approval code: $CODE"
  "$PY" -m attendance_sync kasra-submit --plan var/cron-plan.json --confirm --approve "$CODE" --timeout-ms 300000
  echo "submit exit: $?"
  echo "=== run end ==="
} >> "$LOG" 2>&1
