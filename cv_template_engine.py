"""
ATS-Optimized CV Template Engine.

Generates tailored, ATS-friendly CVs from an HTML template rendered to PDF.
Extracts keywords from job descriptions and ensures they appear in the CV.
Supports multiple PDF backends: playwright, weasyprint, fpdf2.
"""

import json
import logging
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lla.cv_template")

# Built-in ATS-friendly HTML/CSS template (~70 lines)
DEFAULT_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{FULL_NAME}} - CV</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.45;
    color: #222;
    max-width: 800px;
    margin: 0 auto;
    padding: 40px 50px;
  }
  h1 { font-size: 22pt; margin-bottom: 2px; color: #111; }
  .contact { font-size: 9.5pt; color: #555; margin-bottom: 18px; }
  h2 {
    font-size: 12pt;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    border-bottom: 1.5px solid #333;
    padding-bottom: 3px;
    margin-top: 18px;
    margin-bottom: 8px;
    color: #111;
  }
  .entry { margin-bottom: 10px; }
  .entry-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }
  .entry-title { font-weight: bold; font-size: 10.5pt; }
  .entry-date { font-size: 9.5pt; color: #555; white-space: nowrap; }
  .entry-subtitle { font-size: 10pt; color: #444; font-style: italic; }
  ul { margin-left: 18px; margin-top: 3px; }
  li { margin-bottom: 2px; font-size: 10pt; }
  .skills-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 4px;
  }
  .skill-tag {
    background: #f0f0f0;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 9.5pt;
  }
  .summary { font-size: 10.5pt; margin-bottom: 6px; }
</style>
</head>
<body>

<h1>{{FULL_NAME}}</h1>
<div class="contact">{{CONTACT_LINE}}</div>

<h2>Summary</h2>
<p class="summary">{{SUMMARY}}</p>

<h2>Experience</h2>
{{EXPERIENCE}}

<h2>Education</h2>
{{EDUCATION}}

<h2>Skills</h2>
<div class="skills-grid">
{{SKILLS}}
</div>

<h2>Certifications</h2>
{{CERTIFICATIONS}}

</body>
</html>"""


class CVTemplateEngine:
    """Generate ATS-optimized, tailored CVs as HTML and PDF."""

    def __init__(self, ai, cfg: dict):
        self.ai = ai
        self.cfg = cfg
        cv_cfg = cfg.get("cv_template", {})
        self.enabled = cv_cfg.get("enabled", False)
        self.output_dir = cv_cfg.get("output_dir", "data/cvs")
        self.template_path = cv_cfg.get("template_path", "templates/cv-template.html")

        if self.enabled:
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)

    # ── Public API ───────────────────────────────────────────────

    def generate_cv(self, job_title: str, company: str, description: str,
                    match_result: dict = None, archetype: str = None) -> str | None:
        """
        Full pipeline: tailor content, inject keywords, render HTML, convert to PDF.

        Returns path to the generated PDF, or None on failure.
        """
        if not self.enabled:
            log.debug("CV template engine disabled")
            return None

        if not self.ai or not self.ai.enabled:
            log.warning("AI unavailable, cannot generate tailored CV")
            return None

        log.info(f"Generating tailored CV for {job_title} @ {company}")

        # Step 1: AI-tailor content for this role
        content = self._tailor_content(job_title, company, description, archetype)
        if not content:
            log.error("Failed to tailor CV content")
            return None

        # Step 2: Render HTML template
        html = self._render_html(content)

        # Step 3: Inject keywords from JD
        html = self._inject_keywords(html, description)

        # Step 4: Save HTML
        safe_company = re.sub(r'[^\w\-]', '_', company)[:30]
        safe_title = re.sub(r'[^\w\-]', '_', job_title)[:30]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        basename = f"cv_{safe_company}_{safe_title}_{timestamp}"

        html_path = os.path.join(self.output_dir, f"{basename}.html")
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            log.info(f"  HTML saved: {html_path}")
        except Exception as e:
            log.error(f"Failed to save HTML: {e}")
            return None

        # Step 5: Convert to PDF
        pdf_path = os.path.join(self.output_dir, f"{basename}.pdf")
        if self._html_to_pdf(html_path, pdf_path):
            log.info(f"  PDF generated: {pdf_path}")
            return pdf_path

        log.warning("PDF conversion failed; returning HTML path instead")
        return html_path

    def get_keyword_density(self, cv_text: str, jd_text: str) -> dict:
        """
        Calculate what percentage of JD keywords appear in the CV.

        Returns dict with 'total_keywords', 'matched', 'missing',
        'density_pct', and lists of matched/missing keywords.
        """
        jd_keywords = self._extract_keywords_from_text(jd_text)
        cv_lower = cv_text.lower()

        matched = []
        missing = []
        for kw in jd_keywords:
            if kw.lower() in cv_lower:
                matched.append(kw)
            else:
                missing.append(kw)

        total = len(jd_keywords)
        density = (len(matched) / total * 100) if total > 0 else 0.0

        return {
            "total_keywords": total,
            "matched_count": len(matched),
            "missing_count": len(missing),
            "density_pct": round(density, 1),
            "matched": matched,
            "missing": missing,
        }

    # ── Content Tailoring ────────────────────────────────────────

    def _tailor_content(self, job_title: str, company: str,
                        description: str, archetype: str = None) -> dict:
        """
        Use AI to rewrite CV sections tailored for a specific role.

        Returns dict with keys: full_name, contact_line, summary,
        experience, education, skills, certifications.
        """
        personal = self.cfg.get("personal", {})
        cv_text = self.cfg.get("ai", {}).get("cv_text", "")

        system_prompt = (
            "You are an expert CV writer specialising in ATS-optimised resumes "
            "for software engineers. Rewrite the candidate's CV sections to be "
            "tailored for the specific job. Use strong action verbs, quantify "
            "achievements, and mirror language from the job description. "
            "Keep it concise and factual - no fabrication.\n\n"
            "Return ONLY valid JSON with these keys:\n"
            "  full_name: string\n"
            "  contact_line: string (email | location | linkedin)\n"
            "  summary: string (3-4 sentences tailored to this role)\n"
            "  experience: list of objects with keys: title, company, dates, bullets (list of strings)\n"
            "  education: list of objects with keys: degree, school, dates\n"
            "  skills: list of strings (technical skills, prioritised for this role)\n"
            "  certifications: list of strings"
        )

        archetype_context = f"\nRole archetype: {archetype}" if archetype else ""
        user_prompt = (
            f"Job title: {job_title}\n"
            f"Company: {company}\n"
            f"{archetype_context}\n\n"
            f"Job description:\n{description[:3000]}\n\n"
            f"Candidate's current CV:\n{cv_text[:3000]}\n\n"
            f"Candidate name: {personal.get('full_name', '')}\n"
            f"Email: {personal.get('email', '')}\n"
            f"Location: {personal.get('location', '')}\n\n"
            "Rewrite the CV sections to maximise ATS match for this role."
        )

        try:
            raw = self.ai._call_llm(system_prompt, user_prompt)
            return self._parse_json(raw)
        except Exception as e:
            log.error(f"Failed to tailor CV content: {e}")
            return {}

    # ── Keyword Injection ────────────────────────────────────────

    def _inject_keywords(self, html: str, job_description: str) -> str:
        """
        Extract top keywords from JD and ensure they appear in the CV HTML.

        If critical keywords are missing from the body, they are added to the
        skills section to improve ATS match rate.
        """
        jd_keywords = self._extract_keywords_from_text(job_description)
        if not jd_keywords:
            return html

        html_lower = html.lower()
        missing = [kw for kw in jd_keywords if kw.lower() not in html_lower]

        if not missing:
            log.debug("All JD keywords already present in CV")
            return html

        log.info(f"  Injecting {len(missing)} missing keywords into skills section")

        # Build skill tags for missing keywords
        extra_tags = "".join(
            f'<span class="skill-tag">{kw}</span>\n' for kw in missing[:15]
        )

        # Insert before the closing skills div
        if "</div>" in html and "skills-grid" in html:
            # Find the skills-grid div closing tag
            idx = html.find("skills-grid")
            if idx != -1:
                # Find the corresponding closing </div>
                close_idx = html.find("</div>", idx)
                if close_idx != -1:
                    html = html[:close_idx] + extra_tags + html[close_idx:]

        return html

    def _extract_keywords_from_text(self, text: str) -> list[str]:
        """
        Extract important technical keywords from text.

        Uses pattern matching for common tech terms and frameworks.
        """
        if not text:
            return []

        # Common tech keyword patterns
        tech_patterns = [
            r'\b(?:Python|Java|JavaScript|TypeScript|Go|Rust|C\+\+|Ruby|Kotlin|Swift|Scala)\b',
            r'\b(?:React|Angular|Vue|Next\.js|Node\.js|Django|Flask|Spring|FastAPI)\b',
            r'\b(?:AWS|Azure|GCP|Docker|Kubernetes|Terraform|Jenkins|CircleCI)\b',
            r'\b(?:PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch|DynamoDB|Cassandra)\b',
            r'\b(?:REST|GraphQL|gRPC|microservices|API|CI/CD|DevOps|MLOps)\b',
            r'\b(?:machine learning|deep learning|NLP|computer vision|LLM|RAG)\b',
            r'\b(?:Agile|Scrum|Kanban|TDD|BDD|pair programming)\b',
            r'\b(?:Git|Linux|SQL|NoSQL|Kafka|RabbitMQ|Spark|Airflow)\b',
        ]

        found = set()
        for pattern in tech_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for m in matches:
                found.add(m.strip())

        # Also use AI if available for more thorough extraction
        if self.ai and self.ai.enabled and len(found) < 5:
            try:
                ai_keywords = self.ai.extract_skills_from_jd(text)
                for kw in ai_keywords:
                    found.add(kw.strip())
            except Exception:
                pass

        return sorted(found)

    # ── HTML Rendering ───────────────────────────────────────────

    def _render_html(self, content: dict, template_path: str = None) -> str:
        """Fill the HTML template with tailored content."""
        template = self._get_template(template_path)

        # Basic fields
        html = template.replace("{{FULL_NAME}}", content.get("full_name", ""))
        html = html.replace("{{CONTACT_LINE}}", content.get("contact_line", ""))
        html = html.replace("{{SUMMARY}}", content.get("summary", ""))

        # Experience section
        experience_html = ""
        for exp in content.get("experience", []):
            bullets_html = ""
            for bullet in exp.get("bullets", []):
                bullets_html += f"  <li>{bullet}</li>\n"

            experience_html += (
                f'<div class="entry">\n'
                f'  <div class="entry-header">\n'
                f'    <span class="entry-title">{exp.get("title", "")}</span>\n'
                f'    <span class="entry-date">{exp.get("dates", "")}</span>\n'
                f'  </div>\n'
                f'  <div class="entry-subtitle">{exp.get("company", "")}</div>\n'
                f'  <ul>\n{bullets_html}  </ul>\n'
                f'</div>\n'
            )
        html = html.replace("{{EXPERIENCE}}", experience_html)

        # Education section
        education_html = ""
        for edu in content.get("education", []):
            education_html += (
                f'<div class="entry">\n'
                f'  <div class="entry-header">\n'
                f'    <span class="entry-title">{edu.get("degree", "")}</span>\n'
                f'    <span class="entry-date">{edu.get("dates", "")}</span>\n'
                f'  </div>\n'
                f'  <div class="entry-subtitle">{edu.get("school", "")}</div>\n'
                f'</div>\n'
            )
        html = html.replace("{{EDUCATION}}", education_html)

        # Skills section
        skills_html = ""
        for skill in content.get("skills", []):
            skills_html += f'<span class="skill-tag">{skill}</span>\n'
        html = html.replace("{{SKILLS}}", skills_html)

        # Certifications section
        certs_html = ""
        for cert in content.get("certifications", []):
            certs_html += f'<div class="entry"><span class="entry-title">{cert}</span></div>\n'
        html = html.replace("{{CERTIFICATIONS}}", certs_html)

        return html

    def _get_template(self, template_path: str = None) -> str:
        """
        Load HTML template from file or use built-in default.

        Tries: provided path -> config path -> built-in default.
        """
        paths_to_try = [
            template_path,
            self.template_path,
        ]

        for path in paths_to_try:
            if path and os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        template = f.read()
                    log.debug(f"Loaded template from {path}")
                    return template
                except Exception as e:
                    log.warning(f"Failed to load template from {path}: {e}")

        log.debug("Using built-in default HTML template")
        return DEFAULT_HTML_TEMPLATE

    # ── PDF Conversion ───────────────────────────────────────────

    def _html_to_pdf(self, html_path: str, pdf_path: str = None) -> bool:
        """
        Convert HTML file to PDF. Tries backends in order:
        1. Playwright (best quality)
        2. WeasyPrint
        3. fpdf2 (basic fallback)

        Returns True if successful.
        """
        if not pdf_path:
            pdf_path = html_path.replace(".html", ".pdf")

        # Try Playwright first
        if self._try_playwright(html_path, pdf_path):
            return True

        # Try WeasyPrint
        if self._try_weasyprint(html_path, pdf_path):
            return True

        # Try fpdf2
        if self._try_fpdf2(html_path, pdf_path):
            return True

        log.warning("All PDF backends failed")
        return False

    def _try_playwright(self, html_path: str, pdf_path: str) -> bool:
        """Attempt PDF generation via Playwright."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"file://{os.path.abspath(html_path)}")
                page.pdf(path=pdf_path, format="A4",
                         margin={"top": "20mm", "bottom": "20mm",
                                 "left": "15mm", "right": "15mm"})
                browser.close()
            log.debug("PDF generated via Playwright")
            return True
        except ImportError:
            log.debug("Playwright not available")
            return False
        except Exception as e:
            log.warning(f"Playwright PDF failed: {e}")
            return False

    def _try_weasyprint(self, html_path: str, pdf_path: str) -> bool:
        """Attempt PDF generation via WeasyPrint."""
        try:
            import weasyprint
            doc = weasyprint.HTML(filename=html_path)
            doc.write_pdf(pdf_path)
            log.debug("PDF generated via WeasyPrint")
            return True
        except ImportError:
            log.debug("WeasyPrint not available")
            return False
        except Exception as e:
            log.warning(f"WeasyPrint PDF failed: {e}")
            return False

    def _try_fpdf2(self, html_path: str, pdf_path: str) -> bool:
        """Attempt basic PDF generation via fpdf2 (text-only fallback)."""
        try:
            from fpdf import FPDF

            # Read the HTML and strip tags for plain text
            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            text = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', '\n', text)
            text = re.sub(r'\n{3,}', '\n\n', text).strip()

            pdf = FPDF()
            pdf.add_page()
            pdf.set_auto_page_break(auto=True, margin=20)
            pdf.set_font("Helvetica", size=10)

            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    pdf.ln(4)
                    continue

                # Detect section headers (all caps)
                if line.isupper() and len(line) < 40:
                    pdf.set_font("Helvetica", "B", 12)
                    pdf.ln(6)
                    pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
                    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
                    pdf.ln(3)
                    pdf.set_font("Helvetica", size=10)
                else:
                    pdf.multi_cell(0, 5, line)

            pdf.output(pdf_path)
            log.debug("PDF generated via fpdf2 (text fallback)")
            return True
        except ImportError:
            log.debug("fpdf2 not available")
            return False
        except Exception as e:
            log.warning(f"fpdf2 PDF failed: {e}")
            return False

    # ── Helpers ──────────────────────────────────────────────────

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
            log.warning(f"Failed to parse JSON: {text[:120]}")
            return {}
