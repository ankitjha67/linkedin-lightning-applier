"""
Smart Scheduling Based on Hiring Velocity.

Tracks how fast companies fill roles (days from post to close).
Prioritizes companies that hire fast over those that ghost.
Adjusts scan frequency based on time-of-day posting patterns.
"""

import logging
from datetime import datetime, date
from collections import defaultdict

log = logging.getLogger("lla.smart_scheduler")


class SmartScheduler:
    """Optimize job search scheduling based on hiring patterns."""

    def __init__(self, state, cfg: dict = None):
        self.state = state
        self.cfg = cfg or {}
        ss_cfg = self.cfg.get("smart_scheduling", {})
        self.enabled = ss_cfg.get("enabled", False)
        self.prioritize_fast = ss_cfg.get("prioritize_fast_hiring", True)

    def track_job_posting(self, company: str, title: str):
        """Track when a job posting is first seen."""
        if not self.enabled:
            return

        # Normalize title to pattern (remove specific levels, numbers)
        import re
        pattern = re.sub(r'\b(senior|junior|lead|principal|staff|I|II|III|IV|V)\b',
                        '', title, flags=re.IGNORECASE).strip()
        pattern = re.sub(r'\s+', ' ', pattern)

        self.state.update_hiring_velocity(company, pattern)

    def get_priority_companies(self) -> list[str]:
        """Get list of companies known to hire quickly."""
        if not self.enabled:
            return []

        fast = self.state.get_fast_hiring_companies(max_days=14)
        return [c["company"] for c in fast]

    def should_prioritize(self, company: str) -> bool:
        """Check if a company is known to hire fast (should be prioritized)."""
        if not self.enabled or not self.prioritize_fast:
            return False
        return company in self.get_priority_companies()

    def get_optimal_scan_times(self) -> list[int]:
        """
        Analyze when jobs are most frequently posted (by hour of day).
        Returns list of optimal hours to scan.
        """
        if not self.enabled:
            return list(range(8, 20))  # Default: 8am-8pm

        rows = self.state.conn.execute("""
            SELECT applied_at FROM applied_jobs WHERE applied_at IS NOT NULL
        """).fetchall()

        if len(rows) < 10:
            return list(range(8, 20))

        # Count by hour
        hour_counts = defaultdict(int)
        for row in rows:
            try:
                dt = datetime.strptime(row["applied_at"], "%Y-%m-%d %H:%M:%S")
                hour_counts[dt.hour] += 1
            except (ValueError, TypeError):
                continue

        if not hour_counts:
            return list(range(8, 20))

        # Sort hours by frequency, return top hours
        sorted_hours = sorted(hour_counts.keys(), key=lambda h: hour_counts[h], reverse=True)
        return sorted_hours[:12]  # Top 12 most active hours

    def get_scan_interval_adjustment(self) -> float:
        """
        Return a multiplier for scan interval based on current time.
        During peak posting hours: shorter intervals (0.5x)
        During off-peak: longer intervals (2x)
        """
        if not self.enabled:
            return 1.0

        optimal = set(self.get_optimal_scan_times())
        current_hour = datetime.now().hour

        if current_hour in optimal:
            return 0.7  # Scan more frequently during peak hours
        else:
            return 1.5  # Scan less frequently during off-peak

    def get_hiring_report(self) -> str:
        """Generate a hiring velocity report."""
        fast = self.state.get_fast_hiring_companies(max_days=14)
        if not fast:
            return "No hiring velocity data yet."

        lines = ["Hiring Velocity Report", "=" * 30]
        for c in fast[:20]:
            lines.append(f"  {c['company']} — {c['title_pattern']}: "
                        f"{c['days_active']} days to fill")

        return "\n".join(lines)
