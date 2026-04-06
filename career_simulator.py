"""
Career Simulator — Multi-Path Career Projection and Comparison Engine.

Projects multiple career paths side-by-side over 1, 3, and 5-year horizons.
For each path, estimates:
  - Title progression and promotion timeline
  - Salary trajectory with company-type-specific raise rates
  - Total compensation (base + bonus + equity vesting)
  - Growth opportunities and risk factors

Raise rate benchmarks by company type:
  - Startup:    5-15% (high variance, equity-heavy)
  - Big Tech:   8-12% (structured bands, RSU refreshers)
  - Finance:    8-20% (bonus-heavy, volatile)
  - Consulting: 10-18% (up-or-out, clear levels)
  - Enterprise: 3-7% (stable, slower growth)

Persists simulations in career_simulations table for later retrieval.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("lla.career_simulator")

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

SIMULATION_SYSTEM = """You are a career trajectory analyst.
Given multiple career paths with offer details, project each path over 5 years.
Return ONLY valid JSON — a list of path projections:
[
  {
    "path_label": "string",
    "year_1": {"title": "...", "base": N, "bonus": N, "equity": N, "total_comp": N},
    "year_3": {"title": "...", "base": N, "bonus": N, "equity": N, "total_comp": N},
    "year_5": {"title": "...", "base": N, "bonus": N, "equity": N, "total_comp": N},
    "promotion_timeline": "string describing expected promotions",
    "growth_score": 8,
    "risk_score": 4,
    "lifestyle_score": 7
  }
]
Be realistic based on industry norms. Account for vesting cliffs."""

RECOMMENDATION_SYSTEM = """You are a senior career advisor.
Given multi-path simulation results, recommend the best path.
Return ONLY valid JSON:
{
  "recommended_path": "path label",
  "reasoning": "3-4 sentence explanation weighing comp, growth, risk, and lifestyle",
  "trade_offs": ["key trade-off 1", "key trade-off 2", "key trade-off 3"],
  "risk_warning": "primary risk for the recommended path",
  "alternative": "second-best path and when it would be preferred"
}"""

# Raise rate ranges by company type (annual %)
RAISE_RATES = {
    "startup":    {"min": 5, "max": 15, "typical": 10, "equity_growth": 0.20},
    "big_tech":   {"min": 8, "max": 12, "typical": 10, "equity_growth": 0.15},
    "finance":    {"min": 8, "max": 20, "typical": 12, "equity_growth": 0.05},
    "consulting": {"min": 10, "max": 18, "typical": 14, "equity_growth": 0.02},
    "enterprise": {"min": 3, "max": 7, "typical": 5, "equity_growth": 0.08},
    "agency":     {"min": 3, "max": 8, "typical": 5, "equity_growth": 0.02},
    "other":      {"min": 4, "max": 10, "typical": 6, "equity_growth": 0.05},
}

# Typical promotion timelines (years between promotions)
PROMO_CADENCE = {
    "startup":    1.5,
    "big_tech":   2.0,
    "finance":    2.5,
    "consulting": 2.0,
    "enterprise": 3.0,
    "agency":     2.5,
    "other":      2.5,
}

# Title progression templates
TITLE_LADDERS = {
    "engineering": [
        "Software Engineer", "Senior Software Engineer",
        "Staff Engineer", "Principal Engineer", "Distinguished Engineer",
    ],
    "data": [
        "Data Scientist", "Senior Data Scientist",
        "Staff Data Scientist", "Principal Data Scientist", "Head of Data",
    ],
    "product": [
        "Product Manager", "Senior PM",
        "Group PM", "Director of Product", "VP Product",
    ],
    "design": [
        "Designer", "Senior Designer",
        "Staff Designer", "Design Lead", "Head of Design",
    ],
    "general": [
        "IC", "Senior IC",
        "Lead", "Manager", "Director",
    ],
}


class CareerSimulator:
    """Multi-path career projection and comparison engine."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        cs_cfg = cfg.get("career_simulator", {})
        self.enabled = cs_cfg.get("enabled", False)
        self.projection_years = cs_cfg.get("projection_years", 5)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def simulate(self, paths: list) -> Optional[dict]:
        """Project multiple career paths and compare side-by-side.

        Args:
            paths: list of dicts, each with keys:
                - label: display name for the path (e.g. "Google SWE")
                - job_id: optional, to pull offer data from DB
                - company: company name
                - title: job title
                - company_type: startup|big_tech|finance|consulting|enterprise
                - base_salary: starting base
                - bonus: annual bonus
                - equity: equity package description
                - signing_bonus: one-time signing bonus
                - domain: engineering|data|product|design|general

        Returns:
            dict with simulation_id, per-path projections, and comparison
        """
        if not self.enabled:
            log.debug("CareerSimulator disabled; skipping simulation")
            return None

        if not paths or len(paths) < 1:
            log.warning("No paths provided for simulation")
            return None

        log.info("Running career simulation with %d paths", len(paths))

        # Enrich paths from offers table if job_id provided
        enriched = []
        for p in paths:
            enriched.append(self._enrich_path(p))

        # Project each path
        projections = []
        for path in enriched:
            proj = self._project_path(path, years=self.projection_years)
            projections.append(proj)

        # AI-enhanced projections if available
        ai_projections = self._ai_simulate(enriched)
        if ai_projections:
            for i, ai_proj in enumerate(ai_projections):
                if i < len(projections):
                    projections[i]["ai_insights"] = ai_proj

        sim_id = str(uuid.uuid4())[:12]
        simulation_name = " vs ".join(p.get("label", p.get("company", "?"))
                                       for p in enriched[:4])

        result = {
            "simulation_id": sim_id,
            "simulation_name": simulation_name,
            "paths": projections,
            "comparison": self._build_comparison_table(projections),
            "simulated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Persist
        self._save_simulation(sim_id, simulation_name, enriched, result)

        return result

    def compare_paths(self, simulation_id: str) -> Optional[dict]:
        """Load a simulation and return 1yr/3yr/5yr comparison table."""
        if not self.enabled:
            return None

        sim = self._load_simulation(simulation_id)
        if not sim:
            log.warning("Simulation %s not found", simulation_id)
            return None

        paths_data = json.loads(sim.get("paths", "[]"))
        if not paths_data:
            return {"simulation_id": simulation_id, "error": "No path data found"}

        # Build comparison at each milestone
        milestones = {}
        for year in [1, 3, 5]:
            year_data = []
            for path in paths_data:
                yearly = path.get("yearly_projection", [])
                yr_entry = next((y for y in yearly if y.get("year") == year), None)
                if yr_entry:
                    year_data.append({
                        "label": path.get("label", "Unknown"),
                        "title": yr_entry.get("projected_title", ""),
                        "total_comp": yr_entry.get("total_comp", 0),
                        "base": yr_entry.get("base", 0),
                        "cumulative": yr_entry.get("cumulative", 0),
                    })
            milestones[f"year_{year}"] = year_data

        return {
            "simulation_id": simulation_id,
            "simulation_name": sim.get("simulation_name", ""),
            "milestones": milestones,
        }

    def get_recommendation(self, simulation_id: str) -> Optional[dict]:
        """AI-powered recommendation weighing comp, growth, risk, lifestyle."""
        if not self.enabled:
            return None

        sim = self._load_simulation(simulation_id)
        if not sim:
            return None

        paths_data = json.loads(sim.get("paths", "[]"))
        recommendation_text = sim.get("recommendation", "")

        # If already computed, return it
        if recommendation_text:
            try:
                return json.loads(recommendation_text)
            except (json.JSONDecodeError, TypeError):
                pass

        # Generate via AI
        rec = self._ai_recommend(paths_data)
        if not rec:
            rec = self._fallback_recommendation(paths_data)

        # Persist
        try:
            self.state.conn.execute(
                "UPDATE career_simulations SET recommendation = ? WHERE id = ?",
                (json.dumps(rec), simulation_id),
            )
            self.state.conn.commit()
        except Exception as exc:
            log.error("Failed to save recommendation: %s", exc)

        rec["simulation_id"] = simulation_id
        return rec

    # ------------------------------------------------------------------
    # Path projection
    # ------------------------------------------------------------------

    def _project_path(self, path: dict, years: int = 5) -> dict:
        """Project a single career path over N years.

        Returns yearly breakdown with title progression, comp trajectory,
        and cumulative earnings.
        """
        company_type = path.get("company_type", "other")
        domain = path.get("domain", "general")
        base = path.get("base_salary", 0) or 0
        bonus = path.get("bonus", 0) or 0
        equity_str = path.get("equity", "")
        signing = path.get("signing_bonus", 0) or 0

        raise_rate = self._estimate_annual_raise(company_type) / 100.0
        annual_equity = self._parse_annual_equity(equity_str)
        equity_growth = RAISE_RATES.get(company_type, RAISE_RATES["other"])["equity_growth"]
        promo_cadence = PROMO_CADENCE.get(company_type, 2.5)
        title_ladder = TITLE_LADDERS.get(domain, TITLE_LADDERS["general"])

        # Find starting position in ladder
        current_title = path.get("title", "")
        start_idx = self._find_title_index(current_title, title_ladder)

        yearly = []
        cumulative = 0
        current_base = base
        current_bonus = bonus
        current_equity = annual_equity
        current_title_idx = start_idx

        for yr in range(1, years + 1):
            # Promotion check
            if yr > 1 and (yr - 1) % max(1, int(promo_cadence)) == 0:
                if current_title_idx + 1 < len(title_ladder):
                    current_title_idx += 1
                    # Promotion bump
                    current_base = round(current_base * 1.15)
                    current_bonus = round(current_bonus * 1.20)

            # Annual raises (non-promotion years)
            if yr > 1 and not ((yr - 1) % max(1, int(promo_cadence)) == 0):
                current_base = round(current_base * (1 + raise_rate))
                current_bonus = round(current_bonus * (1 + raise_rate * 0.7))

            # Equity: cliff in year 1, then vesting + refreshers
            if yr == 1:
                yr_equity = 0  # Cliff year
            elif yr <= 4:
                yr_equity = current_equity
                current_equity = round(current_equity * (1 + equity_growth))
            else:
                yr_equity = round(current_equity * 0.6)  # Refresher grants only

            yr_signing = signing if yr == 1 else 0
            total = current_base + current_bonus + yr_equity + yr_signing
            cumulative += total

            projected_title = title_ladder[current_title_idx] if current_title_idx < len(title_ladder) else current_title

            yearly.append({
                "year": yr,
                "projected_title": projected_title,
                "base": current_base,
                "bonus": current_bonus,
                "equity": yr_equity,
                "signing": yr_signing,
                "total_comp": total,
                "cumulative": cumulative,
            })

        return {
            "label": path.get("label", path.get("company", "Unknown")),
            "company": path.get("company", ""),
            "company_type": company_type,
            "starting_title": path.get("title", ""),
            "starting_base": base,
            "yearly_projection": yearly,
            "total_5yr_earnings": cumulative,
            "final_title": yearly[-1]["projected_title"] if yearly else current_title,
            "final_base": yearly[-1]["base"] if yearly else base,
            "promotions_expected": current_title_idx - start_idx,
        }

    def _estimate_annual_raise(self, company_type: str) -> float:
        """Return typical annual raise percentage for a company type.

        startup: 5-15%, big_tech: 8-12%, finance: 8-20%,
        consulting: 10-18%, enterprise: 3-7%
        """
        rates = RAISE_RATES.get(company_type, RAISE_RATES["other"])
        return rates["typical"]

    def _calculate_total_comp(self, path: dict, years: int) -> float:
        """Calculate cumulative total compensation over N years."""
        proj = self._project_path(path, years=years)
        return proj.get("total_5yr_earnings", 0)

    # ------------------------------------------------------------------
    # Comparison and recommendation
    # ------------------------------------------------------------------

    def _build_comparison_table(self, projections: list) -> dict:
        """Build a side-by-side comparison at 1yr, 3yr, 5yr milestones."""
        table = {}
        for milestone in [1, 3, 5]:
            entries = []
            for proj in projections:
                yearly = proj.get("yearly_projection", [])
                yr_data = next((y for y in yearly if y.get("year") == milestone), None)
                if yr_data:
                    entries.append({
                        "label": proj.get("label", "Unknown"),
                        "title": yr_data.get("projected_title", ""),
                        "total_comp": yr_data.get("total_comp", 0),
                        "cumulative": yr_data.get("cumulative", 0),
                    })
            table[f"year_{milestone}"] = entries

        # Determine winner at each milestone
        for key, entries in table.items():
            if entries:
                best = max(entries, key=lambda e: e.get("total_comp", 0))
                for e in entries:
                    e["is_top"] = (e["label"] == best["label"])

        return table

    def _ai_simulate(self, paths: list) -> Optional[list]:
        """Use AI for richer career projections."""
        if not self.ai or not self.ai.enabled:
            return None

        paths_text = []
        for p in paths:
            paths_text.append(
                f"Path: {p.get('label', p.get('company', '?'))}\n"
                f"  Company: {p.get('company', 'N/A')} ({p.get('company_type', 'unknown')})\n"
                f"  Title: {p.get('title', 'N/A')}\n"
                f"  Base: ${p.get('base_salary', 0):,.0f}, Bonus: ${p.get('bonus', 0):,.0f}\n"
                f"  Equity: {p.get('equity', 'N/A')}\n"
                f"  Domain: {p.get('domain', 'general')}"
            )

        user_prompt = (
            f"Career paths to simulate:\n\n"
            + "\n\n".join(paths_text)
            + f"\n\nProject each path over {self.projection_years} years."
        )

        try:
            raw = self.ai._call_llm(SIMULATION_SYSTEM, user_prompt)
            result = json.loads(raw)
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            log.warning("Failed to parse AI simulation: %s", exc)

        return None

    def _ai_recommend(self, paths_data: list) -> Optional[dict]:
        """Use AI to recommend the best path."""
        if not self.ai or not self.ai.enabled:
            return None

        user_prompt = (
            f"Simulation results:\n{json.dumps(paths_data, indent=2, default=str)}\n\n"
            f"Which career path do you recommend and why?"
        )

        try:
            raw = self.ai._call_llm(RECOMMENDATION_SYSTEM, user_prompt)
            rec = json.loads(raw)
            rec.setdefault("recommended_path", "")
            rec.setdefault("reasoning", "")
            return rec
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            log.warning("Failed to parse AI recommendation: %s", exc)

        return None

    def _fallback_recommendation(self, paths_data: list) -> dict:
        """Heuristic recommendation when AI is unavailable."""
        if not paths_data:
            return {"error": "No path data available for recommendation."}

        # Pick path with highest 5-year earnings
        best = max(paths_data, key=lambda p: p.get("total_5yr_earnings", 0))

        return {
            "recommended_path": best.get("label", "Unknown"),
            "reasoning": (
                f"{best.get('label', 'This path')} offers the highest projected "
                f"5-year earnings of ${best.get('total_5yr_earnings', 0):,.0f} "
                f"with {best.get('promotions_expected', 0)} expected promotions. "
                f"Final projected title: {best.get('final_title', 'N/A')}."
            ),
            "trade_offs": [
                "Highest comp does not always mean best career move.",
                "Consider work-life balance and personal growth.",
                "AI-enhanced analysis unavailable for deeper insights.",
            ],
            "risk_warning": "This is a heuristic recommendation based on compensation only.",
            "alternative": "Review all paths manually for non-financial factors.",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enrich_path(self, path: dict) -> dict:
        """Enrich a path dict with offer data from DB if job_id is provided."""
        job_id = path.get("job_id")
        if not job_id:
            return path

        try:
            row = self.state.conn.execute(
                "SELECT * FROM offers WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row:
                offer = dict(row)
                # Merge offer data into path (path values take precedence)
                for key in ["company", "title", "base_salary", "bonus", "equity",
                            "signing_bonus", "location", "remote_policy",
                            "growth_potential", "team_size"]:
                    if not path.get(key) and offer.get(key):
                        path[key] = offer[key]
        except Exception as exc:
            log.debug("Could not enrich path from offers: %s", exc)

        # Set defaults
        path.setdefault("label", f"{path.get('company', '?')} — {path.get('title', '?')}")
        path.setdefault("company_type", self._infer_company_type(path.get("company", "")))
        path.setdefault("domain", self._infer_domain(path.get("title", "")))
        path.setdefault("base_salary", 0)
        path.setdefault("bonus", 0)
        path.setdefault("equity", "")
        path.setdefault("signing_bonus", 0)

        return path

    def _save_simulation(self, sim_id: str, name: str,
                         paths: list, result: dict) -> None:
        """Persist simulation to career_simulations table."""
        try:
            self.state.conn.execute(
                """INSERT INTO career_simulations
                   (simulation_name, current_role, paths, recommendation, simulated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    name,
                    paths[0].get("title", "") if paths else "",
                    json.dumps(result.get("paths", []), default=str),
                    "",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.state.conn.commit()
        except Exception as exc:
            log.error("Failed to save simulation: %s", exc)

    def _load_simulation(self, simulation_id: str) -> Optional[dict]:
        """Load a simulation from the database."""
        try:
            row = self.state.conn.execute(
                "SELECT * FROM career_simulations WHERE id = ?",
                (simulation_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            log.error("Failed to load simulation %s: %s", simulation_id, exc)
            return None

    @staticmethod
    def _find_title_index(title: str, ladder: list) -> int:
        """Find the closest match for the current title in the ladder."""
        if not title:
            return 0

        title_lower = title.lower()
        for i, rung in enumerate(ladder):
            if rung.lower() in title_lower or title_lower in rung.lower():
                return i

        # Check for seniority keywords
        if any(w in title_lower for w in ["principal", "distinguished", "fellow"]):
            return min(3, len(ladder) - 1)
        if any(w in title_lower for w in ["staff", "lead"]):
            return min(2, len(ladder) - 1)
        if "senior" in title_lower or "sr" in title_lower:
            return min(1, len(ladder) - 1)

        return 0

    @staticmethod
    def _parse_annual_equity(equity_str: str) -> float:
        """Parse equity string into estimated annual value."""
        if not equity_str:
            return 0

        import re
        cleaned = equity_str.lower().replace(",", "").replace("$", "")

        match = re.search(r"(\d+(?:\.\d+)?)\s*(?:/|over)\s*(\d+)", cleaned)
        if match:
            total = float(match.group(1))
            years = float(match.group(2))
            return round(total / years) if years else 0

        match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
        if match:
            val = float(match.group(1))
            if val > 1000:
                return round(val / 4)

        return 0

    @staticmethod
    def _infer_company_type(company: str) -> str:
        """Infer company type from name."""
        if not company:
            return "other"

        name = company.lower()
        if any(w in name for w in ["google", "meta", "amazon", "apple",
                                    "microsoft", "netflix", "uber", "airbnb"]):
            return "big_tech"
        if any(w in name for w in ["bank", "capital", "goldman", "morgan",
                                    "jpmorgan", "citi", "financial"]):
            return "finance"
        if any(w in name for w in ["mckinsey", "deloitte", "accenture",
                                    "bain", "bcg", "kpmg", "pwc"]):
            return "consulting"
        if any(w in name for w in ["labs", "ai", ".io", "ventures"]):
            return "startup"

        return "enterprise"

    @staticmethod
    def _infer_domain(title: str) -> str:
        """Infer career domain from job title."""
        if not title:
            return "general"

        t = title.lower()
        if any(w in t for w in ["engineer", "developer", "swe", "devops", "sre"]):
            return "engineering"
        if any(w in t for w in ["data", "ml", "machine learning", "analytics"]):
            return "data"
        if any(w in t for w in ["product manager", "pm", "product lead"]):
            return "product"
        if any(w in t for w in ["design", "ux", "ui"]):
            return "design"

        return "general"
