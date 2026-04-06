"""
Career Path Projection Simulator.

Models different career trajectories from current offers and roles.
Projects title progression, salary trajectory, promotion timelines,
skill acquisition, and lifestyle factors over 1-5 year horizons.
Compares paths side-by-side with AI-powered recommendations.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

log = logging.getLogger("lla.career_simulator")

# Raise estimate ranges by company type and role level
RAISE_RANGES = {
    "startup": {"junior": (0.05, 0.12), "mid": (0.08, 0.15), "senior": (0.06, 0.12)},
    "big_tech": {"junior": (0.08, 0.12), "mid": (0.08, 0.12), "senior": (0.06, 0.10)},
    "consulting": {"junior": (0.10, 0.15), "mid": (0.10, 0.15), "senior": (0.08, 0.12)},
    "finance": {"junior": (0.08, 0.15), "mid": (0.10, 0.20), "senior": (0.08, 0.18)},
    "corporate": {"junior": (0.03, 0.06), "mid": (0.04, 0.08), "senior": (0.03, 0.06)},
    "government": {"junior": (0.02, 0.04), "mid": (0.02, 0.04), "senior": (0.02, 0.03)},
    "ngo": {"junior": (0.02, 0.05), "mid": (0.03, 0.06), "senior": (0.02, 0.04)},
}

# Typical promotion timelines (years to next level)
PROMOTION_TIMELINES = {
    "startup": {"junior": 1.5, "mid": 2.0, "senior": 2.5, "lead": 3.0, "director": 4.0},
    "big_tech": {"junior": 2.0, "mid": 2.5, "senior": 3.0, "lead": 3.5, "director": 4.0},
    "consulting": {"junior": 2.0, "mid": 2.0, "senior": 2.5, "lead": 3.0, "director": 3.5},
    "finance": {"junior": 2.0, "mid": 2.5, "senior": 3.0, "lead": 3.0, "director": 4.0},
    "corporate": {"junior": 2.5, "mid": 3.0, "senior": 3.5, "lead": 4.0, "director": 5.0},
    "government": {"junior": 3.0, "mid": 3.5, "senior": 4.0, "lead": 5.0, "director": 6.0},
    "ngo": {"junior": 2.5, "mid": 3.0, "senior": 3.5, "lead": 4.0, "director": 5.0},
}

# Title progression ladder
TITLE_LADDER = ["junior", "mid", "senior", "lead", "director", "vp", "c-level"]


class CareerSimulator:
    """Model and compare career trajectories from offers and roles."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        cs_cfg = cfg.get("career_simulator", {})
        self.enabled = cs_cfg.get("enabled", False)
        self.projection_years = cs_cfg.get("projection_years", 5)
        self.include_equity = cs_cfg.get("include_equity", True)
        self.target_skills = cs_cfg.get("target_skills", [])

    # ------------------------------------------------------------------
    # Public: simulate
    # ------------------------------------------------------------------

    def simulate(self, paths: list, simulation_name: str = "",
                 current_role: str = "") -> dict:
        """
        Run projections for multiple career paths.

        Args:
            paths: list of offer dicts, each with keys like:
                   company, title, base_salary, bonus, equity,
                   location, company_type, industry
            simulation_name: optional label for this simulation
            current_role: the user's current role for context

        Returns dict with simulation_id, projections per path,
        and overall comparison.
        """
        if not self.enabled:
            log.debug("CareerSimulator disabled")
            return {}

        if not paths:
            log.warning("No paths provided for simulation")
            return {}

        log.info(
            f"Simulating {len(paths)} career paths "
            f"(name={simulation_name or 'unnamed'})"
        )

        projections = []
        for i, offer in enumerate(paths):
            label = offer.get("company", f"Path {i + 1}")
            log.info(f"  Projecting path: {label} — {offer.get('title', '?')}")

            projection = self._project_path(
                offer, years=self.projection_years
            )
            projection["path_index"] = i
            projection["label"] = label
            projections.append(projection)

        simulation_name = simulation_name or f"sim_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Persist
        simulation_id = self._save_simulation(
            simulation_name, current_role, projections
        )

        result = {
            "simulation_id": simulation_id,
            "simulation_name": simulation_name,
            "current_role": current_role,
            "paths_count": len(paths),
            "projections": projections,
        }

        log.info(f"Simulation complete: id={simulation_id}")
        return result

    # ------------------------------------------------------------------
    # Internal: _project_path
    # ------------------------------------------------------------------

    def _project_path(self, offer: dict, years: int = 5) -> dict:
        """
        Project a single career path over N years.

        Estimates title progression, salary trajectory, promotion
        timeline, total comp, skill growth, and risk factors.
        """
        company = offer.get("company", "Unknown")
        title = offer.get("title", "Unknown")
        base_salary = offer.get("base_salary", 0)
        bonus = offer.get("bonus", 0)
        equity = offer.get("equity", "")
        location = offer.get("location", "")
        company_type = offer.get("company_type", "corporate")
        industry = offer.get("industry", "")

        role_level = self._classify_role_level(title)

        # Year-by-year projection
        yearly = []
        current_salary = base_salary
        current_title = title
        current_level = role_level
        cumulative_comp = 0

        for year in range(1, years + 1):
            # Estimate raise
            raise_low, raise_high = self._estimate_annual_raise(
                company_type, current_level
            )
            avg_raise = (raise_low + raise_high) / 2
            current_salary = current_salary * (1 + avg_raise)

            # Check promotion
            promo_years = self._estimate_promotion_timeline(
                company_type, current_level
            )
            if year >= promo_years and current_level in TITLE_LADDER:
                idx = TITLE_LADDER.index(current_level)
                if idx + 1 < len(TITLE_LADDER):
                    current_level = TITLE_LADDER[idx + 1]
                    current_title = self._project_title(
                        title, current_level, company_type
                    )
                    # Promotion bump
                    current_salary *= 1.15

            # Total comp
            year_bonus = current_salary * (bonus / base_salary if base_salary else 0)
            year_equity = self._estimate_equity_value(equity, year, company_type)
            total_comp = current_salary + year_bonus + year_equity
            cumulative_comp += total_comp

            yearly.append({
                "year": year,
                "title": current_title,
                "level": current_level,
                "base_salary": round(current_salary),
                "bonus": round(year_bonus),
                "equity_value": round(year_equity),
                "total_comp": round(total_comp),
                "cumulative_comp": round(cumulative_comp),
            })

        # Skill growth assessment
        skills = self._assess_skill_growth(offer, self.target_skills)

        # Risk assessment
        risks = self._assess_risks(offer)

        # Visa timeline if applicable
        visa = self._assess_visa_timeline(offer)

        return {
            "company": company,
            "title": title,
            "starting_salary": base_salary,
            "location": location,
            "company_type": company_type,
            "industry": industry,
            "yearly_projections": yearly,
            "total_5yr_comp": round(cumulative_comp),
            "final_title": yearly[-1]["title"] if yearly else title,
            "final_salary": yearly[-1]["base_salary"] if yearly else base_salary,
            "skills_gained": skills,
            "risks": risks,
            "visa_timeline": visa,
        }

    # ------------------------------------------------------------------
    # Internal: _estimate_annual_raise
    # ------------------------------------------------------------------

    def _estimate_annual_raise(self, company_type: str,
                                role_level: str) -> tuple:
        """
        Estimate annual raise range based on company type and role level.

        Returns (low_pct, high_pct) as decimals, e.g. (0.05, 0.15).
        """
        company_type = company_type.lower().replace(" ", "_")
        ranges = RAISE_RANGES.get(company_type, RAISE_RANGES["corporate"])
        return ranges.get(role_level, ranges.get("mid", (0.04, 0.08)))

    # ------------------------------------------------------------------
    # Internal: _estimate_promotion_timeline
    # ------------------------------------------------------------------

    def _estimate_promotion_timeline(self, company_type: str,
                                      current_level: str) -> float:
        """
        Estimate years to next promotion based on company type and level.

        If AI is available, can refine the estimate with industry context.
        """
        company_type = company_type.lower().replace(" ", "_")
        timelines = PROMOTION_TIMELINES.get(
            company_type, PROMOTION_TIMELINES["corporate"]
        )
        return timelines.get(current_level, 3.0)

    # ------------------------------------------------------------------
    # Internal: _calculate_total_comp_over_time
    # ------------------------------------------------------------------

    def _calculate_total_comp_over_time(self, offer: dict,
                                         years: int = 5) -> list:
        """
        Compute total compensation for each year including base,
        bonus, and equity vesting.
        """
        projection = self._project_path(offer, years)
        return projection.get("yearly_projections", [])

    # ------------------------------------------------------------------
    # Internal: _assess_visa_timeline
    # ------------------------------------------------------------------

    def _assess_visa_timeline(self, offer: dict) -> dict:
        """
        For international moves, estimate visa processing time and impact.

        Checks offer for visa_support field and infers timeline.
        """
        visa_support = offer.get("visa_support", "")
        location = offer.get("location", "")

        if not visa_support:
            return {"required": False, "detail": "No visa information provided"}

        visa_lower = visa_support.lower()

        # Rough processing time estimates
        if "tier 2" in visa_lower or "skilled worker" in visa_lower:
            return {
                "required": True,
                "type": "Skilled Worker Visa (UK)",
                "processing_weeks": "3-8",
                "detail": "UK Skilled Worker visa typically 3-8 weeks processing.",
                "risk": "Employer must be licensed sponsor.",
            }
        elif "h1b" in visa_lower or "h-1b" in visa_lower:
            return {
                "required": True,
                "type": "H-1B (US)",
                "processing_weeks": "12-26 (lottery dependent)",
                "detail": (
                    "H-1B requires lottery selection (April). "
                    "Premium processing available for faster adjudication."
                ),
                "risk": "Lottery-based, ~30% selection rate.",
            }
        elif "no" in visa_lower or "not" in visa_lower:
            return {
                "required": True,
                "type": "Not provided",
                "processing_weeks": "N/A",
                "detail": "Company does not offer visa sponsorship.",
                "risk": "High — must have independent work authorization.",
            }
        else:
            return {
                "required": True,
                "type": visa_support,
                "processing_weeks": "varies",
                "detail": f"Visa type: {visa_support}. Timeline varies.",
                "risk": "Confirm processing timeline with employer.",
            }

    # ------------------------------------------------------------------
    # Internal: _assess_skill_growth
    # ------------------------------------------------------------------

    def _assess_skill_growth(self, offer: dict,
                              target_skills: list) -> list:
        """
        Assess which skills a role will develop, aligned against
        the user's target skill list.
        """
        title = offer.get("title", "").lower()
        company_type = offer.get("company_type", "").lower()
        industry = offer.get("industry", "").lower()
        description = offer.get("description", "").lower()

        skills_map = {
            "startup": [
                "building from scratch", "wearing multiple hats",
                "hiring and team building", "rapid iteration",
                "stakeholder management", "fundraising exposure",
            ],
            "big_tech": [
                "scale engineering", "system design",
                "cross-team collaboration", "data-driven decisions",
                "mentoring", "process optimization",
            ],
            "consulting": [
                "client management", "rapid problem solving",
                "presentation skills", "industry breadth",
                "project management", "analytical frameworks",
            ],
            "finance": [
                "risk modeling", "regulatory knowledge",
                "quantitative analysis", "stakeholder management",
                "attention to detail", "high-pressure decision making",
            ],
        }

        inferred_skills = skills_map.get(company_type, [
            "domain expertise", "professional development",
            "collaboration", "problem solving",
        ])

        # Check title for role-specific skills
        if "manager" in title or "lead" in title or "head" in title:
            inferred_skills.extend(["people management", "strategic planning"])
        if "data" in title or "analyst" in title:
            inferred_skills.extend(["data analysis", "SQL", "visualization"])
        if "engineer" in title or "developer" in title:
            inferred_skills.extend(["technical architecture", "code review"])

        # Cross-reference with target skills
        matched_targets = []
        for skill in target_skills:
            skill_lower = skill.lower()
            if any(skill_lower in s for s in inferred_skills):
                matched_targets.append(skill)
            elif description and skill_lower in description:
                matched_targets.append(skill)

        return list(dict.fromkeys(inferred_skills[:8] + matched_targets))

    # ------------------------------------------------------------------
    # Internal: _assess_risks
    # ------------------------------------------------------------------

    def _assess_risks(self, offer: dict) -> list:
        """Identify risk factors for a career path."""
        risks = []
        company_type = offer.get("company_type", "").lower()
        equity = offer.get("equity", "")

        if company_type == "startup":
            risks.append("Startup failure risk — equity could be worthless")
            risks.append("Potential for high hours and burnout")
            risks.append("Less structured career progression")

        if equity and "%" in str(equity):
            risks.append("Equity value highly uncertain until exit event")

        visa = offer.get("visa_support", "")
        if visa:
            risks.append("Visa dependency — tied to employer for immigration status")

        base = offer.get("base_salary", 0)
        if base and base < 50000:
            risks.append("Below-market base salary — validate with benchmarks")

        location = offer.get("location", "").lower()
        if any(city in location for city in ["san francisco", "new york", "london", "zurich"]):
            risks.append("High cost-of-living location — adjust for purchasing power")

        return risks or ["No significant risks identified"]

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _classify_role_level(self, title: str) -> str:
        """Classify a job title into a level on the career ladder."""
        title_lower = title.lower()

        if any(k in title_lower for k in ["intern", "graduate", "trainee"]):
            return "junior"
        if any(k in title_lower for k in ["junior", "associate", "entry"]):
            return "junior"
        if any(k in title_lower for k in ["senior", "sr.", "sr ", "iii"]):
            return "senior"
        if any(k in title_lower for k in ["lead", "principal", "staff"]):
            return "lead"
        if any(k in title_lower for k in ["director", "head of"]):
            return "director"
        if any(k in title_lower for k in ["vp", "vice president"]):
            return "vp"
        if any(k in title_lower for k in ["cto", "ceo", "cfo", "coo", "chief"]):
            return "c-level"
        return "mid"

    def _project_title(self, original_title: str, new_level: str,
                       company_type: str) -> str:
        """Generate a projected title based on level progression."""
        # Extract the core role from the original title
        core_patterns = [
            r"(?:junior|senior|lead|principal|staff|head of|director of)\s*",
            r"\s*(?:i{1,3}|iv|v)$",
        ]
        core = original_title
        for pattern in core_patterns:
            core = re.sub(pattern, "", core, flags=re.IGNORECASE).strip()

        level_prefixes = {
            "junior": "Junior",
            "mid": "",
            "senior": "Senior",
            "lead": "Lead",
            "director": "Director of",
            "vp": "VP of",
            "c-level": "Head of",
        }
        prefix = level_prefixes.get(new_level, "")
        if prefix:
            return f"{prefix} {core}"
        return core

    def _estimate_equity_value(self, equity: str, year: int,
                                company_type: str) -> float:
        """
        Rough estimate of annual equity value based on vesting schedule.

        Standard 4-year vest with 1-year cliff.
        """
        if not equity:
            return 0.0

        # Try to parse a monetary value from equity string
        numbers = re.findall(r"[\d,]+", str(equity).replace(",", ""))
        if not numbers:
            return 0.0

        try:
            total_equity = float(numbers[0])
        except (ValueError, IndexError):
            return 0.0

        # 4-year vesting, 1-year cliff
        if year < 1:
            return 0.0
        elif year == 1:
            return total_equity * 0.25  # Cliff vest
        elif year <= 4:
            return total_equity * 0.25  # Annual vest
        else:
            return 0.0  # Fully vested after 4 years

    # ------------------------------------------------------------------
    # Public: compare_paths
    # ------------------------------------------------------------------

    def compare_paths(self, simulation_id: int) -> str:
        """
        Side-by-side path comparison with 1yr/3yr/5yr projections.

        Retrieves a saved simulation and formats a comparison table.
        """
        if not self.enabled:
            return "CareerSimulator disabled."

        sim = self._load_simulation(simulation_id)
        if not sim:
            return f"Simulation {simulation_id} not found."

        paths_data = sim.get("paths", [])
        if not paths_data:
            return "No path data in this simulation."

        lines = [
            f"=== Career Path Comparison: {sim.get('simulation_name', '')} ===",
            f"Current Role: {sim.get('current_role', 'N/A')}",
            "",
        ]

        for proj in paths_data:
            company = proj.get("company", "Unknown")
            title = proj.get("title", "Unknown")
            yearly = proj.get("yearly_projections", [])

            lines.append(f"Path: {company} - {title}")
            lines.append(f"  Starting: {proj.get('starting_salary', 0):,}")

            for yr in yearly:
                if yr["year"] in (1, 3, 5):
                    lines.append(
                        f"  Y{yr['year']}: {yr['base_salary']:,} "
                        f"({yr['title']}) — Total comp: {yr['total_comp']:,}"
                    )

            skills = proj.get("skills_gained", [])
            if skills:
                lines.append(f"  Skills: {', '.join(skills[:5])}")

            risks = proj.get("risks", [])
            if risks:
                lines.append(f"  Risk: {risks[0]}")

            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: get_recommendation
    # ------------------------------------------------------------------

    def get_recommendation(self, simulation_id: int) -> str:
        """
        AI-generated recommendation weighing compensation, growth,
        risk, and lifestyle factors across all paths in a simulation.
        """
        if not self.enabled:
            return "CareerSimulator disabled."

        sim = self._load_simulation(simulation_id)
        if not sim:
            return f"Simulation {simulation_id} not found."

        paths_data = sim.get("paths", [])
        if not paths_data:
            return "No path data for recommendation."

        # Build comparison summary for AI
        summary_lines = []
        for proj in paths_data:
            company = proj.get("company", "?")
            title = proj.get("title", "?")
            start_sal = proj.get("starting_salary", 0)
            final_sal = proj.get("final_salary", 0)
            final_title = proj.get("final_title", "?")
            total_5yr = proj.get("total_5yr_comp", 0)
            skills = proj.get("skills_gained", [])
            risks = proj.get("risks", [])

            summary_lines.append(
                f"- {company} ({title}): Start {start_sal:,} -> "
                f"Y5 {final_sal:,} as {final_title}. "
                f"5yr total comp: {total_5yr:,}. "
                f"Skills: {', '.join(skills[:4])}. "
                f"Risks: {'; '.join(risks[:2])}."
            )

        summary = "\n".join(summary_lines)
        current_role = sim.get("current_role", "not specified")

        # Try AI recommendation
        if self.ai and getattr(self.ai, "enabled", False):
            system_prompt = (
                "You are a senior career advisor. Given multiple career path "
                "projections, provide a clear recommendation. Weigh: total "
                "compensation trajectory, skill development, promotion speed, "
                "risk factors, and work-life balance. Be specific and decisive. "
                "End with a clear 'Recommended path:' statement."
            )
            user_prompt = (
                f"Current role: {current_role}\n\n"
                f"Career paths under consideration:\n{summary}\n\n"
                "Provide a recommendation. Which path offers the best "
                "combination of compensation, growth, and risk-adjusted returns? "
                "Be specific about why."
            )

            try:
                recommendation = self.ai._call_llm(system_prompt, user_prompt)
                if recommendation:
                    return recommendation.strip()
            except Exception as e:
                log.warning(f"AI recommendation failed: {e}")

        # Fallback: simple heuristic recommendation
        return self._heuristic_recommendation(paths_data)

    def _heuristic_recommendation(self, paths_data: list) -> str:
        """Simple recommendation without AI based on total 5yr comp."""
        if not paths_data:
            return "No paths to compare."

        best = max(paths_data, key=lambda p: p.get("total_5yr_comp", 0))
        lines = [
            "=== Heuristic Recommendation (AI unavailable) ===",
            "",
            f"Recommended: {best.get('company', '?')} - {best.get('title', '?')}",
            f"Reason: Highest projected 5-year total compensation "
            f"({best.get('total_5yr_comp', 0):,}).",
            "",
            "Note: This is a simple comparison by total comp only. "
            "Enable AI for a nuanced recommendation weighing growth, "
            "risk, and lifestyle factors.",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: generate_decision_brief
    # ------------------------------------------------------------------

    def generate_decision_brief(self, simulation_id: int) -> str:
        """
        Formatted decision brief combining comparison and recommendation.

        Output format:
        Path A: Company - Title
          Y1: salary -> Y3: salary (promotion) -> Y5: salary (track)
          Skills: ...
          Risk: ...

        Path B: ...

        Recommendation: ...
        """
        if not self.enabled:
            return "CareerSimulator disabled."

        sim = self._load_simulation(simulation_id)
        if not sim:
            return f"Simulation {simulation_id} not found."

        paths_data = sim.get("paths", [])
        if not paths_data:
            return "No path data for decision brief."

        labels = "ABCDEFGHIJ"
        lines = [
            f"=== Decision Brief: {sim.get('simulation_name', '')} ===",
            f"Current Role: {sim.get('current_role', 'N/A')}",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        for i, proj in enumerate(paths_data):
            label = labels[i] if i < len(labels) else str(i + 1)
            company = proj.get("company", "Unknown")
            title = proj.get("title", "Unknown")
            yearly = proj.get("yearly_projections", [])

            lines.append(f"Path {label}: {company} - {title}")

            # Build Y1 -> Y3 -> Y5 summary line
            milestones = []
            for yr in yearly:
                if yr["year"] in (1, 3, 5):
                    promo_note = ""
                    if yr["title"] != title:
                        promo_note = f" ({yr['title']})"
                    milestones.append(
                        f"Y{yr['year']}: {yr['base_salary']:,}{promo_note}"
                    )
            if milestones:
                lines.append(f"  {' -> '.join(milestones)}")

            skills = proj.get("skills_gained", [])
            if skills:
                lines.append(f"  Skills: {', '.join(skills[:5])}")

            risks = proj.get("risks", [])
            if risks:
                for risk in risks[:2]:
                    lines.append(f"  Risk: {risk}")

            visa = proj.get("visa_timeline", {})
            if visa and visa.get("required"):
                lines.append(
                    f"  Visa: {visa.get('type', 'Unknown')} "
                    f"({visa.get('processing_weeks', '?')} weeks)"
                )

            lines.append("")

        # Add recommendation
        lines.append("--- Recommendation ---")
        rec = self.get_recommendation(simulation_id)
        lines.append(rec)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal: persistence
    # ------------------------------------------------------------------

    def _save_simulation(self, name: str, current_role: str,
                         projections: list) -> int:
        """Save a simulation to career_simulations table."""
        rec_text = ""
        try:
            self.state.conn.execute(
                """INSERT INTO career_simulations
                   (simulation_name, current_role, paths, recommendation)
                   VALUES (?, ?, ?, ?)""",
                (name, current_role, json.dumps(projections), rec_text),
            )
            self.state.conn.commit()

            row = self.state.conn.execute(
                "SELECT last_insert_rowid() AS id"
            ).fetchone()
            return row["id"] if row else 0
        except Exception as e:
            log.error(f"Error saving simulation: {e}")
            return 0

    def _load_simulation(self, simulation_id: int) -> Optional[dict]:
        """Load a simulation from career_simulations table."""
        try:
            row = self.state.conn.execute(
                """SELECT * FROM career_simulations WHERE id = ?""",
                (simulation_id,),
            ).fetchone()
            if not row:
                return None

            result = dict(row)
            if isinstance(result.get("paths"), str):
                try:
                    result["paths"] = json.loads(result["paths"])
                except (json.JSONDecodeError, TypeError):
                    result["paths"] = []
            return result
        except Exception as e:
            log.warning(f"Error loading simulation {simulation_id}: {e}")
            return None
