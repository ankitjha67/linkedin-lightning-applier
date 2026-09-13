#!/usr/bin/env bash
#
# setup_autopilot.sh — run LinkedIn Lightning Applier 24/7 under cron.
#
# Installs two entries:
#   @reboot            start the bot when the machine comes back up
#   */N * * * *        check it is alive, and restart it if it is not
#
# The bot's own loop already runs continuously, so cron is not the scheduler
# here — it is the supervisor. `autopilot watch` takes a lock, so a tick that
# fires while the bot is running does nothing. Two Chrome sessions signed into
# one LinkedIn account is the fastest way to look like a bot, and a naive cron
# entry causes it by default.
#
# Usage:
#   ./tools/setup_autopilot.sh              # check every 5 minutes (default)
#   ./tools/setup_autopilot.sh 10           # check every 10 minutes
#   ./tools/setup_autopilot.sh --remove     # uninstall
#   ./tools/setup_autopilot.sh --status     # show what is installed
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="# linkedin-lightning-applier-autopilot"
DAILY_MARKER="# linkedin-lightning-applier-daily"

if ! command -v crontab >/dev/null 2>&1; then
  echo "Error: no crontab on this system." >&2
  echo "On Windows use:  powershell -ExecutionPolicy Bypass -File tools\\setup_autopilot.ps1" >&2
  echo "On a Linux server, systemd is a better fit:  tools/lla-autopilot.service" >&2
  exit 1
fi

# --- Pick the interpreter (prefer the project venv, like run_daily.sh) ------
if   [[ -x "$PROJECT_DIR/venv/bin/python"  ]]; then PYTHON="$PROJECT_DIR/venv/bin/python"
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then PYTHON="$PROJECT_DIR/.venv/bin/python"
else PYTHON="$(command -v python3 || command -v python)"
fi

case "${1:-}" in
  --remove)
    crontab -l 2>/dev/null | grep -v "$MARKER" | crontab - || true
    echo "Removed the 24/7 autopilot cron entries."
    echo "The bot itself is still running if it was — stop it with:  lla autopilot stop"
    exit 0
    ;;
  --status)
    echo "Installed entries:"
    crontab -l 2>/dev/null | grep "$MARKER" || echo "  (none)"
    echo
    "$PYTHON" "$PROJECT_DIR/tools/autopilot.py" status
    exit 0
    ;;
esac

EVERY="${1:-5}"
if ! [[ "$EVERY" =~ ^[0-9]+$ ]] || (( EVERY < 1 || EVERY > 59 )); then
  echo "Error: the check interval must be 1-59 minutes (got '$EVERY')" >&2
  exit 1
fi

# --- config.yaml must exist, or every restart will fail identically --------
if [[ ! -f "$PROJECT_DIR/config.yaml" ]]; then
  echo "Error: $PROJECT_DIR/config.yaml does not exist." >&2
  echo "The bot cannot log in without it. Run:  lla setup" >&2
  exit 1
fi

# --- Refuse to fight the once-a-day job ------------------------------------
if crontab -l 2>/dev/null | grep -q "$DAILY_MARKER"; then
  echo "The once-a-day cron job (setup_cron.sh) is installed." >&2
  echo "Running both means a daily --once cycle starting while the 24/7 bot" >&2
  echo "is mid-run. Remove it first:  ./setup_cron.sh --remove" >&2
  exit 1
fi

WATCH="$PYTHON $PROJECT_DIR/tools/autopilot.py watch -c $PROJECT_DIR/config.yaml"
LOGF="$PROJECT_DIR/logs/autopilot_cron.log"

REBOOT_LINE="@reboot cd $PROJECT_DIR && $WATCH >> $LOGF 2>&1 $MARKER"
WATCH_LINE="*/$EVERY * * * * cd $PROJECT_DIR && $WATCH >> $LOGF 2>&1 $MARKER"

mkdir -p "$PROJECT_DIR/logs"

# Idempotent: drop any previous entries, then add these.
#
# The `|| true` is load-bearing. `grep -v` exits 1 when it selects no lines,
# which is exactly the first-run case (an empty crontab), and under
# `set -euo pipefail` that aborts the script before it installs anything —
# silently, with exit 1 and no output.
{ crontab -l 2>/dev/null | grep -v "$MARKER" || true
  echo "$REBOOT_LINE"
  echo "$WATCH_LINE"
} | crontab -

cat <<EOF
Installed 24/7 autopilot:

  $REBOOT_LINE
  $WATCH_LINE

The bot runs continuously; cron restarts it if it dies and starts it after a
reboot. A tick that fires while it is already running does nothing — the lock
prevents a second Chrome session on your LinkedIn account.

  crontab -l                          verify
  lla autopilot status                is it up, and how many applies today
  tail -f $LOGF   supervisor log
  ./tools/setup_autopilot.sh --remove uninstall

cron runs with almost no environment. Put your API key in $PROJECT_DIR/.env
(gitignored) rather than in your shell profile — autopilot loads it on start.
EOF

# --- Start it now so 24/7 begins now, not at the next tick -----------------
"$PYTHON" "$PROJECT_DIR/tools/autopilot.py" start -c "$PROJECT_DIR/config.yaml" || true
