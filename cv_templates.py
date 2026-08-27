"""Add and check your own CV templates.

    lla add-template mine.html            # check it, then install it
    lla add-template --list               # what is installed
    lla add-template --show-default > mine.html   # start from the built-in one

The CV engine renders an HTML template by substituting `{{PLACEHOLDER}}`
tokens. Swapping in your own is the difference between everyone's CV looking
identical and yours looking like yours — but a template with a mistyped
placeholder fails in the worst possible way: it renders, it looks fine, and the
section is simply missing. `{{EXPERINCE}}` produces a CV with no jobs on it,
and nothing anywhere says so.

So a template is checked before it is installed:

  * every placeholder it uses is one the engine actually substitutes — a
    misspelling is caught here rather than discovered by an employer;
  * the placeholders that carry the CV's substance are present;
  * it is HTML the renderer can work with;
  * it renders with sample content, and the sample content comes out the
    other side.

Nothing is installed unless all of that passes.
"""

import re
import shutil
from pathlib import Path

# The tokens cv_template_engine._render_html substitutes. This is the contract.
KNOWN_PLACEHOLDERS = {
    "FULL_NAME", "CONTACT_LINE", "SUMMARY", "EXPERIENCE",
    "EDUCATION", "SKILLS", "CERTIFICATIONS",
}

# Without these a CV is not a CV. The rest are optional sections.
REQUIRED_PLACEHOLDERS = {"FULL_NAME", "EXPERIENCE"}

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

DEFAULT_TEMPLATE_PATH = "templates/cv-template.html"

SAMPLE = {
    "full_name": "Ada Lovelace",
    "contact_line": "ada@example.com · +44 7000 000000 · London",
    "summary": "Analytical engine specialist with a decade of experience.",
    "experience": [{"title": "Senior Analyst", "company": "Analytical Engines Ltd",
                    "dates": "2021 - 2024",
                    "bullets": ["Wrote the first algorithm", "Led a team of six"]}],
    "education": [{"degree": "BSc Mathematics", "school": "UCL",
                   "dates": "2014 - 2018"}],
    "skills": ["Mathematics", "Algorithms"],
    "certifications": ["Fellow of the Royal Society"],
}


class TemplateReport:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.used = set()

    @property
    def ok(self):
        return not self.errors

    def error(self, msg, fix=""):
        self.errors.append((msg, fix))

    def warn(self, msg, fix=""):
        self.warnings.append((msg, fix))


def placeholders_in(html: str) -> set:
    return {m.group(1).upper() for m in PLACEHOLDER_RE.finditer(html or "")}


def check_template(html: str) -> TemplateReport:
    """Everything wrong with a template, before it can do damage."""
    report = TemplateReport()
    if not (html or "").strip():
        report.error("the template is empty")
        return report

    report.used = placeholders_in(html)

    unknown = report.used - KNOWN_PLACEHOLDERS
    for name in sorted(unknown):
        closest = _closest(name, KNOWN_PLACEHOLDERS)
        hint = f" — did you mean {{{{{closest}}}}}?" if closest else ""
        report.error(
            f"{{{{{name}}}}} is not a placeholder the engine fills{hint}",
            "it would be left in the finished CV as literal text")

    missing = REQUIRED_PLACEHOLDERS - report.used
    for name in sorted(missing):
        report.error(f"{{{{{name}}}}} is missing",
                     "without it that content never reaches the page")

    optional_missing = (KNOWN_PLACEHOLDERS - REQUIRED_PLACEHOLDERS) - report.used
    for name in sorted(optional_missing):
        report.warn(f"{{{{{name}}}}} is not used — that section will be dropped",
                    "add it if you want the section")

    low = html.lower()
    if "<html" not in low and "<!doctype" not in low:
        report.warn("no <html> or <!doctype> — some PDF backends need a full document",
                    "wrap it in <html><body>…</body></html>")
    if "<body" not in low:
        report.warn("no <body> element", "some renderers require one")

    for tag in ("script", "iframe", "object"):
        if re.search(rf"<{tag}\b", low):
            report.error(f"contains <{tag}> — it will not render in a PDF and "
                         "may be stripped or blocked",
                         f"remove the <{tag}> block")

    if re.search(r'(src|href)\s*=\s*["\']https?://', html, re.I):
        report.warn("loads a remote image or stylesheet",
                    "PDF backends often render offline — inline the asset or "
                    "the CV will be missing it")
    return report


def _closest(name: str, options) -> str:
    """The known placeholder a typo most likely meant."""
    import difflib
    matches = difflib.get_close_matches(name, sorted(options), n=1, cutoff=0.6)
    return matches[0] if matches else ""


def _render_simple(html: str) -> str:
    """Fill a template with sample content, the way the engine fills it.

    This mirrors cv_template_engine._render_html deliberately rather than
    calling it: that method reads the *configured* template, which is not the
    one being checked. Its job here is only to prove that every placeholder in
    this file is substitutable.
    """
    out = html or ""
    out = out.replace("{{FULL_NAME}}", SAMPLE["full_name"])
    out = out.replace("{{CONTACT_LINE}}", SAMPLE["contact_line"])
    out = out.replace("{{SUMMARY}}", SAMPLE["summary"])
    exp = "".join(
        f"<div class='entry'><span>{e['title']}</span><span>{e['dates']}</span>"
        f"<div>{e['company']}</div><ul>"
        + "".join(f"<li>{b}</li>" for b in e["bullets"]) + "</ul></div>"
        for e in SAMPLE["experience"])
    out = out.replace("{{EXPERIENCE}}", exp)
    edu = "".join(f"<div>{e['degree']} — {e['school']} ({e['dates']})</div>"
                  for e in SAMPLE["education"])
    out = out.replace("{{EDUCATION}}", edu)
    out = out.replace("{{SKILLS}}", ", ".join(SAMPLE["skills"]))
    out = out.replace("{{CERTIFICATIONS}}", ", ".join(SAMPLE["certifications"]))
    return out


def check_renders(html: str) -> tuple:
    """(ok, message). Does sample content actually come out the other side?"""
    rendered = _render_simple(html)
    leftover = placeholders_in(rendered)
    if leftover:
        return False, ("these placeholders survived substitution: "
                       + ", ".join(f"{{{{{p}}}}}" for p in sorted(leftover)))
    if SAMPLE["full_name"] not in rendered:
        return False, "the sample name did not appear in the rendered output"
    if "Wrote the first algorithm" not in rendered:
        return False, "the sample experience bullets did not appear"
    return True, "renders with sample content"


def install(src: str, dest: str = DEFAULT_TEMPLATE_PATH) -> tuple:
    """Copy a checked template into place. (ok, message)."""
    source = Path(src)
    if not source.exists():
        return False, f"{src} does not exist"
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(target, backup)
        shutil.copy2(source, target)
        return True, f"installed at {target} (previous version saved as {backup})"
    shutil.copy2(source, target)
    return True, f"installed at {target}"


def installed_templates(directory: str = "templates") -> list:
    d = Path(directory)
    if not d.exists():
        return []
    return sorted(str(p) for p in d.glob("*.html"))


def default_template() -> str:
    from cv_template_engine import DEFAULT_HTML_TEMPLATE
    return DEFAULT_HTML_TEMPLATE


def format_report(report: TemplateReport, render_msg: str = "") -> str:
    lines = [""]
    if report.used:
        lines.append("  Placeholders used: "
                     + ", ".join(sorted(report.used)))
    for msg, fix in report.errors:
        lines.append(f"  ✗ {msg}")
        if fix:
            lines.append(f"      {fix}")
    for msg, fix in report.warnings:
        lines.append(f"  ⚠ {msg}")
        if fix:
            lines.append(f"      {fix}")
    if render_msg:
        lines.append(f"  {'✓' if report.ok else '·'} {render_msg}")
    if report.ok and not report.warnings:
        lines.append("\n  ✅ the template is sound")
    elif report.ok:
        lines.append(f"\n  Usable, with {len(report.warnings)} thing(s) to consider.")
    else:
        lines.append(f"\n  {len(report.errors)} problem(s) — not installed.")
    return "\n".join(lines)
