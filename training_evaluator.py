"""
Training Evaluator — Course/Certification ROI Scoring.

Evaluates courses and certifications across 6 dimensions to determine
whether they are worth the time investment for the job search.
Produces TAKE / SKIP / TAKE WITH TIMEBOX verdicts with implementation plans.
"""

import json
import logging
from datetime import datetime

log = logging.getLogger("lla.training_evaluator")

# The 6 assessment dimensions
ASSESSMENT_DIMENSIONS = {
    "alignment_score": {
        "label": "North Star Alignment",
        "question": "Does this move you toward your target roles?",
        "strong": "Directly teaches skills from target JDs; fills a critical gap",
        "weak": "Tangential or irrelevant to target roles",
    },
    "recruiter_signal": {
        "label": "Recruiter Signal",
        "question": "What do hiring managers think seeing this on a CV?",
        "strong": "Instant credibility boost; well-recognised by hiring managers",
        "weak": "Unknown brand; no recruiter recognition; may look like padding",
    },
    "time_effort": {
        "label": "Time & Effort",
        "question": "What is the weeks x hours/week investment?",
        "strong": "Achievable in 2-4 weeks at reasonable pace",
        "weak": "6+ months full-time commitment",
    },
    "opportunity_cost": {
        "label": "Opportunity Cost",
        "question": "What can't you do during this time?",
        "strong": "Can do alongside job search; minimal disruption",
        "weak": "Requires pausing job search entirely",
    },
    "risks": {
        "label": "Risks",
        "question": "Outdated content? Weak brand? Too basic?",
        "strong": "Current content, strong brand, appropriate difficulty",
        "weak": "Outdated, unknown provider, too basic or too advanced",
    },
    "portfolio_output": {
        "label": "Portfolio Output",
        "question": "Does it produce a demonstrable artifact?",
        "strong": "Capstone project, published paper, or portfolio piece",
        "weak": "Certificate only, no tangible output",
    },
}


class TrainingEvaluator:
    """Evaluate courses and certifications for job-search ROI."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        te_cfg = cfg.get("training_evaluator", {})
        self.enabled = te_cfg.get("enabled", False)

    # ── Public API ───────────────────────────────────────────────

    def evaluate(self, name: str, provider: str = "", description: str = "",
                 duration: str = "", cost: str = "") -> dict:
        """
        Full evaluation of a course or certification.

        Returns dict with assessments per dimension, verdict, plan, and alternatives.
        """
        if not self.enabled:
            log.debug("Training evaluator disabled, skipping")
            return {}

        if not self.ai or not self.ai.enabled:
            log.warning("AI unavailable, cannot evaluate training")
            return {}

        log.info(f"Evaluating training: {name} ({provider})")

        context = {
            "name": name,
            "provider": provider,
            "description": description,
            "duration": duration,
            "cost": cost,
        }

        # Assess each dimension
        assessments = {}
        for dim_key, dim_info in ASSESSMENT_DIMENSIONS.items():
            result = self._assess_dimension(dim_key, name, provider, description)
            assessments[dim_key] = result
            log.info(f"  {dim_info['label']}: {result.get('summary', 'N/A')}")

        # Generate verdict
        verdict, reasoning = self._generate_verdict(assessments, context)
        log.info(f"  Verdict: {verdict}")

        # Generate plan if TAKE or TAKE WITH TIMEBOX
        plan = ""
        if verdict in ("TAKE", "TAKE WITH TIMEBOX"):
            plan = self._generate_plan(name, verdict, duration, context)

        # Suggest alternatives if SKIP
        alternatives = ""
        if verdict == "SKIP":
            alternatives = self._suggest_alternatives(name, context)

        # Persist to database
        try:
            self.state.conn.execute(
                """INSERT INTO training_evaluations
                   (name, provider, alignment_score, recruiter_signal,
                    time_effort, opportunity_cost, risks, portfolio_output,
                    verdict, plan)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name, provider,
                    assessments.get("alignment_score", {}).get("score", 0),
                    assessments.get("recruiter_signal", {}).get("score", 0),
                    json.dumps(assessments.get("time_effort", {})),
                    json.dumps(assessments.get("opportunity_cost", {})),
                    json.dumps(assessments.get("risks", {})),
                    json.dumps(assessments.get("portfolio_output", {})),
                    verdict, plan,
                ),
            )
            self.state.conn.commit()
            log.info(f"  Saved evaluation for '{name}'")
        except Exception as e:
            log.error(f"Failed to save training evaluation: {e}")

        return {
            "name": name,
            "provider": provider,
            "assessments": assessments,
            "verdict": verdict,
            "reasoning": reasoning,
            "plan": plan,
            "alternatives": alternatives,
        }

    def get_all_evaluations(self) -> list[dict]:
        """List all previously evaluated trainings."""
        try:
            rows = self.state.conn.execute(
                """SELECT name, provider, alignment_score, recruiter_signal,
                          time_effort, opportunity_cost, risks, portfolio_output,
                          verdict, plan, evaluated_at
                   FROM training_evaluations ORDER BY evaluated_at DESC"""
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error(f"Failed to fetch training evaluations: {e}")
            return []

    def prioritize_training(self, gap_skills: list[str]) -> list[dict]:
        """
        Given a list of skill gaps, recommend which to train on first.

        Returns prioritized list with recommended training type for each skill.
        """
        if not self.enabled:
            log.debug("Training evaluator disabled")
            return []

        if not self.ai or not self.ai.enabled:
            log.warning("AI unavailable for training prioritization")
            return []

        if not gap_skills:
            log.info("No skill gaps provided for prioritization")
            return []

        system_prompt = (
            "You are a career coach specialising in software engineering upskilling. "
            "Given a list of skill gaps, prioritize them and recommend the best "
            "training approach for each. Consider: demand in job market, speed of "
            "acquisition, portfolio impact, and interview value."
        )

        target_roles = self._default_target_roles()

        user_prompt = (
            f"Target roles: {', '.join(target_roles)}\n"
            f"Skill gaps: {', '.join(gap_skills)}\n\n"
            "For each skill, provide:\n"
            "1. skill: the skill name\n"
            "2. priority: HIGH / MEDIUM / LOW\n"
            "3. training_type: course / certification / project / book / tutorial\n"
            "4. recommended_resource: specific resource name\n"
            "5. estimated_weeks: time to basic competency\n"
            "6. reasoning: one sentence why this priority\n\n"
            "Return ONLY valid JSON: a list of objects with those 6 keys. "
            "Order by priority (HIGH first)."
        )

        try:
            raw = self.ai._call_llm(system_prompt, user_prompt)
            priorities = self._parse_json_list(raw)
            log.info(f"Prioritized {len(priorities)} skill gaps for training")
            return priorities
        except Exception as e:
            log.error(f"Failed to prioritize training: {e}")
            return []

    # ── Internal Methods ─────────────────────────────────────────

    def _assess_dimension(self, dimension: str, name: str,
                          provider: str, description: str) -> dict:
        """
        Use AI to assess a single dimension.

        Returns dict with 'score' (1-5), 'summary', and 'detail'.
        """
        dim = ASSESSMENT_DIMENSIONS[dimension]
        target_roles = self._default_target_roles()

        system_prompt = (
            "You are evaluating a course/certification for a software engineer's "
            "job search. Assess the training on one specific dimension. "
            "Return ONLY a JSON object with keys:\n"
            "  'score' (integer 1-5),\n"
            "  'summary' (one phrase, e.g. 'Strong AWS signal'),\n"
            "  'detail' (2-3 sentences of analysis)."
        )

        user_prompt = (
            f"Training: {name}\n"
            f"Provider: {provider or 'unknown'}\n"
            f"Description: {description or 'not provided'}\n"
            f"Candidate target roles: {', '.join(target_roles)}\n\n"
            f"Dimension: {dim['label']}\n"
            f"Question: {dim['question']}\n"
            f"  Strong (5): {dim['strong']}\n"
            f"  Weak (1): {dim['weak']}\n\n"
            f"Assess this training on '{dim['label']}' (1-5)."
        )

        try:
            raw = self.ai._call_llm(system_prompt, user_prompt)
            data = self._parse_json(raw)
            data["score"] = max(1.0, min(5.0, float(data.get("score", 3))))
            return data
        except Exception as e:
            log.warning(f"Failed to assess {dimension}, defaulting: {e}")
            return {"score": 3.0, "summary": "Assessment unavailable", "detail": ""}

    def _generate_verdict(self, assessments: dict, context: dict) -> tuple[str, str]:
        """
        Determine TAKE / SKIP / TAKE WITH TIMEBOX verdict.

        Returns (verdict, reasoning).
        """
        scores = {k: v.get("score", 3.0) for k, v in assessments.items()}
        avg_score = sum(scores.values()) / len(scores) if scores else 3.0

        # Core decision factors
        alignment = scores.get("alignment_score", 3.0)
        recruiter = scores.get("recruiter_signal", 3.0)
        portfolio = scores.get("portfolio_output", {})
        portfolio_score = scores.get("portfolio_output", 3.0)

        # High alignment + recruiter signal => TAKE
        if alignment >= 4.0 and recruiter >= 3.5 and avg_score >= 3.5:
            verdict = "TAKE"
            reasoning = (
                f"Strong alignment ({alignment:.1f}/5) and recruiter signal "
                f"({recruiter:.1f}/5). Average score {avg_score:.1f}/5. "
                f"This training is worth the investment."
            )
        # Decent but with concerns => TAKE WITH TIMEBOX
        elif alignment >= 3.0 and avg_score >= 2.5:
            verdict = "TAKE WITH TIMEBOX"
            weak_dims = [
                ASSESSMENT_DIMENSIONS[k]["label"]
                for k, v in scores.items() if v < 3.0
            ]
            reasoning = (
                f"Decent alignment ({alignment:.1f}/5) but concerns in: "
                f"{', '.join(weak_dims) if weak_dims else 'minor areas'}. "
                f"Set a strict timebox and re-evaluate halfway."
            )
        else:
            verdict = "SKIP"
            reasoning = (
                f"Low alignment ({alignment:.1f}/5) or weak overall "
                f"(avg {avg_score:.1f}/5). Time is better spent on direct "
                f"job search activities or higher-signal training."
            )

        return verdict, reasoning

    def _generate_plan(self, name: str, verdict: str,
                       duration: str = "", context: dict = None) -> str:
        """Generate a 4-12 week plan with weekly deliverables."""
        system_prompt = (
            "You are a learning coach. Create a concise study plan for a "
            "software engineer taking a course/certification alongside an "
            "active job search. Include weekly deliverables and checkpoints. "
            "If the verdict is TAKE WITH TIMEBOX, emphasise the timebox and "
            "include explicit 'stop/continue' decision points."
        )

        user_prompt = (
            f"Training: {name}\n"
            f"Duration: {duration or 'unknown'}\n"
            f"Verdict: {verdict}\n\n"
            "Create a study plan (4-12 weeks depending on duration) with:\n"
            "- Weekly learning goals\n"
            "- Weekly deliverable (notes, mini-project, practice problems)\n"
            "- Checkpoint at weeks 2 and 4 to evaluate progress\n"
            "- How to integrate learnings into job search immediately\n"
            "- Final deliverable: portfolio piece or certification exam date"
        )

        try:
            return self.ai._call_llm(system_prompt, user_prompt)
        except Exception as e:
            log.error(f"Failed to generate training plan: {e}")
            return ""

    def _suggest_alternatives(self, name: str, context: dict = None) -> str:
        """Suggest better alternatives when verdict is SKIP."""
        system_prompt = (
            "You are a career coach. The candidate decided to SKIP a training. "
            "Suggest 2-3 better alternatives that would provide stronger signal "
            "for their target roles. Be specific with names and providers."
        )

        target_roles = self._default_target_roles()
        user_prompt = (
            f"Skipped training: {name}\n"
            f"Target roles: {', '.join(target_roles)}\n\n"
            "Suggest 2-3 alternative trainings/certifications that would be "
            "more impactful. For each, explain why it is better in 1-2 sentences."
        )

        try:
            return self.ai._call_llm(system_prompt, user_prompt)
        except Exception as e:
            log.error(f"Failed to suggest alternatives: {e}")
            return ""

    def _default_target_roles(self) -> list[str]:
        """Extract target roles from config."""
        roles = self.cfg.get("training_evaluator", {}).get("target_roles", [])
        if not roles:
            search_terms = self.cfg.get("search", {}).get("terms", [])
            if search_terms:
                roles = search_terms[:5]
        return roles or ["Software Engineer"]

    def _parse_json(self, raw: str) -> dict:
        """Parse JSON from AI response, handling markdown fences."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.warning(f"Failed to parse JSON: {text[:100]}")
            return {}

    def _parse_json_list(self, raw: str) -> list:
        """Parse a JSON list from AI response."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            result = json.loads(text)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            log.warning(f"Failed to parse JSON list: {text[:100]}")
            return []
