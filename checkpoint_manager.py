"""
Checkpoint Manager — Crash Recovery.

Saves bot state mid-cycle so it can resume after crashes without
duplicate applications or lost progress. Tracks:
- Current cycle position (which search term, which location, which job index)
- Jobs processed in current cycle (to avoid re-processing)
- Pending actions (messages to send, follow-ups queued)

On restart, checks for an active checkpoint and resumes from where it left off.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lla.checkpoint")

CHECKPOINT_FILE = "data/checkpoint.json"


class CheckpointManager:
    """Save and restore bot state for crash recovery."""

    def __init__(self, cfg: dict = None, checkpoint_path: str = CHECKPOINT_FILE):
        self.cfg = cfg or {}
        self.path = checkpoint_path
        cp_cfg = self.cfg.get("checkpoint", {})
        self.enabled = cp_cfg.get("enabled", True)
        self.auto_save_interval = cp_cfg.get("auto_save_interval", 5)  # save every N jobs
        self._counter = 0

        Path(os.path.dirname(self.path)).mkdir(parents=True, exist_ok=True)

    def save(self, cycle_state: dict):
        """Save current cycle state to disk."""
        if not self.enabled:
            return

        checkpoint = {
            "timestamp": datetime.now().isoformat(),
            "version": 2,
            **cycle_state,
        }

        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(checkpoint, f, indent=2, default=str)
            os.replace(tmp, self.path)  # atomic on POSIX
        except Exception as e:
            log.warning(f"Checkpoint save failed: {e}")

    def load(self) -> dict | None:
        """Load checkpoint from disk. Returns None if no checkpoint exists."""
        if not self.enabled or not os.path.exists(self.path):
            return None

        try:
            with open(self.path, "r") as f:
                data = json.load(f)

            # Validate checkpoint age — stale checkpoints (>2h) are discarded
            ts = data.get("timestamp", "")
            if ts:
                cp_time = datetime.fromisoformat(ts)
                age_hours = (datetime.now() - cp_time).total_seconds() / 3600
                if age_hours > 2:
                    log.info(f"Discarding stale checkpoint ({age_hours:.1f}h old)")
                    self.clear()
                    return None

            log.info(f"Resuming from checkpoint: {ts}")
            return data

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            log.warning(f"Corrupt checkpoint, starting fresh: {e}")
            self.clear()
            return None

    def clear(self):
        """Remove checkpoint (cycle completed cleanly)."""
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except Exception:
            pass

    def save_cycle_progress(self, search_term: str, location: str,
                            job_index: int, cycle_seen_ids: set,
                            applied_count: int = 0, skipped_count: int = 0):
        """Save progress within a cycle (called periodically)."""
        self._counter += 1
        if self._counter % self.auto_save_interval != 0:
            return

        self.save({
            "in_cycle": True,
            "search_term": search_term,
            "location": location,
            "job_index": job_index,
            "cycle_seen_ids": list(cycle_seen_ids),
            "applied_count": applied_count,
            "skipped_count": skipped_count,
        })

    def get_resume_point(self) -> dict | None:
        """Get the resume point for the current cycle.

        Returns dict with search_term, location, job_index, cycle_seen_ids
        or None if no checkpoint.
        """
        data = self.load()
        if not data or not data.get("in_cycle"):
            return None

        return {
            "search_term": data.get("search_term", ""),
            "location": data.get("location", ""),
            "job_index": data.get("job_index", 0),
            "cycle_seen_ids": set(data.get("cycle_seen_ids", [])),
            "applied_count": data.get("applied_count", 0),
            "skipped_count": data.get("skipped_count", 0),
        }
