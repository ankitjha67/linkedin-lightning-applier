#!/usr/bin/env bash
#
# run_daily.sh — Single daily run of LinkedIn Lightning Applier.
#
# Runs ONE scan cycle (python main.py --once) then exits, so it's safe to
# schedule from cron without overlapping processes. Logs each run to logs/.
#
# Configure your secrets in this file or in a .env next to it. The Gemini
# key is read from the GEMINI_API_KEY environment variable.
#
# Usage:
#   ./run_daily.sh                 # run once now
#   ./setup_cron.sh                # install the daily cron job
#
set -euo pipefail

# --- Resolve project directory (so cron can run from anywhere) -------------
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# --- Load .env if present (KEY=value lines) --------------------------------
if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

# --- API key (override here or via .env / shell environment) ---------------
# export GEMINI_API_KEY="your-gemini-2.5-pro-key"

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') WARNING: GEMINI_API_KEY not set — AI features will be limited." >&2
fi

# --- Pick the Python interpreter (prefer project venv) ---------------------
if [[ -x "$PROJECT_DIR/venv/bin/python" ]]; then
  PYTHON="$PROJECT_DIR/venv/bin/python"
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi

# --- Run a single cycle, tee output to a dated log -------------------------
mkdir -p logs
LOG_FILE="logs/daily_$(date '+%Y%m%d_%H%M%S').log"

echo "$(date '+%Y-%m-%d %H:%M:%S') Starting daily run with $PYTHON" | tee -a "$LOG_FILE"
"$PYTHON" main.py --once -c config.yaml 2>&1 | tee -a "$LOG_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') Daily run finished." | tee -a "$LOG_FILE"

# --- Prune logs older than 30 days -----------------------------------------
find logs -name 'daily_*.log' -mtime +30 -delete 2>/dev/null || true
