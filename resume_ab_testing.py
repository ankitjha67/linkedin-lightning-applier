"""
Resume A/B Testing.

Generates 2-3 resume variants per job with different emphasis styles.
Tracks which variants get callbacks. Over time, learns which resume
style works best for which job type.
"""

import logging
import os
import random
from datetime import datetime

log = logging.getLogger("lla.ab_testing")

VARIANT_STYLES = {
    "skills_first": {
        "name": "Skills-First",
        "instruction": "Lead with a strong skills/technical competencies section at the top, "
                       "before work experience. Emphasize tools, technologies, and certifications.",
    },
    "achievement_focused": {
        "name": "Achievement-Focused",
        "instruction": "Lead every experience bullet with a quantified achievement or impact metric. "
                       "Format: 'Achieved X by doing Y, resulting in Z.' Numbers and percentages prominent.",
    },
    "narrative": {
        "name": "Narrative",
        "instruction": "Write a strong 3-4 sentence professional summary at the top that tells a career story. "
                       "Connect the dots between roles. Show career progression and trajectory.",
    },
    "keyword_dense": {
        "name": "Keyword-Dense",
        "instruction": "Maximize keyword matching with the job description. Mirror their exact terminology. "
                       "Include an 'Areas of Expertise' grid/table with key terms from the JD.",
    },
    "concise": {
        "name": "Concise",
        "instruction": "Strictly one page. Maximum 3 bullets per role. Only include the most relevant "
                       "experience for this specific job. Cut everything that doesn't directly match.",
    },
}


class ResumeABTester:
    """Generate and track resume variants for A/B testing."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        ab_cfg = cfg.get("resume_ab_testing", {})
        self.enabled = ab_cfg.get("enabled", False)
        self.variants_per_job = ab_cfg.get("variants_per_job", 2)
        self.output_dir = ab_cfg.get("output_dir",
                                     cfg.get("resume_tailoring", {}).get("output_dir",
                                                                         "data/tailored_resumes"))
        self.active_styles = ab_cfg.get("styles", list(VARIANT_STYLES.keys())[:3])

    def generate_variants(self, job_title: str, company: str, description: str,
                          match_result: dict = None) -> list[dict]:
        """
        Generate multiple resume variants for A/B testing.

        Returns list of {style, name, file_path} dicts.
        """
        if not self.enabled or not self.ai or not self.ai.enabled:
            return []

        # Select styles to test — prefer styles with less data
        styles_to_use = self._select_styles()

        variants = []
        for style_key in styles_to_use[:self.variants_per_job]:
            style = VARIANT_STYLES.get(style_key, VARIANT_STYLES["skills_first"])

            try:
                content = self._generate_variant(
                    job_title, company, description, style, match_result
                )
                if content:
                    file_path = self._save_variant(content, job_title, company, style_key)
                    if file_path:
                        variants.append({
                            "style": style_key,
                            "name": style["name"],
                            "file_path": file_path,
                        })
            except Exception as e:
                log.debug(f"Variant generation failed for {style_key}: {e}")

        return variants

    def select_best_variant(self, variants: list[dict], job_title: str = "") -> dict:
        """
        Select which variant to use based on historical performance.
        Uses Thompson sampling (Bayesian bandit) for exploration/exploitation.
        """
        if not variants:
            return {}
        if len(variants) == 1:
            return variants[0]

        # Get historical performance per style
        perf = self.state.get_variant_performance()
        perf_map = {p["variant_style"]: p for p in perf}

        best_score = -1
        best_variant = variants[0]

        for v in variants:
            style = v["style"]
            data = perf_map.get(style)

            if data and data["total_used"] > 0:
                # Thompson sampling: draw from Beta distribution
                successes = data["responses"] + 1  # +1 prior
                failures = data["total_used"] - data["responses"] + 1
                # Approximate Beta sample with random.betavariate
                score = random.betavariate(successes, failures)
            else:
                # No data — use optimistic prior (encourages exploration)
                score = random.betavariate(2, 2)

            if score > best_score:
                best_score = score
                best_variant = v

        log.info(f"   A/B: Selected '{best_variant['name']}' variant "
                f"(score: {best_score:.3f})")
        return best_variant

    def record_variant_used(self, job_id: str, variant: dict):
        """Record which variant was used for a job application."""
        if not variant:
            return
        self.state.save_resume_variant(
            job_id=job_id,
            variant_name=variant.get("name", ""),
            variant_style=variant.get("style", ""),
            file_path=variant.get("file_path", ""),
            was_used=True,
        )

    def record_variant_response(self, job_id: str, response_type: str):
        """Record that a variant got a response (for learning)."""
        positive = response_type in ("callback", "interview", "offer")
        self.state.conn.execute("""
            UPDATE resume_variants
            SET got_response=?, response_type=?
            WHERE job_id=? AND was_used=1
        """, (1 if positive else 0, response_type, job_id))
        self.state.conn.commit()

    def _select_styles(self) -> list[str]:
        """Select styles to test, preferring under-explored ones."""
        perf = self.state.get_variant_performance()
        usage_counts = {p["variant_style"]: p["total_used"] for p in perf}

        # Sort by usage (least used first) to encourage exploration
        available = list(self.active_styles)
        available.sort(key=lambda s: usage_counts.get(s, 0))
        return available

    def _generate_variant(self, job_title: str, company: str, description: str,
                          style: dict, match_result: dict = None) -> str:
        """Generate a single resume variant using AI."""
        skill_info = ""
        if match_result:
            matches = match_result.get("skill_matches", [])
            if matches:
                skill_info = f"\nKey skills to emphasize: {', '.join(matches)}"

        master_cv = self.cfg.get("ai", {}).get("cv_text", "") or \
                    (self.ai.profile_context if self.ai else "")

        system = f"""You are a professional resume writer creating a {style['name']} style resume.

STYLE INSTRUCTION: {style['instruction']}

RULES:
- Keep all factual information accurate — do NOT fabricate
- Reorder and emphasize based on this style's approach
- Use keywords from the job description naturally
- Fit on 1-2 pages maximum
- Output in plain text with section headers (ALL CAPS)

MASTER RESUME:
{master_cv[:3500]}
{skill_info}"""

        user = f"""Create a {style['name']} style resume for:
Job: {job_title} at {company}
Description: {description[:1500]}"""

        try:
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 2000
            result = self.ai._call_llm(system, user)
            self.ai.max_tokens = old_max
            return result if result and len(result) > 100 else ""
        except Exception as e:
            log.debug(f"Variant generation AI call failed: {e}")
            return ""

    def _save_variant(self, content: str, job_title: str, company: str,
                      style_key: str) -> str:
        """Save variant to file."""
        import re
        os.makedirs(self.output_dir, exist_ok=True)

        safe_company = re.sub(r'[^\w\s-]', '', company)[:25].strip()
        safe_title = re.sub(r'[^\w\s-]', '', job_title)[:25].strip()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_company}_{safe_title}_{style_key}_{ts}"

        # Try PDF first
        try:
            from fpdf import FPDF
            filepath = os.path.join(self.output_dir, f"{filename}.pdf")
            pdf = FPDF()
            pdf.set_auto_page_break(auto=True, margin=20)
            pdf.add_page()

            personal = self.cfg.get("personal", {})
            name = personal.get("full_name", "")
            if name:
                pdf.set_font("Helvetica", "B", 16)
                pdf.cell(0, 10, name, ln=True, align="C")
                pdf.ln(2)

            pdf.set_font("Helvetica", "", 10)
            for line in content.split("\n"):
                stripped = line.strip()
                if not stripped:
                    pdf.ln(2)
                elif stripped.upper() == stripped and len(stripped) > 3:
                    pdf.ln(3)
                    pdf.set_font("Helvetica", "B", 11)
                    pdf.cell(0, 7, stripped, ln=True)
                    pdf.set_font("Helvetica", "", 10)
                else:
                    pdf.multi_cell(0, 5, stripped)

            pdf.output(filepath)
            return filepath
        except ImportError:
            pass

        # Fallback to text
        filepath = os.path.join(self.output_dir, f"{filename}.txt")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    def get_performance_report(self) -> str:
        """Generate A/B testing performance report."""
        perf = self.state.get_variant_performance()
        if not perf:
            return "No A/B testing data yet."

        lines = ["Resume A/B Testing Report", "=" * 40]
        for p in perf:
            style_name = VARIANT_STYLES.get(p["variant_style"], {}).get("name", p["variant_style"])
            lines.append(
                f"  {style_name}: {p['responses']}/{p['total_used']} responses "
                f"({p['response_rate']}%)"
            )

        # Recommendation
        if perf and perf[0]["total_used"] >= 5:
            best = perf[0]
            best_name = VARIANT_STYLES.get(best["variant_style"], {}).get("name", best["variant_style"])
            lines.append(f"\nRecommendation: '{best_name}' performs best at {best['response_rate']}%")

        return "\n".join(lines)
