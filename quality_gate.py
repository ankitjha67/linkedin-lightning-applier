"""
Application Quality Scoring Gate.

Scores application quality BEFORE submitting. Evaluates resume match,
cover letter quality, form completeness, and overall fit. Can block
weak applications below a configurable threshold or warn with
improvement suggestions.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

log = logging.getLogger("lla.quality_gate")


class QualityGate:
    """Score and gate application quality before submission."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        qg_cfg = cfg.get("quality_gate", {})
        self.enabled = qg_cfg.get("enabled", False)
        self.min_quality_score = qg_cfg.get("min_quality_score", 50)
        self.block_below_threshold = qg_cfg.get("block_below_threshold", False)
        self.show_suggestions = qg_cfg.get("show_suggestions", True)
        self.weights = qg_cfg.get("weights", {
            "resume_match": 0.40,
            "cover_letter": 0.25,
            "match_score": 0.25,
            "form_completeness": 0.10,
        })

    # ------------------------------------------------------------------
    # Public: score_application
    # ------------------------------------------------------------------

    def score_application(self, job_id: str, title: str, company: str,
                          description: str, resume_text: str = "",
                          cover_letter: str = "", match_score: float = 0,
                          answers_dict: dict = None) -> dict:
        """
        Comprehensive quality assessment of an application.

        Scores resume match, cover letter, form completeness, and
        computes a weighted overall quality score. Persists to
        quality_scores table.

        Returns dict with all component scores, overall, issues, and
        whether the application should proceed.
        """
        if not self.enabled:
            log.debug("QualityGate disabled, auto-approving")
            return {"overall_quality": 100, "proceed": True, "issues": []}

        log.info(f"Scoring application: {title} @ {company} (job_id={job_id})")

        # Check for cached score
        cached = self._get_cached_score(job_id)
        if cached:
            log.info(f"Returning cached quality score for {job_id}")
            return cached

        # Compute individual scores
        resume_match_pct = self._score_resume_match(resume_text, description)
        cl_score = self._score_cover_letter(cover_letter, title, company)
        form_score = self._score_form_completeness(answers_dict or {})

        # Detect issues
        issues = self._detect_issues(resume_text, cover_letter, description)

        # Compute weighted overall
        scores = {
            "resume_match": resume_match_pct,
            "cover_letter": cl_score,
            "match_score": match_score,
            "form_completeness": form_score,
        }
        overall = self._compute_overall(scores)

        # Build result
        proceed = self.should_proceed(overall)
        result = {
            "job_id": job_id,
            "title": title,
            "company": company,
            "resume_match_pct": round(resume_match_pct, 1),
            "cover_letter_score": round(cl_score, 1),
            "form_completeness": round(form_score, 1),
            "match_score": round(match_score, 1),
            "overall_quality": round(overall, 1),
            "issues": issues,
            "proceed": proceed,
            "blocked": self.block_below_threshold and not proceed,
        }

        # Add suggestions if enabled
        if self.show_suggestions and not proceed:
            result["suggestions"] = self._generate_suggestions(
                resume_match_pct, cl_score, form_score, match_score, issues
            )

        # Persist
        try:
            self.state.conn.execute(
                """INSERT INTO quality_scores
                   (job_id, resume_match_pct, cover_letter_score,
                    form_completeness, overall_quality, issues)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       resume_match_pct = excluded.resume_match_pct,
                       cover_letter_score = excluded.cover_letter_score,
                       form_completeness = excluded.form_completeness,
                       overall_quality = excluded.overall_quality,
                       issues = excluded.issues,
                       scored_at = datetime('now','localtime')""",
                (job_id, round(resume_match_pct, 1), round(cl_score, 1),
                 round(form_score, 1), round(overall, 1), json.dumps(issues)),
            )
            self.state.conn.commit()
        except Exception as e:
            log.error(f"Error saving quality score: {e}")

        log.info(
            f"Quality score: {overall:.0f}/100 "
            f"(resume={resume_match_pct:.0f}, cl={cl_score:.0f}, "
            f"form={form_score:.0f}, match={match_score:.0f}) "
            f"proceed={proceed}"
        )
        return result

    # ------------------------------------------------------------------
    # Internal: _score_resume_match
    # ------------------------------------------------------------------

    def _score_resume_match(self, resume_text: str,
                            description: str) -> float:
        """
        Compute keyword overlap percentage between resume and job description.

        Extracts significant words (3+ chars) from the JD, then checks
        what fraction appear in the resume. Returns 0-100.
        """
        if not resume_text or not description:
            return 0.0

        # Normalize and tokenize
        desc_words = set(self._extract_keywords(description.lower()))
        resume_words = set(self._extract_keywords(resume_text.lower()))

        if not desc_words:
            return 50.0  # No keywords to match against

        # Count how many JD keywords appear in the resume
        matched = desc_words & resume_words
        match_pct = (len(matched) / len(desc_words)) * 100

        # Cap at 100
        return min(match_pct, 100.0)

    def _extract_keywords(self, text: str) -> list:
        """
        Extract significant keywords from text.

        Filters out common stop words and short tokens.
        Returns list of unique lowercase words.
        """
        stop_words = {
            "the", "and", "for", "are", "but", "not", "you", "all",
            "can", "had", "her", "was", "one", "our", "out", "has",
            "have", "been", "will", "with", "this", "that", "from",
            "they", "were", "said", "each", "which", "their", "about",
            "would", "make", "like", "just", "over", "such", "take",
            "than", "them", "very", "some", "could", "what", "there",
            "when", "your", "into", "also", "more", "other", "should",
            "work", "able", "must", "well", "role", "join", "team",
            "experience", "working", "looking", "opportunity", "company",
            "including", "within", "using", "based", "please", "apply",
        }

        words = re.findall(r"[a-z][a-z0-9+#.-]{2,}", text)
        return [w for w in words if w not in stop_words]

    # ------------------------------------------------------------------
    # Internal: _score_cover_letter
    # ------------------------------------------------------------------

    def _score_cover_letter(self, cover_letter: str, title: str,
                            company: str) -> float:
        """
        Score the cover letter on specificity, relevance, and authenticity.

        Uses AI if available, otherwise falls back to heuristic checks.
        Returns 0-100.
        """
        if not cover_letter:
            return 0.0

        # Heuristic baseline
        score = 30.0  # Base score for having a cover letter

        letter_lower = cover_letter.lower()

        # Specificity: mentions company name?
        if company and company.lower() in letter_lower:
            score += 15.0
        else:
            score -= 10.0

        # Specificity: mentions role title?
        if title and title.lower() in letter_lower:
            score += 10.0

        # Length check: too short or too long
        word_count = len(cover_letter.split())
        if word_count < 50:
            score -= 15.0  # Way too short
        elif word_count < 100:
            score -= 5.0   # A bit short
        elif word_count > 600:
            score -= 5.0   # A bit long

        # Generic phrase detection
        generic_phrases = [
            "i am writing to express my interest",
            "i am a hard worker",
            "i am a team player",
            "please find attached",
            "to whom it may concern",
            "dear sir or madam",
            "i believe i would be a great fit",
            "i am confident that",
        ]
        generic_count = sum(1 for p in generic_phrases if p in letter_lower)
        score -= generic_count * 5.0

        # Positive signals
        positive_signals = [
            "specifically", "because", "example", "achieved",
            "built", "led", "reduced", "increased", "delivered",
            "implemented", "designed", "collaborated",
        ]
        signal_count = sum(1 for s in positive_signals if s in letter_lower)
        score += min(signal_count * 3.0, 20.0)

        # AI scoring if available
        if self.ai and getattr(self.ai, "enabled", False):
            ai_score = self._ai_score_cover_letter(cover_letter, title, company)
            if ai_score is not None:
                # Blend heuristic and AI: 40% heuristic, 60% AI
                score = (score * 0.4) + (ai_score * 0.6)

        return max(0.0, min(100.0, score))

    def _ai_score_cover_letter(self, cover_letter: str, title: str,
                                company: str) -> Optional[float]:
        """Use AI to score a cover letter. Returns 0-100 or None on failure."""
        system_prompt = (
            "You are an expert hiring manager reviewing cover letters. "
            "Score the following cover letter from 0 to 100 based on:\n"
            "- Specificity (does it mention the company and role by name?)\n"
            "- Relevance (does it address the job requirements?)\n"
            "- Authenticity (does it sound genuine and human?)\n"
            "- Impact (does it highlight concrete achievements?)\n\n"
            "Respond with ONLY a number between 0 and 100, nothing else."
        )
        user_prompt = (
            f"Role: {title}\nCompany: {company}\n\n"
            f"Cover Letter:\n{cover_letter[:2000]}"
        )

        try:
            response = self.ai._call_llm(system_prompt, user_prompt)
            if response:
                # Extract number from response
                numbers = re.findall(r"\d+", response.strip())
                if numbers:
                    val = float(numbers[0])
                    return max(0.0, min(100.0, val))
        except Exception as e:
            log.warning(f"AI cover letter scoring failed: {e}")

        return None

    # ------------------------------------------------------------------
    # Internal: _score_form_completeness
    # ------------------------------------------------------------------

    def _score_form_completeness(self, answers_dict: dict) -> float:
        """
        Compute percentage of form fields with non-empty, non-default answers.

        Returns 0-100.
        """
        if not answers_dict:
            return 0.0

        total = len(answers_dict)
        if total == 0:
            return 0.0

        default_values = {"", "n/a", "na", "none", "-", "select", "choose", "0"}

        filled = 0
        for key, value in answers_dict.items():
            val_str = str(value).strip().lower()
            if val_str and val_str not in default_values:
                filled += 1

        return (filled / total) * 100

    # ------------------------------------------------------------------
    # Internal: _detect_issues
    # ------------------------------------------------------------------

    def _detect_issues(self, resume_text: str, cover_letter: str,
                       description: str) -> list:
        """
        Detect specific problems with the application.

        Returns list of issue strings, e.g.:
          "Resume doesn't mention Python (required)"
          "Cover letter is generic"
          "Salary expectation missing"
        """
        issues = []

        # Check for required skills not in resume
        if description and resume_text:
            required_skills = self._extract_required_skills(description)
            resume_lower = resume_text.lower()
            for skill in required_skills:
                if skill.lower() not in resume_lower:
                    issues.append(
                        f"Resume doesn't mention '{skill}' (appears required)"
                    )

        # Cover letter checks
        if not cover_letter:
            issues.append("No cover letter provided")
        elif len(cover_letter.split()) < 50:
            issues.append("Cover letter is too short (under 50 words)")
        else:
            cl_lower = cover_letter.lower()
            generic_openers = [
                "to whom it may concern",
                "dear sir or madam",
                "i am writing to express",
            ]
            for opener in generic_openers:
                if opener in cl_lower:
                    issues.append(
                        f"Cover letter uses generic phrase: '{opener}'"
                    )
                    break

        # Resume checks
        if not resume_text:
            issues.append("No resume text available for matching")
        elif len(resume_text.split()) < 100:
            issues.append("Resume text is unusually short")

        return issues

    def _extract_required_skills(self, description: str) -> list:
        """
        Extract likely required skills from a job description.

        Looks for patterns like 'required: X, Y, Z' or 'must have X'
        and common tech keywords.
        """
        skills = []

        # Common tech skills to look for
        tech_skills = [
            "python", "java", "javascript", "typescript", "react",
            "node.js", "aws", "azure", "gcp", "docker", "kubernetes",
            "sql", "nosql", "mongodb", "postgresql", "redis",
            "machine learning", "deep learning", "tensorflow", "pytorch",
            "c++", "c#", "go", "rust", "scala", "kotlin", "swift",
            "linux", "git", "ci/cd", "terraform", "ansible",
            "rest api", "graphql", "microservices", "agile", "scrum",
        ]

        desc_lower = description.lower()
        for skill in tech_skills:
            if skill in desc_lower:
                skills.append(skill)

        # Look for "required" section patterns
        required_match = re.search(
            r"(?:required|must[- ]have|essential)[:\s]*(.*?)(?:\n\n|$)",
            desc_lower,
            re.DOTALL,
        )
        if required_match:
            section = required_match.group(1)
            # Extract bullet points
            bullets = re.findall(r"[-*]\s*(.+)", section)
            for bullet in bullets[:10]:
                cleaned = bullet.strip().rstrip(".")
                if 3 < len(cleaned) < 60:
                    skills.append(cleaned)

        return skills[:15]  # Cap at 15 to avoid noise

    # ------------------------------------------------------------------
    # Internal: _compute_overall
    # ------------------------------------------------------------------

    def _compute_overall(self, scores: dict) -> float:
        """
        Compute weighted composite quality score.

        Default weights: resume_match 40%, cover_letter 25%,
        match_score 25%, form_completeness 10%.
        """
        total = 0.0
        total_weight = 0.0

        for key, weight in self.weights.items():
            value = scores.get(key, 0)
            if value is None:
                value = 0
            total += value * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        return total / total_weight

    # ------------------------------------------------------------------
    # Public: should_proceed
    # ------------------------------------------------------------------

    def should_proceed(self, quality_score: float) -> bool:
        """
        Return True if the quality score is above the configured threshold.

        The threshold is configurable via min_quality_score (default 50%).
        """
        return quality_score >= self.min_quality_score

    # ------------------------------------------------------------------
    # Public: get_improvement_suggestions
    # ------------------------------------------------------------------

    def get_improvement_suggestions(self, job_id: str) -> list:
        """
        Return specific actions to improve application quality for a job.

        Reads the stored quality_scores entry and generates targeted advice.
        """
        if not self.enabled:
            return []

        try:
            row = self.state.conn.execute(
                """SELECT resume_match_pct, cover_letter_score,
                          form_completeness, overall_quality, issues
                   FROM quality_scores WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
        except Exception as e:
            log.warning(f"Error fetching quality score for {job_id}: {e}")
            return []

        if not row:
            return ["No quality data found. Score the application first."]

        suggestions = []
        resume_pct = row["resume_match_pct"] or 0
        cl_score = row["cover_letter_score"] or 0
        form_pct = row["form_completeness"] or 0

        if resume_pct < 50:
            suggestions.append(
                f"Resume keyword match is {resume_pct:.0f}%. Add more keywords "
                "from the job description to your resume — especially skills "
                "and technologies mentioned in the requirements section."
            )

        if cl_score < 40:
            suggestions.append(
                f"Cover letter scores {cl_score:.0f}%. Make it specific: "
                "mention the company by name, reference specific requirements, "
                "and include concrete achievements with metrics."
            )
        elif cl_score < 60:
            suggestions.append(
                f"Cover letter scores {cl_score:.0f}%. Add a specific example "
                "of a relevant achievement. Replace generic phrases with "
                "concrete details about why this role interests you."
            )

        if form_pct < 80:
            suggestions.append(
                f"Form completeness is {form_pct:.0f}%. Review all fields "
                "and provide thoughtful answers — empty or default answers "
                "signal low effort to recruiters."
            )

        # Parse stored issues
        issues_raw = row["issues"]
        if issues_raw:
            try:
                issues = json.loads(issues_raw)
                for issue in issues[:5]:
                    suggestions.append(f"Fix: {issue}")
            except (json.JSONDecodeError, TypeError):
                pass

        return suggestions or ["Application quality looks good!"]

    # ------------------------------------------------------------------
    # Internal: _generate_suggestions (inline, for score_application)
    # ------------------------------------------------------------------

    def _generate_suggestions(self, resume_pct: float, cl_score: float,
                               form_score: float, match_score: float,
                               issues: list) -> list:
        """Generate suggestions based on component scores."""
        suggestions = []

        if resume_pct < 40:
            suggestions.append(
                "Tailor your resume: add keywords from the job description."
            )
        if cl_score < 30:
            suggestions.append(
                "Write a specific cover letter mentioning the company and role."
            )
        if form_score < 70:
            suggestions.append(
                "Complete all form fields with thoughtful answers."
            )
        if match_score < 50:
            suggestions.append(
                "This role may not be a strong fit. Consider focusing on "
                "roles that better match your profile."
            )
        for issue in issues[:3]:
            suggestions.append(f"Address: {issue}")

        return suggestions

    # ------------------------------------------------------------------
    # Public: get_quality_distribution
    # ------------------------------------------------------------------

    def get_quality_distribution(self) -> dict:
        """
        Histogram of quality scores across all scored applications.

        Returns dict with buckets: {"0-20": N, "21-40": N, ...}
        and summary stats.
        """
        if not self.enabled:
            return {}

        try:
            rows = self.state.conn.execute(
                """SELECT overall_quality FROM quality_scores
                   ORDER BY overall_quality""",
            ).fetchall()
        except Exception as e:
            log.warning(f"Error fetching quality distribution: {e}")
            return {}

        if not rows:
            return {"total": 0, "buckets": {}}

        scores = [row["overall_quality"] for row in rows]
        buckets = {
            "0-20": 0, "21-40": 0, "41-60": 0,
            "61-80": 0, "81-100": 0,
        }

        for s in scores:
            if s <= 20:
                buckets["0-20"] += 1
            elif s <= 40:
                buckets["21-40"] += 1
            elif s <= 60:
                buckets["41-60"] += 1
            elif s <= 80:
                buckets["61-80"] += 1
            else:
                buckets["81-100"] += 1

        return {
            "total": len(scores),
            "mean": round(sum(scores) / len(scores), 1),
            "min": round(min(scores), 1),
            "max": round(max(scores), 1),
            "median": round(sorted(scores)[len(scores) // 2], 1),
            "buckets": buckets,
        }

    # ------------------------------------------------------------------
    # Public: get_quality_trends
    # ------------------------------------------------------------------

    def get_quality_trends(self) -> dict:
        """
        Track whether application quality is improving over time.

        Compares average quality of the last 10 applications vs
        the 10 before that.
        """
        if not self.enabled:
            return {}

        try:
            rows = self.state.conn.execute(
                """SELECT overall_quality, scored_at FROM quality_scores
                   ORDER BY scored_at DESC LIMIT 20""",
            ).fetchall()
        except Exception as e:
            log.warning(f"Error fetching quality trends: {e}")
            return {}

        if len(rows) < 4:
            return {"trend": "insufficient_data", "detail": "Need at least 4 scored applications."}

        scores = [row["overall_quality"] for row in rows]
        recent = scores[:len(scores) // 2]
        older = scores[len(scores) // 2:]

        avg_recent = sum(recent) / len(recent) if recent else 0
        avg_older = sum(older) / len(older) if older else 0

        if avg_older > 0:
            change_pct = ((avg_recent - avg_older) / avg_older) * 100
        else:
            change_pct = 0

        if change_pct > 10:
            trend = "improving"
        elif change_pct < -10:
            trend = "declining"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "avg_recent": round(avg_recent, 1),
            "avg_older": round(avg_older, 1),
            "change_pct": round(change_pct, 1),
            "recent_count": len(recent),
            "older_count": len(older),
            "detail": (
                f"Recent avg: {avg_recent:.0f}, Previous avg: {avg_older:.0f} "
                f"({change_pct:+.0f}% change). Trend: {trend}."
            ),
        }

    # ------------------------------------------------------------------
    # Internal: _get_cached_score
    # ------------------------------------------------------------------

    def _get_cached_score(self, job_id: str) -> Optional[dict]:
        """Retrieve a previously computed quality score."""
        try:
            row = self.state.conn.execute(
                """SELECT * FROM quality_scores WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
            if row:
                result = dict(row)
                if isinstance(result.get("issues"), str):
                    try:
                        result["issues"] = json.loads(result["issues"])
                    except (json.JSONDecodeError, TypeError):
                        result["issues"] = []
                result["proceed"] = self.should_proceed(
                    result.get("overall_quality", 0)
                )
                return result
        except Exception as e:
            log.warning(f"Error reading cached score for {job_id}: {e}")

        return None
