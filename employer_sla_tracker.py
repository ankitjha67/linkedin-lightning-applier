"""
Employer Response Time Intelligence.

Tracks how long each company takes at each stage of the hiring pipeline.
Auto-learns from the response_tracking table and computes SLA statistics
per company and stage. Identifies overdue applications and ranks companies
by responsiveness.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("lla.employer_sla_tracker")


class EmployerSLATracker:
    """Track and predict employer response times across pipeline stages."""

    def __init__(self, cfg: dict, state):
        self.cfg = cfg
        self.state = state
        sla_cfg = cfg.get("employer_sla", {})
        self.enabled = sla_cfg.get("enabled", False)
        self.overdue_multiplier = sla_cfg.get("overdue_multiplier", 1.5)
        self.min_sample_size = sla_cfg.get("min_sample_size", 1)
        self.default_sla_days = sla_cfg.get("default_sla_days", {
            "applied": 14,
            "screening": 7,
            "interview": 10,
            "offer": 5,
            "rejection": 21,
        })

    # ------------------------------------------------------------------
    # Public: track_transition
    # ------------------------------------------------------------------

    def track_transition(self, company: str, stage: str,
                         days_taken: float) -> bool:
        """
        Record a single stage transition time for a company.

        After recording, recalculates that company+stage SLA automatically.
        Returns True on success, False on failure.
        """
        if not self.enabled:
            log.debug("EmployerSLATracker disabled, skipping track")
            return False

        if not company or not stage or days_taken < 0:
            log.warning(
                f"Invalid transition data: company={company}, "
                f"stage={stage}, days={days_taken}"
            )
            return False

        log.info(
            f"Tracking transition: {company} / {stage} = {days_taken:.1f} days"
        )

        # Fetch current SLA row if it exists
        try:
            existing = self.state.conn.execute(
                """SELECT avg_days, min_days, max_days, sample_size
                   FROM employer_sla
                   WHERE company = ? AND stage = ?""",
                (company, stage),
            ).fetchone()
        except Exception as e:
            log.warning(f"Error fetching existing SLA: {e}")
            existing = None

        if existing:
            old_avg = existing["avg_days"]
            old_min = existing["min_days"]
            old_max = existing["max_days"]
            old_n = existing["sample_size"]
            new_n = old_n + 1
            new_avg = ((old_avg * old_n) + days_taken) / new_n
            new_min = min(old_min, days_taken)
            new_max = max(old_max, days_taken)

            try:
                self.state.conn.execute(
                    """UPDATE employer_sla
                       SET avg_days = ?, min_days = ?, max_days = ?,
                           sample_size = ?, last_updated = datetime('now','localtime')
                       WHERE company = ? AND stage = ?""",
                    (round(new_avg, 2), round(new_min, 2), round(new_max, 2),
                     new_n, company, stage),
                )
                self.state.conn.commit()
            except Exception as e:
                log.error(f"Error updating SLA for {company}/{stage}: {e}")
                return False
        else:
            try:
                self.state.conn.execute(
                    """INSERT INTO employer_sla
                       (company, stage, avg_days, min_days, max_days, sample_size)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (company, stage, round(days_taken, 2),
                     round(days_taken, 2), round(days_taken, 2)),
                )
                self.state.conn.commit()
            except Exception as e:
                log.error(f"Error inserting SLA for {company}/{stage}: {e}")
                return False

        log.debug(f"SLA updated for {company}/{stage}")
        return True

    # ------------------------------------------------------------------
    # Public: compute_slas
    # ------------------------------------------------------------------

    def compute_slas(self, company: str = None) -> int:
        """
        Compute SLAs from response_tracking data.

        Analyzes all response_tracking rows (or just for one company)
        and upserts employer_sla records. Returns number of SLAs computed.
        """
        if not self.enabled:
            return 0

        log.info(
            f"Computing SLAs{' for ' + company if company else ' for all companies'}..."
        )

        records = self._compute_from_history(company)
        count = 0

        for key, stats in records.items():
            comp, stage = key
            if stats["count"] < self.min_sample_size:
                continue

            avg_d = stats["total_days"] / stats["count"]
            try:
                self.state.conn.execute(
                    """INSERT INTO employer_sla
                       (company, stage, avg_days, min_days, max_days, sample_size)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(company, stage) DO UPDATE SET
                           avg_days = excluded.avg_days,
                           min_days = excluded.min_days,
                           max_days = excluded.max_days,
                           sample_size = excluded.sample_size,
                           last_updated = datetime('now','localtime')""",
                    (comp, stage, round(avg_d, 2), round(stats["min_days"], 2),
                     round(stats["max_days"], 2), stats["count"]),
                )
                count += 1
            except Exception as e:
                log.warning(f"Error upserting SLA {comp}/{stage}: {e}")

        if count > 0:
            self.state.conn.commit()
        log.info(f"Computed {count} SLA records")
        return count

    # ------------------------------------------------------------------
    # Internal: _compute_from_history
    # ------------------------------------------------------------------

    def _compute_from_history(self, company: str = None) -> dict:
        """
        Analyze applied_at vs response timestamps in response_tracking.

        Returns dict keyed by (company, stage) with aggregated stats.
        """
        results = {}

        try:
            if company:
                rows = self.state.conn.execute(
                    """SELECT company, response_type, days_to_response
                       FROM response_tracking
                       WHERE company = ? AND days_to_response > 0""",
                    (company,),
                ).fetchall()
            else:
                rows = self.state.conn.execute(
                    """SELECT company, response_type, days_to_response
                       FROM response_tracking
                       WHERE days_to_response > 0""",
                ).fetchall()
        except Exception as e:
            log.warning(f"Error reading response_tracking: {e}")
            return results

        for row in rows:
            comp = row["company"] or "Unknown"
            stage = row["response_type"] or "applied"
            days = row["days_to_response"] or 0

            key = (comp, stage)
            if key not in results:
                results[key] = {
                    "total_days": 0.0,
                    "min_days": float("inf"),
                    "max_days": 0.0,
                    "count": 0,
                }

            results[key]["total_days"] += days
            results[key]["count"] += 1
            results[key]["min_days"] = min(results[key]["min_days"], days)
            results[key]["max_days"] = max(results[key]["max_days"], days)

        # Fix inf for min_days where count is 0
        for key in results:
            if results[key]["min_days"] == float("inf"):
                results[key]["min_days"] = 0.0

        return results

    # ------------------------------------------------------------------
    # Public: get_company_sla
    # ------------------------------------------------------------------

    def get_company_sla(self, company: str) -> list:
        """
        Get the full SLA table for a specific company.

        Returns list of dicts, one per stage, with avg/min/max days.
        """
        if not self.enabled or not company:
            return []

        try:
            rows = self.state.conn.execute(
                """SELECT stage, avg_days, min_days, max_days,
                          sample_size, last_updated
                   FROM employer_sla
                   WHERE company = ?
                   ORDER BY avg_days ASC""",
                (company,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.warning(f"Error getting SLA for {company}: {e}")
            return []

    # ------------------------------------------------------------------
    # Public: get_all_slas
    # ------------------------------------------------------------------

    def get_all_slas(self) -> list:
        """Return all SLA records across all companies."""
        if not self.enabled:
            return []

        try:
            rows = self.state.conn.execute(
                """SELECT company, stage, avg_days, min_days, max_days,
                          sample_size, last_updated
                   FROM employer_sla
                   ORDER BY company, avg_days ASC""",
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.warning(f"Error getting all SLAs: {e}")
            return []

    # ------------------------------------------------------------------
    # Public: predict_response_date
    # ------------------------------------------------------------------

    def predict_response_date(self, company: str,
                              current_stage: str) -> dict:
        """
        Predict when a response is expected based on company history.

        Returns dict with expected_date, avg_days, confidence level.
        Example: "Based on Google's history, expect response by 2025-02-15"
        """
        if not self.enabled or not company:
            return {"prediction": "No data available", "confidence": "none"}

        try:
            row = self.state.conn.execute(
                """SELECT avg_days, min_days, max_days, sample_size
                   FROM employer_sla
                   WHERE company = ? AND stage = ?""",
                (company, current_stage),
            ).fetchone()
        except Exception as e:
            log.warning(f"Error predicting response for {company}: {e}")
            row = None

        if not row:
            default_days = self.default_sla_days.get(current_stage, 14)
            expected = datetime.now() + timedelta(days=default_days)
            return {
                "company": company,
                "stage": current_stage,
                "avg_days": default_days,
                "expected_date": expected.strftime("%Y-%m-%d"),
                "confidence": "low",
                "note": (
                    f"No historical data for {company}. "
                    f"Using default {default_days} days for '{current_stage}'."
                ),
            }

        avg_days = row["avg_days"]
        sample = row["sample_size"]
        expected = datetime.now() + timedelta(days=avg_days)

        if sample >= 5:
            confidence = "high"
        elif sample >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "company": company,
            "stage": current_stage,
            "avg_days": avg_days,
            "min_days": row["min_days"],
            "max_days": row["max_days"],
            "sample_size": sample,
            "expected_date": expected.strftime("%Y-%m-%d"),
            "confidence": confidence,
            "note": (
                f"Based on {company}'s history ({sample} data points), "
                f"expect response by {expected.strftime('%Y-%m-%d')} "
                f"(avg {avg_days:.1f} days, range {row['min_days']:.0f}-"
                f"{row['max_days']:.0f} days)."
            ),
        }

    # ------------------------------------------------------------------
    # Public: is_overdue
    # ------------------------------------------------------------------

    def is_overdue(self, job_id: str) -> dict:
        """
        Check if a specific application is past its expected SLA.

        Looks up the job in applied_jobs and response_tracking, then
        compares elapsed time against the company's historical SLA.
        """
        if not self.enabled or not job_id:
            return {"overdue": False, "detail": "disabled"}

        try:
            job_row = self.state.conn.execute(
                """SELECT company, title, applied_at FROM applied_jobs
                   WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
        except Exception as e:
            log.warning(f"Error looking up job {job_id}: {e}")
            return {"overdue": False, "detail": str(e)}

        if not job_row:
            return {"overdue": False, "detail": "Job not found"}

        company = job_row["company"]
        applied_at_str = job_row["applied_at"]

        try:
            applied_at = datetime.strptime(applied_at_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return {"overdue": False, "detail": "Cannot parse applied_at date"}

        elapsed_days = (datetime.now() - applied_at).total_seconds() / 86400

        # Check if we already have a response
        try:
            resp = self.state.conn.execute(
                """SELECT response_type FROM response_tracking
                   WHERE job_id = ? AND response_at IS NOT NULL
                   ORDER BY response_at DESC LIMIT 1""",
                (job_id,),
            ).fetchone()
            if resp:
                return {
                    "overdue": False,
                    "detail": f"Already received: {resp['response_type']}",
                }
        except Exception:
            pass

        # Look up SLA for the 'applied' stage
        try:
            sla_row = self.state.conn.execute(
                """SELECT avg_days, max_days FROM employer_sla
                   WHERE company = ? AND stage = 'applied'""",
                (company,),
            ).fetchone()
        except Exception:
            sla_row = None

        if sla_row:
            threshold = sla_row["avg_days"] * self.overdue_multiplier
            overdue = elapsed_days > threshold
        else:
            default = self.default_sla_days.get("applied", 14)
            threshold = default * self.overdue_multiplier
            overdue = elapsed_days > threshold

        return {
            "job_id": job_id,
            "company": company,
            "title": job_row["title"],
            "elapsed_days": round(elapsed_days, 1),
            "threshold_days": round(threshold, 1),
            "overdue": overdue,
            "detail": (
                f"{company} typically responds in {threshold / self.overdue_multiplier:.0f} days. "
                f"It has been {elapsed_days:.0f} days."
            ),
        }

    # ------------------------------------------------------------------
    # Public: get_overdue_applications
    # ------------------------------------------------------------------

    def get_overdue_applications(self) -> list:
        """
        List all applications where the company is past their historical SLA.

        Checks every applied job without a response against employer_sla data.
        """
        if not self.enabled:
            return []

        try:
            jobs = self.state.conn.execute(
                """SELECT job_id FROM applied_jobs
                   WHERE job_id NOT IN (
                       SELECT job_id FROM response_tracking
                       WHERE response_at IS NOT NULL
                   )
                   ORDER BY applied_at ASC""",
            ).fetchall()
        except Exception as e:
            log.warning(f"Error fetching unanswered jobs: {e}")
            return []

        overdue_list = []
        for job in jobs:
            result = self.is_overdue(job["job_id"])
            if result.get("overdue"):
                overdue_list.append(result)

        log.info(f"Found {len(overdue_list)} overdue applications")
        return overdue_list

    # ------------------------------------------------------------------
    # Public: get_fastest_companies / get_slowest_companies
    # ------------------------------------------------------------------

    def get_fastest_companies(self, stage: str = "applied",
                              limit: int = 10) -> list:
        """Companies ranked by fastest average response time for a stage."""
        if not self.enabled:
            return []

        try:
            rows = self.state.conn.execute(
                """SELECT company, avg_days, min_days, max_days, sample_size
                   FROM employer_sla
                   WHERE stage = ? AND sample_size >= ?
                   ORDER BY avg_days ASC LIMIT ?""",
                (stage, self.min_sample_size, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.warning(f"Error getting fastest companies: {e}")
            return []

    def get_slowest_companies(self, stage: str = "applied",
                              limit: int = 10) -> list:
        """Companies ranked by slowest average response time for a stage."""
        if not self.enabled:
            return []

        try:
            rows = self.state.conn.execute(
                """SELECT company, avg_days, min_days, max_days, sample_size
                   FROM employer_sla
                   WHERE stage = ? AND sample_size >= ?
                   ORDER BY avg_days DESC LIMIT ?""",
                (stage, self.min_sample_size, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.warning(f"Error getting slowest companies: {e}")
            return []

    # ------------------------------------------------------------------
    # Public: generate_sla_report
    # ------------------------------------------------------------------

    def generate_sla_report(self) -> str:
        """
        Generate a formatted text report showing company response patterns.

        Groups by company, lists each stage with avg/min/max days,
        highlights overdue applications, and ranks fastest/slowest.
        """
        if not self.enabled:
            return "Employer SLA tracking is disabled."

        all_slas = self.get_all_slas()
        if not all_slas:
            return "No SLA data available yet. Apply to more jobs to build data."

        lines = ["=== Employer Response Time Report ===", ""]

        # Group by company
        by_company = {}
        for sla in all_slas:
            comp = sla["company"]
            if comp not in by_company:
                by_company[comp] = []
            by_company[comp].append(sla)

        for comp in sorted(by_company.keys()):
            stages = by_company[comp]
            lines.append(f"  {comp}:")
            for s in stages:
                lines.append(
                    f"    {s['stage']:15s}  avg {s['avg_days']:5.1f}d  "
                    f"(min {s['min_days']:.0f}d / max {s['max_days']:.0f}d)  "
                    f"[n={s['sample_size']}]"
                )
            lines.append("")

        # Fastest and slowest
        fastest = self.get_fastest_companies(limit=5)
        if fastest:
            lines.append("--- Fastest Responders (applied stage) ---")
            for i, f in enumerate(fastest, 1):
                lines.append(
                    f"  {i}. {f['company']} — avg {f['avg_days']:.1f} days"
                )
            lines.append("")

        slowest = self.get_slowest_companies(limit=5)
        if slowest:
            lines.append("--- Slowest Responders (applied stage) ---")
            for i, s in enumerate(slowest, 1):
                lines.append(
                    f"  {i}. {s['company']} — avg {s['avg_days']:.1f} days"
                )
            lines.append("")

        # Overdue
        overdue = self.get_overdue_applications()
        if overdue:
            lines.append(f"--- Overdue Applications ({len(overdue)}) ---")
            for o in overdue[:10]:
                lines.append(
                    f"  {o.get('company', '?')} — {o.get('title', '?')} — "
                    f"{o.get('elapsed_days', 0):.0f} days "
                    f"(threshold: {o.get('threshold_days', 0):.0f}d)"
                )
            lines.append("")

        return "\n".join(lines)
