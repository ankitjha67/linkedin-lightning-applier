"""
Application Forensics — Deep Analysis of Application Outcomes.

Examines every angle of the job search funnel: which company types respond,
what timing works best, how match scores correlate with callbacks, whether
recruiter messaging makes a difference, and which JD keywords predict
success.  Synthesizes findings into actionable AI-generated recommendations.

Analysis dimensions:
  1. Company type — size, industry signals in name/title
  2. Timing — day of week, hour of day for applications
  3. Match score — response rate curve across score buckets
  4. Recruiter messaging — with vs. without outreach
  5. Keywords — JD terms that correlate with positive outcomes
"""

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("lla.application_forensics")

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

INSIGHTS_SYSTEM = """You are a data-driven career strategist.
Given the application analytics below, produce 5-7 actionable recommendations.
Return ONLY valid JSON:
{
  "insights": [
    {"title": "short title", "detail": "1-2 sentence explanation", "priority": "high|medium|low"}
  ],
  "summary": "3-4 sentence executive summary of application performance",
  "biggest_win": "the single most impactful finding",
  "biggest_risk": "the single biggest area of concern"
}"""

# Score buckets for analysis
SCORE_BUCKETS = [
    (0, 30, "0-30"),
    (31, 50, "31-50"),
    (51, 70, "51-70"),
    (71, 85, "71-85"),
    (86, 100, "86-100"),
]

# Common positive-signal keywords in JDs
TRACKED_KEYWORDS = [
    "remote", "hybrid", "visa", "sponsor", "equity", "stock", "bonus",
    "flexible", "growth", "senior", "lead", "principal", "staff",
    "startup", "series", "enterprise", "fortune", "agile", "innovative",
]


class ApplicationForensics:
    """Deep analytics engine for application outcomes and strategy optimisation."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        af_cfg = cfg.get("application_forensics", {})
        self.enabled = af_cfg.get("enabled", False)
        self.min_data_points = af_cfg.get("min_data_points", 10)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_full_analysis(self) -> Optional[dict]:
        """Analyse all dimensions and return a comprehensive forensics report.

        Runs each sub-analysis, feeds them into AI for synthesis, and persists
        the report in the forensics_reports table.
        """
        if not self.enabled:
            log.debug("ApplicationForensics disabled; skipping analysis")
            return None

        log.info("Running full application forensics analysis")

        total = self._count_applications()
        if total < self.min_data_points:
            log.warning("Only %d applications — need %d for meaningful analysis",
                        total, self.min_data_points)
            return {"error": f"Insufficient data: {total} applications (need {self.min_data_points})"}

        # Run each dimension
        by_company = self._analyze_by_company_type()
        by_timing = self._analyze_by_timing()
        by_score = self._analyze_by_match_score()
        by_messaging = self._analyze_by_recruiter_messaging()
        by_keywords = self._analyze_by_keywords()

        analysis = {
            "total_applications": total,
            "by_company_type": by_company,
            "by_timing": by_timing,
            "by_match_score": by_score,
            "by_recruiter_messaging": by_messaging,
            "by_keywords": by_keywords,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

        # AI synthesis
        insights = self.generate_insights(analysis)
        analysis["insights"] = insights

        # Persist report
        self._save_report("full_analysis", analysis)

        return analysis

    def generate_insights(self, analysis: dict) -> dict:
        """Use AI to synthesize 5-7 actionable recommendations from raw analysis."""
        if not self.ai or not self.ai.enabled:
            return self._fallback_insights(analysis)

        user_prompt = (
            f"Application analytics data:\n"
            f"{json.dumps(analysis, indent=2, default=str)}\n\n"
            f"Generate 5-7 actionable insights and recommendations."
        )

        try:
            raw = self.ai._call_llm(INSIGHTS_SYSTEM, user_prompt)
            result = json.loads(raw)
            result.setdefault("insights", [])
            result.setdefault("summary", "Analysis complete.")
            return result
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            log.warning("Failed to parse AI insights: %s", exc)

        return self._fallback_insights(analysis)

    def get_latest_report(self) -> Optional[dict]:
        """Retrieve the most recent saved forensics report."""
        if not self.enabled:
            return None

        try:
            row = self.state.conn.execute(
                """SELECT * FROM forensics_reports
                   WHERE report_type = 'full_analysis'
                   ORDER BY generated_at DESC LIMIT 1"""
            ).fetchone()
            if not row:
                return None

            report = dict(row)
            report["patterns"] = json.loads(report.get("patterns", "[]"))
            report["recommendations"] = json.loads(report.get("recommendations", "[]"))
            return report
        except Exception as exc:
            log.error("Failed to load latest report: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Analysis dimensions
    # ------------------------------------------------------------------

    def _analyze_by_company_type(self) -> dict:
        """Response rates segmented by company characteristics.

        Groups companies by inferred type (startup, enterprise, agency, etc.)
        based on signals in the company name and job data.
        """
        try:
            rows = self.state.conn.execute(
                """SELECT a.company, a.title, a.location,
                          r.response_type, r.days_to_response
                   FROM applied_jobs a
                   LEFT JOIN response_tracking r ON a.job_id = r.job_id"""
            ).fetchall()
        except Exception as exc:
            log.error("Company-type analysis failed: %s", exc)
            return {}

        buckets = defaultdict(lambda: {"total": 0, "responded": 0, "avg_days": []})

        for row in rows:
            row = dict(row)
            ctype = self._infer_company_type(row.get("company", ""))
            buckets[ctype]["total"] += 1
            if row.get("response_type") and row["response_type"] != "":
                buckets[ctype]["responded"] += 1
                if row.get("days_to_response") and row["days_to_response"] > 0:
                    buckets[ctype]["avg_days"].append(row["days_to_response"])

        result = {}
        for ctype, data in buckets.items():
            avg_d = data["avg_days"]
            result[ctype] = {
                "total": data["total"],
                "responded": data["responded"],
                "response_rate": round(data["responded"] / data["total"] * 100, 1)
                                 if data["total"] else 0,
                "avg_days_to_response": round(sum(avg_d) / len(avg_d), 1) if avg_d else 0,
            }

        return result

    def _analyze_by_timing(self) -> dict:
        """Best day-of-week and approximate time for applications.

        Correlates application timestamp with response rates.
        """
        try:
            rows = self.state.conn.execute(
                """SELECT a.applied_at, r.response_type
                   FROM applied_jobs a
                   LEFT JOIN response_tracking r ON a.job_id = r.job_id
                   WHERE a.applied_at IS NOT NULL AND a.applied_at != ''"""
            ).fetchall()
        except Exception as exc:
            log.error("Timing analysis failed: %s", exc)
            return {}

        day_stats = defaultdict(lambda: {"total": 0, "responded": 0})
        hour_stats = defaultdict(lambda: {"total": 0, "responded": 0})

        for row in rows:
            row = dict(row)
            applied_at = row.get("applied_at", "")
            if not applied_at:
                continue

            try:
                dt = datetime.fromisoformat(applied_at.replace("Z", "+00:00"))
                day_name = dt.strftime("%A")
                hour = dt.hour
            except (ValueError, TypeError):
                continue

            day_stats[day_name]["total"] += 1
            hour_stats[hour]["responded"] += 0  # ensure key exists
            hour_stats[hour]["total"] += 1

            has_response = bool(row.get("response_type"))
            if has_response:
                day_stats[day_name]["responded"] += 1
                hour_stats[hour]["responded"] += 1

        day_results = {}
        for day, data in day_stats.items():
            day_results[day] = {
                "total": data["total"],
                "responded": data["responded"],
                "response_rate": round(data["responded"] / data["total"] * 100, 1)
                                 if data["total"] else 0,
            }

        hour_results = {}
        for hour in sorted(hour_stats.keys()):
            data = hour_stats[hour]
            hour_results[str(hour)] = {
                "total": data["total"],
                "responded": data["responded"],
                "response_rate": round(data["responded"] / data["total"] * 100, 1)
                                 if data["total"] else 0,
            }

        # Find best day and hour
        best_day = max(day_results, key=lambda d: day_results[d]["response_rate"],
                       default="N/A") if day_results else "N/A"
        best_hour = max(hour_results, key=lambda h: hour_results[h]["response_rate"],
                        default="N/A") if hour_results else "N/A"

        return {
            "by_day": day_results,
            "by_hour": hour_results,
            "best_day": best_day,
            "best_hour": best_hour,
        }

    def _analyze_by_match_score(self) -> dict:
        """Response rate curve segmented by match score buckets."""
        try:
            rows = self.state.conn.execute(
                """SELECT m.score, r.response_type
                   FROM match_scores m
                   LEFT JOIN response_tracking r ON m.job_id = r.job_id
                   WHERE m.score > 0"""
            ).fetchall()
        except Exception as exc:
            log.error("Match score analysis failed: %s", exc)
            return {}

        bucket_stats = {label: {"total": 0, "responded": 0}
                        for _, _, label in SCORE_BUCKETS}

        for row in rows:
            row = dict(row)
            score = row.get("score", 0)
            for low, high, label in SCORE_BUCKETS:
                if low <= score <= high:
                    bucket_stats[label]["total"] += 1
                    if row.get("response_type") and row["response_type"] != "":
                        bucket_stats[label]["responded"] += 1
                    break

        result = {}
        for label, data in bucket_stats.items():
            result[label] = {
                "total": data["total"],
                "responded": data["responded"],
                "response_rate": round(data["responded"] / data["total"] * 100, 1)
                                 if data["total"] else 0,
            }

        # Find the score threshold where response rate jumps
        threshold = "N/A"
        for label, data in result.items():
            if data["response_rate"] >= 20 and data["total"] >= 3:
                threshold = label
                break

        result["effective_threshold"] = threshold
        return result

    def _analyze_by_recruiter_messaging(self) -> dict:
        """Compare outcomes for applications with and without recruiter outreach."""
        try:
            rows = self.state.conn.execute(
                """SELECT r.recruiter_messaged, r.response_type, r.days_to_response
                   FROM response_tracking r"""
            ).fetchall()
        except Exception as exc:
            log.error("Recruiter messaging analysis failed: %s", exc)
            return {}

        groups = {
            "with_message": {"total": 0, "responded": 0, "days": []},
            "without_message": {"total": 0, "responded": 0, "days": []},
        }

        for row in rows:
            row = dict(row)
            key = "with_message" if row.get("recruiter_messaged") else "without_message"
            groups[key]["total"] += 1
            if row.get("response_type") and row["response_type"] != "":
                groups[key]["responded"] += 1
                if row.get("days_to_response") and row["days_to_response"] > 0:
                    groups[key]["days"].append(row["days_to_response"])

        result = {}
        for key, data in groups.items():
            avg_days = data["days"]
            result[key] = {
                "total": data["total"],
                "responded": data["responded"],
                "response_rate": round(data["responded"] / data["total"] * 100, 1)
                                 if data["total"] else 0,
                "avg_days_to_response": round(sum(avg_days) / len(avg_days), 1)
                                        if avg_days else 0,
            }

        # Calculate lift
        with_rate = result.get("with_message", {}).get("response_rate", 0)
        without_rate = result.get("without_message", {}).get("response_rate", 0)
        lift = round(with_rate - without_rate, 1)
        result["messaging_lift_pct"] = lift
        result["messaging_effective"] = lift > 5

        return result

    def _analyze_by_keywords(self) -> dict:
        """Correlate JD keywords with response rates."""
        try:
            rows = self.state.conn.execute(
                """SELECT a.description, r.response_type
                   FROM applied_jobs a
                   LEFT JOIN response_tracking r ON a.job_id = r.job_id
                   WHERE a.description IS NOT NULL AND a.description != ''"""
            ).fetchall()
        except Exception as exc:
            log.error("Keyword analysis failed: %s", exc)
            return {}

        keyword_stats = {kw: {"total": 0, "responded": 0} for kw in TRACKED_KEYWORDS}

        for row in rows:
            row = dict(row)
            desc_lower = (row.get("description", "") or "").lower()
            has_response = bool(row.get("response_type") and row["response_type"] != "")

            for kw in TRACKED_KEYWORDS:
                if kw in desc_lower:
                    keyword_stats[kw]["total"] += 1
                    if has_response:
                        keyword_stats[kw]["responded"] += 1

        result = {}
        for kw, data in keyword_stats.items():
            if data["total"] > 0:
                result[kw] = {
                    "total": data["total"],
                    "responded": data["responded"],
                    "response_rate": round(data["responded"] / data["total"] * 100, 1),
                }

        # Sort by response rate descending
        sorted_kws = sorted(result.items(),
                            key=lambda x: x[1]["response_rate"], reverse=True)
        top_keywords = [kw for kw, _ in sorted_kws[:5]]
        bottom_keywords = [kw for kw, _ in sorted_kws[-3:]] if len(sorted_kws) >= 3 else []

        return {
            "keyword_stats": result,
            "top_keywords": top_keywords,
            "bottom_keywords": bottom_keywords,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _count_applications(self) -> int:
        """Total number of applications in the database."""
        try:
            row = self.state.conn.execute(
                "SELECT COUNT(*) as c FROM applied_jobs"
            ).fetchone()
            return row["c"] if row else 0
        except Exception:
            return 0

    def _save_report(self, report_type: str, analysis: dict) -> None:
        """Persist a forensics report."""
        insights = analysis.get("insights", {})
        patterns = []
        recommendations = []

        if isinstance(insights, dict):
            patterns = [i.get("title", "") for i in insights.get("insights", [])]
            recommendations = [i.get("detail", "") for i in insights.get("insights", [])]

        try:
            self.state.conn.execute(
                """INSERT INTO forensics_reports
                   (report_type, findings, patterns, recommendations,
                    data_points, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    report_type,
                    json.dumps(analysis, default=str),
                    json.dumps(patterns),
                    json.dumps(recommendations),
                    analysis.get("total_applications", 0),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.state.conn.commit()
        except Exception as exc:
            log.error("Failed to save forensics report: %s", exc)

    @staticmethod
    def _infer_company_type(company_name: str) -> str:
        """Infer company type from name heuristics."""
        if not company_name:
            return "unknown"

        name = company_name.lower()
        if any(w in name for w in ["google", "meta", "amazon", "apple",
                                    "microsoft", "netflix"]):
            return "faang"
        if any(w in name for w in ["bank", "capital", "financial", "goldman",
                                    "morgan", "citi", "jpmorgan"]):
            return "finance"
        if any(w in name for w in ["consulting", "deloitte", "mckinsey",
                                    "accenture", "pwc", "kpmg"]):
            return "consulting"
        if any(w in name for w in ["agency", "studio", "creative", "media"]):
            return "agency"
        if any(w in name for w in ["labs", "ai", "io", ".io"]):
            return "startup"

        return "other"

    @staticmethod
    def _fallback_insights(analysis: dict) -> dict:
        """Generate basic insights without AI."""
        insights = []
        total = analysis.get("total_applications", 0)

        # Check messaging effectiveness
        msg_data = analysis.get("by_recruiter_messaging", {})
        if msg_data.get("messaging_effective"):
            lift = msg_data.get("messaging_lift_pct", 0)
            insights.append({
                "title": "Recruiter messaging boosts responses",
                "detail": f"Applications with recruiter outreach show {lift}% higher response rate.",
                "priority": "high",
            })

        # Check score correlation
        score_data = analysis.get("by_match_score", {})
        threshold = score_data.get("effective_threshold", "N/A")
        if threshold != "N/A":
            insights.append({
                "title": f"Focus on jobs scoring {threshold}+",
                "detail": "Response rates increase significantly above this match score range.",
                "priority": "high",
            })

        # Timing
        timing = analysis.get("by_timing", {})
        best_day = timing.get("best_day", "N/A")
        if best_day != "N/A":
            insights.append({
                "title": f"Apply on {best_day}s for best results",
                "detail": f"Applications submitted on {best_day} have the highest response rate.",
                "priority": "medium",
            })

        # Keywords
        kw_data = analysis.get("by_keywords", {})
        top_kw = kw_data.get("top_keywords", [])
        if top_kw:
            insights.append({
                "title": f"Target postings with: {', '.join(top_kw[:3])}",
                "detail": "These keywords in job descriptions correlate with higher response rates.",
                "priority": "medium",
            })

        if not insights:
            insights.append({
                "title": "Insufficient patterns detected",
                "detail": f"With {total} applications, continue applying to build more data.",
                "priority": "low",
            })

        return {
            "insights": insights,
            "summary": f"Analysis of {total} applications across multiple dimensions.",
            "biggest_win": insights[0]["title"] if insights else "N/A",
            "biggest_risk": "Low sample size" if total < 50 else "Review full report",
        }
