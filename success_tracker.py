"""
Application Success Tracking.

Tracks which applications get responses. Correlates response rates with:
job age at apply time, match score, resume version, whether recruiter was messaged.
Simple prediction model for response probability.
"""

import logging
import math
from datetime import datetime

log = logging.getLogger("lla.success_tracker")


class SuccessTracker:
    """Track and analyze application success rates."""

    def __init__(self, state, cfg: dict = None):
        self.state = state
        self.cfg = cfg or {}
        st_cfg = self.cfg.get("success_tracking", {})
        self.enabled = st_cfg.get("enabled", False)

    def record_response(self, job_id: str, response_type: str = "callback",
                        notes: str = ""):
        """
        Record a response to an application.

        response_type: callback, interview, rejection, offer, ghosted
        """
        if not self.enabled:
            return

        # Get application details
        row = self.state.conn.execute(
            "SELECT * FROM applied_jobs WHERE job_id=?", (job_id,)
        ).fetchone()

        if not row:
            log.warning(f"No application found for job_id={job_id}")
            return

        # Check if recruiter was messaged
        msg = self.state.conn.execute(
            "SELECT 1 FROM message_queue WHERE job_id=? AND status='sent'", (job_id,)
        ).fetchone()

        self.state.save_response(
            job_id=job_id,
            title=row["title"],
            company=row["company"],
            applied_at=row["applied_at"],
            response_type=response_type,
            match_score=row["match_score"],
            resume_version=row["resume_version"],
            recruiter_messaged=msg is not None,
            notes=notes,
        )
        log.info(f"📩 Response recorded: {response_type} for {row['title']} @ {row['company']}")

    def get_stats(self) -> dict:
        """Get overall response statistics."""
        return self.state.get_response_stats()

    def get_correlation_analysis(self) -> dict:
        """Analyze what factors correlate with getting responses."""
        if not self.enabled:
            return {}

        results = {}

        # Response rate by match score bucket
        for lo, hi, label in [(0, 49, "low (0-49)"), (50, 69, "mid (50-69)"),
                              (70, 89, "high (70-89)"), (90, 100, "very high (90-100)")]:
            total = self.state.conn.execute("""
                SELECT COUNT(*) as c FROM applied_jobs WHERE match_score BETWEEN ? AND ?
            """, (lo, hi)).fetchone()["c"]
            responses = self.state.conn.execute("""
                SELECT COUNT(*) as c FROM response_tracking
                WHERE match_score BETWEEN ? AND ?
            """, (lo, hi)).fetchone()["c"]
            rate = (responses / total * 100) if total > 0 else 0
            results[f"score_{label}"] = {"total": total, "responses": responses,
                                         "rate": round(rate, 1)}

        # Response rate when recruiter was messaged vs not
        for messaged, label in [(1, "messaged"), (0, "not_messaged")]:
            total = self.state.conn.execute("""
                SELECT COUNT(DISTINCT job_id) as c FROM message_queue
                WHERE status=?
            """, ("sent" if messaged else "pending",)).fetchone()["c"]
            responses = self.state.conn.execute("""
                SELECT COUNT(*) as c FROM response_tracking WHERE recruiter_messaged=?
            """, (messaged,)).fetchone()["c"]
            rate = (responses / max(total, 1) * 100)
            results[f"recruiter_{label}"] = {"total": total, "responses": responses,
                                              "rate": round(rate, 1)}

        # Average days to response
        avg = self.state.conn.execute("""
            SELECT AVG(days_to_response) as avg_d FROM response_tracking
            WHERE days_to_response > 0
        """).fetchone()
        results["avg_days_to_response"] = round(avg["avg_d"], 1) if avg["avg_d"] else 0

        return results

    def predict_response_probability(self, match_score: int = 0,
                                     recruiter_messaged: bool = False) -> float:
        """
        Simple logistic regression-like prediction of response probability.
        Based on historical data patterns.
        """
        if not self.enabled:
            return 0.0

        # Get baseline response rate
        total_applied = self.state.total_applied()
        total_responses = self.state.conn.execute(
            "SELECT COUNT(*) as c FROM response_tracking"
        ).fetchone()["c"]

        if total_applied == 0:
            return 0.1  # Default 10% if no data

        base_rate = total_responses / total_applied

        # Adjust by match score
        score_multiplier = 1.0
        if match_score >= 90:
            score_multiplier = 2.0
        elif match_score >= 70:
            score_multiplier = 1.5
        elif match_score >= 50:
            score_multiplier = 1.0
        else:
            score_multiplier = 0.5

        # Adjust by recruiter messaging
        msg_multiplier = 1.5 if recruiter_messaged else 1.0

        probability = base_rate * score_multiplier * msg_multiplier
        return min(probability, 0.95)  # Cap at 95%
