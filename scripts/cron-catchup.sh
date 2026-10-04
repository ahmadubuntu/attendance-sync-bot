#!/bin/bash
# Daily attendance catch-up: check the previous days and register what is still missing.
#
# Runs the preview, reconciles against Kasra, plans the missing payloads, and submits each
# one with its approval code. The approval is content-bound: the code is computed from the
# exact payloads this same run produced, so nothing is sent that this run did not plan.
# Every created document is read back from Kasra before the run reports success.
#
# Logs to var/cron.log (rotated by size). Nothing secret is printed.
# bash, not sh: the environment guard below uses ${!name-} to report a missing variable by
# name, and on Ubuntu /bin/sh is dash, which parses that as a syntax error and exits before a
# single line of the log is written.
set -u

# Resolve the project directory from this script's own location, so the cron entry keeps
# working if the checkout moves and no absolute path has to be edited by hand.
PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT" || exit 1

LOG="var/cron.log"
mkdir -p var
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ] && mv "$LOG" "$LOG.old"
PY="$PROJECT/.venv/bin/python"

# How far back the run looks. The default is the ordinary daily window: the last week, of
# which only yesterday onward can still be registered. A catch-up run overrides it to cover the
# stretch that needs making good -- without that, days older than a week would never be looked
# at again and a fix that lands late would leave a hole in the record forever.
DAYS="${AS_DAYS:-7}"

# Cron runs a minimal shell with no login profile, so pull the six exports the app needs
# out of ~/.bashrc. Values are never printed or logged.
if [ -f "$HOME/.bashrc" ]; then
  . "$HOME/.bashrc" 2>/dev/null || true
fi

# Credentials for the unattended run. They live only in this 0600 file, never in the repo and
# never in the log; the values are sourced, not printed. The environment is checked first so an
# interactive run keeps working without the file.
ENV_FILE="${AS_ENV_FILE:-$HOME/.config/attendance-sync/cron.env}"
if [ -f "$ENV_FILE" ]; then
  . "$ENV_FILE"
fi

# Verify required environment variables are set before touching Kasra.
for name in GOFT_URL GOFT_TOKEN GOFT_CHANNEL_ID KASRA_URL KASRA_USERNAME KASRA_PASSWORD; do
  # ${!name-} rather than $name: under `set -u` an unset variable aborts the whole script with
  # "unbound variable" before the guard below can report which one is missing.
  eval value=\${!name-}
  [ -n "$value" ] || { echo "Missing env var: $name" >> "$LOG"; exit 1; }
done

# Run the CLI without relying on an editable install: the package lives in ./src.
APP="import sys; sys.path.insert(0, '$PROJECT/src'); from attendance_sync.cli import main; main()"

notify() {
  command -v notify-send >/dev/null 2>&1 || return 0
  notify-send "AttendanceSync cron" "$1" || true
}

# Every run stamps the log with its end marker, including the early exits, so a log with a
# start but no end means the run died rather than finished.
finish() {
  echo "=== $(date -Is) run end ==="
}

jalali_days_ago() {
  "$PY" - "$1" <<'EOF'
import sys
from datetime import date, timedelta
import jdatetime
print(jdatetime.date.fromgregorian(date=date.today() - timedelta(days=int(sys.argv[1]))).strftime('%Y/%m/%d'))
EOF
}

{
  echo "=== $(date -Is) run start ==="

  if ! "$PY" -c "$APP" preview --days "$DAYS" --output var/cron-review; then
    echo "preview failed; nothing submitted"
    notify "AttendanceSync: preview failed, check var/cron.log"
    finish
    exit 0
  fi

  START=$(jalali_days_ago $((DAYS - 1)))
  END=$(jalali_days_ago 1)
  echo "window $START..$END (looking back $DAYS days)"

  if ! "$PY" -c "$APP" kasra-plan --review var/cron-review.json --start "$START" --end "$END" --output var/cron-plan.json; then
    echo "plan failed; nothing submitted"
    notify "AttendanceSync: plan failed, check var/cron.log"
    finish
    exit 0
  fi

  PAYLOADS=$("$PY" - <<'EOF'
import json
print(len(json.load(open('var/cron-plan.json', encoding='utf-8')).get('payloads') or []))
EOF
)
  echo "payloads: $PAYLOADS"

  if [ "$PAYLOADS" = "0" ]; then
    echo "nothing missing; done"
    notify "AttendanceSync: nothing to register"
    finish
    exit 0
  fi

  # Submit one payload per browser session: one session that saves several documents in a
  # row drags a modal overlay across saves, so the read-back of the later ones fails.
  # The loop itself lives in attendance_sync.cron_submit so it can be tested without a
  # browser or a network; AS_SRC carries the src dir into its children.
  export AS_SRC="$PROJECT/src"
  # PYTHONPATH as well, not just AS_SRC: this heredoc imports the package itself, before any
  # child of it is started, and a child can only be reached once the parent has imported.
  export PYTHONPATH="$PROJECT/src${PYTHONPATH:+:$PYTHONPATH}"
  SUMMARY=$("$PY" - <<'EOF' 2>&1
from attendance_sync.cron_submit import submit_plan
import json

plan = json.load(open('var/cron-plan.json', encoding='utf-8'))
submit_plan(plan, 'var/cron-single.json')
EOF
)
  echo "$SUMMARY"

  # Read the counters this run printed, not the whole log: an old failure from days ago
  # must not make today's summary claim that something failed.
  RESULT=$(printf '%s\n' "$SUMMARY" | sed -n 's/^RESULT ok=\([0-9]*\) failed=\([0-9]*\).*/\1 \2/p' | tail -1)
  OK=$(printf '%s' "$RESULT" | cut -d' ' -f1)
  FAILED=$(printf '%s' "$RESULT" | cut -d' ' -f2)
  [ -n "$OK" ] || OK=0
  [ -n "$FAILED" ] || FAILED=0

  if [ "$FAILED" -gt 0 ] 2>/dev/null; then
    notify "AttendanceSync: $FAILED of $PAYLOADS failed, check var/cron.log"
  elif [ "$OK" -gt 0 ] 2>/dev/null; then
    notify "AttendanceSync: registered $OK"
  else
    notify "AttendanceSync: nothing was registered"
  fi
  echo "=== run end ==="
} >> "$LOG" 2>&1