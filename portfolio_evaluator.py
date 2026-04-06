"""
Portfolio Project Scoring.

Evaluates whether building a specific project would help the job search.
Scores across 6 weighted dimensions to produce BUILD / SKIP / PIVOT verdicts.
Generates 2-week implementation plans for BUILD projects.
"""

import json
import logging
from datetime import datetime

log = logging.getLogger("lla.portfolio_evaluator")

# Scoring dimensions with weights and descriptions
DIMENSIONS = {
    "signal_score": {
        "weight": 0.25,
        "label": "Signal for Target Roles",
        "five": "Directly demonstrates skills from target JDs",
        "one": "Completely unrelated to target roles",
    },
    "uniqueness": {
        "weight": 0.20,
        "label": "Uniqueness",
        "five": "Nobody has built this; novel combination of tech",
        "one": "Every bootcamp grad has this on their portfolio",
    },
    "demoability": {
        "weight": 0.20,
        "label": "Demo-ability",
        "five": "Live interactive demo in under 2 minutes",
        "one": "Code-only, nothing visual or interactive",
    },
    "metrics_potential": {
        "weight": 0.15,
        "label": "Metrics Potential",
        "five": "Clear measurable outcomes (latency, throughput, accuracy)",
        "one": "No meaningful metrics possible",
    },
    "time_to_mvp": {
        "weight": 0.10,
        "label": "Time to MVP",
        "five": "Shippable MVP in 1 week or less",
        "one": "3+ months to anything presentable",
    },
    "star_potential": {
        "weight": 0.10,
        "label": "STAR Story Potential",
        "five": "Rich tradeoffs, decisions, and narrative for interviews",
        "one": "Pure implementation, no interesting decisions to discuss",
    },
}


class PortfolioEvaluator:
    """Evaluate portfolio project ideas for job-search impact."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        pe_cfg = cfg.get("portfolio_evaluator", {})
        self.enabled = pe_cfg.get("enabled", False)

    # ── Public API ───────────────────────────────────────────────

    def evaluate(self, name: str, description: str, target_roles: list = None) -> dict:
        """
        Score a portfolio project across all 6 dimensions.

        Returns dict with per-dimension scores, weighted total, verdict, and plan.
        """
        if not self.enabled:
            log.debug("Portfolio evaluator disabled, skipping")
            return {}

        if not self.ai or not self.ai.enabled:
            log.warning("AI unavailable, cannot evaluate portfolio project")
            return {}

        log.info(f"Evaluating portfolio project: {name}")
        target_roles = target_roles or self._default_target_roles()

        # Score each dimension via AI
        scores = {}
        for dim_key, dim_info in DIMENSIONS.items():
            score = self._score_dimension(dim_key, name, description, target_roles)
            scores[dim_key] = score
            log.info(f"  {dim_info['label']}: {score}/5")

        total = self._compute_total(scores)
        log.info(f"  Weighted total: {total:.2f}/5.0")

        verdict, reasoning = self._generate_verdict(total, scores, name, description)
        log.info(f"  Verdict: {verdict}")

        plan = ""
        if verdict == "BUILD":
            plan = self._generate_plan(name, description)

        # Persist to database
        try:
            self.state.conn.execute(
                """INSERT INTO portfolio_projects
                   (name, description, signal_score, uniqueness, demoability,
                    metrics_potential, time_to_mvp, star_potential, total_score,
                    verdict, plan)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, description,
                 scores.get("signal_score", 0),
                 scores.get("uniqueness", 0),
                 scores.get("demoability", 0),
                 scores.get("metrics_potential", 0),
                 scores.get("time_to_mvp", 0),
                 scores.get("star_potential", 0),
                 total, verdict, plan),
            )
            self.state.conn.commit()
            log.info(f"  Saved evaluation for '{name}'")
        except Exception as e:
            log.error(f"Failed to save portfolio evaluation: {e}")

        return {
            "name": name,
            "description": description,
            "scores": scores,
            "total": round(total, 2),
            "verdict": verdict,
            "reasoning": reasoning,
            "plan": plan,
        }

    def get_all_evaluations(self) -> list[dict]:
        """List all previously evaluated portfolio projects."""
        try:
            rows = self.state.conn.execute(
                """SELECT name, description, signal_score, uniqueness, demoability,
                          metrics_potential, time_to_mvp, star_potential,
                          total_score, verdict, plan, evaluated_at
                   FROM portfolio_projects ORDER BY total_score DESC"""
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error(f"Failed to fetch evaluations: {e}")
            return []

    def compare_projects(self, names: list[str]) -> str:
        """
        Side-by-side comparison table for the given project names.

        Returns a formatted text table.
        """
        if not names:
            return "No project names provided."

        placeholders = ",".join("?" for _ in names)
        try:
            rows = self.state.conn.execute(
                f"""SELECT name, signal_score, uniqueness, demoability,
                           metrics_potential, time_to_mvp, star_potential,
                           total_score, verdict
                    FROM portfolio_projects
                    WHERE name IN ({placeholders})
                    ORDER BY total_score DESC""",
                names,
            ).fetchall()
        except Exception as e:
            log.error(f"Failed to compare projects: {e}")
            return "Error fetching project data."

        if not rows:
            return "No evaluated projects found with those names."

        # Build comparison table
        header = f"{'Project':<30} {'Signal':>6} {'Unique':>6} {'Demo':>6} {'Metric':>6} {'MVP':>6} {'STAR':>6} {'Total':>6} {'Verdict':>8}"
        sep = "-" * len(header)
        lines = [header, sep]
        for r in rows:
            lines.append(
                f"{r['name']:<30} {r['signal_score']:>6.1f} {r['uniqueness']:>6.1f} "
                f"{r['demoability']:>6.1f} {r['metrics_potential']:>6.1f} "
                f"{r['time_to_mvp']:>6.1f} {r['star_potential']:>6.1f} "
                f"{r['total_score']:>6.2f} {r['verdict']:>8}"
            )
        return "\n".join(lines)

    def suggest_projects(self, target_roles: list[str] = None) -> list[dict]:
        """
        AI suggests 3-5 portfolio project ideas based on target roles
        and identified skill gaps.
        """
        if not self.enabled:
            log.debug("Portfolio evaluator disabled, skipping suggestions")
            return []

        if not self.ai or not self.ai.enabled:
            log.warning("AI unavailable, cannot suggest projects")
            return []

        target_roles = target_roles or self._default_target_roles()

        # Gather skill gaps from the database if available
        skill_gaps = self._get_skill_gaps()

        system_prompt = (
            "You are a senior engineering career coach. Suggest portfolio projects "
            "that would maximise interview signal for the candidate's target roles. "
            "Each suggestion must be realistic to build in 1-2 weeks."
        )

        user_prompt = (
            f"Target roles: {', '.join(target_roles)}\n"
            f"Skill gaps to address: {', '.join(skill_gaps) if skill_gaps else 'unknown'}\n\n"
            "Suggest 3-5 portfolio projects. For each, provide:\n"
            "1. name: short project name\n"
            "2. description: 2-3 sentence description\n"
            "3. skills_demonstrated: comma-separated list\n"
            "4. estimated_days: number of days to MVP\n\n"
            "Return ONLY valid JSON: a list of objects with those 4 keys."
        )

        try:
            raw = self.ai._call_llm(system_prompt, user_prompt)
            suggestions = self._parse_json_list(raw)
            log.info(f"Generated {len(suggestions)} project suggestions")
            return suggestions
        except Exception as e:
            log.error(f"Failed to generate project suggestions: {e}")
            return []

    # ── Internal Methods ─────────────────────────────────────────

    def _score_dimension(self, dimension: str, name: str, description: str,
                         target_roles: list) -> float:
        """Use AI to score a single dimension 1-5."""
        dim = DIMENSIONS[dimension]

        system_prompt = (
            "You are a portfolio project evaluator for software engineers. "
            "Score the given project on a specific dimension from 1 to 5. "
            "Return ONLY a JSON object with keys 'score' (integer 1-5) and "
            "'reasoning' (one sentence)."
        )

        user_prompt = (
            f"Project: {name}\n"
            f"Description: {description}\n"
            f"Target roles: {', '.join(target_roles)}\n\n"
            f"Dimension: {dim['label']}\n"
            f"  5 = {dim['five']}\n"
            f"  1 = {dim['one']}\n\n"
            f"Score this project on '{dim['label']}' (1-5)."
        )

        try:
            raw = self.ai._call_llm(system_prompt, user_prompt)
            data = self._parse_json(raw)
            score = float(data.get("score", 3))
            return max(1.0, min(5.0, score))
        except Exception as e:
            log.warning(f"Failed to score {dimension}, defaulting to 3: {e}")
            return 3.0

    def _compute_total(self, scores: dict) -> float:
        """Compute weighted total from dimension scores."""
        total = 0.0
        for dim_key, dim_info in DIMENSIONS.items():
            total += scores.get(dim_key, 3.0) * dim_info["weight"]
        return total

    def _generate_verdict(self, total: float, scores: dict,
                          name: str, description: str) -> tuple[str, str]:
        """
        Determine BUILD / SKIP / PIVOT verdict based on total score.

        Returns (verdict, reasoning).
        """
        if total > 3.5:
            verdict = "BUILD"
        elif total < 2.5:
            verdict = "SKIP"
        else:
            verdict = "PIVOT"

        # Identify strongest and weakest dimensions
        sorted_dims = sorted(scores.items(), key=lambda x: x[1])
        weakest = sorted_dims[0]
        strongest = sorted_dims[-1]
        weak_label = DIMENSIONS[weakest[0]]["label"]
        strong_label = DIMENSIONS[strongest[0]]["label"]

        reasoning = (
            f"Total score {total:.2f}/5.0 => {verdict}. "
            f"Strongest: {strong_label} ({strongest[1]:.1f}). "
            f"Weakest: {weak_label} ({weakest[1]:.1f})."
        )

        if verdict == "PIVOT":
            reasoning += (
                f" Consider pivoting to improve {weak_label} "
                f"while preserving {strong_label}."
            )

        return verdict, reasoning

    def _generate_plan(self, name: str, description: str) -> str:
        """Generate a 2-week implementation plan: week 1 MVP, week 2 polish."""
        system_prompt = (
            "You are a senior engineering mentor. Create a concise 2-week plan "
            "for a portfolio project. Week 1: build MVP. Week 2: polish, add "
            "metrics, write README, prepare interview talking points. "
            "Return a structured plan with daily goals."
        )

        user_prompt = (
            f"Project: {name}\n"
            f"Description: {description}\n\n"
            "Create a 2-week plan. Week 1 focuses on a working MVP. "
            "Week 2 focuses on polish, metrics dashboard, documentation, "
            "and an interview preparation pack (STAR stories, demo script). "
            "Be specific and actionable."
        )

        try:
            return self.ai._call_llm(system_prompt, user_prompt)
        except Exception as e:
            log.error(f"Failed to generate plan: {e}")
            return ""

    def _default_target_roles(self) -> list[str]:
        """Extract target roles from config."""
        roles = self.cfg.get("portfolio_evaluator", {}).get("target_roles", [])
        if not roles:
            search_terms = self.cfg.get("search", {}).get("terms", [])
            if search_terms:
                roles = search_terms[:5]
        return roles or ["Software Engineer"]

    def _get_skill_gaps(self) -> list[str]:
        """Pull top skill gaps from skill_frequency table."""
        try:
            rows = self.state.conn.execute(
                """SELECT skill FROM skill_frequency
                   WHERE times_matched = 0
                   ORDER BY times_seen DESC LIMIT 10"""
            ).fetchall()
            return [r["skill"] for r in rows]
        except Exception:
            return []

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
            log.warning(f"Failed to parse JSON from AI response: {text[:100]}")
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
            if isinstance(result, list):
                return result
            return []
        except json.JSONDecodeError:
            log.warning(f"Failed to parse JSON list: {text[:100]}")
            return []
