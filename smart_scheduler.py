"""
Smart Scheduling Based on Hiring Velocity & Learned Patterns.

Tracks how fast companies fill roles (days from post to close).
Learns optimal scan times from historical job posting patterns.
Prioritizes companies/roles with highest response probability.
Dynamically adjusts scan intervals based on market activity.
"""

import logging
import math
import re
from collections import defaultdict
from datetime import datetime, date, timedelta

log = logging.getLogger("lla.smart_scheduler")


class SmartScheduler:
    """Optimize job search scheduling based on hiring patterns and market signals."""

    def __init__(self, state, cfg: dict = None):
        self.state = state
        self.cfg = cfg or {}
        ss_cfg = self.cfg.get("smart_scheduling", {})
        self.enabled = ss_cfg.get("enabled", False)
        self.prioritize_fast = ss_cfg.get("prioritize_fast_hiring", True)
        self.peak_boost = ss_cfg.get("peak_hour_boost", 0.6)
        self.offpeak_factor = ss_cfg.get("offpeak_factor", 1.8)
        # Caches refreshed per cycle
        self._hour_weights = None
        self._priority_companies = None
        self._search_term_scores = None

    # ── Job Tracking ──────────────────────────────────────────

    def track_job_posting(self, company: str, title: str):
        """Track when a job posting is first seen."""
        if not self.enabled or not self.state:
            return

        pattern = self._normalize_title(title)
        self.state.update_hiring_velocity(company, pattern)

    def mark_position_filled(self, company: str, title: str):
        """Mark a position as filled (no longer appearing in search results)."""
        if not self.enabled or not self.state:
            return
        pattern = self._normalize_title(title)
        self.state.conn.execute("""
            UPDATE hiring_velocity SET filled=1
            WHERE company=? AND title_pattern=?
        """, (company, pattern))
        self.state.conn.commit()

    def _normalize_title(self, title: str) -> str:
        """Normalize job title to a pattern for grouping."""
        pattern = re.sub(
            r'\b(senior|sr|junior|jr|lead|principal|staff|'
            r'associate|I{1,3}|IV|V|[0-9]+)\b',
            '', title, flags=re.IGNORECASE
        )
        pattern = re.sub(r'\s+', ' ', pattern).strip()
        return pattern[:80]

    # ── Priority Companies ────────────────────────────────────

    def get_priority_companies(self) -> list[dict]:
        """Get companies ranked by hiring speed and response history."""
        if not self.enabled or not self.state:
            return []

        if self._priority_companies is not None:
            return self._priority_companies

        # Combine hiring velocity with response tracking
        companies = {}

        # Fast hiring companies
        fast = self.state.get_fast_hiring_companies(max_days=14)
        for c in fast:
            name = c["company"]
            if name not in companies:
                companies[name] = {"company": name, "speed_score": 0, "response_score": 0,
                                   "total_score": 0, "avg_days_to_fill": 0}
            # Faster = higher score (inverse of days)
            companies[name]["avg_days_to_fill"] = c["days_active"]
            companies[name]["speed_score"] = max(0, 15 - c["days_active"]) / 15.0

        # Companies that responded to our applications
        responses = self.state.conn.execute("""
            SELECT company,
                   COUNT(*) as total,
                   SUM(CASE WHEN response_type IN ('callback','interview','offer') THEN 1 ELSE 0 END) as positive
            FROM response_tracking
            GROUP BY company
        """).fetchall()
        for r in responses:
            name = r["company"]
            if name not in companies:
                companies[name] = {"company": name, "speed_score": 0, "response_score": 0,
                                   "total_score": 0, "avg_days_to_fill": 0}
            companies[name]["response_score"] = r["positive"] / max(r["total"], 1)

        # Compute composite score
        for name, data in companies.items():
            data["total_score"] = round(
                data["speed_score"] * 0.4 + data["response_score"] * 0.6, 3
            )

        result = sorted(companies.values(), key=lambda x: x["total_score"], reverse=True)
        self._priority_companies = result
        return result

    def should_prioritize(self, company: str) -> bool:
        """Check if a company should be prioritized in the apply queue."""
        if not self.enabled or not self.prioritize_fast:
            return False
        priority = self.get_priority_companies()
        top_names = {c["company"] for c in priority[:20] if c["total_score"] > 0.3}
        return company in top_names

    def get_company_score(self, company: str) -> float:
        """Get a company's priority score (0-1)."""
        for c in self.get_priority_companies():
            if c["company"] == company:
                return c["total_score"]
        return 0.0

    # ── Optimal Scan Times ────────────────────────────────────

    def _compute_hour_weights(self) -> dict[int, float]:
        """Learn hourly weights from when successful applications were found."""
        if self._hour_weights is not None:
            return self._hour_weights

        if not self.state:
            self._hour_weights = {h: 1.0 for h in range(24)}
            return self._hour_weights

        # Count applied jobs by hour
        rows = self.state.conn.execute("""
            SELECT CAST(strftime('%H', applied_at) AS INTEGER) as hour, COUNT(*) as c
            FROM applied_jobs WHERE applied_at IS NOT NULL
            GROUP BY hour
        """).fetchall()

        counts = defaultdict(int)
        for r in rows:
            counts[r["hour"]] = r["c"]

        if not counts:
            # Default: business hours weighted higher
            self._hour_weights = {}
            for h in range(24):
                if 8 <= h <= 11:
                    self._hour_weights[h] = 1.5  # Morning peak
                elif 13 <= h <= 16:
                    self._hour_weights[h] = 1.3  # Afternoon
                elif 6 <= h <= 19:
                    self._hour_weights[h] = 1.0  # Business hours
                else:
                    self._hour_weights[h] = 0.5  # Off-hours
            return self._hour_weights

        total = sum(counts.values())
        avg = total / max(len(counts), 1)

        # Weight each hour relative to average
        self._hour_weights = {}
        for h in range(24):
            c = counts.get(h, 0)
            self._hour_weights[h] = min(c / max(avg, 1), 2.5)  # Cap at 2.5x

        return self._hour_weights

    def get_optimal_scan_times(self) -> list[int]:
        """Get hours sorted by activity level (best hours first)."""
        weights = self._compute_hour_weights()
        return sorted(weights.keys(), key=lambda h: weights[h], reverse=True)

    def get_scan_interval_adjustment(self) -> float:
        """
        Return a multiplier for scan interval based on current time.

        During peak posting hours: shorter intervals (e.g., 0.6x)
        During off-peak: longer intervals (e.g., 1.8x)
        Smoothly interpolated, not just a step function.
        """
        if not self.enabled:
            return 1.0

        weights = self._compute_hour_weights()
        current_hour = datetime.now().hour
        current_weight = weights.get(current_hour, 1.0)

        # Also consider adjacent hours for smoothing
        prev_weight = weights.get((current_hour - 1) % 24, 1.0)
        next_weight = weights.get((current_hour + 1) % 24, 1.0)
        smoothed = current_weight * 0.6 + prev_weight * 0.2 + next_weight * 0.2

        if smoothed >= 1.2:
            # Peak hours — scan more frequently
            return max(self.peak_boost, 0.3)
        elif smoothed >= 0.8:
            # Normal hours
            return 1.0
        else:
            # Off-peak — scan less frequently
            return min(self.offpeak_factor, 3.0)

    # ── Search Term Optimization ──────────────────────────────

    def get_search_term_scores(self) -> dict[str, float]:
        """Score search terms by historical success rate."""
        if not self.enabled or not self.state:
            return {}

        if self._search_term_scores is not None:
            return self._search_term_scores

        # Applied per search term
        applied = self.state.conn.execute("""
            SELECT search_term, COUNT(*) as c FROM applied_jobs
            WHERE search_term != '' GROUP BY search_term
        """).fetchall()

        # Responses per search term
        responses = self.state.conn.execute("""
            SELECT a.search_term,
                   SUM(CASE WHEN r.response_type IN ('callback','interview','offer') THEN 1 ELSE 0 END) as positive
            FROM applied_jobs a
            JOIN response_tracking r ON a.job_id = r.job_id
            WHERE a.search_term != ''
            GROUP BY a.search_term
        """).fetchall()

        applied_map = {r["search_term"]: r["c"] for r in applied}
        response_map = {r["search_term"]: r["positive"] for r in responses}

        scores = {}
        for term, count in applied_map.items():
            pos = response_map.get(term, 0)
            # Wilson score lower bound (for ranking with small samples)
            if count > 0:
                p_hat = pos / count
                z = 1.96  # 95% confidence
                denom = 1 + z * z / count
                center = p_hat + z * z / (2 * count)
                spread = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * count)) / count)
                score = (center - spread) / denom
                scores[term] = round(max(score, 0), 4)
            else:
                scores[term] = 0.0

        self._search_term_scores = dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))
        return self._search_term_scores

    def optimize_search_order(self, terms: list[str]) -> list[str]:
        """Reorder search terms by historical success. Unknown terms go last."""
        if not self.enabled:
            return terms

        scores = self.get_search_term_scores()
        scored = [(t, scores.get(t, -1)) for t in terms]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [t for t, _ in scored]

    # ── Market Activity Detection ─────────────────────────────

    def get_market_activity_level(self) -> str:
        """Assess current market activity: hot, normal, slow."""
        if not self.enabled or not self.state:
            return "normal"

        # Compare recent applications to historical average
        recent = self.state.conn.execute("""
            SELECT COUNT(*) as c FROM applied_jobs
            WHERE date(applied_at) >= date('now', '-3 days')
        """).fetchone()["c"]

        historical = self.state.conn.execute("""
            SELECT AVG(applied) as avg_a FROM daily_stats
            WHERE date >= date('now', '-30 days')
        """).fetchone()
        avg = historical["avg_a"] if historical["avg_a"] else 0

        daily_recent = recent / 3.0 if recent else 0

        if avg == 0:
            return "normal"
        elif daily_recent > avg * 1.5:
            return "hot"
        elif daily_recent < avg * 0.5:
            return "slow"
        return "normal"

    # ── Cache Invalidation ────────────────────────────────────

    def invalidate_caches(self):
        """Clear cached computations (call at start of each cycle)."""
        self._hour_weights = None
        self._priority_companies = None
        self._search_term_scores = None

    # ── Reports ───────────────────────────────────────────────

    def get_hiring_report(self) -> str:
        """Generate a comprehensive hiring intelligence report."""
        lines = ["Hiring Intelligence Report", "=" * 40]

        # Market activity
        activity = self.get_market_activity_level()
        lines.append(f"Market Activity: {activity.upper()}")

        # Optimal scan times
        top_hours = self.get_optimal_scan_times()[:6]
        lines.append(f"Peak Posting Hours: {', '.join(f'{h:02d}:00' for h in top_hours)}")

        # Current interval adjustment
        adj = self.get_scan_interval_adjustment()
        lines.append(f"Current Scan Multiplier: {adj:.1f}x")

        # Priority companies
        priority = self.get_priority_companies()[:10]
        if priority:
            lines.extend(["", "Top Priority Companies:"])
            for c in priority:
                parts = [f"  {c['company']}"]
                if c["avg_days_to_fill"]:
                    parts.append(f"fills in {c['avg_days_to_fill']}d")
                parts.append(f"score={c['total_score']:.2f}")
                lines.append(" — ".join(parts))

        # Search term rankings
        term_scores = self.get_search_term_scores()
        if term_scores:
            lines.extend(["", "Search Term Success Ranking:"])
            for term, score in list(term_scores.items())[:10]:
                lines.append(f"  {term}: {score:.4f}")

        # Fast hiring
        fast = self.state.get_fast_hiring_companies(max_days=7) if self.state else []
        if fast:
            lines.extend(["", "Fastest Hiring (< 7 days):"])
            for c in fast[:10]:
                lines.append(f"  {c['company']} — {c['title_pattern']}: {c['days_active']}d")

        return "\n".join(lines)
