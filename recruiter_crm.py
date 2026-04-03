"""
Recruiter CRM / Relationship Scoring Module

Scores recruiter relationships based on interaction history (applications,
messages, follow-ups, responses, interviews). Uses AI to suggest outreach
strategies for warm contacts.
"""

import logging
import math
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class RecruiterCRM:
    """Tracks recruiter interactions and computes relationship scores."""

    INTERACTION_TYPES = (
        "applied",
        "messaged",
        "followed_up",
        "responded",
        "interviewed",
        "referred",
        "rejected",
        "offer",
    )

    # Weights for each interaction type in score computation
    INTERACTION_WEIGHTS = {
        "applied": 0.1,
        "messaged": 0.15,
        "followed_up": 0.1,
        "responded": 0.3,
        "interviewed": 0.4,
        "referred": 0.5,
        "rejected": -0.1,
        "offer": 0.6,
    }

    # Score thresholds
    WARM_THRESHOLD = 2.0
    HOT_THRESHOLD = 5.0

    def __init__(self, ai, cfg, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        crm_cfg = cfg.get("recruiter_crm", {})
        self.enabled = crm_cfg.get("enabled", False)
        self.score_decay_days = crm_cfg.get("score_decay_days", 90)
        self.recency_bonus_days = crm_cfg.get("recency_bonus_days", 14)
        self.recency_bonus_value = crm_cfg.get("recency_bonus_value", 1.0)
        if self.enabled:
            logger.info("RecruiterCRM enabled")
        else:
            logger.debug("RecruiterCRM disabled")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_interaction(self, recruiter_name, company, interaction_type,
                        job_id=None, notes=""):
        """Record an interaction with a recruiter.

        Args:
            recruiter_name: Full name of the recruiter.
            company: Company they work for.
            interaction_type: One of INTERACTION_TYPES.
            job_id: Related LinkedIn job ID (optional).
            notes: Free-text notes about the interaction.

        Returns:
            True if logged, False otherwise.
        """
        if not self.enabled:
            logger.debug("Interaction logging skipped: module disabled")
            return False

        if interaction_type not in self.INTERACTION_TYPES:
            logger.warning(
                "Unknown interaction type '%s'; defaulting to 'applied'",
                interaction_type,
            )
            interaction_type = "applied"

        try:
            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                """INSERT INTO recruiter_interactions
                   (recruiter_name, company, interaction_type, job_id, notes, occurred_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (recruiter_name, company, interaction_type, str(job_id) if job_id else "", notes, now),
            )
            self.state.conn.commit()

            # Recompute the score after every interaction
            self._update_score(recruiter_name, company)

            logger.info(
                "Logged %s interaction with %s at %s",
                interaction_type, recruiter_name, company,
            )
            return True
        except Exception:
            logger.exception(
                "Failed to log interaction with %s at %s", recruiter_name, company
            )
            return False

    def compute_score(self, recruiter_name, company):
        """Compute a weighted relationship score for a recruiter.

        Formula:
            base + sum(interaction_weight) + (responses * 0.3) + recency_bonus - decay

        Args:
            recruiter_name: Recruiter full name.
            company: Company name.

        Returns:
            Float score (0.0 minimum). Higher is better.
        """
        if not self.enabled:
            return 0.0

        try:
            interactions = self.state.conn.execute(
                """SELECT interaction_type, occurred_at
                   FROM recruiter_interactions
                   WHERE recruiter_name = ? AND company = ?
                   ORDER BY occurred_at DESC""",
                (recruiter_name, company),
            ).fetchall()
        except Exception:
            logger.exception("Failed to fetch interactions for %s at %s", recruiter_name, company)
            return 0.0

        if not interactions:
            return 0.0

        now = datetime.now(timezone.utc)
        base_score = 1.0
        weighted_sum = 0.0
        response_count = 0
        most_recent = None

        for row in interactions:
            itype = row["interaction_type"]
            weight = self.INTERACTION_WEIGHTS.get(itype, 0.1)
            weighted_sum += weight

            if itype == "responded":
                response_count += 1

            created = row["occurred_at"]
            if most_recent is None:
                most_recent = created

        # Response rate bonus
        total_interactions = len(interactions)
        response_bonus = response_count * 0.3

        # Recency bonus: if most recent interaction is within recency_bonus_days
        recency_bonus = 0.0
        if most_recent:
            try:
                last_dt = datetime.fromisoformat(most_recent.replace("Z", "+00:00"))
                if hasattr(last_dt, "tzinfo") and last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                days_since = (now - last_dt).days
                if days_since <= self.recency_bonus_days:
                    recency_bonus = self.recency_bonus_value
            except (ValueError, TypeError):
                pass

        # Decay: score degrades over time if no recent interaction
        decay = 0.0
        if most_recent:
            try:
                last_dt = datetime.fromisoformat(most_recent.replace("Z", "+00:00"))
                if hasattr(last_dt, "tzinfo") and last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                days_since = (now - last_dt).days
                if days_since > self.score_decay_days:
                    # Logarithmic decay past the threshold
                    excess = days_since - self.score_decay_days
                    decay = math.log1p(excess) * 0.5
            except (ValueError, TypeError):
                pass

        score = max(0.0, base_score + weighted_sum + response_bonus + recency_bonus - decay)
        return round(score, 2)

    def get_top_recruiters(self, limit=20):
        """Get recruiters ranked by relationship score.

        Args:
            limit: Maximum number of recruiters to return.

        Returns:
            List of dicts with recruiter_name, company, score, interaction_count, last_interaction.
        """
        if not self.enabled:
            return []

        try:
            rows = self.state.conn.execute(
                """SELECT recruiter_name, company, score, updated_at
                   FROM recruiter_scores
                   ORDER BY score DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        except Exception:
            logger.exception("Failed to fetch top recruiters")
            return []

        results = []
        for row in rows:
            name = row["recruiter_name"]
            company = row["company"]
            interaction_count = self._count_interactions(name, company)
            last = self._last_interaction_date(name, company)
            results.append({
                "recruiter_name": name,
                "company": company,
                "score": row["score"],
                "interaction_count": interaction_count,
                "last_interaction": last,
            })

        return results

    def get_recruiter_profile(self, name, company):
        """Get full interaction history for a specific recruiter.

        Args:
            name: Recruiter name.
            company: Company name.

        Returns:
            Dict with recruiter info, score, interaction history, and summary.
        """
        if not self.enabled:
            return {}

        score = self.compute_score(name, company)

        try:
            interactions = self.state.conn.execute(
                """SELECT interaction_type, job_id, notes, occurred_at
                   FROM recruiter_interactions
                   WHERE recruiter_name = ? AND company = ?
                   ORDER BY occurred_at DESC""",
                (name, company),
            ).fetchall()
        except Exception:
            logger.exception("Failed to fetch profile for %s at %s", name, company)
            interactions = []

        history = []
        type_counts = {}
        for row in interactions:
            itype = row["interaction_type"]
            type_counts[itype] = type_counts.get(itype, 0) + 1
            history.append({
                "type": itype,
                "job_id": row["job_id"],
                "notes": row["notes"],
                "date": row["occurred_at"],
            })

        # Classify warmth
        if score >= self.HOT_THRESHOLD:
            warmth = "hot"
        elif score >= self.WARM_THRESHOLD:
            warmth = "warm"
        else:
            warmth = "cold"

        return {
            "recruiter_name": name,
            "company": company,
            "score": score,
            "warmth": warmth,
            "interaction_count": len(history),
            "type_counts": type_counts,
            "history": history,
        }

    def should_prioritize_job(self, recruiter_name, company):
        """Check if a job should be prioritized based on recruiter relationship.

        Returns True if there is a warm or hot relationship with the recruiter.

        Args:
            recruiter_name: Recruiter name.
            company: Company name.

        Returns:
            True if warm relationship exists, False otherwise.
        """
        if not self.enabled:
            return False

        score = self.compute_score(recruiter_name, company)
        prioritize = score >= self.WARM_THRESHOLD
        if prioritize:
            logger.info(
                "Prioritizing job: warm relationship with %s at %s (score=%.2f)",
                recruiter_name, company, score,
            )
        return prioritize

    def generate_outreach_strategy(self, recruiter_name, company):
        """Use AI to suggest next touchpoint with a recruiter.

        Args:
            recruiter_name: Recruiter name.
            company: Company name.

        Returns:
            String with AI-generated outreach suggestions, or a default
            message if AI is unavailable.
        """
        if not self.enabled:
            return "Module disabled."

        profile = self.get_recruiter_profile(recruiter_name, company)
        if not profile:
            return "No interaction history found."

        prompt = self._build_outreach_prompt(profile)

        if not self.ai:
            return self._fallback_outreach(profile)

        try:
            response = self.ai.chat(prompt)
            if response:
                logger.info("Generated AI outreach strategy for %s at %s", recruiter_name, company)
                return response
        except Exception:
            logger.exception("AI outreach generation failed for %s at %s", recruiter_name, company)

        return self._fallback_outreach(profile)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _update_score(self, recruiter_name, company):
        """Recompute and persist the score for a recruiter."""
        score = self.compute_score(recruiter_name, company)
        try:
            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                """INSERT INTO recruiter_scores (recruiter_name, company, score, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(recruiter_name, company)
                   DO UPDATE SET score = ?, updated_at = ?""",
                (recruiter_name, company, score, now, score, now),
            )
            self.state.conn.commit()
        except Exception:
            logger.exception("Failed to update score for %s at %s", recruiter_name, company)

    def _count_interactions(self, name, company):
        """Count total interactions with a recruiter."""
        try:
            row = self.state.conn.execute(
                """SELECT COUNT(*) as cnt FROM recruiter_interactions
                   WHERE recruiter_name = ? AND company = ?""",
                (name, company),
            ).fetchone()
            return row["cnt"] if row else 0
        except Exception:
            return 0

    def _last_interaction_date(self, name, company):
        """Get date of the most recent interaction."""
        try:
            row = self.state.conn.execute(
                """SELECT occurred_at FROM recruiter_interactions
                   WHERE recruiter_name = ? AND company = ?
                   ORDER BY occurred_at DESC LIMIT 1""",
                (name, company),
            ).fetchone()
            return row["occurred_at"] if row else None
        except Exception:
            return None

    def _build_outreach_prompt(self, profile):
        """Build the AI prompt for outreach strategy generation."""
        history_summary = []
        for h in profile.get("history", [])[:10]:
            history_summary.append(f"- {h['date'][:10]}: {h['type']} (notes: {h.get('notes', 'none')})")

        return (
            f"I have an existing relationship with recruiter {profile['recruiter_name']} "
            f"at {profile['company']}. Warmth level: {profile['warmth']}. "
            f"Relationship score: {profile['score']}.\n\n"
            f"Interaction history (most recent first):\n"
            + "\n".join(history_summary)
            + "\n\nSuggest a concise, professional outreach strategy for the next touchpoint. "
            "Include timing, channel (LinkedIn message, email, etc.), and a brief message template. "
            "Keep it under 150 words."
        )

    def _fallback_outreach(self, profile):
        """Generate a rule-based outreach suggestion when AI is unavailable."""
        warmth = profile.get("warmth", "cold")
        name = profile.get("recruiter_name", "the recruiter")
        company = profile.get("company", "the company")

        if warmth == "hot":
            return (
                f"Strong relationship with {name} at {company}. "
                "Consider a direct LinkedIn message referencing your recent conversations. "
                "Mention specific roles you are interested in and ask about upcoming openings."
            )
        elif warmth == "warm":
            return (
                f"Moderate relationship with {name} at {company}. "
                "Send a brief LinkedIn message checking in. Reference your previous application "
                "or interview and express continued interest in the company."
            )
        else:
            return (
                f"Limited interaction history with {name} at {company}. "
                "Consider engaging with their LinkedIn posts first, then send a personalized "
                "connection request mentioning a shared interest or mutual connection."
            )
