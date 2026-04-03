"""
Optimal Apply Timing.

Prioritizes freshly posted jobs over stale ones. Applications submitted
within the first hour get 8x more recruiter views.

Parses "posted X ago" text, assigns freshness scores, and reorders
the job queue so the newest jobs are processed first.
"""

import logging
import re
from datetime import datetime, timedelta

log = logging.getLogger("lla.apply_timing")

# Freshness decay: jobs lose priority as they age
# Applied within 1 hour = 1.0 (highest), 24 hours = 0.5, 7 days = 0.1
FRESHNESS_CURVE = [
    (1, 1.0),        # < 1 hour: perfect
    (2, 0.95),       # 1-2 hours: excellent
    (6, 0.85),       # 2-6 hours: very good
    (12, 0.70),      # 6-12 hours: good
    (24, 0.50),      # 12-24 hours: moderate
    (48, 0.30),      # 1-2 days: low
    (168, 0.15),     # 2-7 days: very low
    (720, 0.05),     # 7-30 days: minimal
]


class ApplyTimingOptimizer:
    """Optimize application timing by prioritizing fresh job postings."""

    def __init__(self, cfg: dict = None):
        self.cfg = cfg or {}
        at_cfg = self.cfg.get("apply_timing", {})
        self.enabled = at_cfg.get("enabled", False)
        self.max_age_hours = at_cfg.get("max_age_hours", 168)  # 7 days
        self.prioritize_fresh = at_cfg.get("prioritize_fresh", True)
        self.skip_stale = at_cfg.get("skip_stale", False)
        self.stale_threshold_hours = at_cfg.get("stale_threshold_hours", 168)

    def parse_posted_time(self, text: str) -> float | None:
        """
        Parse LinkedIn's "posted X ago" text into hours.

        Handles: "Just now", "3 hours ago", "1 day ago", "2 weeks ago",
                 "Reposted 5 hours ago", "30 minutes ago"

        Returns hours as float, or None if unparseable.
        """
        if not text:
            return None

        text = text.lower().strip()

        # Remove "reposted" prefix
        text = re.sub(r'^reposted\s+', '', text)

        if "just now" in text or "moment" in text:
            return 0.1

        # "X minutes ago"
        m = re.search(r'(\d+)\s*min', text)
        if m:
            return int(m.group(1)) / 60.0

        # "X hours ago"
        m = re.search(r'(\d+)\s*hour', text)
        if m:
            return float(m.group(1))

        # "X days ago"
        m = re.search(r'(\d+)\s*day', text)
        if m:
            return float(m.group(1)) * 24

        # "X weeks ago"
        m = re.search(r'(\d+)\s*week', text)
        if m:
            return float(m.group(1)) * 168

        # "X months ago"
        m = re.search(r'(\d+)\s*month', text)
        if m:
            return float(m.group(1)) * 720

        return None

    def get_freshness_score(self, hours: float | None) -> float:
        """
        Get freshness score (0.0 - 1.0) based on job age in hours.
        Higher = fresher = should be processed first.
        """
        if hours is None:
            return 0.5  # Unknown age — neutral

        if hours <= 0:
            return 1.0

        for max_hours, score in FRESHNESS_CURVE:
            if hours <= max_hours:
                return score

        return 0.01  # Very old

    def should_skip_stale(self, hours: float | None) -> tuple[bool, str]:
        """Check if a job is too stale to bother applying."""
        if not self.enabled or not self.skip_stale:
            return False, ""

        if hours is None:
            return False, ""

        if hours > self.stale_threshold_hours:
            days = hours / 24
            return True, f"stale posting ({days:.0f} days old, threshold: {self.stale_threshold_hours/24:.0f}d)"

        return False, ""

    def prioritize_jobs(self, job_ids: list[str],
                        posted_times: dict[str, str]) -> list[str]:
        """
        Reorder job IDs by freshness (newest first).

        Args:
            job_ids: List of job IDs in original order
            posted_times: Dict mapping job_id -> posted_time text

        Returns:
            Reordered job IDs (freshest first)
        """
        if not self.enabled or not self.prioritize_fresh:
            return job_ids

        scored = []
        for jid in job_ids:
            posted_text = posted_times.get(jid, "")
            hours = self.parse_posted_time(posted_text)
            score = self.get_freshness_score(hours)
            scored.append((jid, score, hours))

        # Sort by freshness score descending (freshest first)
        scored.sort(key=lambda x: x[1], reverse=True)

        reordered = [jid for jid, _, _ in scored]

        # Log if order changed significantly
        if reordered != job_ids and len(reordered) > 1:
            top = scored[0]
            log.debug(f"  Job queue reordered by freshness. "
                     f"Freshest: {top[0]} ({top[2]:.1f}h old, score={top[1]:.2f})")

        return reordered

    def get_timing_stats(self, state) -> dict:
        """Analyze apply timing vs response rates."""
        if not state:
            return {}

        # Get applied jobs with posted_time info
        rows = state.conn.execute("""
            SELECT a.job_id, a.posted_time, a.applied_at,
                   r.response_type
            FROM applied_jobs a
            LEFT JOIN response_tracking r ON a.job_id = r.job_id
            WHERE a.posted_time != ''
        """).fetchall()

        if not rows:
            return {"data_points": 0}

        buckets = {
            "0-1h": {"applied": 0, "responded": 0},
            "1-6h": {"applied": 0, "responded": 0},
            "6-24h": {"applied": 0, "responded": 0},
            "1-7d": {"applied": 0, "responded": 0},
            "7d+": {"applied": 0, "responded": 0},
        }

        for row in rows:
            hours = self.parse_posted_time(row["posted_time"])
            if hours is None:
                continue

            if hours <= 1:
                bucket = "0-1h"
            elif hours <= 6:
                bucket = "1-6h"
            elif hours <= 24:
                bucket = "6-24h"
            elif hours <= 168:
                bucket = "1-7d"
            else:
                bucket = "7d+"

            buckets[bucket]["applied"] += 1
            if row["response_type"] in ("callback", "interview", "offer"):
                buckets[bucket]["responded"] += 1

        for bucket, data in buckets.items():
            data["response_rate"] = round(
                data["responded"] / max(data["applied"], 1) * 100, 1
            )

        return {"data_points": len(rows), "by_freshness": buckets}
