"""
Real-Time Job Market Intelligence.

Tracks job market conditions for the user's target roles and locations.
Captures periodic snapshots of posting volume, salary ranges, new companies,
and trend direction. Generates weekly briefs and market heat maps.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("lla.market_pulse")


class MarketPulse:
    """Analyze and track job market conditions from accumulated application data."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        mp_cfg = cfg.get("market_pulse", {})
        self.enabled = mp_cfg.get("enabled", False)
        self.snapshot_roles = mp_cfg.get("roles", [])
        self.snapshot_locations = mp_cfg.get("locations", [])
        self.rising_threshold = mp_cfg.get("rising_threshold", 0.20)
        self.falling_threshold = mp_cfg.get("falling_threshold", 0.20)
        self.layoff_drop_pct = mp_cfg.get("layoff_drop_pct", 0.50)

    # ------------------------------------------------------------------
    # Public: capture_snapshot
    # ------------------------------------------------------------------

    def capture_snapshot(self, role_pattern: str = "", location: str = "") -> dict:
        """
        Analyze current market from accumulated data for a role/location pair.

        Counts postings in the last 7 and 30 days, computes average salary,
        detects new companies, and determines trend direction. Persists
        the snapshot to market_snapshots.
        """
        if not self.enabled:
            log.debug("MarketPulse disabled, skipping snapshot")
            return {}

        role_pattern = role_pattern or "%"
        location = location or "%"

        log.info(f"Capturing market snapshot: role={role_pattern}, location={location}")

        now = datetime.now()
        seven_days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        thirty_days_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        # Count postings in last 7 days
        try:
            row = self.state.conn.execute(
                """SELECT COUNT(*) AS cnt FROM applied_jobs
                   WHERE title LIKE ? AND location LIKE ?
                   AND applied_at >= ?""",
                (f"%{role_pattern}%", f"%{location}%", seven_days_ago),
            ).fetchone()
            count_7d = row["cnt"] if row else 0
        except Exception as e:
            log.warning(f"Error counting 7-day postings: {e}")
            count_7d = 0

        # Count postings in last 30 days
        try:
            row = self.state.conn.execute(
                """SELECT COUNT(*) AS cnt FROM applied_jobs
                   WHERE title LIKE ? AND location LIKE ?
                   AND applied_at >= ?""",
                (f"%{role_pattern}%", f"%{location}%", thirty_days_ago),
            ).fetchone()
            count_30d = row["cnt"] if row else 0
        except Exception as e:
            log.warning(f"Error counting 30-day postings: {e}")
            count_30d = 0

        # Compute average salary from salary_data
        avg_min, avg_max, currency = self._compute_avg_salary(
            role_pattern, location, thirty_days_ago
        )

        # Detect new companies
        new_companies = self._detect_new_companies(role_pattern, location, days=14)

        # Compute trend
        trend = self._compute_trend(role_pattern, location)

        snapshot = {
            "role_pattern": role_pattern,
            "location": location,
            "posting_count": count_30d,
            "count_7d": count_7d,
            "count_30d": count_30d,
            "avg_salary_min": avg_min,
            "avg_salary_max": avg_max,
            "currency": currency,
            "new_companies": new_companies,
            "trend": trend,
            "snapshot_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Persist to DB
        try:
            self.state.conn.execute(
                """INSERT INTO market_snapshots
                   (role_pattern, location, posting_count, avg_salary_min,
                    avg_salary_max, currency, new_companies, trend)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    role_pattern, location, count_30d, avg_min, avg_max,
                    currency, json.dumps(new_companies), trend,
                ),
            )
            self.state.conn.commit()
            log.info(f"Snapshot saved: {count_30d} postings, trend={trend}")
        except Exception as e:
            log.error(f"Error saving snapshot: {e}")

        return snapshot

    # ------------------------------------------------------------------
    # Public: get_latest_snapshot
    # ------------------------------------------------------------------

    def get_latest_snapshot(self, role_pattern: str = "",
                           location: str = "") -> dict:
        """Retrieve the most recent snapshot for a role/location pair."""
        if not self.enabled:
            return {}

        role_pattern = role_pattern or "%"
        location = location or "%"

        try:
            row = self.state.conn.execute(
                """SELECT * FROM market_snapshots
                   WHERE role_pattern LIKE ? AND location LIKE ?
                   ORDER BY snapshot_at DESC LIMIT 1""",
                (f"%{role_pattern}%", f"%{location}%"),
            ).fetchone()
            if row:
                result = dict(row)
                if isinstance(result.get("new_companies"), str):
                    try:
                        result["new_companies"] = json.loads(result["new_companies"])
                    except (json.JSONDecodeError, TypeError):
                        result["new_companies"] = []
                return result
        except Exception as e:
            log.warning(f"Error retrieving snapshot: {e}")

        return {}

    # ------------------------------------------------------------------
    # Public: generate_weekly_brief
    # ------------------------------------------------------------------

    def generate_weekly_brief(self) -> str:
        """
        AI-synthesized weekly market report covering all configured
        target roles and locations.

        Format: "Backend Engineer demand in London up 12% this month.
        3 new companies posting. Median salary shifted from 85K to 90K.
        Recommendation: increase application volume in this market."
        """
        if not self.enabled:
            return ""

        if not self.ai or not getattr(self.ai, "enabled", False):
            log.warning("AI not available for weekly brief")
            return self._generate_brief_without_ai()

        # Gather snapshots for all role/location combos
        summaries = []
        roles = self.snapshot_roles or [""]
        locations = self.snapshot_locations or [""]

        for role in roles:
            for loc in locations:
                snap = self.get_latest_snapshot(role, loc)
                if snap:
                    summaries.append(snap)

        if not summaries:
            # Capture fresh snapshots
            for role in roles:
                for loc in locations:
                    snap = self.capture_snapshot(role, loc)
                    if snap:
                        summaries.append(snap)

        if not summaries:
            return "No market data available for weekly brief."

        # Build context for AI
        data_lines = []
        for s in summaries:
            new_cos = s.get("new_companies", [])
            if isinstance(new_cos, str):
                try:
                    new_cos = json.loads(new_cos)
                except (json.JSONDecodeError, TypeError):
                    new_cos = []
            data_lines.append(
                f"Role: {s.get('role_pattern', 'All')}, "
                f"Location: {s.get('location', 'All')}, "
                f"Postings (30d): {s.get('posting_count', 0)}, "
                f"Avg Salary: {s.get('currency', '')} "
                f"{s.get('avg_salary_min', 0):,.0f}-{s.get('avg_salary_max', 0):,.0f}, "
                f"Trend: {s.get('trend', 'stable')}, "
                f"New companies: {', '.join(new_cos[:10]) if new_cos else 'none'}"
            )

        data_block = "\n".join(data_lines)

        system_prompt = (
            "You are a senior career market analyst. Write concise, actionable "
            "weekly job market briefs. Use specific numbers. End each section "
            "with a concrete recommendation."
        )
        user_prompt = (
            "Generate a weekly job market brief from this data. For each "
            "role/location combination, write 2-3 sentences covering demand "
            "trend, salary movement, and new entrants. End with an overall "
            "recommendation.\n\n"
            f"Data:\n{data_block}"
        )

        try:
            brief = self.ai._call_llm(system_prompt, user_prompt)
            if brief:
                log.info("Weekly brief generated via AI")
                return brief.strip()
        except Exception as e:
            log.warning(f"AI brief generation failed: {e}")

        return self._generate_brief_without_ai()

    def _generate_brief_without_ai(self) -> str:
        """Fallback brief without AI - just formatted data."""
        roles = self.snapshot_roles or [""]
        locations = self.snapshot_locations or [""]
        lines = ["=== Weekly Market Brief ===", ""]

        for role in roles:
            for loc in locations:
                snap = self.get_latest_snapshot(role, loc)
                if not snap:
                    continue
                new_cos = snap.get("new_companies", [])
                if isinstance(new_cos, str):
                    try:
                        new_cos = json.loads(new_cos)
                    except (json.JSONDecodeError, TypeError):
                        new_cos = []
                role_label = snap.get("role_pattern", "All roles")
                loc_label = snap.get("location", "All locations")
                lines.append(f"{role_label} in {loc_label}:")
                lines.append(f"  Postings (30d): {snap.get('posting_count', 0)}")
                lines.append(f"  Trend: {snap.get('trend', 'stable')}")
                currency = snap.get("currency", "")
                sal_min = snap.get("avg_salary_min", 0)
                sal_max = snap.get("avg_salary_max", 0)
                if sal_min or sal_max:
                    lines.append(
                        f"  Avg Salary: {currency} {sal_min:,.0f} - {sal_max:,.0f}"
                    )
                if new_cos:
                    lines.append(f"  New companies: {', '.join(new_cos[:5])}")
                lines.append("")

        return "\n".join(lines) if len(lines) > 2 else "No market data available."

    # ------------------------------------------------------------------
    # Public: get_market_heat_map
    # ------------------------------------------------------------------

    def get_market_heat_map(self) -> list:
        """
        Build a role x location matrix with demand levels.

        Returns list of dicts:
          [{role, location, count_30d, trend, heat: "hot"/"warm"/"cool"/"cold"}]
        """
        if not self.enabled:
            return []

        roles = self.snapshot_roles or [""]
        locations = self.snapshot_locations or [""]
        heat_map = []

        for role in roles:
            for loc in locations:
                snap = self.get_latest_snapshot(role, loc)
                if not snap:
                    snap = self.capture_snapshot(role, loc)
                if not snap:
                    continue

                count = snap.get("posting_count", 0)
                trend = snap.get("trend", "stable")

                # Classify heat level based on posting count and trend
                if count >= 20 and trend == "rising":
                    heat = "hot"
                elif count >= 10 or (count >= 5 and trend == "rising"):
                    heat = "warm"
                elif count >= 3:
                    heat = "cool"
                else:
                    heat = "cold"

                heat_map.append({
                    "role": snap.get("role_pattern", role),
                    "location": snap.get("location", loc),
                    "count_30d": count,
                    "trend": trend,
                    "heat": heat,
                })

        log.info(f"Heat map generated: {len(heat_map)} cells")
        return heat_map

    # ------------------------------------------------------------------
    # Public: detect_layoff_signals
    # ------------------------------------------------------------------

    def detect_layoff_signals(self, company: str) -> dict:
        """
        Check if a company has reduced postings significantly.

        Compares the number of postings in the last 14 days vs the
        14 days before that. A drop of >= layoff_drop_pct signals trouble.
        """
        if not self.enabled or not company:
            return {"company": company, "signal": False, "detail": "disabled"}

        now = datetime.now()
        recent_start = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        prior_start = (now - timedelta(days=28)).strftime("%Y-%m-%d %H:%M:%S")

        try:
            row = self.state.conn.execute(
                """SELECT COUNT(*) AS cnt FROM applied_jobs
                   WHERE company LIKE ? AND applied_at >= ?""",
                (f"%{company}%", recent_start),
            ).fetchone()
            recent_count = row["cnt"] if row else 0

            row = self.state.conn.execute(
                """SELECT COUNT(*) AS cnt FROM applied_jobs
                   WHERE company LIKE ? AND applied_at >= ? AND applied_at < ?""",
                (f"%{company}%", prior_start, recent_start),
            ).fetchone()
            prior_count = row["cnt"] if row else 0
        except Exception as e:
            log.warning(f"Error detecting layoff signals for {company}: {e}")
            return {"company": company, "signal": False, "detail": str(e)}

        if prior_count == 0:
            return {
                "company": company,
                "signal": False,
                "recent_count": recent_count,
                "prior_count": prior_count,
                "detail": "No prior data for comparison",
            }

        drop_pct = (prior_count - recent_count) / prior_count
        is_signal = drop_pct >= self.layoff_drop_pct

        result = {
            "company": company,
            "signal": is_signal,
            "recent_count": recent_count,
            "prior_count": prior_count,
            "drop_pct": round(drop_pct * 100, 1),
            "detail": (
                f"Postings dropped {drop_pct * 100:.0f}% — possible layoff signal"
                if is_signal
                else f"Postings changed by {drop_pct * 100:.0f}% — within normal range"
            ),
        }
        log.info(
            f"Layoff check for {company}: signal={is_signal}, "
            f"drop={drop_pct * 100:.0f}%"
        )
        return result

    # ------------------------------------------------------------------
    # Public: get_emerging_roles
    # ------------------------------------------------------------------

    def get_emerging_roles(self) -> list:
        """
        Find new job titles appearing that did not exist 30+ days ago.

        Compares distinct titles seen in the last 14 days against titles
        seen more than 30 days ago. Returns titles unique to the recent window.
        """
        if not self.enabled:
            return []

        now = datetime.now()
        recent_cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        old_cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        try:
            recent_rows = self.state.conn.execute(
                """SELECT DISTINCT title FROM applied_jobs
                   WHERE applied_at >= ?""",
                (recent_cutoff,),
            ).fetchall()
            recent_titles = {row["title"].strip().lower() for row in recent_rows if row["title"]}

            old_rows = self.state.conn.execute(
                """SELECT DISTINCT title FROM applied_jobs
                   WHERE applied_at < ?""",
                (old_cutoff,),
            ).fetchall()
            old_titles = {row["title"].strip().lower() for row in old_rows if row["title"]}
        except Exception as e:
            log.warning(f"Error detecting emerging roles: {e}")
            return []

        emerging = sorted(recent_titles - old_titles)
        log.info(f"Emerging roles found: {len(emerging)}")
        return emerging

    # ------------------------------------------------------------------
    # Public: compare_markets
    # ------------------------------------------------------------------

    def compare_markets(self, locations: list) -> list:
        """
        Side-by-side comparison of the same configured roles across
        multiple locations.

        Returns a list of dicts with location, posting count, avg salary,
        trend, and a rank for each metric.
        """
        if not self.enabled or not locations:
            return []

        roles = self.snapshot_roles or [""]
        comparisons = []

        for loc in locations:
            for role in roles:
                snap = self.get_latest_snapshot(role, loc)
                if not snap:
                    snap = self.capture_snapshot(role, loc)
                if not snap:
                    continue
                comparisons.append({
                    "role": snap.get("role_pattern", role),
                    "location": loc,
                    "posting_count": snap.get("posting_count", 0),
                    "avg_salary_min": snap.get("avg_salary_min", 0),
                    "avg_salary_max": snap.get("avg_salary_max", 0),
                    "currency": snap.get("currency", ""),
                    "trend": snap.get("trend", "stable"),
                })

        # Rank by posting count descending
        comparisons.sort(key=lambda x: x["posting_count"], reverse=True)
        for i, c in enumerate(comparisons):
            c["rank"] = i + 1

        log.info(f"Market comparison across {len(locations)} locations")
        return comparisons

    # ------------------------------------------------------------------
    # Internal: _compute_trend
    # ------------------------------------------------------------------

    def _compute_trend(self, role_pattern: str, location: str) -> str:
        """
        Compare last 7 days vs previous 7 days.

        Returns:
          "rising"  if > rising_threshold increase
          "falling" if > falling_threshold decrease
          "stable"  otherwise
        """
        now = datetime.now()
        seven_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        fourteen_ago = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

        try:
            row = self.state.conn.execute(
                """SELECT COUNT(*) AS cnt FROM applied_jobs
                   WHERE title LIKE ? AND location LIKE ?
                   AND applied_at >= ?""",
                (f"%{role_pattern}%", f"%{location}%", seven_ago),
            ).fetchone()
            recent = row["cnt"] if row else 0

            row = self.state.conn.execute(
                """SELECT COUNT(*) AS cnt FROM applied_jobs
                   WHERE title LIKE ? AND location LIKE ?
                   AND applied_at >= ? AND applied_at < ?""",
                (f"%{role_pattern}%", f"%{location}%", fourteen_ago, seven_ago),
            ).fetchone()
            previous = row["cnt"] if row else 0
        except Exception as e:
            log.warning(f"Error computing trend: {e}")
            return "stable"

        if previous == 0:
            return "rising" if recent > 0 else "stable"

        change_pct = (recent - previous) / previous
        if change_pct > self.rising_threshold:
            return "rising"
        elif change_pct < -self.falling_threshold:
            return "falling"
        return "stable"

    # ------------------------------------------------------------------
    # Internal: _detect_new_companies
    # ------------------------------------------------------------------

    def _detect_new_companies(self, role_pattern: str, location: str,
                              days: int = 14) -> list:
        """
        Find companies posting for the first time in this role/location
        within the last ``days`` days.

        A company is 'new' if it has no postings for this role/location
        older than the cutoff date.
        """
        now = datetime.now()
        cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

        try:
            # Companies seen recently
            recent_rows = self.state.conn.execute(
                """SELECT DISTINCT company FROM applied_jobs
                   WHERE title LIKE ? AND location LIKE ?
                   AND applied_at >= ?""",
                (f"%{role_pattern}%", f"%{location}%", cutoff),
            ).fetchall()
            recent_companies = {row["company"] for row in recent_rows if row["company"]}

            # Companies seen before the cutoff
            old_rows = self.state.conn.execute(
                """SELECT DISTINCT company FROM applied_jobs
                   WHERE title LIKE ? AND location LIKE ?
                   AND applied_at < ?""",
                (f"%{role_pattern}%", f"%{location}%", cutoff),
            ).fetchall()
            old_companies = {row["company"] for row in old_rows if row["company"]}
        except Exception as e:
            log.warning(f"Error detecting new companies: {e}")
            return []

        new_companies = sorted(recent_companies - old_companies)
        return new_companies

    # ------------------------------------------------------------------
    # Internal: _compute_salary_trajectory
    # ------------------------------------------------------------------

    def _compute_salary_trajectory(self, role_pattern: str,
                                   location: str) -> dict:
        """
        Salary trend over last 30, 60, and 90 days.

        Returns dict with avg_min and avg_max for each period, plus
        direction ("up", "down", "flat").
        """
        now = datetime.now()
        periods = {
            "30d": (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
            "60d": (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S"),
            "90d": (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
        }

        trajectory = {}
        for label, cutoff in periods.items():
            avg_min, avg_max, currency = self._compute_avg_salary(
                role_pattern, location, cutoff
            )
            trajectory[label] = {
                "avg_min": avg_min,
                "avg_max": avg_max,
                "currency": currency,
            }

        # Determine direction from 90d vs 30d
        avg_30 = trajectory["30d"]["avg_max"]
        avg_90 = trajectory["90d"]["avg_max"]
        if avg_90 > 0 and avg_30 > 0:
            change = (avg_30 - avg_90) / avg_90
            if change > 0.05:
                trajectory["direction"] = "up"
            elif change < -0.05:
                trajectory["direction"] = "down"
            else:
                trajectory["direction"] = "flat"
        else:
            trajectory["direction"] = "unknown"

        return trajectory

    # ------------------------------------------------------------------
    # Internal: _compute_avg_salary
    # ------------------------------------------------------------------

    def _compute_avg_salary(self, role_pattern: str, location: str,
                            since: str) -> tuple:
        """
        Compute average salary_min and salary_max from salary_data table.

        Returns (avg_min, avg_max, currency).
        """
        try:
            row = self.state.conn.execute(
                """SELECT AVG(salary_min) AS avg_min,
                          AVG(salary_max) AS avg_max,
                          currency
                   FROM salary_data
                   WHERE title LIKE ? AND location LIKE ?
                   AND collected_at >= ? AND salary_min > 0
                   GROUP BY currency
                   ORDER BY COUNT(*) DESC LIMIT 1""",
                (f"%{role_pattern}%", f"%{location}%", since),
            ).fetchone()
            if row:
                return (
                    round(row["avg_min"] or 0, 2),
                    round(row["avg_max"] or 0, 2),
                    row["currency"] or "",
                )
        except Exception as e:
            log.warning(f"Error computing avg salary: {e}")

        return (0.0, 0.0, "")
