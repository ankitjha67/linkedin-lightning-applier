"""
Interview Prep Generator.

After applying, auto-generates interview prep per job:
company research, likely questions (from JD), talking points
mapped to requirements. Saved per job in the database.
"""

import logging
from typing import Optional

log = logging.getLogger("lla.interview_prep")


class InterviewPrepGenerator:
    """Generate interview preparation materials per job using AI."""

    def __init__(self, ai, cfg: dict = None):
        self.ai = ai
        self.cfg = cfg or {}
        ip_cfg = self.cfg.get("interview_prep", {})
        self.enabled = ip_cfg.get("enabled", False)
        self.auto_generate = ip_cfg.get("auto_generate", True)

    def generate(self, job_id: str, title: str, company: str,
                 description: str, state=None) -> dict:
        """
        Generate interview prep materials for a job.

        Returns dict with: company_research, likely_questions, talking_points
        """
        if not self.enabled or not self.ai or not self.ai.enabled:
            return {}

        # Check if already generated
        if state:
            existing = state.get_interview_prep(job_id)
            if existing:
                return existing

        log.info(f"   📚 Generating interview prep for {title} @ {company}...")

        result = {
            "company_research": self._generate_company_research(company, description),
            "likely_questions": self._generate_questions(title, company, description),
            "talking_points": self._generate_talking_points(title, company, description),
        }

        # Save to database
        if state and any(result.values()):
            state.save_interview_prep(
                job_id=job_id, company=company, title=title,
                company_research=result["company_research"],
                likely_questions=result["likely_questions"],
                talking_points=result["talking_points"],
            )
            log.info(f"   📚 Interview prep saved for {job_id}")

        return result

    def _generate_company_research(self, company: str, description: str) -> str:
        """Generate company research notes."""
        system = f"""You are an interview preparation assistant. Generate brief company research notes.
Include: what the company does, industry, any signals from the job description about culture/values.
Keep it under 200 words.

{self.ai.profile_context}"""

        user = f"""Research notes for {company}.
Job description excerpt: {description[:1000]}

Brief company research (under 200 words):"""

        try:
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 500
            result = self.ai._call_llm(system, user)
            self.ai.max_tokens = old_max
            return result or ""
        except Exception as e:
            log.debug(f"Company research generation failed: {e}")
            return ""

    def _generate_questions(self, title: str, company: str, description: str) -> str:
        """Generate likely interview questions based on JD."""
        system = f"""You are an interview preparation assistant. Generate likely interview questions.
Create 8-10 questions: mix of technical, behavioral, and role-specific.
Base questions on the specific job requirements in the description.

{self.ai.profile_context}"""

        user = f"""Generate likely interview questions for:
Role: {title}
Company: {company}
Job Description: {description[:1500]}

List 8-10 questions (numbered):"""

        try:
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 800
            result = self.ai._call_llm(system, user)
            self.ai.max_tokens = old_max
            return result or ""
        except Exception as e:
            log.debug(f"Question generation failed: {e}")
            return ""

    def _generate_talking_points(self, title: str, company: str, description: str) -> str:
        """Generate talking points mapping candidate experience to job requirements."""
        system = f"""You are an interview preparation assistant. Map the candidate's experience to the job requirements.
For each key requirement, provide a specific talking point from the candidate's background.
Format: "Requirement → Your experience/example"
Keep it actionable and specific.

{self.ai.profile_context}"""

        user = f"""Create talking points for:
Role: {title}
Company: {company}
Job Description: {description[:1500]}

Map each key requirement to the candidate's relevant experience:"""

        try:
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 800
            result = self.ai._call_llm(system, user)
            self.ai.max_tokens = old_max
            return result or ""
        except Exception as e:
            log.debug(f"Talking points generation failed: {e}")
            return ""
