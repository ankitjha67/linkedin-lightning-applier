"""
Archetype Classifier — Role Archetype Classification.

Classifies job postings into predefined archetypes (backend_engineer,
data_scientist, devops_sre, etc.) using a two-pass approach:
  1. Fast keyword-based classification
  2. AI-based classification for ambiguous cases

Results are cached in the job_archetypes table to avoid re-classification.
Archetypes drive downstream personalization in CV tailoring, story selection,
and interview prep.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

log = logging.getLogger("lla.archetype_classifier")


# Default archetypes — can be overridden via config
DEFAULT_ARCHETYPES = {
    "backend_engineer": {
        "keywords": ["backend", "server", "api", "microservices", "distributed"],
        "emphasis": "system design, scalability, APIs",
    },
    "frontend_engineer": {
        "keywords": ["frontend", "react", "angular", "vue", "ui/ux"],
        "emphasis": "user interfaces, accessibility, performance",
    },
    "fullstack": {
        "keywords": ["full-stack", "fullstack", "end-to-end"],
        "emphasis": "complete feature delivery",
    },
    "data_engineer": {
        "keywords": ["data engineer", "etl", "pipeline", "warehouse", "spark"],
        "emphasis": "data pipelines, infrastructure",
    },
    "data_scientist": {
        "keywords": ["data scientist", "machine learning", "ml", "statistical"],
        "emphasis": "models, experiments, insights",
    },
    "devops_sre": {
        "keywords": ["devops", "sre", "infrastructure", "kubernetes", "terraform"],
        "emphasis": "reliability, deployment, monitoring",
    },
    "product_manager": {
        "keywords": ["product manager", "pm", "product owner", "roadmap"],
        "emphasis": "product strategy, user research, metrics",
    },
    "engineering_manager": {
        "keywords": ["engineering manager", "tech lead", "team lead"],
        "emphasis": "team leadership, delivery, mentoring",
    },
    "ai_ml_engineer": {
        "keywords": ["ai engineer", "ml engineer", "llm", "nlp", "deep learning"],
        "emphasis": "AI systems, model deployment, evaluation",
    },
    "security": {
        "keywords": ["security", "cybersecurity", "infosec", "penetration"],
        "emphasis": "security architecture, compliance",
    },
    "risk_finance": {
        "keywords": ["risk", "credit risk", "basel", "regulatory", "compliance", "financial"],
        "emphasis": "risk models, regulatory, capital",
    },
}


class ArchetypeClassifier:
    """Classify jobs into role archetypes using keywords and AI."""

    def __init__(self, ai, cfg: dict):
        self.ai = ai
        self.cfg = cfg
        ac_cfg = cfg.get("archetype_classifier", {})
        self.enabled = ac_cfg.get("enabled", False)
        self.confidence_threshold = ac_cfg.get("confidence_threshold", 0.6)
        self.use_ai_fallback = ac_cfg.get("use_ai_fallback", True)

        # Merge default archetypes with config overrides
        self.archetypes = dict(DEFAULT_ARCHETYPES)
        custom = ac_cfg.get("archetypes", {})
        if custom:
            self.archetypes.update(custom)
            log.info(f"Loaded {len(custom)} custom archetypes")

        log.debug(f"ArchetypeClassifier initialized with {len(self.archetypes)} archetypes")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, title: str, description: str) -> dict:
        """
        Classify a job into an archetype.

        Returns:
            {
                "archetype": str,        # primary archetype key
                "confidence": float,     # 0.0 - 1.0
                "secondary": str,        # second-best archetype or ""
                "reasoning": str,        # why this archetype was chosen
            }
        """
        if not self.enabled:
            return {
                "archetype": "unknown",
                "confidence": 0.0,
                "secondary": "",
                "reasoning": "Classifier disabled",
            }

        # Pass 1: keyword-based classification (fast)
        kw_result = self._keyword_classify(title, description)

        # If keyword match is confident enough, use it directly
        if kw_result["confidence"] >= self.confidence_threshold:
            log.info(
                f"Keyword classified '{title}' as {kw_result['archetype']} "
                f"(confidence={kw_result['confidence']:.2f})"
            )
            return kw_result

        # Pass 2: AI-based classification for ambiguous cases
        if self.use_ai_fallback and self.ai and self.ai.enabled:
            ai_result = self._ai_classify(title, description)
            if ai_result["confidence"] > kw_result["confidence"]:
                log.info(
                    f"AI classified '{title}' as {ai_result['archetype']} "
                    f"(confidence={ai_result['confidence']:.2f})"
                )
                return ai_result

        # Fall back to keyword result even if low confidence
        log.info(
            f"Low-confidence classification for '{title}': "
            f"{kw_result['archetype']} ({kw_result['confidence']:.2f})"
        )
        return kw_result

    def get_archetype_info(self, archetype: str) -> dict:
        """
        Return emphasis areas and framing advice for an archetype.

        Returns:
            {
                "archetype": str,
                "emphasis": str,
                "framing_advice": str,
                "keywords": list,
            }
        """
        info = self.archetypes.get(archetype, {})
        if not info:
            return {
                "archetype": archetype,
                "emphasis": "general software engineering",
                "framing_advice": "Emphasize breadth of experience and adaptability.",
                "keywords": [],
            }

        framing = self._get_framing_advice(archetype, info.get("emphasis", ""))

        return {
            "archetype": archetype,
            "emphasis": info.get("emphasis", ""),
            "framing_advice": framing,
            "keywords": info.get("keywords", []),
        }

    def get_classification(self, job_id: str, state) -> Optional[dict]:
        """Retrieve a cached classification from the database."""
        try:
            row = state.conn.execute(
                "SELECT * FROM job_archetypes WHERE job_id = ?",
                (job_id,)
            ).fetchone()
            if not row:
                return None
            return {
                "archetype": row["archetype"],
                "confidence": row["confidence"],
                "secondary": row["secondary"],
                "classified_at": row["classified_at"],
            }
        except Exception as e:
            log.error(f"Failed to retrieve classification for {job_id}: {e}")
            return None

    def save_classification(self, job_id: str, archetype: str,
                            confidence: float, secondary: str, state) -> bool:
        """Save a classification to the database cache."""
        try:
            state.conn.execute(
                """INSERT OR REPLACE INTO job_archetypes
                   (job_id, archetype, confidence, secondary, classified_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    job_id, archetype, confidence, secondary,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            )
            state.conn.commit()
            log.debug(f"Saved classification for {job_id}: {archetype} ({confidence:.2f})")
            return True
        except Exception as e:
            log.error(f"Failed to save classification: {e}")
            return False

    # ------------------------------------------------------------------
    # Keyword classification
    # ------------------------------------------------------------------

    def _keyword_classify(self, title: str, description: str) -> dict:
        """
        Fast keyword-based classification.

        Scores each archetype by counting keyword matches in title and
        description. Title matches get 3x weight.
        """
        title_lower = title.lower()
        desc_lower = description.lower() if description else ""

        scores = {}
        for arch_name, arch_info in self.archetypes.items():
            score = 0
            matched_keywords = []
            for keyword in arch_info.get("keywords", []):
                kw_lower = keyword.lower()
                # Title match = 3x weight
                if kw_lower in title_lower:
                    score += 3
                    matched_keywords.append(f"{keyword} (title)")
                # Description match = 1x weight
                if kw_lower in desc_lower:
                    score += 1
                    matched_keywords.append(f"{keyword} (desc)")
            scores[arch_name] = {
                "score": score,
                "matched": matched_keywords,
            }

        if not scores:
            return {
                "archetype": "unknown",
                "confidence": 0.0,
                "secondary": "",
                "reasoning": "No archetypes configured",
            }

        # Sort by score descending
        ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
        best_name, best_info = ranked[0]
        best_score = best_info["score"]

        # Calculate confidence: normalize by max possible score
        max_keywords = max(
            len(a.get("keywords", [])) for a in self.archetypes.values()
        )
        max_possible = max_keywords * 4  # 3 for title + 1 for desc per keyword
        confidence = min(1.0, best_score / max_possible) if max_possible > 0 else 0.0

        # Get secondary
        secondary = ""
        if len(ranked) > 1 and ranked[1][1]["score"] > 0:
            secondary = ranked[1][0]

        # Build reasoning
        matched_str = ", ".join(best_info["matched"]) if best_info["matched"] else "no keywords"
        reasoning = f"Keyword match: {matched_str}. Score: {best_score}/{max_possible}."

        return {
            "archetype": best_name if best_score > 0 else "unknown",
            "confidence": confidence if best_score > 0 else 0.0,
            "secondary": secondary,
            "reasoning": reasoning,
        }

    # ------------------------------------------------------------------
    # AI classification
    # ------------------------------------------------------------------

    def _ai_classify(self, title: str, description: str) -> dict:
        """
        AI-based classification for ambiguous cases.

        Sends the title and description to the LLM along with the list
        of valid archetypes, and asks it to classify.
        """
        archetype_list = []
        for name, info in self.archetypes.items():
            archetype_list.append(f"- {name}: {info.get('emphasis', 'general')}")
        archetypes_text = "\n".join(archetype_list)

        system = (
            "You are a job classification expert. Classify the given job into "
            "exactly one primary archetype and optionally one secondary archetype.\n\n"
            "VALID ARCHETYPES:\n"
            f"{archetypes_text}\n\n"
            "Respond in this exact JSON format (no other text):\n"
            '{"archetype": "name", "confidence": 0.85, "secondary": "name_or_empty", '
            '"reasoning": "brief explanation"}\n\n'
            "Rules:\n"
            "- archetype MUST be one of the valid names listed above\n"
            "- confidence is 0.0 to 1.0\n"
            "- secondary can be empty string if no clear secondary\n"
            "- reasoning should be 1-2 sentences"
        )
        user = (
            f"Job Title: {title}\n\n"
            f"Job Description (first 2000 chars):\n{description[:2000]}"
        )

        try:
            response = self.ai._call_llm(system, user)
            if not response:
                return self._empty_result("AI returned empty response")

            # Parse JSON
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
                cleaned = re.sub(r'\s*```$', '', cleaned)

            data = json.loads(cleaned)

            archetype = data.get("archetype", "unknown")
            # Validate archetype is in our list
            if archetype not in self.archetypes:
                # Try fuzzy match
                archetype = self._fuzzy_match_archetype(archetype)

            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            secondary = data.get("secondary", "")
            if secondary and secondary not in self.archetypes:
                secondary = self._fuzzy_match_archetype(secondary)

            reasoning = data.get("reasoning", "AI classification")

            return {
                "archetype": archetype,
                "confidence": confidence,
                "secondary": secondary,
                "reasoning": reasoning,
            }
        except json.JSONDecodeError as e:
            log.warning(f"AI classification returned invalid JSON: {e}")
            return self._empty_result("AI returned invalid JSON")
        except Exception as e:
            log.warning(f"AI classification failed: {e}")
            return self._empty_result(f"AI error: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fuzzy_match_archetype(self, name: str) -> str:
        """Try to match a non-exact archetype name to a valid one."""
        name_lower = name.lower().replace(" ", "_").replace("-", "_")

        # Direct match after normalization
        if name_lower in self.archetypes:
            return name_lower

        # Partial match
        for arch_name in self.archetypes:
            if name_lower in arch_name or arch_name in name_lower:
                return arch_name

        return "unknown"

    def _get_framing_advice(self, archetype: str, emphasis: str) -> str:
        """Generate framing advice for a given archetype."""
        advice_map = {
            "backend_engineer": (
                "Lead with system design and scalability stories. "
                "Quantify throughput, latency improvements, and uptime metrics. "
                "Mention distributed systems patterns by name."
            ),
            "frontend_engineer": (
                "Highlight user-facing impact, accessibility compliance, "
                "and performance budgets. Show design system contributions "
                "and cross-browser testing experience."
            ),
            "fullstack": (
                "Demonstrate end-to-end ownership from DB schema to UI. "
                "Show you can context-switch and ship complete features. "
                "Emphasize product thinking alongside technical depth."
            ),
            "data_engineer": (
                "Focus on pipeline reliability, data quality, and scale. "
                "Mention specific tools (Spark, Airflow, dbt) and volumes processed. "
                "Show understanding of data governance."
            ),
            "data_scientist": (
                "Lead with business impact of models. Show experiment design rigor, "
                "A/B test methodology, and stakeholder communication. "
                "Balance technical depth with business acumen."
            ),
            "devops_sre": (
                "Quantify uptime improvements, deployment frequency, MTTR. "
                "Show IaC experience, monitoring philosophy, and incident response. "
                "Emphasize automation and toil reduction."
            ),
            "product_manager": (
                "Lead with user outcomes and business metrics. Show prioritization "
                "frameworks, stakeholder management, and data-driven decisions. "
                "Demonstrate technical literacy without over-engineering."
            ),
            "engineering_manager": (
                "Balance people stories with delivery metrics. Show hiring, "
                "mentoring, and performance management experience. "
                "Demonstrate strategic thinking and cross-team coordination."
            ),
            "ai_ml_engineer": (
                "Highlight model deployment and production ML experience. "
                "Show evaluation methodology, MLOps practices, and "
                "understanding of LLM capabilities and limitations."
            ),
            "security": (
                "Lead with threat modeling and risk assessment experience. "
                "Show compliance knowledge (SOC2, ISO27001, GDPR). "
                "Demonstrate both offensive and defensive security thinking."
            ),
            "risk_finance": (
                "Emphasize regulatory knowledge and quantitative rigor. "
                "Show model validation, stress testing, and audit experience. "
                "Demonstrate understanding of business context in financial services."
            ),
        }
        return advice_map.get(archetype, f"Emphasize {emphasis}. Show depth and relevant impact.")

    def _empty_result(self, reasoning: str) -> dict:
        """Return an empty classification result."""
        return {
            "archetype": "unknown",
            "confidence": 0.0,
            "secondary": "",
            "reasoning": reasoning,
        }
