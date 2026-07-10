"""
Drafter–reviewer loop for application documents.

The drafter (resume_tailor / cover_letter_gen) writes; a second pass — a fresh
reviewer prompt — critiques the draft the way a hiring-focused editor would,
then the drafter revises. This catches missed keywords, weak framing, and
generic language a single pass leaves in. From MadsLorentzen/ai-job-search's
drafter-reviewer separation, adapted to our single AIAnswerer.

Also enforces the project's honesty rule: `check_honesty()` asks the model to
flag any claim in the draft not supported by the candidate's profile, so genuine
gaps stay visible and are never fabricated.

All methods degrade to no-ops (returning the input unchanged) when AI is
unavailable, so the pipeline never hard-fails.
"""

import logging

log = logging.getLogger("lla.doc_reviewer")


class DocumentReviewer:
    """Critique-and-revise a CV or cover letter with a second AI pass."""

    def __init__(self, ai):
        self.ai = ai

    def _ok(self) -> bool:
        return bool(self.ai and getattr(self.ai, "enabled", False))

    def review(self, doc_text: str, kind: str, job_title: str, company: str,
               jd_text: str) -> str:
        """Return a reviewer critique of the draft (bullet list), or "" if no AI."""
        if not self._ok() or not doc_text:
            return ""
        system = (
            "You are a meticulous hiring-side reviewer critiquing a candidate's "
            f"{kind}. Be specific and terse. Return 3-6 bullet points."
        )
        prompt = (
            f"Role: {job_title} at {company}\n\n"
            f"Job description (excerpt):\n{(jd_text or '')[:1200]}\n\n"
            f"Candidate's {kind} draft:\n{doc_text[:3000]}\n\n"
            "Critique the draft: missing keywords from the posting, weak or generic "
            "framing, anything a recruiter would skim past, and formatting/length "
            "issues. Do NOT rewrite it — only list the problems to fix."
        )
        try:
            return self.ai.generate(prompt, system=system) or ""
        except Exception as exc:
            log.debug("review failed: %s", exc)
            return ""

    def revise(self, doc_text: str, critique: str, kind: str) -> str:
        """Apply a critique to the draft and return the revised text.

        Returns the original text unchanged if AI is unavailable or the critique
        is empty.
        """
        if not self._ok() or not doc_text or not critique:
            return doc_text
        system = (
            f"You revise a candidate's {kind}. Apply the reviewer's feedback while "
            "preserving all true facts. Never invent skills or experience. Return "
            "ONLY the revised document, no preamble."
        )
        prompt = (
            f"Current {kind}:\n{doc_text}\n\n"
            f"Reviewer feedback to apply:\n{critique}\n\n"
            f"Return the improved {kind}."
        )
        try:
            revised = self.ai.generate(prompt, system=system)
            return revised.strip() if revised and revised.strip() else doc_text
        except Exception as exc:
            log.debug("revise failed: %s", exc)
            return doc_text

    def check_honesty(self, doc_text: str, profile_text: str) -> list:
        """Flag claims in the draft not supported by the candidate profile.

        Returns a list of flagged claim strings (empty if all supported / no AI).
        The honesty rule: surface unsupported claims so they can be removed —
        never stuff in skills the candidate doesn't have.
        """
        if not self._ok() or not doc_text or not profile_text:
            return []
        system = (
            "You audit a job application document for honesty. Compare every "
            "concrete claim against the candidate's profile. List ONLY claims the "
            "profile does not support, one per line. If all claims are supported, "
            "reply with exactly: NONE"
        )
        prompt = (
            f"Candidate profile (ground truth):\n{profile_text[:3000]}\n\n"
            f"Document to audit:\n{doc_text[:3000]}\n\n"
            "List unsupported claims, or NONE."
        )
        try:
            out = (self.ai.generate(prompt, system=system) or "").strip()
        except Exception as exc:
            log.debug("honesty check failed: %s", exc)
            return []
        if not out or out.upper().startswith("NONE"):
            return []
        return [ln.strip("-• \t") for ln in out.splitlines() if ln.strip()]

    def draft_review_revise(self, doc_text: str, kind: str, job_title: str,
                            company: str, jd_text: str,
                            profile_text: str = "") -> dict:
        """Full loop: review → revise → honesty check.

        Returns {revised, critique, honesty_flags}.
        """
        critique = self.review(doc_text, kind, job_title, company, jd_text)
        revised = self.revise(doc_text, critique, kind) if critique else doc_text
        flags = self.check_honesty(revised, profile_text) if profile_text else []
        return {"revised": revised, "critique": critique, "honesty_flags": flags}
