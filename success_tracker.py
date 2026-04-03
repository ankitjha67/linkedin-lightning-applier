"""
Application Success Tracking with Learned Prediction.

Tracks which applications get responses. Correlates response rates with:
match score, resume version, recruiter messaging, company, location,
visa status, job age, salary range, day of week applied.

Uses logistic regression trained on historical data to predict response
probability for new applications. Exports insights to CSV.
"""

import json
import logging
import math
from collections import defaultdict
from datetime import datetime, date

log = logging.getLogger("lla.success_tracker")

RESPONSE_TYPES = ("callback", "interview", "rejection", "offer", "ghosted")


class SuccessTracker:
    """Track, analyze, and predict application success using learned models."""

    def __init__(self, state, cfg: dict = None):
        self.state = state
        self.cfg = cfg or {}
        st_cfg = self.cfg.get("success_tracking", {})
        self.enabled = st_cfg.get("enabled", False)
        # Learned model weights (logistic regression coefficients)
        self._weights = None
        self._model_stale = True

    # ── Recording ─────────────────────────────────────────────

    def record_response(self, job_id: str, response_type: str = "callback",
                        notes: str = ""):
        """Record a response to an application."""
        if not self.enabled:
            return

        if response_type not in RESPONSE_TYPES:
            log.warning(f"Unknown response type '{response_type}', using 'callback'")
            response_type = "callback"

        row = self.state.conn.execute(
            "SELECT * FROM applied_jobs WHERE job_id=?", (job_id,)
        ).fetchone()

        if not row:
            log.warning(f"No application found for job_id={job_id}")
            return

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
        self._model_stale = True
        log.info(f"Response recorded: {response_type} for {row['title']} @ {row['company']}")

    def mark_ghosted(self, days_threshold: int = 14):
        """Auto-mark old applications with no response as ghosted."""
        if not self.enabled:
            return 0

        # Find applied jobs older than threshold with no response
        rows = self.state.conn.execute("""
            SELECT a.job_id, a.title, a.company, a.applied_at, a.match_score,
                   a.resume_version
            FROM applied_jobs a
            LEFT JOIN response_tracking r ON a.job_id = r.job_id
            WHERE r.job_id IS NULL
              AND julianday('now') - julianday(a.applied_at) > ?
        """, (days_threshold,)).fetchall()

        count = 0
        for row in rows:
            msg = self.state.conn.execute(
                "SELECT 1 FROM message_queue WHERE job_id=? AND status='sent'",
                (row["job_id"],)
            ).fetchone()
            self.state.save_response(
                job_id=row["job_id"], title=row["title"], company=row["company"],
                applied_at=row["applied_at"], response_type="ghosted",
                match_score=row["match_score"],
                resume_version=row["resume_version"],
                recruiter_messaged=msg is not None,
            )
            count += 1

        if count:
            self._model_stale = True
            log.info(f"Marked {count} applications as ghosted (>{days_threshold} days)")
        return count

    # ── Statistics ────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get overall response statistics."""
        return self.state.get_response_stats()

    def get_correlation_analysis(self) -> dict:
        """Deep multi-factor correlation analysis."""
        if not self.enabled:
            return {}

        results = {}

        # 1. Response rate by match score bucket
        for lo, hi, label in [(0, 49, "0-49"), (50, 69, "50-69"),
                              (70, 89, "70-89"), (90, 100, "90-100")]:
            applied = self.state.conn.execute(
                "SELECT COUNT(*) as c FROM applied_jobs WHERE match_score BETWEEN ? AND ?",
                (lo, hi)).fetchone()["c"]
            positive = self.state.conn.execute("""
                SELECT COUNT(*) as c FROM response_tracking
                WHERE match_score BETWEEN ? AND ?
                  AND response_type IN ('callback', 'interview', 'offer')
            """, (lo, hi)).fetchone()["c"]
            rate = (positive / applied * 100) if applied > 0 else 0
            results[f"score_{label}"] = {
                "applied": applied, "positive_responses": positive,
                "response_rate": round(rate, 1)
            }

        # 2. Recruiter messaging impact
        for messaged, label in [(1, "messaged"), (0, "not_messaged")]:
            applied = self.state.conn.execute("""
                SELECT COUNT(*) as c FROM applied_jobs a
                WHERE EXISTS (
                    SELECT 1 FROM message_queue m
                    WHERE m.job_id = a.job_id AND m.status = 'sent'
                ) = ?
            """, (messaged,)).fetchone()["c"]
            positive = self.state.conn.execute("""
                SELECT COUNT(*) as c FROM response_tracking
                WHERE recruiter_messaged = ?
                  AND response_type IN ('callback', 'interview', 'offer')
            """, (messaged,)).fetchone()["c"]
            rate = (positive / max(applied, 1) * 100)
            results[f"recruiter_{label}"] = {
                "applied": applied, "positive_responses": positive,
                "response_rate": round(rate, 1)
            }

        # 3. Tailored vs generic resume
        for has_resume, label in [("has", "tailored"), ("none", "generic")]:
            if has_resume == "has":
                cond = "resume_version != '' AND resume_version IS NOT NULL"
            else:
                cond = "(resume_version = '' OR resume_version IS NULL)"
            applied = self.state.conn.execute(
                f"SELECT COUNT(*) as c FROM applied_jobs WHERE {cond}"
            ).fetchone()["c"]
            positive = self.state.conn.execute(f"""
                SELECT COUNT(*) as c FROM response_tracking r
                JOIN applied_jobs a ON r.job_id = a.job_id
                WHERE {cond.replace('resume_version', 'a.resume_version')}
                  AND r.response_type IN ('callback', 'interview', 'offer')
            """).fetchone()["c"]
            rate = (positive / max(applied, 1) * 100)
            results[f"resume_{label}"] = {
                "applied": applied, "positive_responses": positive,
                "response_rate": round(rate, 1)
            }

        # 4. Visa sponsorship impact
        for visa, label in [("yes", "visa_yes"), ("no", "visa_no"), ("unknown", "visa_unknown")]:
            applied = self.state.conn.execute(
                "SELECT COUNT(*) as c FROM applied_jobs WHERE visa_sponsorship=?",
                (visa,)).fetchone()["c"]
            positive = self.state.conn.execute("""
                SELECT COUNT(*) as c FROM response_tracking r
                JOIN applied_jobs a ON r.job_id = a.job_id
                WHERE a.visa_sponsorship = ?
                  AND r.response_type IN ('callback', 'interview', 'offer')
            """, (visa,)).fetchone()["c"]
            rate = (positive / max(applied, 1) * 100)
            results[f"visa_{label}"] = {
                "applied": applied, "positive_responses": positive,
                "response_rate": round(rate, 1)
            }

        # 5. Day of week analysis
        dow_data = self.state.conn.execute("""
            SELECT
                CASE CAST(strftime('%w', applied_at) AS INTEGER)
                    WHEN 0 THEN 'Sun' WHEN 1 THEN 'Mon' WHEN 2 THEN 'Tue'
                    WHEN 3 THEN 'Wed' WHEN 4 THEN 'Thu' WHEN 5 THEN 'Fri'
                    WHEN 6 THEN 'Sat' END as dow,
                COUNT(*) as total
            FROM applied_jobs
            WHERE applied_at IS NOT NULL
            GROUP BY dow ORDER BY CAST(strftime('%w', applied_at) AS INTEGER)
        """).fetchall()
        results["by_day_of_week"] = {r["dow"]: r["total"] for r in dow_data}

        # 6. Top responding companies
        top_companies = self.state.conn.execute("""
            SELECT company, COUNT(*) as responses,
                   SUM(CASE WHEN response_type IN ('callback','interview','offer') THEN 1 ELSE 0 END) as positive
            FROM response_tracking
            GROUP BY company ORDER BY positive DESC LIMIT 10
        """).fetchall()
        results["top_responding_companies"] = [
            {"company": r["company"], "responses": r["responses"], "positive": r["positive"]}
            for r in top_companies
        ]

        # 7. Average days to response by type
        avg_days = self.state.conn.execute("""
            SELECT response_type, AVG(days_to_response) as avg_d, COUNT(*) as c
            FROM response_tracking WHERE days_to_response > 0
            GROUP BY response_type
        """).fetchall()
        results["avg_days_by_type"] = {
            r["response_type"]: {"avg_days": round(r["avg_d"], 1), "count": r["c"]}
            for r in avg_days
        }

        return results

    # ── Prediction Model ──────────────────────────────────────

    def _build_feature_vector(self, match_score: int, recruiter_messaged: bool,
                               has_tailored_resume: bool = False,
                               visa_status: str = "unknown",
                               day_of_week: int = None) -> list[float]:
        """Build feature vector for prediction."""
        if day_of_week is None:
            day_of_week = datetime.now().weekday()

        return [
            1.0,                                        # bias
            match_score / 100.0,                       # normalized score
            (match_score / 100.0) ** 2,                # score squared (nonlinear)
            1.0 if recruiter_messaged else 0.0,        # messaging flag
            1.0 if has_tailored_resume else 0.0,       # resume flag
            1.0 if visa_status == "yes" else 0.0,      # visa confirmed
            1.0 if visa_status == "no" else 0.0,       # visa denied
            1.0 if day_of_week < 5 else 0.0,           # weekday flag
            1.0 if day_of_week in (1, 2) else 0.0,     # Tue/Wed (peak hiring)
        ]

    def _train_model(self):
        """Train logistic regression on historical data."""
        if not self.state:
            return

        # Collect training data
        rows = self.state.conn.execute("""
            SELECT r.match_score, r.recruiter_messaged, r.resume_version,
                   r.response_type, a.visa_sponsorship, a.applied_at
            FROM response_tracking r
            LEFT JOIN applied_jobs a ON r.job_id = a.job_id
        """).fetchall()

        if len(rows) < 5:
            # Not enough data — use reasonable priors
            self._weights = [
                -1.5,   # bias (base ~18% response rate)
                2.0,    # match_score (higher = better)
                0.5,    # score_squared
                0.8,    # recruiter_messaged
                0.4,    # tailored_resume
                0.3,    # visa_yes
                -0.5,   # visa_no
                0.2,    # weekday
                0.1,    # tue_wed
            ]
            self._model_stale = False
            return

        # Build training set
        X, y = [], []
        for row in rows:
            try:
                dow = datetime.strptime(row["applied_at"], "%Y-%m-%d %H:%M:%S").weekday() if row["applied_at"] else 2
            except (ValueError, TypeError):
                dow = 2
            features = self._build_feature_vector(
                match_score=row["match_score"] or 50,
                recruiter_messaged=bool(row["recruiter_messaged"]),
                has_tailored_resume=bool(row["resume_version"]),
                visa_status=row["visa_sponsorship"] or "unknown",
                day_of_week=dow,
            )
            X.append(features)
            # Positive outcome = callback, interview, or offer
            label = 1.0 if row["response_type"] in ("callback", "interview", "offer") else 0.0
            y.append(label)

        # Mini-batch gradient descent logistic regression
        n_features = len(X[0])
        weights = [0.0] * n_features
        lr = 0.1
        reg = 0.01  # L2 regularization

        for epoch in range(200):
            total_loss = 0.0
            for i in range(len(X)):
                z = sum(w * x for w, x in zip(weights, X[i]))
                pred = 1.0 / (1.0 + math.exp(-max(-500, min(500, z))))  # sigmoid
                error = pred - y[i]
                total_loss += -y[i] * math.log(max(pred, 1e-10)) - (1 - y[i]) * math.log(max(1 - pred, 1e-10))

                for j in range(n_features):
                    grad = error * X[i][j] + reg * weights[j]
                    weights[j] -= lr * grad / len(X)

            # Decay learning rate
            if epoch % 50 == 49:
                lr *= 0.5

        self._weights = weights
        self._model_stale = False

        # Log model quality
        correct = 0
        for i in range(len(X)):
            z = sum(w * x for w, x in zip(weights, X[i]))
            pred = 1.0 / (1.0 + math.exp(-max(-500, min(500, z))))
            if (pred >= 0.5 and y[i] == 1.0) or (pred < 0.5 and y[i] == 0.0):
                correct += 1
        accuracy = correct / len(X) * 100 if X else 0
        log.info(f"Success model trained: {len(X)} samples, {accuracy:.1f}% accuracy, "
                 f"weights={[round(w, 3) for w in weights]}")

    def predict_response_probability(self, match_score: int = 50,
                                     recruiter_messaged: bool = False,
                                     has_tailored_resume: bool = False,
                                     visa_status: str = "unknown") -> float:
        """
        Predict probability of positive response using trained logistic regression.

        Returns float 0.0-1.0 representing probability of callback/interview/offer.
        """
        if not self.enabled:
            return 0.0

        if self._model_stale or self._weights is None:
            try:
                self._train_model()
            except Exception as e:
                log.debug(f"Model training failed: {e}")
                # Fallback heuristic
                base = 0.12
                base *= 1 + (match_score - 50) / 100
                if recruiter_messaged:
                    base *= 1.4
                if has_tailored_resume:
                    base *= 1.2
                return min(max(base, 0.01), 0.95)

        features = self._build_feature_vector(
            match_score, recruiter_messaged, has_tailored_resume, visa_status
        )
        z = sum(w * x for w, x in zip(self._weights, features))
        probability = 1.0 / (1.0 + math.exp(-max(-500, min(500, z))))

        return round(min(max(probability, 0.01), 0.95), 3)

    def get_feature_importance(self) -> dict:
        """Get feature importance from trained model weights."""
        if self._weights is None:
            self._train_model()
        if self._weights is None:
            return {}

        names = ["bias", "match_score", "score_squared", "recruiter_messaged",
                 "tailored_resume", "visa_yes", "visa_no", "weekday", "tue_wed"]
        importance = {}
        for name, weight in zip(names, self._weights):
            if name == "bias":
                continue
            importance[name] = round(weight, 3)

        return dict(sorted(importance.items(), key=lambda x: abs(x[1]), reverse=True))

    def generate_insights_report(self) -> str:
        """Generate a human-readable insights report."""
        stats = self.get_stats()
        corr = self.get_correlation_analysis()
        importance = self.get_feature_importance()

        lines = [
            "Application Success Report",
            "=" * 40,
            f"Total responses tracked: {stats.get('total_responses', 0)}",
            f"Average days to response: {stats.get('avg_days_to_response', 'N/A')}",
            "",
            "Response Rate by Match Score:",
        ]

        for key in ["score_0-49", "score_50-69", "score_70-89", "score_90-100"]:
            data = corr.get(key, {})
            lines.append(f"  {key}: {data.get('response_rate', 0)}% "
                        f"({data.get('positive_responses', 0)}/{data.get('applied', 0)})")

        lines.extend(["", "Recruiter Messaging Impact:"])
        for key in ["recruiter_messaged", "recruiter_not_messaged"]:
            data = corr.get(key, {})
            lines.append(f"  {key}: {data.get('response_rate', 0)}%")

        lines.extend(["", "Resume Tailoring Impact:"])
        for key in ["resume_tailored", "resume_generic"]:
            data = corr.get(key, {})
            lines.append(f"  {key}: {data.get('response_rate', 0)}%")

        if importance:
            lines.extend(["", "Feature Importance (model weights):"])
            for name, weight in importance.items():
                direction = "+" if weight > 0 else ""
                lines.append(f"  {name}: {direction}{weight}")

        top = corr.get("top_responding_companies", [])
        if top:
            lines.extend(["", "Top Responding Companies:"])
            for c in top[:5]:
                lines.append(f"  {c['company']}: {c['positive']}/{c['responses']} positive")

        return "\n".join(lines)
