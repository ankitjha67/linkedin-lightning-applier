"""
Ghost Predictor — Estimates the Probability a Job Application Will Be Ghosted.

Combines multiple risk signals to predict whether an application will receive
any response.  Signals include:

  - Company historical ghost rate (from response_tracking)
  - Posting age (older posts = higher risk)
  - JD quality (vague descriptions ghost more)
  - Salary transparency (posted salary = lower risk)
  - Match score (low match = higher risk)

Outputs a ghost probability (0-1) with per-factor breakdowns and risk labels.
"""

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("lla.ghost_predictor")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

JD_QUALITY_SYSTEM = """You are a job description quality analyst.
Score this job description on a scale of 1-10 for clarity and completeness.
Return ONLY valid JSON:
{
  "quality_score": 7,
  "red_flags": ["list of vague or concerning elements"],
  "specificity": 8,
  "requirements_clarity": 6
}
Higher scores = clearer, more specific JDs. Lower scores = vague, boilerplate."""

# Risk factor weights for logistic combination
DEFAULT_WEIGHTS = {
    "company_history": 0.30,
    "posting_age": 0.20,
    "jd_quality": 0.20,
    "salary_transparency": 0.15,
    "match_score": 0.15,
}

# Posting age risk curve (days -> factor)
AGE_THRESHOLDS = [
    (3, 0.1),    # 0-3 days: low risk
    (7, 0.2),    # 4-7 days: slight risk
    (14, 0.4),   # 1-2 weeks: moderate
    (30, 0.6),   # 2-4 weeks: elevated
    (60, 0.8),   # 1-2 months: high
    (999, 0.95), # 2+ months: very high
]


class GhostPredictor:
    """Predicts the probability that a job application will be ghosted."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        gp_cfg = cfg.get("ghost_predictor", {})
        self.enabled = gp_cfg.get("enabled", False)
        self.high_risk_threshold = gp_cfg.get("high_risk_threshold", 0.65)
        self.weights = gp_cfg.get("weights", dict(DEFAULT_WEIGHTS))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def predict(self, job_id: str, title: str, company: str,
                description: str = "", posted_time: str = "",
                match_score: int = 0) -> Optional[dict]:
        """Compute ghost probability (0-1) with per-factor breakdown.

        Args:
            job_id: unique job identifier
            title: job title
            company: company name
            description: full job description text
            posted_time: when the job was posted (ISO or relative like "2 weeks ago")
            match_score: candidate match score 0-100

        Returns:
            dict with probability, risk_level, factors, and recommendations
        """
        if not self.enabled:
            log.debug("GhostPredictor disabled; skipping prediction")
            return None

        log.info("Predicting ghost probability for %s at %s", title, company)

        # Compute individual factors (each returns 0-1 risk)
        factors = {
            "company_history": self._company_ghost_rate(company),
            "posting_age": self._posting_age_factor(posted_time),
            "jd_quality": self._jd_quality_score(description),
            "salary_transparency": self._salary_transparency_factor(description),
            "match_score": self._match_score_factor(match_score),
        }

        # Weighted logistic combination
        probability = self._compute_probability(factors)

        # Risk level label
        if probability >= 0.75:
            risk_level = "very_high"
        elif probability >= 0.55:
            risk_level = "high"
        elif probability >= 0.35:
            risk_level = "moderate"
        elif probability >= 0.15:
            risk_level = "low"
        else:
            risk_level = "very_low"

        # Build risk factors summary
        risk_factors = []
        for name, value in sorted(factors.items(), key=lambda x: x[1], reverse=True):
            if value >= 0.5:
                risk_factors.append({
                    "factor": name,
                    "risk_value": round(value, 3),
                    "severity": "high" if value >= 0.7 else "moderate",
                })

        result = {
            "job_id": job_id,
            "company": company,
            "title": title,
            "ghost_probability": round(probability, 3),
            "risk_level": risk_level,
            "factors": {k: round(v, 3) for k, v in factors.items()},
            "risk_factors": risk_factors,
            "recommendation": self._get_recommendation(probability, factors),
            "predicted_at": datetime.now(timezone.utc).isoformat(),
        }

        # Persist prediction
        self._save_prediction(result)

        return result

    def get_company_ghost_rankings(self) -> list:
        """Return companies ranked by historical ghost rate (highest first)."""
        if not self.enabled:
            return []

        try:
            rows = self.state.conn.execute(
                """SELECT company,
                          COUNT(*) as total_apps,
                          SUM(CASE WHEN response_type = '' OR response_type IS NULL
                              THEN 1 ELSE 0 END) as ghosted,
                          AVG(days_to_response) as avg_response_days
                   FROM response_tracking
                   GROUP BY company
                   HAVING total_apps >= 2
                   ORDER BY (CAST(ghosted AS REAL) / total_apps) DESC"""
            ).fetchall()

            rankings = []
            for row in rows:
                row = dict(row)
                total = row["total_apps"]
                ghosted = row["ghosted"]
                rate = round(ghosted / total, 3) if total else 0
                rankings.append({
                    "company": row["company"],
                    "total_applications": total,
                    "ghosted_count": ghosted,
                    "ghost_rate": rate,
                    "avg_response_days": round(row["avg_response_days"] or 0, 1),
                    "risk_label": "high" if rate > 0.7 else ("moderate" if rate > 0.4 else "low"),
                })

            return rankings
        except Exception as exc:
            log.error("Failed to compute ghost rankings: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Factor computation
    # ------------------------------------------------------------------

    def _company_ghost_rate(self, company: str) -> float:
        """Historical ghost rate from response_tracking for this company.

        Returns 0-1 (proportion of applications that received no response).
        Falls back to 0.5 (neutral) if no data.
        """
        if not company:
            return 0.5

        try:
            row = self.state.conn.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN response_type = '' OR response_type IS NULL
                              THEN 1 ELSE 0 END) as ghosted
                   FROM response_tracking
                   WHERE company = ?""",
                (company,),
            ).fetchone()

            if row and row["total"] >= 2:
                rate = row["ghosted"] / row["total"]
                log.debug("Company %s ghost rate: %.2f (%d apps)", company, rate, row["total"])
                return rate

        except Exception as exc:
            log.debug("Could not compute ghost rate for %s: %s", company, exc)

        return 0.5  # Neutral prior

    def _posting_age_factor(self, posted_time: str) -> float:
        """Older postings have higher ghost risk.

        Accepts ISO timestamps or relative strings like "2 weeks ago".
        Returns 0-1 risk factor.
        """
        if not posted_time:
            return 0.4  # Unknown age = moderate risk

        days = self._parse_posting_age_days(posted_time)
        if days is None:
            return 0.4

        for threshold_days, risk in AGE_THRESHOLDS:
            if days <= threshold_days:
                return risk

        return 0.95

    def _jd_quality_score(self, description: str) -> float:
        """Score JD quality; vague descriptions ghost more.

        Returns 0-1 risk (higher = worse JD = more ghost risk).
        """
        if not description:
            return 0.7  # No description = high risk

        desc_lower = description.lower()
        length = len(description)

        # Heuristic signals
        score = 0.5  # Baseline

        # Length penalty (very short JDs are suspicious)
        if length < 200:
            score += 0.2
        elif length < 500:
            score += 0.1
        elif length > 2000:
            score -= 0.1

        # Specificity checks
        has_requirements = any(w in desc_lower for w in
                               ["requirements", "qualifications", "must have", "required"])
        has_responsibilities = any(w in desc_lower for w in
                                   ["responsibilities", "you will", "duties", "role involves"])
        has_tech_stack = any(w in desc_lower for w in
                             ["python", "java", "react", "aws", "sql", "kubernetes",
                              "docker", "typescript", "golang", "rust"])
        has_team_info = any(w in desc_lower for w in
                            ["team of", "report to", "team size", "department"])

        if has_requirements:
            score -= 0.1
        if has_responsibilities:
            score -= 0.1
        if has_tech_stack:
            score -= 0.1
        if has_team_info:
            score -= 0.05

        # Red flag phrases
        red_flags = ["fast-paced", "wear many hats", "self-starter",
                     "rockstar", "ninja", "guru", "competitive salary"]
        flag_count = sum(1 for f in red_flags if f in desc_lower)
        score += flag_count * 0.05

        # AI enhancement if available
        if self.ai and self.ai.enabled and length > 100:
            ai_quality = self._ai_jd_quality(description)
            if ai_quality is not None:
                # Invert: AI gives 1-10 quality, we want 0-1 risk
                score = 1.0 - (ai_quality / 10.0)

        return max(0.0, min(1.0, score))

    def _salary_transparency_factor(self, description: str) -> float:
        """Posted salary signals = lower ghost risk.

        Returns 0-1 risk (lower if salary is mentioned).
        """
        if not description:
            return 0.6

        desc_lower = description.lower()

        # Check for salary indicators
        salary_patterns = [
            r"\$[\d,]+",                          # $120,000
            r"\d+k\s*[-–]\s*\d+k",               # 120k-150k
            r"salary\s*[:]\s*\$?\d+",             # salary: $120000
            r"compensation\s*[:]\s*\$?\d+",       # compensation: $120000
            r"base\s+salary",                      # "base salary"
            r"pay\s+range",                        # "pay range"
            r"annual\s+(?:salary|compensation)",   # "annual salary"
        ]

        for pattern in salary_patterns:
            if re.search(pattern, desc_lower):
                return 0.2  # Salary posted = low ghost risk

        # Check for salary-adjacent language
        if any(w in desc_lower for w in ["competitive pay", "market rate",
                                          "commensurate with experience"]):
            return 0.4  # Vague salary mention = moderate

        return 0.6  # No salary info = elevated risk

    def _match_score_factor(self, match_score: int) -> float:
        """Lower match scores correlate with higher ghost probability."""
        if match_score <= 0:
            return 0.5  # Unknown

        # Inverse linear mapping: 100 score -> 0.1 risk, 0 score -> 0.9 risk
        risk = max(0.1, min(0.9, 1.0 - (match_score / 110.0)))
        return risk

    def _compute_probability(self, factors: dict) -> float:
        """Weighted logistic combination of risk factors.

        Applies a logistic (sigmoid) curve to the weighted sum so that
        extreme factors pull the probability toward 0 or 1 non-linearly.
        """
        weighted_sum = 0
        total_weight = 0

        for name, risk_value in factors.items():
            weight = self.weights.get(name, 0.1)
            weighted_sum += risk_value * weight
            total_weight += weight

        if total_weight == 0:
            return 0.5

        # Normalise to 0-1 range
        linear_score = weighted_sum / total_weight

        # Apply logistic curve centred at 0.5 with steepness 6
        # This makes moderate risks stay moderate but pushes extremes
        logit_input = (linear_score - 0.5) * 6
        probability = 1.0 / (1.0 + math.exp(-logit_input))

        return probability

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_posting_age_days(self, posted_time: str) -> Optional[int]:
        """Parse posting time into age in days."""
        if not posted_time:
            return None

        # Try ISO timestamp
        try:
            dt = datetime.fromisoformat(posted_time.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)
            return max(0, delta.days)
        except (ValueError, TypeError):
            pass

        # Try relative strings: "2 weeks ago", "3 days ago", "1 month ago"
        lower = posted_time.lower().strip()
        match = re.search(r"(\d+)\s*(minute|hour|day|week|month|year)", lower)
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            multipliers = {
                "minute": 0, "hour": 0, "day": 1,
                "week": 7, "month": 30, "year": 365,
            }
            return num * multipliers.get(unit, 1)

        # "Just posted" / "today"
        if any(w in lower for w in ["just", "today", "now"]):
            return 0

        return None

    def _ai_jd_quality(self, description: str) -> Optional[float]:
        """Use AI to score JD quality 1-10."""
        try:
            # Truncate to avoid excessive token usage
            truncated = description[:3000]
            raw = self.ai._call_llm(JD_QUALITY_SYSTEM, truncated)
            result = json.loads(raw)
            quality = float(result.get("quality_score", 5))
            return max(1, min(10, quality))
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as exc:
            log.debug("AI JD quality scoring failed: %s", exc)
            return None

    def _save_prediction(self, result: dict) -> None:
        """Persist ghost prediction to database."""
        try:
            self.state.conn.execute(
                """INSERT OR REPLACE INTO ghost_predictions
                   (job_id, company, title, ghost_probability,
                    risk_factors, predicted_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    result["job_id"],
                    result["company"],
                    result["title"],
                    result["ghost_probability"],
                    json.dumps(result.get("risk_factors", [])),
                    result["predicted_at"],
                ),
            )
            self.state.conn.commit()
        except Exception as exc:
            log.error("Failed to save ghost prediction: %s", exc)

    @staticmethod
    def _get_recommendation(probability: float, factors: dict) -> str:
        """Generate a human-readable recommendation based on the prediction."""
        if probability >= 0.75:
            return ("High ghost risk. Consider skipping unless this is a top-priority "
                    "company. Focus effort on roles with better response signals.")

        if probability >= 0.55:
            top_factor = max(factors, key=factors.get) if factors else "unknown"
            return (f"Elevated ghost risk (primary driver: {top_factor}). "
                    f"Apply but follow up proactively within 5 business days.")

        if probability >= 0.35:
            return ("Moderate ghost risk. Standard application approach with "
                    "a follow-up at the 7-day mark is recommended.")

        return ("Low ghost risk. This application looks promising. "
                "Apply with a tailored resume and targeted cover letter.")
