"""
LinkedIn Profile Optimizer.

Analyzes top keywords across all target JDs. Compares against your
LinkedIn headline, summary, and skills. Suggests specific profile
changes to improve search visibility and recruiter matching.
"""

import logging
import re
from collections import Counter

log = logging.getLogger("lla.profile_opt")


class ProfileOptimizer:
    """Analyze JD keywords and suggest LinkedIn profile improvements."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        po_cfg = cfg.get("profile_optimizer", {})
        self.enabled = po_cfg.get("enabled", False)
        self.auto_suggest = po_cfg.get("auto_suggest", True)
        self.min_jobs_for_analysis = po_cfg.get("min_jobs_for_analysis", 10)

        # Load current profile text from config
        self.profile_text = self._load_profile_text()

    def _load_profile_text(self) -> str:
        """Assemble current profile text from config."""
        parts = []
        personal = self.cfg.get("personal", {})
        qa = self.cfg.get("question_answers", {})

        if personal.get("linkedin_headline"):
            parts.append(f"HEADLINE: {personal['linkedin_headline']}")
        if qa.get("summary"):
            parts.append(f"SUMMARY: {qa['summary']}")
        if qa.get("about yourself"):
            parts.append(f"ABOUT: {qa['about yourself']}")
        if qa.get("skills"):
            parts.append(f"SKILLS: {qa['skills']}")
        if qa.get("technical skills"):
            parts.append(f"TECHNICAL SKILLS: {qa['technical skills']}")

        cv = self.cfg.get("ai", {}).get("cv_text", "")
        if cv:
            parts.append(f"CV: {cv[:2000]}")

        return "\n".join(parts)

    def analyze_keyword_gaps(self) -> dict:
        """
        Compare most-demanded keywords in JDs against your profile.

        Returns:
            {
                missing_keywords: [{keyword, frequency, in_jd_pct}],
                present_keywords: [{keyword, frequency}],
                profile_coverage: float (0-100%),
                suggestions: [str]
            }
        """
        if not self.enabled:
            return {}

        # Get top skills from skill_frequency table
        top_skills = self.state.get_top_skills(50)
        if len(top_skills) < self.min_jobs_for_analysis:
            return {"status": "not_enough_data",
                    "message": f"Need at least {self.min_jobs_for_analysis} jobs analyzed"}

        total_jobs = self.state.conn.execute(
            "SELECT COUNT(*) as c FROM applied_jobs"
        ).fetchone()["c"] + self.state.conn.execute(
            "SELECT COUNT(*) as c FROM skipped_jobs"
        ).fetchone()["c"]

        profile_lower = self.profile_text.lower()

        missing = []
        present = []

        for skill_data in top_skills:
            skill = skill_data["skill"]
            freq = skill_data["times_seen"]
            in_jd_pct = round(freq / max(total_jobs, 1) * 100, 1)

            # Check if skill is in profile
            skill_in_profile = (
                skill.lower() in profile_lower or
                any(s in profile_lower for s in skill.lower().split("/"))
            )

            entry = {"keyword": skill, "frequency": freq, "in_jd_pct": in_jd_pct}

            if skill_in_profile:
                present.append(entry)
            else:
                missing.append(entry)

        # Sort by frequency
        missing.sort(key=lambda x: x["frequency"], reverse=True)
        present.sort(key=lambda x: x["frequency"], reverse=True)

        total_keywords = len(missing) + len(present)
        coverage = round(len(present) / max(total_keywords, 1) * 100, 1)

        return {
            "missing_keywords": missing[:20],
            "present_keywords": present[:20],
            "profile_coverage": coverage,
            "total_keywords_tracked": total_keywords,
        }

    def generate_suggestions(self) -> list[dict]:
        """Generate specific profile improvement suggestions."""
        if not self.enabled:
            return []

        analysis = self.analyze_keyword_gaps()
        if analysis.get("status") == "not_enough_data":
            return []

        missing = analysis.get("missing_keywords", [])
        coverage = analysis.get("profile_coverage", 0)
        suggestions = []

        # Headline suggestions
        high_freq_missing = [k for k in missing if k["in_jd_pct"] > 20]
        if high_freq_missing:
            top_keywords = [k["keyword"] for k in high_freq_missing[:5]]
            suggestions.append({
                "section": "headline",
                "suggestion": f"Add these high-demand keywords to your headline: {', '.join(top_keywords)}",
                "keywords": top_keywords,
                "impact": "high",
            })

        # Skills section suggestions
        skills_missing = [k for k in missing if k["in_jd_pct"] > 10]
        if skills_missing:
            skills_to_add = [k["keyword"] for k in skills_missing[:10]]
            suggestions.append({
                "section": "skills",
                "suggestion": f"Add these skills to your LinkedIn Skills section: {', '.join(skills_to_add)}",
                "keywords": skills_to_add,
                "impact": "high",
            })

        # Summary/About suggestions
        if coverage < 60:
            top_missing = [k["keyword"] for k in missing[:8]]
            suggestions.append({
                "section": "about",
                "suggestion": f"Your profile covers only {coverage}% of demanded keywords. "
                             f"Weave these into your About section: {', '.join(top_missing)}",
                "keywords": top_missing,
                "impact": "medium",
            })

        # AI-powered suggestion
        if self.ai and self.ai.enabled and missing:
            ai_suggestion = self._generate_ai_suggestion(missing, analysis)
            if ai_suggestion:
                suggestions.append({
                    "section": "overall",
                    "suggestion": ai_suggestion,
                    "keywords": [],
                    "impact": "high",
                })

        # Save suggestions
        for s in suggestions:
            self.state.save_profile_suggestion(
                section=s["section"],
                suggestion=s["suggestion"],
                keyword=", ".join(s.get("keywords", []))[:200],
                frequency=len(s.get("keywords", [])),
            )

        return suggestions

    def _generate_ai_suggestion(self, missing: list[dict], analysis: dict) -> str:
        """Use AI to generate specific profile rewrite suggestions."""
        top_missing = [k["keyword"] for k in missing[:10]]
        coverage = analysis.get("profile_coverage", 0)

        system = """You are a LinkedIn profile optimization expert.
Given a candidate's current profile and the most in-demand keywords they're missing,
provide ONE specific, actionable suggestion. Be concrete — give exact text they could use.
Maximum 3 sentences."""

        user = f"""Current profile:
{self.profile_text[:1500]}

Missing high-demand keywords: {', '.join(top_missing)}
Current keyword coverage: {coverage}%

Give ONE specific suggestion to improve their profile visibility:"""

        try:
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 300
            result = self.ai._call_llm(system, user)
            self.ai.max_tokens = old_max
            return result or ""
        except Exception:
            return ""

    def generate_optimized_headline(self) -> str:
        """Generate an optimized LinkedIn headline using top keywords."""
        if not self.ai or not self.ai.enabled:
            return ""

        analysis = self.analyze_keyword_gaps()
        present = [k["keyword"] for k in analysis.get("present_keywords", [])[:5]]
        missing = [k["keyword"] for k in analysis.get("missing_keywords", [])[:5]]

        current = self.cfg.get("personal", {}).get("linkedin_headline", "")

        system = """Rewrite a LinkedIn headline to maximize keyword visibility.
Include the most in-demand keywords naturally. Keep under 120 characters.
Format: "Role | Key Skills | Differentiator"
Output ONLY the headline text, nothing else."""

        user = f"""Current headline: {current}
Top matching keywords: {', '.join(present)}
High-demand missing keywords: {', '.join(missing)}

Optimized headline:"""

        try:
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 100
            result = self.ai._call_llm(system, user)
            self.ai.max_tokens = old_max
            return (result or "")[:120]
        except Exception:
            return ""

    def generate_report(self) -> str:
        """Generate full profile optimization report."""
        analysis = self.analyze_keyword_gaps()

        if analysis.get("status") == "not_enough_data":
            return analysis["message"]

        lines = [
            "LinkedIn Profile Optimization Report",
            "=" * 40,
            f"Keyword coverage: {analysis.get('profile_coverage', 0)}%",
            f"Keywords tracked: {analysis.get('total_keywords_tracked', 0)}",
            "",
            "Keywords ON your profile (top 10):",
        ]

        for k in analysis.get("present_keywords", [])[:10]:
            lines.append(f"  [Y] {k['keyword']}: in {k['in_jd_pct']}% of JDs")

        lines.append("\nKeywords MISSING from your profile (top 10):")
        for k in analysis.get("missing_keywords", [])[:10]:
            lines.append(f"  [N] {k['keyword']}: in {k['in_jd_pct']}% of JDs")

        suggestions = self.generate_suggestions()
        if suggestions:
            lines.append("\nActionable Suggestions:")
            for i, s in enumerate(suggestions, 1):
                lines.append(f"  {i}. [{s['section'].upper()}] {s['suggestion']}")

        optimized = self.generate_optimized_headline()
        if optimized:
            lines.extend(["", f"Suggested headline: {optimized}"])

        return "\n".join(lines)
