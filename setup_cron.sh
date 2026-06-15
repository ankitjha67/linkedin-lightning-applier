#!/usr/bin/env bash
#
# setup_cron.sh — Install a daily cron job for LinkedIn Lightning Applier.
#
# Schedules run_daily.sh to run once per day. Idempotent: re-running updates
# the existing entry instead of duplicating it.
#
# Usage:
#   ./setup_cron.sh            # install at the default time (09:00 daily)
#   ./setup_cron.sh 7 30       # install at 07:30 daily (HOUR MINUTE)
#   ./setup_cron.sh --remove   # remove the cron job
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$PROJECT_DIR/run_daily.sh"
MARKER="# linkedin-lightning-applier-daily"

chmod +x "$RUNNER" 2>/dev/null || true

# --- Remove mode -----------------------------------------------------------
if [[ "${1:-}" == "--remove" ]]; then
  crontab -l 2>/dev/null | grep -v "$MARKER" | crontab - || true
  echo "Removed the daily cron job."
  exit 0
fi

# --- Parse HOUR / MINUTE (defaults: 09:00) ---------------------------------
HOUR="${1:-9}"
MINUTE="${2:-0}"

if ! [[ "$HOUR" =~ ^[0-9]+$ && "$HOUR" -ge 0 && "$HOUR" -le 23 ]]; then
  echo "Error: HOUR must be 0-23 (got '$HOUR')" >&2; exit 1
fi
if ! [[ "$MINUTE" =~ ^[0-9]+$ && "$MINUTE" -ge 0 && "$MINUTE" -le 59 ]]; then
  echo "Error: MINUTE must be 0-59 (got '$MINUTE')" >&2; exit 1
fi

CRON_LINE="$MINUTE $HOUR * * * cd $PROJECT_DIR && $RUNNER $MARKER"

# --- Install / replace the entry -------------------------------------------
( crontab -l 2>/dev/null | grep -v "$MARKER" ; echo "$CRON_LINE" ) | crontab -

printf 'Installed daily cron job:\n  %s\n\n' "$CRON_LINE"
printf 'It will run every day at %02d:%02d (server local time).\n' "$HOUR" "$MINUTE"
echo "Verify with:  crontab -l"
echo "Remove with:  ./setup_cron.sh --remove"
echo ""
echo "IMPORTANT: cron runs with a minimal environment. Put your key in a .env"
echo "file next to run_daily.sh:   echo 'GEMINI_API_KEY=your-key' > $PROJECT_DIR/.env"
