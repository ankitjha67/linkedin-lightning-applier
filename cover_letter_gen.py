"""
Cover Letter Generator.

Generates professionally formatted PDF cover letters per job.
Uploads as file attachment when ATS forms have a cover letter upload field.
Complements the inline cover letter text in ai.py.
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lla.cover_letter")


class CoverLetterGenerator:
    """Generate tailored PDF cover letters per job application."""

    def __init__(self, ai, cfg: dict):
        self.ai = ai
        self.cfg = cfg
        cl_cfg = cfg.get("cover_letter", {})
        self.enabled = cl_cfg.get("enabled", False)
        self.output_dir = cl_cfg.get("output_dir", "data/cover_letters")
        self.tone = cl_cfg.get("tone", "professional")  # professional, conversational, enthusiastic
        self.length = cl_cfg.get("length", "medium")  # short (3-4 sentences), medium (2 paragraphs), full (3-4 paragraphs)

        if self.enabled:
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)

    def generate(self, job_title: str, company: str, description: str,
                 match_result: dict = None, recruiter_name: str = "") -> str | None:
        """
        Generate a tailored cover letter and save as PDF.

        Returns file path to the generated PDF, or None.
        """
        if not self.enabled or not self.ai or not self.ai.enabled:
            return None

        log.info(f"   Generating cover letter for {job_title} @ {company}...")

        # Generate content
        content = self._generate_content(
            job_title, company, description, match_result, recruiter_name
        )
        if not content:
            return None

        # Save as PDF
        return self._save_pdf(content, job_title, company, recruiter_name)

    def _generate_content(self, job_title: str, company: str, description: str,
                          match_result: dict = None, recruiter_name: str = "") -> str | None:
        """Generate cover letter text using AI."""
        personal = self.cfg.get("personal", {})
        name = personal.get("full_name", "")

        skill_context = ""
        if match_result:
            matches = match_result.get("skill_matches", [])
            if matches:
                skill_context = f"\nYour matching skills: {', '.join(matches[:8])}"

        length_instructions = {
            "short": "Write 3-4 sentences total. Extremely concise.",
            "medium": "Write 2 short paragraphs (4-6 sentences total).",
            "full": "Write 3-4 paragraphs: opening hook, relevant experience, "
                    "why this company specifically, and a closing call to action.",
        }

        tone_instructions = {
            "professional": "Formal but warm. No jargon or buzzwords.",
            "conversational": "Friendly and genuine. Write like a confident email, not a formal letter.",
            "enthusiastic": "Show genuine excitement about the role and company. High energy but not over the top.",
        }

        system = f"""You write tailored cover letters for job applications.

RULES:
- {length_instructions.get(self.length, length_instructions['medium'])}
- {tone_instructions.get(self.tone, tone_instructions['professional'])}
- Reference specific requirements from the job description
- Mention 2-3 concrete qualifications from the candidate's background
- Never use generic phrases like "I am writing to express my interest"
- Never say "I believe I would be a great fit" — show it with specifics
- Do NOT include date, address headers, or "Dear Hiring Manager" — just the body
- Sound human, not AI-generated
{skill_context}

CANDIDATE:
{self.ai.profile_context[:3000]}"""

        addressee = f"to {recruiter_name}" if recruiter_name else ""
        user = f"""Write a cover letter {addressee} for:
Role: {job_title}
Company: {company}
Job Description: {description[:2000]}

Body text only (no headers, no sign-off):"""

        try:
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 800
            result = self.ai._call_llm(system, user)
            self.ai.max_tokens = old_max
            return result if result and len(result) > 50 else None
        except Exception as e:
            log.warning(f"Cover letter generation failed: {e}")
            return None

    def _save_pdf(self, content: str, job_title: str, company: str,
                  recruiter_name: str = "") -> str | None:
        """Save cover letter as a formatted PDF."""
        try:
            from fpdf import FPDF
        except ImportError:
            log.warning("fpdf2 not installed. Saving as text.")
            return self._save_txt(content, job_title, company)

        personal = self.cfg.get("personal", {})
        name = personal.get("full_name", "")

        safe_company = re.sub(r'[^\w\s-]', '', company)[:30].strip()
        safe_title = re.sub(r'[^\w\s-]', '', job_title)[:30].strip()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.output_dir, f"CL_{safe_company}_{safe_title}_{ts}.pdf")

        try:
            pdf = FPDF()
            pdf.set_auto_page_break(auto=True, margin=25)
            pdf.add_page()
            pdf.set_margins(25, 25, 25)

            # Header: candidate name and contact
            pdf.set_font("Helvetica", "B", 14)
            pdf.cell(0, 8, name, ln=True)
            pdf.set_font("Helvetica", "", 9)

            contact_parts = []
            if personal.get("email"):
                contact_parts.append(personal["email"])
            if personal.get("phone"):
                contact_parts.append(personal["phone"])
            if personal.get("city"):
                loc = personal["city"]
                if personal.get("country"):
                    loc += f", {personal['country']}"
                contact_parts.append(loc)
            if contact_parts:
                pdf.cell(0, 5, " | ".join(contact_parts), ln=True)

            pdf.ln(6)

            # Date
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(0, 5, datetime.now().strftime("%B %d, %Y"), ln=True)
            pdf.ln(4)

            # Addressee
            if recruiter_name:
                pdf.cell(0, 5, f"Dear {recruiter_name},", ln=True)
            else:
                pdf.cell(0, 5, f"Dear {company} Hiring Team,", ln=True)
            pdf.ln(4)

            # RE line
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 5, f"RE: {job_title}", ln=True)
            pdf.ln(4)

            # Body
            pdf.set_font("Helvetica", "", 10)
            for paragraph in content.split("\n\n"):
                paragraph = paragraph.strip()
                if paragraph:
                    pdf.multi_cell(0, 5, paragraph)
                    pdf.ln(3)

            # Sign-off
            pdf.ln(2)
            pdf.cell(0, 5, "Best regards,", ln=True)
            pdf.ln(4)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 5, name, ln=True)

            pdf.output(filepath)
            log.info(f"   Cover letter saved: {filepath}")
            return filepath

        except Exception as e:
            log.warning(f"Cover letter PDF failed: {e}")
            return self._save_txt(content, job_title, company)

    def _save_txt(self, content: str, job_title: str, company: str) -> str | None:
        """Fallback: save as text file."""
        safe_company = re.sub(r'[^\w\s-]', '', company)[:30].strip()
        safe_title = re.sub(r'[^\w\s-]', '', job_title)[:30].strip()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.output_dir, f"CL_{safe_company}_{safe_title}_{ts}.txt")

        try:
            Path(filepath).write_text(content, encoding="utf-8")
            return filepath
        except Exception as e:
            log.warning(f"Cover letter text save failed: {e}")
            return None

    def generate_inline(self, job_title: str, company: str,
                        description: str = "") -> str:
        """Generate cover letter as inline text (for textarea fields, not file upload)."""
        if not self.enabled or not self.ai or not self.ai.enabled:
            return ""

        # Use existing ai.answer_cover_letter for inline text
        return self.ai.answer_cover_letter(job_title, company, description)
