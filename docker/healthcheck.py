#!/usr/bin/env python3
"""
Health check endpoint for Docker container.
Returns 0 (healthy) if the bot is running and responsive.
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

def check_health():
    """Check if the bot is healthy."""
    # Check if state.db exists and was recently modified
    db_path = Path("data/state.db")
    if not db_path.exists():
        print("UNHEALTHY: state.db not found")
        return 1

    # Check if DB was modified in last 30 minutes
    mtime = datetime.fromtimestamp(db_path.stat().st_mtime)
    if datetime.now() - mtime > timedelta(minutes=30):
        print(f"UNHEALTHY: state.db last modified {mtime}")
        return 1

    # Check if log file is recent
    log_dir = Path("logs")
    if log_dir.exists():
        logs = sorted(log_dir.glob("lla_*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        if logs:
            log_mtime = datetime.fromtimestamp(logs[0].stat().st_mtime)
            if datetime.now() - log_mtime > timedelta(minutes=30):
                print(f"UNHEALTHY: latest log stale since {log_mtime}")
                return 1

    print("HEALTHY")
    return 0

if __name__ == "__main__":
    sys.exit(check_health())
