"""
Job Evaluator — Structured A-F evaluation per job.

Generates a 6-block report for each job:
  Block A — Role Summary
  Block B — CV Match (requirement-by-requirement)
  Block C — Level Strategy
  Block D — Comp Research
  Block E — Personalization Plan
  Block F — Interview Plan (STAR+R stories)

Each block is a separate AI call with a focused prompt.
Results are persisted to the job_evaluations table.
"""

import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("lla.job_evaluator")


class JobEvaluator:
    """Structured multi-block job evaluation using AI."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        ev_cfg = cfg.get("job_evaluation", {})
        self.enabled = ev_cfg.get("enabled", False)
        self.max_description_chars = ev_cfg.get("max_description_chars", 3000)
        self.grade_thresholds = ev_cfg.get("grade_thresholds", {
            "A": 85, "B": 70, "C": 55, "D": 40, "E": 25, "F": 0
        })

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, job_id: str, title: str, company: str,
                 description: str, match_result: dict = None) -> dict:
        """
        Generate a full 6-block evaluation for a job.

        Returns a dict with keys: role_summary, cv_match, gap_analysis,
        level_strategy, comp_research, personalization, interview_plan,
        match_grade, full_report.
        """
        if not self.enabled:
            log.debug("JobEvaluator disabled, skipping evaluation")
            return {}

        if not self.ai or not self.ai.enabled:
            log.warning("AI not available, cannot evaluate job")
            return {}

        # Check for cached evaluation
        existing = self.get_evaluation(job_id)
        if existing:
            log.info(f"Returning cached evaluation for {job_id}")
            return existing

        log.info(f"Evaluating {title} @ {company} (job_id={job_id})...")

        desc = description[:self.max_description_chars] if description else ""
        cv_text = getattr(self.ai, "profile_context", "")
        archetype = ""
        if match_result and isinstance(match_result, dict):
            archetype = match_result.get("archetype", "")

        # Generate each block via dedicated AI call
        role_summary = self._generate_block_a(title, company, desc)
        cv_match = self._generate_block_b(title, company, desc, cv_text)
        level_strategy = self._generate_block_c(title, company, desc, cv_text)
        comp_research = self._generate_block_d(title, company, desc)
        personalization = self._generate_block_e(title, company, desc, cv_text)
        interview_plan = self._generate_block_f(title, company, desc, cv_text)

        # Derive gap analysis from cv_match
        gap_analysis = self._extract_gap_analysis(cv_match)

        # Calculate grade
        match_score = 0
        gap_count = 0
        if match_result and isinstance(match_result, dict):
            match_score = match_result.get("score", 0)
        gap_count = gap_analysis.count("blocker") + gap_analysis.count("hard gap")
        grade = self.generate_grade(match_score, gap_count)

        # Assemble full report
        full_report = self._assemble_report(
            title, company, grade, role_summary, cv_match,
            gap_analysis, level_strategy, comp_research,
            personalization, interview_plan
        )

        result = {
            "job_id": job_id,
            "company": company,
            "title": title,
            "archetype": archetype,
            "match_grade": grade,
            "role_summary": role_summary,
            "cv_match": cv_match,
            "gap_analysis": gap_analysis,
            "level_strategy": level_strategy,
            "comp_research": comp_research,
            "personalization": personalization,
            "interview_plan": interview_plan,
            "full_report": full_report,
        }

        # Persist
        self._save_evaluation(result)
        log.info(f"Evaluation complete for {title} @ {company}: grade={grade}")
        return result

    def get_evaluation(self, job_id: str) -> Optional[dict]:
        """Retrieve a saved evaluation from the database."""
        try:
            row = self.state.conn.execute(
                "SELECT * FROM job_evaluations WHERE job_id = ?",
                (job_id,)
            ).fetchone()
            if not row:
                return None
            return {
                "job_id": row["job_id"],
                "company": row["company"],
                "title": row["title"],
                "archetype": row["archetype"],
                "match_grade": row["match_grade"],
                "role_summary": row["role_summary"],
                "cv_match": row["cv_match"],
                "gap_analysis": row["gap_analysis"],
                "level_strategy": row["level_strategy"],
                "comp_research": row["comp_research"],
                "personalization": row["personalization"],
                "interview_plan": row["interview_plan"],
                "full_report": row["full_report"],
                "evaluated_at": row["evaluated_at"],
            }
        except Exception as e:
            log.error(f"Failed to retrieve evaluation for {job_id}: {e}")
            return None

    def generate_grade(self, match_score: int, gap_count: int) -> str:
        """
        Calculate A-F grade from match score and gap count.

        Each hard gap penalizes the score by 8 points.
        """
        adjusted = max(0, match_score - (gap_count * 8))
        for grade in ["A", "B", "C", "D", "E"]:
            if adjusted >= self.grade_thresholds.get(grade, 0):
                return grade
        return "F"

    def get_evaluation_summary(self, job_id: str) -> str:
        """Return a short 2-line summary of the evaluation."""
        ev = self.get_evaluation(job_id)
        if not ev:
            return ""
        grade = ev.get("match_grade", "?")
        title = ev.get("title", "Unknown")
        company = ev.get("company", "Unknown")
        role_summary = ev.get("role_summary", "")
        # Extract first sentence of role summary as TL;DR
        tldr = role_summary.split(".")[0].strip() if role_summary else "No summary"
        return (
            f"[{grade}] {title} @ {company}\n"
            f"    {tldr}."
        )

    # ------------------------------------------------------------------
    # Block generators
    # ------------------------------------------------------------------

    def _generate_block_a(self, title: str, company: str, desc: str) -> str:
        """Block A -- Role Summary: archetype, domain, function, seniority, remote, TL;DR."""
        system = (
            "You are a senior career analyst. Given a job title, company, and description, "
            "produce a structured role summary. Include:\n"
            "1. Archetype (e.g., backend_engineer, data_scientist, devops_sre)\n"
            "2. Domain (e.g., fintech, healthcare, e-commerce)\n"
            "3. Function (e.g., platform, product, infrastructure)\n"
            "4. Seniority (junior / mid / senior / staff / principal / manager / director)\n"
            "5. Remote status (remote / hybrid / on-site / unclear)\n"
            "6. TL;DR (one sentence summarizing what this role actually does)\n\n"
            "Format as plain text with labeled lines. Be precise and concise."
        )
        user = (
            f"Job Title: {title}\n"
            f"Company: {company}\n"
            f"Description:\n{desc}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Block A (Role Summary) failed: {e}")
            return ""

    def _generate_block_b(self, title: str, company: str,
                          desc: str, cv_text: str) -> str:
        """Block B -- CV Match: map each JD requirement to CV lines, identify gaps."""
        system = (
            "You are a resume-to-JD matching expert. For each requirement in the job description:\n"
            "1. Quote the JD requirement\n"
            "2. Cite the matching CV line(s) verbatim, or state 'NO MATCH'\n"
            "3. If NO MATCH, classify as:\n"
            "   - HARD BLOCKER: required skill with no adjacent experience\n"
            "   - NICE-TO-HAVE: preferred skill, not critical\n"
            "4. For each gap, suggest:\n"
            "   - Adjacent experience that partially covers it\n"
            "   - Mitigation plan (what to say in interview)\n\n"
            "Be thorough. Cover EVERY requirement mentioned in the JD.\n"
            "Format each requirement as a numbered item."
        )
        user = (
            f"Job: {title} @ {company}\n\n"
            f"JOB DESCRIPTION:\n{desc}\n\n"
            f"CANDIDATE CV:\n{cv_text}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Block B (CV Match) failed: {e}")
            return ""

    def _generate_block_c(self, title: str, company: str,
                          desc: str, cv_text: str) -> str:
        """Block C -- Level Strategy: detected vs candidate level, sell-up phrases."""
        system = (
            "You are a career leveling strategist. Analyze the job description to detect "
            "the seniority level, then compare against the candidate's CV.\n\n"
            "Produce:\n"
            "1. DETECTED LEVEL: What level the JD implies (junior/mid/senior/staff/principal)\n"
            "2. CANDIDATE LEVEL: What level the CV suggests\n"
            "3. GAP ANALYSIS: Is the candidate above, at, or below the role level?\n"
            "4. SELL SENIOR WITHOUT LYING: 3-5 phrases the candidate can use to position "
            "themselves at the right level without exaggerating. Reference real CV items.\n"
            "5. DOWNLEVEL CONTINGENCY: If the role is above the candidate's level, what to "
            "say to still be competitive. If at-level or below, suggest how to negotiate up.\n\n"
            "Be specific and actionable. Reference real experience from the CV."
        )
        user = (
            f"Job: {title} @ {company}\n\n"
            f"JOB DESCRIPTION:\n{desc}\n\n"
            f"CANDIDATE CV:\n{cv_text}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Block C (Level Strategy) failed: {e}")
            return ""

    def _generate_block_d(self, title: str, company: str, desc: str) -> str:
        """Block D -- Comp Research: salary range, company rep, demand trend."""
        system = (
            "You are a compensation research analyst. Based on the job title, company, and "
            "description, estimate:\n\n"
            "1. SALARY RANGE: Estimated base salary range (low / mid / high) for this role "
            "and company tier. Specify currency and whether it is annual.\n"
            "2. TOTAL COMP: Estimated total compensation including bonus, equity, benefits.\n"
            "3. COMPANY REPUTATION: Tier (FAANG-adjacent / well-known / mid-market / startup / unknown), "
            "Glassdoor-style rating estimate, known pros/cons as an employer.\n"
            "4. MARKET DEMAND: Is this role type in high/medium/low demand right now? "
            "Any notable trends (AI hiring surge, layoffs in this space, etc.).\n"
            "5. NEGOTIATION NOTES: Key leverage points for salary negotiation.\n\n"
            "Be realistic. Clearly state when you are estimating vs citing known data."
        )
        user = (
            f"Job: {title} @ {company}\n"
            f"Description excerpt:\n{desc[:1500]}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Block D (Comp Research) failed: {e}")
            return ""

    def _generate_block_e(self, title: str, company: str,
                          desc: str, cv_text: str) -> str:
        """Block E -- Personalization Plan: top 5 CV changes + top 5 LinkedIn changes."""
        system = (
            "You are a career branding expert. Given the target job and the candidate's CV, "
            "produce two lists:\n\n"
            "TOP 5 CV CHANGES for this specific role:\n"
            "For each change, specify:\n"
            "- Which section to modify (summary, experience bullet, skills, etc.)\n"
            "- Exact current text (quote from CV)\n"
            "- Suggested replacement text\n"
            "- Why this change matters for THIS role\n\n"
            "TOP 5 LINKEDIN PROFILE CHANGES:\n"
            "For each change, specify:\n"
            "- Which section (headline, about, experience, skills, featured)\n"
            "- What to change or add\n"
            "- Why it matters for visibility to this company/role type\n\n"
            "Be specific and actionable. Every suggestion must reference the JD."
        )
        user = (
            f"TARGET ROLE: {title} @ {company}\n\n"
            f"JOB DESCRIPTION:\n{desc}\n\n"
            f"CURRENT CV:\n{cv_text}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Block E (Personalization) failed: {e}")
            return ""

    def _generate_block_f(self, title: str, company: str,
                          desc: str, cv_text: str) -> str:
        """Block F -- Interview Plan: STAR+R stories, case study, red-flag questions."""
        system = (
            "You are a senior interview coach. Prepare an interview plan:\n\n"
            "STAR+R STORIES (generate 4-6):\n"
            "For each story:\n"
            "- JD Requirement it addresses (quote from JD)\n"
            "- Theme (e.g., 'leadership', 'technical challenge', 'conflict resolution')\n"
            "- Title (short memorable name)\n"
            "- Situation (2-3 sentences, specific context from candidate's CV)\n"
            "- Task (what was the specific challenge)\n"
            "- Action (what the candidate did, emphasize leadership and initiative)\n"
            "- Result (quantified outcome where possible)\n"
            "- Reflection (what was learned, how it applies to this role)\n\n"
            "CASE STUDY RECOMMENDATION:\n"
            "- Suggest one technical or business case the candidate should prepare\n"
            "- Why this case is likely for this company/role\n"
            "- Key points to cover\n\n"
            "RED-FLAG QUESTIONS:\n"
            "- List 3-5 tough questions the interviewer might ask based on CV gaps\n"
            "- For each, suggest a strong response strategy\n\n"
            "Base ALL stories on the candidate's actual CV. Do not fabricate experience."
        )
        user = (
            f"TARGET ROLE: {title} @ {company}\n\n"
            f"JOB DESCRIPTION:\n{desc}\n\n"
            f"CANDIDATE CV:\n{cv_text}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Block F (Interview Plan) failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_gap_analysis(self, cv_match: str) -> str:
        """Parse Block B output to extract a condensed gap analysis."""
        if not cv_match:
            return "No gap analysis available (CV match block empty)."
        lines = []
        for line in cv_match.split("\n"):
            lower = line.lower()
            if any(kw in lower for kw in [
                "no match", "hard blocker", "nice-to-have", "gap",
                "mitigation", "adjacent experience", "blocker"
            ]):
                lines.append(line.strip())
        if not lines:
            return "No significant gaps identified."
        return "\n".join(lines)

    def _assemble_report(self, title: str, company: str, grade: str,
                         role_summary: str, cv_match: str, gap_analysis: str,
                         level_strategy: str, comp_research: str,
                         personalization: str, interview_plan: str) -> str:
        """Assemble all blocks into a single formatted report."""
        sep = "=" * 60
        return (
            f"{sep}\n"
            f"JOB EVALUATION REPORT: {title} @ {company}\n"
            f"Grade: {grade}\n"
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"{sep}\n\n"
            f"--- BLOCK A: ROLE SUMMARY ---\n{role_summary}\n\n"
            f"--- BLOCK B: CV MATCH ---\n{cv_match}\n\n"
            f"--- GAP ANALYSIS ---\n{gap_analysis}\n\n"
            f"--- BLOCK C: LEVEL STRATEGY ---\n{level_strategy}\n\n"
            f"--- BLOCK D: COMP RESEARCH ---\n{comp_research}\n\n"
            f"--- BLOCK E: PERSONALIZATION PLAN ---\n{personalization}\n\n"
            f"--- BLOCK F: INTERVIEW PLAN ---\n{interview_plan}\n\n"
            f"{sep}\n"
            f"END OF REPORT\n"
            f"{sep}\n"
        )

    def _save_evaluation(self, result: dict) -> None:
        """Persist evaluation to job_evaluations table."""
        try:
            self.state.conn.execute(
                """INSERT OR REPLACE INTO job_evaluations
                   (job_id, company, title, archetype, match_grade,
                    role_summary, cv_match, gap_analysis, level_strategy,
                    comp_research, personalization, interview_plan,
                    full_report, evaluated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result["job_id"], result["company"], result["title"],
                    result["archetype"], result["match_grade"],
                    result["role_summary"], result["cv_match"],
                    result["gap_analysis"], result["level_strategy"],
                    result["comp_research"], result["personalization"],
                    result["interview_plan"], result["full_report"],
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            )
            self.state.conn.commit()
            log.debug(f"Saved evaluation for job_id={result['job_id']}")
        except Exception as e:
            log.error(f"Failed to save evaluation: {e}")
