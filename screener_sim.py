"""
Screener Simulator — score your application the way the employer's AI will.

Adapted (candidate-side) from interviewstreet/hiring-agent (MIT), HackerRank's
open-source resume screener. Their agent scores incoming resumes with a strict
rubric: capped category scores with evidence, a bonus/deduction ledger, and
fairness constraints. This module inverts it: run the same style of evaluation
on YOUR resume against a JD *before* you submit, so you fix what the screener
would penalize.

Three layers, most-deterministic first:

  * ``lint_resume(text)`` — no AI. Hygiene checks ported from the rubric's
    DEDUCTIONS section: project/role links missing, generic project names,
    unquantified bullets, missing contact/profile links, length.
  * ``compute_total(evaluation)`` — pure ledger math with the same hard caps
    as the source (category maxima, bonus <= 20, clamped final score), so even
    a sloppy LLM response is clamped into a valid score.
  * ``ScreenerSimulator.simulate(...)`` — the AI pass: role-aware rubric
    (engineering vs general-professional), evidence per category, strengths and
    areas for improvement. Degrades to lint-only without AI.

Fairness rules from the source are kept verbatim in the prompt: scores must
never depend on name, gender, school, grades, or location.
"""

import json
import logging
import re

log = logging.getLogger("lla.screener")

MAX_BONUS = 20
MIN_FINAL, MAX_FINAL = -20, 120

# Category caps per rubric profile (mirrors hiring-agent's hard limits)
RUBRICS = {
    "engineering": {
        "open_source": 35,
        "self_projects": 30,
        "production": 25,
        "technical_skills": 10,
    },
    "professional": {
        "relevant_experience": 35,
        "domain_expertise": 30,
        "impact_and_outcomes": 25,
        "skills_and_tools": 10,
    },
}

_GENERIC_PROJECT_NAMES = {
    "calculator", "todo app", "to-do app", "todo list", "weather app",
    "portfolio website", "notes app", "note-taking app", "recipe app",
    "chat app", "blog website", "ecommerce website", "e-commerce website",
}

_URL_RE = re.compile(r"https?://\S+|www\.\S+|github\.com/\S+|linkedin\.com/\S+", re.I)
_NUMBER_RE = re.compile(r"\d+%|\d+\+|[$€£₹]\s?\d|\b\d{2,}\b")
_BULLET_RE = re.compile(r"^\s*(?:[-*•·]|\d+\.)\s+(.*)$", re.M)
_ENG_HINTS = re.compile(
    r"software|developer|engineer(?!ing manager)|programming|python|java(?:script)?|"
    r"backend|frontend|full[- ]stack|devops|machine learning|data scientist", re.I)


def pick_rubric(jd_text: str) -> str:
    """Choose the rubric profile from the JD: engineering vs professional."""
    return "engineering" if _ENG_HINTS.search(jd_text or "") else "professional"


# ---------------------------------------------------------------------------
# Layer 1: deterministic hygiene lint (no AI)
# ---------------------------------------------------------------------------

def lint_resume(text: str) -> dict:
    """Deterministic screener-hygiene checks derived from the rubric's deductions.

    Returns {issues: [..], stats: {..}}. Every issue is something an employer-side
    screener explicitly penalizes.
    """
    issues, text = [], (text or "")
    urls = _URL_RE.findall(text)
    low = text.lower()

    # Links: screeners cut 30-50% for unverifiable work
    if not urls:
        issues.append("no links at all — screeners score unverifiable work 30-50% lower; "
                      "add LinkedIn/GitHub/portfolio/project URLs")
    else:
        if "linkedin.com" not in low:
            issues.append("no LinkedIn URL (screeners award bonus points for it)")
        if "github.com" not in low and _ENG_HINTS.search(text):
            issues.append("technical resume without a GitHub URL")

    # Quantified impact: bullets without numbers read as unverified claims
    bullets = _BULLET_RE.findall(text)
    if bullets:
        quantified = sum(1 for b in bullets if _NUMBER_RE.search(b))
        ratio = quantified / len(bullets)
        if ratio < 0.3:
            issues.append(
                f"only {quantified}/{len(bullets)} bullets are quantified — add numbers "
                "(%, amounts, scale) so impact is verifiable")
    # Generic project names: explicit per-name deduction in the rubric
    for name in _GENERIC_PROJECT_NAMES:
        if name in low:
            issues.append(f'generic project name "{name}" — screeners deduct for '
                          "tutorial-grade projects; rename around the problem solved")

    # Contact details must be literal text
    if not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
        issues.append("no literal email address in the text")

    words = len(text.split())
    if words and words < 150:
        issues.append(f"very short ({words} words) — likely parses as an empty profile")

    return {
        "issues": issues,
        "stats": {"urls": len(urls), "bullets": len(bullets), "words": words},
    }


# ---------------------------------------------------------------------------
# Layer 2: ledger math with hard caps (pure, mirrors hiring-agent's clamps)
# ---------------------------------------------------------------------------

def compute_total(evaluation: dict, rubric: str = "professional") -> dict:
    """Clamp category scores to their caps and apply the bonus/deduction ledger.

    Accepts the rubric JSON shape {scores: {cat: {score,..}}, bonus_points:
    {total}, deductions: {total}} and returns {categories, category_total,
    bonus, deductions, final, max_possible}. Any out-of-range value from a
    sloppy LLM is clamped, never trusted.
    """
    caps = RUBRICS.get(rubric, RUBRICS["professional"])
    cats, cat_total = {}, 0
    for cat, cap in caps.items():
        raw = (((evaluation or {}).get("scores") or {}).get(cat) or {})
        score = raw.get("score", 0)
        try:
            score = max(0, min(int(score), cap))
        except (TypeError, ValueError):
            score = 0
        cats[cat] = {"score": score, "max": cap,
                     "evidence": str(raw.get("evidence", ""))[:500]}
        cat_total += score

    def _num(block, key="total"):
        try:
            return max(0, int(((evaluation or {}).get(block) or {}).get(key, 0)))
        except (TypeError, ValueError):
            return 0

    bonus = min(_num("bonus_points"), MAX_BONUS)
    deductions = _num("deductions")
    final = max(MIN_FINAL, min(cat_total + bonus - deductions, MAX_FINAL))
    return {"categories": cats, "category_total": cat_total, "bonus": bonus,
            "deductions": deductions, "final": final,
            "max_possible": sum(caps.values()) + MAX_BONUS}


# ---------------------------------------------------------------------------
# Layer 3: the AI screener pass
# ---------------------------------------------------------------------------

_FAIRNESS = """
CRITICAL FAIRNESS REQUIREMENTS — SCORES MUST NEVER DEPEND ON:
- Candidate's name, gender, or personal demographic information
- College, university, or educational institution name
- CGPA, GPA, or academic grades
- City, location, or geographical information
Base scores ONLY on skills, project/work complexity and real-world impact,
production experience, and demonstrated problem-solving.
"""

_CATEGORY_GUIDANCE = {
    "engineering": """
- open_source (0-35): contributions to OTHER people's projects score high; personal
  repos alone cap at 10; no GitHub presence scores 0-4.
- self_projects (0-30): complex real-world projects with links score high; tutorial
  projects (todo/calculator/weather) cap low; projects without links score 30-50% lower.
- production (0-25): real internship/work/production experience; founder or
  early-stage-engineer roles earn extra.
- technical_skills (0-10): breadth and evidence of problem-solving.""",
    "professional": """
- relevant_experience (0-35): directly relevant roles and responsibilities matching
  the posting's requirements; seniority and scope of ownership.
- domain_expertise (0-30): depth in the posting's domain (regulations, frameworks,
  methodologies, certifications actually held).
- impact_and_outcomes (0-25): quantified results (revenue, savings, risk reduced,
  scale); unquantified claims score low.
- skills_and_tools (0-10): tools/technologies the posting asks for, evidenced in
  the work history rather than just listed.""",
}


class ScreenerSimulator:
    """Simulate the employer-side AI screen on your resume for one JD."""

    def __init__(self, ai, cfg: dict = None):
        self.ai = ai
        self.cfg = cfg or {}
        sc = self.cfg.get("screener", {})
        self.enabled = sc.get("enabled", True)
        self.pass_score = sc.get("pass_score", 65)
        #: "off" | "warn" | "block" — how gate() treats a below-threshold score
        self.gate_mode = sc.get("gate", "warn")
        #: run the gate inside the autonomous loop too (one LLM call per job)
        self.gate_in_run = sc.get("gate_in_run", False)

    def gate(self, resume_text: str, jd_text: str, rubric: str = "") -> dict:
        """Pre-submit gate shared by `lla docs`, the loop, and the batch applier.

        Returns {action, final, threshold, reason, result} where action is:
          "pass"  — scored at/above threshold, or gating is off
          "warn"  — below threshold but gate mode is "warn" (submit anyway)
          "block" — below threshold and gate mode is "block" (skip the submit)
          "skip"  — could not score (no AI / no usable JD); never blocks
        """
        base = {"final": None, "threshold": self.pass_score, "result": None}
        if not self.enabled or self.gate_mode == "off":
            return {**base, "action": "pass", "reason": "screener gate off"}
        # A one-line description can't support a meaningful rubric evaluation.
        if not jd_text or len(jd_text.strip()) < 200:
            return {**base, "action": "skip", "reason": "job description too short to score"}
        if not (self.ai and getattr(self.ai, "enabled", False)):
            return {**base, "action": "skip", "reason": "AI unavailable"}

        result = self.simulate(resume_text, jd_text, rubric)
        base["result"] = result
        if not result["ai_used"] or not result["total"]:
            return {**base, "action": "skip", "reason": "screener evaluation failed"}
        final = result["total"]["final"]
        base["final"] = final
        if final >= self.pass_score:
            return {**base, "action": "pass",
                    "reason": f"screener {final} >= {self.pass_score}"}
        action = "block" if self.gate_mode == "block" else "warn"
        return {**base, "action": action,
                "reason": f"screener {final} < {self.pass_score}"}

    def simulate(self, resume_text: str, jd_text: str, rubric: str = "") -> dict:
        """Full simulation: lint + (with AI) rubric evaluation + ledger total.

        Returns {rubric, lint, evaluation, total, passed, ai_used}.
        """
        rubric = rubric or pick_rubric(jd_text)
        lint = lint_resume(resume_text)
        result = {"rubric": rubric, "lint": lint, "evaluation": None,
                  "total": None, "passed": None, "ai_used": False}

        if not (self.ai and getattr(self.ai, "enabled", False)):
            return result

        caps = RUBRICS[rubric]
        schema_scores = ",\n        ".join(
            f'"{c}": {{"score": 0, "max": {m}, "evidence": "string"}}'
            for c, m in caps.items())
        prompt = f"""You are an employer-side AI resume screener evaluating a candidate
against a job posting. Be strict and evidence-based.
{_FAIRNESS}
SCORING CATEGORIES:{_CATEGORY_GUIDANCE[rubric]}

BONUS (max 20 total): portfolio/LinkedIn links, recognized programs or
certifications, founder/early-stage experience, high-quality publications.
DEDUCTIONS: unverifiable claims, projects/roles without links, generic
tutorial-grade work, keyword stuffing.

JOB POSTING:
{(jd_text or "")[:2000]}

RESUME:
{(resume_text or "")[:4000]}

Respond with ONLY this JSON (all fields required):
{{
    "scores": {{
        {schema_scores}
    }},
    "bonus_points": {{"total": 0, "breakdown": "string"}},
    "deductions": {{"total": 0, "reasons": "string"}},
    "key_strengths": ["1-5 items"],
    "areas_for_improvement": ["1-3 items"]
}}"""
        try:
            raw = self.ai.generate(
                prompt, system="You are a strict, fair resume screener. Reply with only JSON.")
            evaluation = _extract_json(raw)
        except Exception as exc:
            log.debug("screener AI pass failed: %s", exc)
            evaluation = None
        if evaluation:
            result["evaluation"] = evaluation
            result["total"] = compute_total(evaluation, rubric)
            result["passed"] = result["total"]["final"] >= self.pass_score
            result["ai_used"] = True
        return result


def _extract_json(text: str):
    """Pull the first JSON object out of an LLM reply (handles ```json fences)."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = m.group(1) if m else None
    if not candidate:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start:end + 1]
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None
