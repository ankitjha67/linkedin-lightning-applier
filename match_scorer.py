"""
Job-Candidate Match Scoring Engine.

Uses AI to score how well a job description matches the candidate's CV (0-100%).
Scores are used to filter out low-match jobs before applying.
"""

import json
import logging
import re
from typing import Optional

log = logging.getLogger("lla.match_scorer")


class MatchScorer:
    """Score job-candidate fit using AI analysis."""

    def __init__(self, ai, cfg: dict):
        self.ai = ai
        self.cfg = cfg
        ms_cfg = cfg.get("match_scoring", {})
        self.enabled = ms_cfg.get("enabled", False)
        self.minimum_score = ms_cfg.get("minimum_score", 70)
        self.score_in_csv = ms_cfg.get("score_in_csv", True)

    def score_job(self, title: str, company: str, description: str,
                  location: str = "") -> dict:
        """
        Score a job against the candidate's CV.

        Returns:
            dict with keys: score (int 0-100), skill_matches (list),
            missing_skills (list), explanation (str)
        """
        if not self.enabled or not self.ai or not self.ai.enabled:
            return {"score": 0, "skill_matches": [], "missing_skills": [], "explanation": "scoring disabled"}

        if not description:
            return {"score": 50, "skill_matches": [], "missing_skills": [], "explanation": "no description available"}

        system_prompt = f"""You are a job-candidate match scoring engine.
Score how well the candidate's profile matches the job description on a scale of 0-100.

SCORING CRITERIA:
- 90-100: Perfect match — candidate has all required skills + experience level
- 70-89: Strong match — candidate has most required skills, minor gaps
- 50-69: Partial match — candidate has some relevant skills but significant gaps
- 30-49: Weak match — limited skill overlap
- 0-29: Poor match — different field/specialization entirely

CANDIDATE PROFILE:
{self.ai.profile_context}

RESPOND IN EXACTLY THIS JSON FORMAT (no other text):
{{"score": <number 0-100>, "skill_matches": ["skill1", "skill2"], "missing_skills": ["skill1", "skill2"], "explanation": "one sentence"}}"""

        user_prompt = f"""Score this job:
Title: {title}
Company: {company}
Location: {location}
Description: {description[:2000]}"""

        try:
            # Use higher max_tokens for structured response
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 500
            result = self.ai._call_llm(system_prompt, user_prompt)
            self.ai.max_tokens = old_max

            if result:
                return self._parse_score(result)
        except Exception as e:
            log.warning(f"Match scoring failed: {e}")

        return {"score": 50, "skill_matches": [], "missing_skills": [], "explanation": "scoring error"}

    def _parse_score(self, raw: str) -> dict:
        """Parse AI response into structured score dict."""
        # Try JSON parse first
        try:
            # Extract JSON from response (may have markdown wrapping)
            json_match = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                score = max(0, min(100, int(data.get("score", 50))))
                return {
                    "score": score,
                    "skill_matches": data.get("skill_matches", []),
                    "missing_skills": data.get("missing_skills", []),
                    "explanation": data.get("explanation", ""),
                }
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # Fallback: try to extract just the score number
        numbers = re.findall(r'\b(\d{1,3})\b', raw)
        for n in numbers:
            n_int = int(n)
            if 0 <= n_int <= 100:
                return {
                    "score": n_int,
                    "skill_matches": [],
                    "missing_skills": [],
                    "explanation": raw[:200],
                }

        return {"score": 50, "skill_matches": [], "missing_skills": [], "explanation": "could not parse"}

    def should_apply(self, score: int) -> bool:
        """Check if score meets minimum threshold."""
        if not self.enabled:
            return True
        return score >= self.minimum_score
