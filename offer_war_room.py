"""
Offer War Room — Multi-Offer Comparison, Negotiation Strategy & Decision Engine.

Manages the full offer evaluation lifecycle: storing offers, comparing them
across weighted dimensions, projecting 5-year earnings, generating negotiation
playbooks, and recommending the optimal choice.

Scoring dimensions (default weights):
  - compensation  (0.30) — base + bonus + equity + signing
  - growth        (0.20) — promotion velocity, learning, title trajectory
  - work_life     (0.15) — remote policy, location, team culture
  - stability     (0.15) — company stage, funding, layoff history
  - benefits      (0.10) — health, retirement, perks, visa support
  - mission       (0.10) — alignment with career goals and values
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("lla.offer_war_room")

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

COMPARISON_SYSTEM = """You are a career strategist comparing job offers.
Given the offers and scoring weights, score each offer on every dimension (1-10).
Return ONLY valid JSON — a dict mapping job_id to a dict of dimension scores:
{
  "job_id_1": {"compensation": 8, "growth": 7, "work_life": 6, "stability": 9, "benefits": 7, "mission": 5},
  "job_id_2": ...
}
Be objective; justify each score with the data provided."""

NEGOTIATION_SYSTEM = """You are an expert salary negotiation coach.
Given the offer details and market data, produce a JSON negotiation playbook:
{
  "leverage_points": ["list of 3-5 leverage points"],
  "counter_offer": {"base_salary": number, "bonus": number, "equity": "string", "signing_bonus": number},
  "script_opening": "string — first thing to say",
  "script_counter": "string — the counter-offer pitch",
  "walk_away_number": number,
  "anchoring_range": {"low": number, "high": number},
  "timing_advice": "string",
  "risk_assessment": "string"
}"""

RECOMMENDATION_SYSTEM = """You are a career advisor making a final offer recommendation.
Given the comparison matrix with weighted scores, produce a JSON recommendation:
{
  "recommended_job_id": "string",
  "reasoning": "3-4 sentence explanation",
  "trade_offs": ["list of key trade-offs to consider"],
  "negotiation_priority": "which offer to negotiate first and why"
}"""

DEFAULT_WEIGHTS = {
    "compensation": 0.30,
    "growth": 0.20,
    "work_life": 0.15,
    "stability": 0.15,
    "benefits": 0.10,
    "mission": 0.10,
}

DIMENSIONS = list(DEFAULT_WEIGHTS.keys())


class OfferWarRoom:
    """Multi-offer comparison, negotiation playbook, and decision engine."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        owr_cfg = cfg.get("offer_war_room", {})
        self.enabled = owr_cfg.get("enabled", False)
        self.default_raise_pct = owr_cfg.get("default_raise_pct", 5)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_offer(self, job_id: str, company: str, title: str,
                  base_salary: float = 0, bonus: float = 0,
                  equity: str = "", signing_bonus: float = 0,
                  benefits: str = "", visa_support: str = "",
                  start_date: str = "", location: str = "",
                  remote_policy: str = "", growth_potential: str = "",
                  team_size: str = "", pros: str = "", cons: str = "",
                  deadline: str = "") -> Optional[dict]:
        """Save or update an offer in the offers table."""
        if not self.enabled:
            log.debug("OfferWarRoom disabled; skipping add_offer")
            return None

        log.info("Adding offer for %s at %s (base=%s)", title, company, base_salary)
        now = datetime.now(timezone.utc).isoformat()

        try:
            self.state.conn.execute(
                """INSERT OR REPLACE INTO offers
                   (job_id, company, title, base_salary, bonus, equity,
                    signing_bonus, benefits, visa_support, start_date,
                    location, remote_policy, growth_potential, team_size,
                    pros, cons, deadline, received_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, company, title, base_salary, bonus, equity,
                 signing_bonus, benefits, visa_support, start_date,
                 location, remote_policy, growth_potential, team_size,
                 pros, cons, deadline, now),
            )
            self.state.conn.commit()
        except Exception as exc:
            log.error("Failed to save offer %s: %s", job_id, exc)
            return None

        return {
            "job_id": job_id,
            "company": company,
            "title": title,
            "base_salary": base_salary,
            "total_first_year": base_salary + bonus + signing_bonus,
            "saved": True,
        }

    def compare_offers(self, job_ids: list, weights: Optional[dict] = None) -> Optional[dict]:
        """Score each offer on 6 dimensions, return comparison matrix.

        Args:
            job_ids: list of job_id strings to compare
            weights: optional dict of dimension -> weight (0-1), defaults applied
        Returns:
            dict with per-offer scores, weighted totals, and ranking
        """
        if not self.enabled:
            return None

        if len(job_ids) < 2:
            log.warning("Need at least 2 offers to compare")
            return None

        weights = weights or dict(DEFAULT_WEIGHTS)
        # Normalise weights
        total_w = sum(weights.values()) or 1
        weights = {k: v / total_w for k, v in weights.items()}

        offers = []
        for jid in job_ids:
            offer = self._load_offer(jid)
            if offer:
                offers.append(offer)
            else:
                log.warning("Offer %s not found, skipping", jid)

        if len(offers) < 2:
            log.warning("Not enough valid offers to compare")
            return None

        # AI scoring
        scores = self._ai_score_offers(offers, weights)
        if not scores:
            scores = self._fallback_score_offers(offers)

        # Calculate weighted totals
        ranking = []
        for offer in offers:
            jid = offer["job_id"]
            offer_scores = scores.get(jid, {dim: 5 for dim in DIMENSIONS})
            weighted_total = sum(
                offer_scores.get(dim, 5) * weights.get(dim, 0)
                for dim in DIMENSIONS
            )
            ranking.append({
                "job_id": jid,
                "company": offer["company"],
                "title": offer["title"],
                "dimension_scores": offer_scores,
                "weighted_total": round(weighted_total, 2),
                "projection_5yr": self._project_earnings(offer, years=5),
            })

        ranking.sort(key=lambda x: x["weighted_total"], reverse=True)

        # Persist comparison
        comp_id = str(uuid.uuid4())[:12]
        try:
            self.state.conn.execute(
                """INSERT INTO offer_comparisons
                   (comparison_name, offer_ids, weights, scores,
                    recommendation, negotiation_plan, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"Comparison {comp_id}",
                    json.dumps(job_ids),
                    json.dumps(weights),
                    json.dumps({r["job_id"]: r["dimension_scores"] for r in ranking}),
                    ranking[0]["job_id"] if ranking else "",
                    "",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.state.conn.commit()
        except Exception as exc:
            log.error("Failed to save comparison: %s", exc)

        return {
            "comparison_id": comp_id,
            "weights": weights,
            "ranking": ranking,
            "top_pick": ranking[0] if ranking else None,
        }

    def generate_negotiation_playbook(self, job_id: str) -> Optional[dict]:
        """Generate leverage points, counter-offer script, and walk-away number."""
        if not self.enabled:
            return None

        offer = self._load_offer(job_id)
        if not offer:
            log.warning("Offer %s not found for negotiation playbook", job_id)
            return None

        log.info("Generating negotiation playbook for %s at %s",
                 offer["title"], offer["company"])

        # Gather market context
        market_data = self._get_market_context(offer["title"], offer["location"])

        user_prompt = (
            f"Offer details:\n"
            f"  Company: {offer['company']}\n"
            f"  Title: {offer['title']}\n"
            f"  Base salary: ${offer['base_salary']:,.0f}\n"
            f"  Bonus: ${offer['bonus']:,.0f}\n"
            f"  Equity: {offer['equity']}\n"
            f"  Signing bonus: ${offer['signing_bonus']:,.0f}\n"
            f"  Location: {offer['location']}\n"
            f"  Remote: {offer['remote_policy']}\n"
            f"  Benefits: {offer['benefits']}\n"
            f"  Growth potential: {offer['growth_potential']}\n"
            f"  Team size: {offer['team_size']}\n"
            f"  Pros: {offer['pros']}\n"
            f"  Cons: {offer['cons']}\n"
            f"  Deadline: {offer['deadline']}\n\n"
            f"Market data: {json.dumps(market_data)}\n\n"
            f"Generate a negotiation playbook."
        )

        try:
            raw = self.ai._call_llm(NEGOTIATION_SYSTEM, user_prompt)
            playbook = json.loads(raw)
            playbook.setdefault("leverage_points", [])
            playbook.setdefault("counter_offer", {})
            playbook.setdefault("walk_away_number", offer["base_salary"] * 0.9)
            playbook["offer_job_id"] = job_id
            playbook["company"] = offer["company"]
            return playbook
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            log.warning("Failed to parse negotiation playbook: %s", exc)

        # Fallback playbook
        bump = 0.15
        return {
            "offer_job_id": job_id,
            "company": offer["company"],
            "leverage_points": [
                "Competing offers (if applicable)",
                "Market rate for this role and location",
                "Unique skills or experience you bring",
            ],
            "counter_offer": {
                "base_salary": round(offer["base_salary"] * (1 + bump)),
                "bonus": round(offer["bonus"] * (1 + bump)),
                "equity": offer["equity"],
                "signing_bonus": round(max(offer["signing_bonus"], offer["base_salary"] * 0.1)),
            },
            "script_opening": (
                f"I'm very excited about this opportunity at {offer['company']}. "
                f"After reviewing the full package, I'd like to discuss a few adjustments."
            ),
            "walk_away_number": round(offer["base_salary"] * 0.9),
            "anchoring_range": {
                "low": round(offer["base_salary"] * 1.10),
                "high": round(offer["base_salary"] * 1.25),
            },
            "timing_advice": "Negotiate 24-48 hours after receiving the offer; show enthusiasm first.",
            "risk_assessment": "Moderate — standard negotiation should be well-received.",
        }

    def get_recommendation(self, comparison_id: str) -> Optional[dict]:
        """Retrieve a comparison and generate an AI recommendation."""
        if not self.enabled:
            return None

        try:
            row = self.state.conn.execute(
                "SELECT * FROM offer_comparisons WHERE id = ? "
                "OR comparison_name LIKE ?",
                (comparison_id, f"%{comparison_id}%"),
            ).fetchone()
        except Exception as exc:
            log.error("Failed to load comparison %s: %s", comparison_id, exc)
            return None

        if not row:
            log.warning("Comparison %s not found", comparison_id)
            return None

        row = dict(row)
        offer_ids = json.loads(row.get("offer_ids", "[]"))
        weights = json.loads(row.get("weights", "{}"))
        scores = json.loads(row.get("scores", "{}"))

        # Enrich with offer details
        offers_detail = []
        for jid in offer_ids:
            offer = self._load_offer(jid)
            if offer:
                offer_scores = scores.get(jid, {})
                weighted = sum(
                    offer_scores.get(d, 5) * weights.get(d, 0) for d in DIMENSIONS
                )
                offers_detail.append({
                    "job_id": jid,
                    "company": offer["company"],
                    "title": offer["title"],
                    "base_salary": offer["base_salary"],
                    "scores": offer_scores,
                    "weighted_total": round(weighted, 2),
                })

        user_prompt = (
            f"Comparison data:\n{json.dumps(offers_detail, indent=2)}\n\n"
            f"Weights: {json.dumps(weights)}\n\n"
            f"Which offer should the candidate accept and why?"
        )

        try:
            raw = self.ai._call_llm(RECOMMENDATION_SYSTEM, user_prompt)
            rec = json.loads(raw)
            rec["comparison_id"] = comparison_id
            rec["offers_evaluated"] = len(offers_detail)
            return rec
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            log.warning("Failed to parse recommendation: %s", exc)

        # Fallback: pick highest weighted
        if offers_detail:
            best = max(offers_detail, key=lambda o: o["weighted_total"])
            return {
                "comparison_id": comparison_id,
                "recommended_job_id": best["job_id"],
                "reasoning": (
                    f"{best['company']} ({best['title']}) scored highest with a "
                    f"weighted total of {best['weighted_total']}/10."
                ),
                "trade_offs": ["Auto-recommendation; AI analysis unavailable."],
                "offers_evaluated": len(offers_detail),
            }

        return {"comparison_id": comparison_id, "error": "No offers found for comparison."}

    def get_all_offers(self) -> list:
        """Return all current offers."""
        if not self.enabled:
            return []

        try:
            rows = self.state.conn.execute(
                "SELECT * FROM offers ORDER BY received_at DESC"
            ).fetchall()
            results = []
            for r in rows:
                offer = dict(r)
                offer["total_first_year"] = (
                    offer.get("base_salary", 0)
                    + offer.get("bonus", 0)
                    + offer.get("signing_bonus", 0)
                )
                results.append(offer)
            return results
        except Exception as exc:
            log.error("Failed to load offers: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _project_earnings(self, offer: dict, years: int = 5) -> dict:
        """5-year compensation projection with estimated annual raises.

        Returns yearly breakdown of base, bonus, equity vesting, and cumulative total.
        """
        base = offer.get("base_salary", 0) or 0
        bonus = offer.get("bonus", 0) or 0
        signing = offer.get("signing_bonus", 0) or 0
        equity_str = offer.get("equity", "")
        raise_pct = self.default_raise_pct / 100.0

        # Parse equity if possible (e.g., "$200000/4yr" or "50000 RSUs")
        annual_equity = self._parse_annual_equity(equity_str)

        projection = []
        cumulative = 0
        current_base = base
        current_bonus = bonus

        for yr in range(1, years + 1):
            if yr > 1:
                current_base = round(current_base * (1 + raise_pct))
                current_bonus = round(current_bonus * (1 + raise_pct * 0.5))

            year_equity = annual_equity if yr <= 4 else round(annual_equity * 0.7)
            year_signing = signing if yr == 1 else 0
            year_total = current_base + current_bonus + year_equity + year_signing
            cumulative += year_total

            projection.append({
                "year": yr,
                "base": current_base,
                "bonus": current_bonus,
                "equity": year_equity,
                "signing": year_signing,
                "total": year_total,
                "cumulative": cumulative,
            })

        return {
            "years": years,
            "projection": projection,
            "total_5yr": cumulative,
            "avg_annual": round(cumulative / years) if years else 0,
        }

    def _load_offer(self, job_id: str) -> Optional[dict]:
        """Load an offer from the database."""
        try:
            row = self.state.conn.execute(
                "SELECT * FROM offers WHERE job_id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            log.error("Failed to load offer %s: %s", job_id, exc)
            return None

    def _ai_score_offers(self, offers: list, weights: dict) -> Optional[dict]:
        """Use AI to score offers across all dimensions."""
        if not self.ai or not self.ai.enabled:
            return None

        offers_text = []
        for o in offers:
            offers_text.append(
                f"Job ID: {o['job_id']}\n"
                f"  Company: {o['company']}, Title: {o['title']}\n"
                f"  Base: ${o.get('base_salary', 0):,.0f}, Bonus: ${o.get('bonus', 0):,.0f}\n"
                f"  Equity: {o.get('equity', 'N/A')}, Signing: ${o.get('signing_bonus', 0):,.0f}\n"
                f"  Location: {o.get('location', 'N/A')}, Remote: {o.get('remote_policy', 'N/A')}\n"
                f"  Benefits: {o.get('benefits', 'N/A')}\n"
                f"  Growth: {o.get('growth_potential', 'N/A')}, Team: {o.get('team_size', 'N/A')}\n"
                f"  Pros: {o.get('pros', '')}, Cons: {o.get('cons', '')}"
            )

        user_prompt = (
            f"Offers to compare:\n\n" + "\n\n".join(offers_text) + "\n\n"
            f"Weights: {json.dumps(weights)}\n\n"
            f"Score each offer on each dimension (1-10)."
        )

        try:
            raw = self.ai._call_llm(COMPARISON_SYSTEM, user_prompt)
            scores = json.loads(raw)
            if isinstance(scores, dict):
                for jid in scores:
                    for dim in DIMENSIONS:
                        if dim in scores[jid]:
                            scores[jid][dim] = max(1, min(10, int(scores[jid][dim])))
                        else:
                            scores[jid][dim] = 5
                return scores
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("Failed to parse AI offer scores: %s", exc)

        return None

    def _fallback_score_offers(self, offers: list) -> dict:
        """Score offers heuristically when AI is unavailable."""
        if not offers:
            return {}

        salaries = [o.get("base_salary", 0) for o in offers]
        max_salary = max(salaries) if salaries else 1

        scores = {}
        for o in offers:
            base = o.get("base_salary", 0) or 0
            comp_score = round((base / max_salary) * 10, 1) if max_salary else 5
            has_remote = "remote" in (o.get("remote_policy", "") or "").lower()
            has_equity = bool(o.get("equity", ""))

            scores[o["job_id"]] = {
                "compensation": min(10, comp_score),
                "growth": 6 if o.get("growth_potential") else 5,
                "work_life": 7 if has_remote else 5,
                "stability": 6,
                "benefits": 6 if o.get("benefits") else 5,
                "mission": 5,
            }

        return scores

    def _parse_annual_equity(self, equity_str: str) -> float:
        """Attempt to parse equity string into annual value."""
        if not equity_str:
            return 0

        equity_str = equity_str.lower().replace(",", "").replace("$", "")

        # Pattern: "200000/4yr" or "200000 over 4 years"
        import re
        match = re.search(r"(\d+(?:\.\d+)?)\s*(?:/|over)\s*(\d+)", equity_str)
        if match:
            total = float(match.group(1))
            years = float(match.group(2))
            return round(total / years) if years else 0

        # Pattern: just a number (assume 4-year vest)
        match = re.search(r"(\d+(?:\.\d+)?)", equity_str)
        if match:
            val = float(match.group(1))
            if val > 1000:
                return round(val / 4)

        return 0

    def _get_market_context(self, title: str, location: str) -> dict:
        """Pull salary data from the database for context."""
        try:
            row = self.state.conn.execute(
                """SELECT AVG(salary_min) as avg_min, AVG(salary_max) as avg_max,
                          COUNT(*) as samples
                   FROM salary_data
                   WHERE title LIKE ? AND location LIKE ?""",
                (f"%{title}%", f"%{location}%"),
            ).fetchone()
            if row and row["samples"] > 0:
                return {
                    "market_min": round(row["avg_min"]),
                    "market_max": round(row["avg_max"]),
                    "samples": row["samples"],
                }
        except Exception as exc:
            log.debug("No market data available: %s", exc)

        return {"market_min": 0, "market_max": 0, "samples": 0}
