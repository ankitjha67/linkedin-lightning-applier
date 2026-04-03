"""Salary Negotiation Prep module.

When an interview or offer is detected, auto-generates a negotiation brief
with market rates, company ranges, leverage points, and counter-offer suggestions.
"""

import logging
import json
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class SalaryNegotiator:
    """Generates salary negotiation briefs backed by market data and AI analysis."""

    def __init__(self, ai, cfg, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        self.enabled = cfg.get("salary_negotiation", {}).get("enabled", False)
        self._ensure_tables()

    def _ensure_tables(self):
        """Create required tables if they do not exist."""
        try:
            self.state.conn.execute(
                """CREATE TABLE IF NOT EXISTS salary_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    company TEXT,
                    location TEXT,
                    min_salary REAL,
                    max_salary REAL,
                    median_salary REAL,
                    currency TEXT DEFAULT 'USD',
                    source TEXT,
                    updated_at TEXT
                )"""
            )
            self.state.conn.execute(
                """CREATE TABLE IF NOT EXISTS negotiation_briefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL UNIQUE,
                    title TEXT,
                    company TEXT,
                    location TEXT,
                    market_rate_min REAL,
                    market_rate_max REAL,
                    market_rate_median REAL,
                    company_range_min REAL,
                    company_range_max REAL,
                    leverage_points TEXT,
                    counter_offer_min REAL,
                    counter_offer_target REAL,
                    counter_offer_max REAL,
                    full_brief TEXT,
                    created_at TEXT
                )"""
            )
            self.state.conn.commit()
        except Exception as exc:
            logger.error("Failed to initialise salary negotiation tables: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_brief(self, job_id, title, company, location):
        """Build a complete negotiation brief for the given job and persist it."""
        if not self.enabled:
            logger.debug("SalaryNegotiator is disabled; skipping brief generation.")
            return None

        try:
            market = self.get_market_rate(title, location)
            company_range = self.get_company_range(company)
            leverage = self.generate_leverage_points(title, company, market)
            current_salary = self.cfg.get("salary_negotiation", {}).get("current_salary")
            counter = self.generate_counter_offer(market, company_range, current_salary)

            brief = {
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "market_rate": market,
                "company_range": company_range,
                "leverage_points": leverage,
                "counter_offer": counter,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

            full_brief_text = self._format_brief(brief)
            brief["full_brief"] = full_brief_text

            self._save_brief(brief)
            logger.info("Negotiation brief generated for job %s at %s.", job_id, company)
            return brief
        except Exception as exc:
            logger.error("Error generating negotiation brief for %s: %s", job_id, exc)
            return None

    def get_market_rate(self, title, location):
        """Look up market salary data from the salary_data table."""
        try:
            rows = self.state.conn.execute(
                """SELECT min_salary, max_salary, median_salary
                   FROM salary_data
                   WHERE LOWER(title) LIKE ? AND LOWER(location) LIKE ?
                   ORDER BY updated_at DESC LIMIT 5""",
                (f"%{title.lower()}%", f"%{location.lower()}%"),
            ).fetchall()

            if not rows:
                rows = self.state.conn.execute(
                    """SELECT min_salary, max_salary, median_salary
                       FROM salary_data
                       WHERE LOWER(title) LIKE ?
                       ORDER BY updated_at DESC LIMIT 5""",
                    (f"%{title.lower()}%",),
                ).fetchall()

            if not rows:
                logger.warning("No market data found for '%s' in '%s'.", title, location)
                return {"min": None, "max": None, "median": None}

            avg_min = sum(r[0] for r in rows if r[0]) / max(sum(1 for r in rows if r[0]), 1)
            avg_max = sum(r[1] for r in rows if r[1]) / max(sum(1 for r in rows if r[1]), 1)
            avg_med = sum(r[2] for r in rows if r[2]) / max(sum(1 for r in rows if r[2]), 1)

            return {"min": round(avg_min, 2), "max": round(avg_max, 2), "median": round(avg_med, 2)}
        except Exception as exc:
            logger.error("Error fetching market rate: %s", exc)
            return {"min": None, "max": None, "median": None}

    def get_company_range(self, company):
        """Retrieve historical salary data for a specific company."""
        try:
            rows = self.state.conn.execute(
                """SELECT min_salary, max_salary, median_salary
                   FROM salary_data
                   WHERE LOWER(company) LIKE ?
                   ORDER BY updated_at DESC LIMIT 10""",
                (f"%{company.lower()}%",),
            ).fetchall()

            if not rows:
                logger.info("No historical salary data found for company '%s'.", company)
                return {"min": None, "max": None}

            all_mins = [r[0] for r in rows if r[0]]
            all_maxs = [r[1] for r in rows if r[1]]

            return {
                "min": round(min(all_mins), 2) if all_mins else None,
                "max": round(max(all_maxs), 2) if all_maxs else None,
            }
        except Exception as exc:
            logger.error("Error fetching company range for '%s': %s", company, exc)
            return {"min": None, "max": None}

    def generate_leverage_points(self, title, company, match_result):
        """Use AI to generate leverage points for the negotiation."""
        try:
            market_info = ""
            if match_result and match_result.get("median"):
                market_info = (
                    f"Market median for this role is approximately "
                    f"${match_result['median']:,.0f}."
                )

            prompt = (
                f"Generate 3-5 concise salary negotiation leverage points for a "
                f"'{title}' position at '{company}'. {market_info}\n"
                f"Focus on: unique skills value, market demand, competitive landscape, "
                f"and timing advantages. Return as a JSON list of strings."
            )

            response = self.ai.generate(prompt)
            try:
                points = json.loads(response)
                if isinstance(points, list):
                    return points
            except (json.JSONDecodeError, TypeError):
                pass

            return [line.strip("- ").strip() for line in response.strip().splitlines() if line.strip()]
        except Exception as exc:
            logger.error("Error generating leverage points: %s", exc)
            return ["Unable to generate leverage points at this time."]

    def generate_counter_offer(self, market_rate, company_range, current_salary=None):
        """Calculate a suggested counter-offer range."""
        try:
            base = None
            if market_rate and market_rate.get("median"):
                base = market_rate["median"]
            elif company_range and company_range.get("max"):
                base = company_range["max"]
            elif current_salary:
                base = float(current_salary)

            if base is None:
                return {"min": None, "target": None, "max": None}

            # If we have current salary, ensure the floor beats it
            floor = base
            if current_salary:
                floor = max(base, float(current_salary) * 1.10)

            # Constrain within company range when available
            if company_range and company_range.get("max"):
                ceiling = max(floor, company_range["max"])
            else:
                ceiling = floor * 1.20

            target = (floor + ceiling) / 2.0

            return {
                "min": round(floor, 2),
                "target": round(target, 2),
                "max": round(ceiling, 2),
            }
        except Exception as exc:
            logger.error("Error generating counter offer: %s", exc)
            return {"min": None, "target": None, "max": None}

    def get_brief(self, job_id):
        """Retrieve a previously saved negotiation brief."""
        try:
            row = self.state.conn.execute(
                "SELECT full_brief FROM negotiation_briefs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row:
                return row[0]
            logger.info("No brief found for job %s.", job_id)
            return None
        except Exception as exc:
            logger.error("Error retrieving brief for %s: %s", job_id, exc)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _save_brief(self, brief):
        """Persist the negotiation brief to the database."""
        try:
            market = brief.get("market_rate", {})
            company = brief.get("company_range", {})
            counter = brief.get("counter_offer", {})
            leverage_json = json.dumps(brief.get("leverage_points", []))

            self.state.conn.execute(
                """INSERT OR REPLACE INTO negotiation_briefs
                   (job_id, title, company, location,
                    market_rate_min, market_rate_max, market_rate_median,
                    company_range_min, company_range_max,
                    leverage_points,
                    counter_offer_min, counter_offer_target, counter_offer_max,
                    full_brief, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    brief["job_id"], brief["title"], brief["company"], brief["location"],
                    market.get("min"), market.get("max"), market.get("median"),
                    company.get("min"), company.get("max"),
                    leverage_json,
                    counter.get("min"), counter.get("target"), counter.get("max"),
                    brief.get("full_brief", ""),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.state.conn.commit()
        except Exception as exc:
            logger.error("Error saving negotiation brief: %s", exc)

    def _format_brief(self, brief):
        """Render a human-readable negotiation brief."""
        lines = [
            "=" * 60,
            "SALARY NEGOTIATION BRIEF",
            "=" * 60,
            f"Role:     {brief['title']}",
            f"Company:  {brief['company']}",
            f"Location: {brief['location']}",
            "",
            "--- Market Rate ---",
        ]

        market = brief.get("market_rate", {})
        if market.get("median"):
            lines.append(f"  Median: ${market['median']:,.0f}")
            lines.append(f"  Range:  ${market.get('min', 0):,.0f} - ${market.get('max', 0):,.0f}")
        else:
            lines.append("  No market data available.")

        lines.append("")
        lines.append("--- Company Historical Range ---")
        cr = brief.get("company_range", {})
        if cr.get("min") or cr.get("max"):
            lines.append(f"  ${cr.get('min', '?'):,} - ${cr.get('max', '?'):,}")
        else:
            lines.append("  No company-specific data available.")

        lines.append("")
        lines.append("--- Leverage Points ---")
        for i, pt in enumerate(brief.get("leverage_points", []), 1):
            lines.append(f"  {i}. {pt}")

        lines.append("")
        lines.append("--- Suggested Counter-Offer ---")
        co = brief.get("counter_offer", {})
        if co.get("target"):
            lines.append(f"  Walk-away floor: ${co['min']:,.0f}")
            lines.append(f"  Target:          ${co['target']:,.0f}")
            lines.append(f"  Stretch goal:    ${co['max']:,.0f}")
        else:
            lines.append("  Insufficient data to suggest a counter-offer.")

        lines.append("=" * 60)
        return "\n".join(lines)
