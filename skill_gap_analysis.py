"""
Skill Gap Analysis.

Across ALL jobs seen (applied + skipped), aggregates the most-requested skills.
Compares against the candidate's CV to identify gaps:
"These 10 skills appear in 80% of target jobs but aren't on your CV."
Actionable career intelligence for upskilling decisions.
"""

import logging
import re
from collections import defaultdict

log = logging.getLogger("lla.skill_gap")


class SkillGapAnalyzer:
    """Analyze skill demand across job descriptions and identify gaps vs CV."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        sg_cfg = cfg.get("skill_gap_analysis", {})
        self.enabled = sg_cfg.get("enabled", False)
        self.auto_extract = sg_cfg.get("auto_extract", True)

        # Build candidate skill set from config
        self.candidate_skills = set()
        self._load_candidate_skills()

    def _load_candidate_skills(self):
        """Extract candidate's skills from config/CV text."""
        # From question_answers
        qa = self.cfg.get("question_answers", {})
        for key in ["skills", "technical skills", "programming", "certifications"]:
            val = qa.get(key, "")
            if val:
                for skill in re.split(r'[,;|]', str(val)):
                    s = skill.strip().lower()
                    if s and len(s) > 1:
                        self.candidate_skills.add(s)

        # From CV text
        cv = self.cfg.get("ai", {}).get("cv_text", "")
        if cv and self.ai and self.ai.enabled:
            # Use AI to extract skills from CV (one-time on init)
            try:
                skills = self.ai.extract_skills_from_jd(cv)
                for s in skills:
                    self.candidate_skills.add(s.strip().lower())
            except Exception:
                pass

        if self.candidate_skills:
            log.debug(f"Candidate skills loaded: {len(self.candidate_skills)}")

    def analyze_job(self, job_id: str, title: str, description: str):
        """Extract skills from a job description and update frequency data."""
        if not self.enabled or not description:
            return

        skills = self._extract_skills(description)
        if not skills:
            return

        for skill in skills:
            skill_lower = skill.strip().lower()
            if len(skill_lower) < 2:
                continue

            # Check if candidate has this skill
            matched = any(
                skill_lower in cs or cs in skill_lower
                for cs in self.candidate_skills
            )
            self.state.increment_skill(skill_lower, matched)

    def _extract_skills(self, text: str) -> list[str]:
        """Extract skills from text using AI or regex patterns."""
        # Try AI extraction first
        if self.ai and self.ai.enabled and self.auto_extract:
            try:
                skills = self.ai.extract_skills_from_jd(text)
                if skills:
                    return skills
            except Exception:
                pass

        # Fallback: regex-based extraction
        return self._regex_extract_skills(text)

    def _regex_extract_skills(self, text: str) -> list[str]:
        """Extract skills using pattern matching (no AI needed)."""
        # Common technical skill patterns
        known_skills = {
            "python", "java", "javascript", "typescript", "c++", "c#", "go", "golang",
            "rust", "ruby", "php", "swift", "kotlin", "scala", "r", "matlab",
            "sql", "nosql", "mongodb", "postgresql", "mysql", "redis", "elasticsearch",
            "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "ansible",
            "react", "angular", "vue", "node.js", "django", "flask", "spring",
            "machine learning", "deep learning", "nlp", "computer vision",
            "data science", "data engineering", "data analysis",
            "ci/cd", "jenkins", "github actions", "gitlab ci",
            "agile", "scrum", "kanban", "jira", "confluence",
            "tableau", "power bi", "excel", "spss", "sas",
            "financial modeling", "risk management", "credit risk", "market risk",
            "basel", "regulatory", "compliance", "audit",
            "project management", "stakeholder management", "leadership",
            "communication", "presentation", "problem solving",
            "api", "rest", "graphql", "microservices", "distributed systems",
            "linux", "git", "bash", "networking", "security",
            "tensorflow", "pytorch", "spark", "hadoop", "kafka",
            "figma", "sketch", "adobe", "photoshop", "illustrator",
        }

        text_lower = text.lower()
        found = []

        for skill in known_skills:
            if re.search(r'\b' + re.escape(skill) + r'\b', text_lower):
                found.append(skill)

        # Also extract "X years of Y" patterns
        exp_pattern = re.findall(
            r'(?:experience (?:with|in)|proficiency in|knowledge of|familiar with)\s+([A-Za-z\s/+#.]+)',
            text, re.IGNORECASE
        )
        for match in exp_pattern:
            skills = [s.strip() for s in re.split(r'[,;and]', match) if s.strip()]
            for s in skills:
                if 2 < len(s) < 30:
                    found.append(s.lower())

        return list(set(found))

    def get_skill_gaps(self, limit: int = 20) -> list[dict]:
        """Get skills most demanded by jobs but missing from CV."""
        return self.state.get_skill_gaps(limit)

    def get_top_demanded_skills(self, limit: int = 30) -> list[dict]:
        """Get most frequently demanded skills across all jobs."""
        return self.state.get_top_skills(limit)

    def get_match_rate(self) -> float:
        """Overall skill match rate: what % of demanded skills does the candidate have?"""
        rows = self.state.conn.execute("""
            SELECT SUM(times_matched) as matched, SUM(times_seen) as total
            FROM skill_frequency
        """).fetchone()
        if not rows or not rows["total"]:
            return 0
        return round(rows["matched"] / rows["total"] * 100, 1)

    def generate_report(self) -> str:
        """Generate skill gap analysis report."""
        gaps = self.get_skill_gaps(15)
        top = self.get_top_demanded_skills(20)
        match_rate = self.get_match_rate()

        lines = [
            "Skill Gap Analysis Report",
            "=" * 40,
            f"Overall skill match rate: {match_rate}%",
            f"Your skills: {len(self.candidate_skills)}",
            "",
            "Top Demanded Skills (across all jobs seen):",
        ]

        for s in top[:15]:
            match_icon = "Y" if s["times_matched"] > 0 else "N"
            lines.append(f"  [{match_icon}] {s['skill']}: seen {s['times_seen']}x")

        if gaps:
            lines.extend(["", "Biggest Skill Gaps (high demand, you don't have):"])
            for g in gaps[:10]:
                lines.append(f"  {g['skill']}: requested {g['times_seen']}x, "
                            f"gap {g['gap_pct']}%")

            lines.extend(["", "Upskilling Recommendation:"])
            top_gaps = [g["skill"] for g in gaps[:5]]
            lines.append(f"  Focus on: {', '.join(top_gaps)}")

        return "\n".join(lines)
