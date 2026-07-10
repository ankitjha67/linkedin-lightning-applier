"""
Application-documents orchestrator.

One code path that both the CLI (`lla docs`) and the autonomous loop (main.py)
use to produce a tailored LaTeX CV + cover letter for a job, run the ATS
text-layer check, and (optionally) the drafter-reviewer critique + honesty check.

Ties together latex_docs, ats_pdf_check and doc_reviewer. Everything degrades
gracefully: no AI → template content; no LaTeX engine → `.tex` only; no
pdf-text tools → ATS check runs on the plain-text content.
"""

import logging
import re

from ats_pdf_check import ats_report, extract_pdf_text
from doc_reviewer import DocumentReviewer
from latex_docs import LaTeXDocsBuilder, available_engine

log = logging.getLogger("lla.application_docs")


def _slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:maxlen] or "job"


class ApplicationDocsGenerator:
    """Generate + verify tailored application documents for one job."""

    def __init__(self, ai, cfg: dict):
        self.ai = ai
        self.cfg = cfg
        self.personal = cfg.get("personal", {})
        dc = cfg.get("latex_docs", {})
        self.enabled = dc.get("enabled", False)
        # Off by default in the autonomous loop — opt in explicitly.
        self.auto_generate = dc.get("auto_generate", False)
        self.min_coverage = dc.get("min_coverage", 60)
        self.cv_text = cfg.get("ai", {}).get("cv_text", "") or cfg.get("ai", {}).get("cv_summary", "")
        self.builder = LaTeXDocsBuilder(cfg)
        self.reviewer = DocumentReviewer(ai)

    # ------------------------------------------------------------------
    # Content tailoring (AI, with graceful fallback)
    # ------------------------------------------------------------------

    def tailor_content(self, job_title: str, company: str, jd: str):
        """Return (profile_statement, competencies:list, cover_paragraphs:list)."""
        if not (self.ai and getattr(self.ai, "enabled", False)):
            prof = (self.cv_text.strip().split("\n")[0] if self.cv_text
                    else f"Candidate applying for {job_title}.")
            return prof, ["See attached CV for full competencies."], [
                f"I am writing to apply for the {job_title} position at {company}.",
                "My background aligns with the requirements outlined in the posting.",
                "I would welcome the opportunity to discuss my fit further.",
            ]
        cv = self.cv_text[:2000]
        try:
            prof = self.ai.generate(
                f"Write a 3-4 line CV profile statement for a {job_title} application "
                f"at {company}, grounded ONLY in this candidate background:\n{cv}\n\n"
                f"Job posting:\n{jd[:1200]}\nReturn only the statement.",
                system="You write concise, truthful CV profile statements. Never invent facts.")
            comp_raw = self.ai.generate(
                f"List 5 core competencies (one per line, no numbering) for this "
                f"candidate tailored to the posting. Ground them in the background; do "
                f"not invent.\nBackground:\n{cv}\nPosting:\n{jd[:1000]}",
                system="You extract truthful, posting-relevant competencies.")
            competencies = [c.strip("-• \t") for c in (comp_raw or "").splitlines()
                            if c.strip()][:6]
            cover_raw = self.ai.generate(
                f"Write 3 short cover-letter paragraphs for the {job_title} role at "
                f"{company}. Truthful, specific, forward-looking. Ground in:\n{cv}\n"
                f"Posting:\n{jd[:1200]}\nReturn paragraphs separated by blank lines.",
                system="You write authentic, non-generic cover letters. Never fabricate.")
            paras = [p.strip() for p in (cover_raw or "").split("\n\n") if p.strip()][:4]
            return (prof or f"Candidate for {job_title}.",
                    competencies or ["See CV."],
                    paras or [f"I am applying for {job_title} at {company}."])
        except Exception as exc:
            log.debug("tailor_content failed: %s", exc)
            return (f"Candidate for {job_title}.", ["See CV."],
                    [f"I am applying for {job_title} at {company}."])

    # ------------------------------------------------------------------
    # Full generate + verify
    # ------------------------------------------------------------------

    def generate(self, job_title: str, company: str, jd: str,
                 match_result: dict = None, review: bool = True) -> dict:
        """Build CV + cover letter, ATS-check, and (optionally) review.

        Returns {cv_tex, cv_pdf, cover_tex, cover_pdf, engine, ats,
                 critique, honesty_flags}.
        """
        profile, competencies, cover_paras = self.tailor_content(job_title, company, jd)
        base = f"{_slug(company)}_{_slug(job_title)}"

        cv = self.builder.build_cv(
            {"title": job_title, "profile": profile, "competencies": competencies},
            basename=f"{base}_cv")
        cover = self.builder.build_cover_letter(
            {"company": company, "opening": f"Dear {company} Hiring Team,",
             "paragraphs": cover_paras}, basename=f"{base}_cover")

        cv_plain = "\n".join([
            f"{self.personal.get('full_name', '')} {self.personal.get('email', '')} "
            f"{self.personal.get('phone', '')}",
            profile, "\n".join(competencies), self.cv_text,
        ])
        pdf_text = extract_pdf_text(cv["pdf"]) if cv.get("pdf") else None
        report = ats_report(pdf_text or cv_plain, jd, self.personal, self.min_coverage)

        result = {
            "cv_tex": cv["tex"], "cv_pdf": cv.get("pdf"),
            "cover_tex": cover["tex"], "cover_pdf": cover.get("pdf"),
            "engine": available_engine(self.builder.engine),
            "ats": report, "critique": "", "honesty_flags": [],
        }
        if review and self.ai and getattr(self.ai, "enabled", False):
            loop = self.reviewer.draft_review_revise(
                "\n\n".join(cover_paras), "cover letter", job_title, company, jd,
                self.cv_text)
            result["critique"] = loop["critique"]
            result["honesty_flags"] = loop["honesty_flags"]
        return result
