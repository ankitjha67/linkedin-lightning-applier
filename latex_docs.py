"""
LaTeX document engine — typeset CV + cover letter (moderncv).

Produces professional, ATS-friendly PDFs a cut above the fpdf2 output: a
`moderncv` CV and a matching cover letter, generated from your profile + the
AI-tailored content for a specific job, then compiled if a LaTeX engine is
installed. Inspired by MadsLorentzen/ai-job-search (MIT) — reworked to use the
standard `moderncv` CTAN package so nothing has to be bundled (no vendored
fonts / custom .cls).

Design:
  * Rendering is pure string templating — `render_cv_tex()` / `render_cover_tex()`
    take a plain dict and return `.tex`. Fully testable with no binaries.
  * `available_engine()` / `compile_tex()` shell out to lualatex/xelatex/pdflatex
    only if present; otherwise we still write the `.tex` for you to compile.
  * `escape_latex()` makes arbitrary profile text safe to drop into LaTeX.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger("lla.latex_docs")

# LaTeX special characters → escaped forms
_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
# Engines in preference order (lualatex handles modern fonts best)
_ENGINES = ("lualatex", "xelatex", "pdflatex")


def escape_latex(text) -> str:
    """Make arbitrary text safe to insert into a LaTeX document."""
    if text is None:
        return ""
    out = []
    for ch in str(text):
        out.append(_LATEX_ESCAPES.get(ch, ch))
    return "".join(out)


def available_engine(preferred: str = "") -> Optional[str]:
    """Return the first installed LaTeX engine, or None if none is present."""
    order = ([preferred] if preferred else []) + [e for e in _ENGINES if e != preferred]
    for eng in order:
        if eng and shutil.which(eng):
            return eng
    return None


def render_cv_tex(data: dict) -> str:
    """Render a moderncv CV to a .tex string from a plain data dict.

    data keys: first, last, title, phone, email, linkedin, github, location,
    style, color, profile (str), competencies (list[str]),
    experience (list[{years,title,company,location,bullets:list[str]}]),
    education (list[{years,degree,school,location,detail}]),
    skills (list[{category,items}]).
    """
    e = escape_latex
    style = data.get("style", "banking")
    color = data.get("color", "blue")
    lines = [
        r"\documentclass[11pt,a4paper,sans]{moderncv}",
        r"\moderncvstyle{%s}" % style,
        r"\moderncvcolor{%s}" % color,
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[scale=0.8]{geometry}",
        r"\name{%s}{%s}" % (e(data.get("first", "")), e(data.get("last", ""))),
    ]
    if data.get("title"):
        lines.append(r"\title{%s}" % e(data["title"]))
    if data.get("location"):
        lines.append(r"\address{%s}{}{}" % e(data["location"]))
    if data.get("phone"):
        lines.append(r"\phone[mobile]{%s}" % e(data["phone"]))
    if data.get("email"):
        lines.append(r"\email{%s}" % e(data["email"]))
    if data.get("linkedin"):
        lines.append(r"\social[linkedin]{%s}" % e(data["linkedin"]))
    if data.get("github"):
        lines.append(r"\social[github]{%s}" % e(data["github"]))

    lines += [r"\begin{document}", r"\makecvtitle"]

    if data.get("profile"):
        lines.append(r"\vspace{4pt}")
        lines.append(r"\small{%s}" % e(data["profile"]))

    comps = data.get("competencies") or []
    if comps:
        lines.append(r"\section{Core Competencies}")
        lines.append(r"\begin{itemize}")
        for c in comps:
            lines.append(r"\item %s" % e(c))
        lines.append(r"\end{itemize}")

    exp = data.get("experience") or []
    if exp:
        lines.append(r"\section{Experience}")
        for job in exp:
            bullets = job.get("bullets") or []
            body = ""
            if bullets:
                body = r"\begin{itemize}" + "".join(
                    r"\item %s" % e(b) for b in bullets) + r"\end{itemize}"
            lines.append(r"\cventry{%s}{%s}{%s}{%s}{}{%s}" % (
                e(job.get("years", "")), e(job.get("title", "")),
                e(job.get("company", "")), e(job.get("location", "")), body))

    edu = data.get("education") or []
    if edu:
        lines.append(r"\section{Education}")
        for ed in edu:
            lines.append(r"\cventry{%s}{%s}{%s}{%s}{}{%s}" % (
                e(ed.get("years", "")), e(ed.get("degree", "")),
                e(ed.get("school", "")), e(ed.get("location", "")),
                e(ed.get("detail", ""))))

    skills = data.get("skills") or []
    if skills:
        lines.append(r"\section{Skills}")
        for s in skills:
            lines.append(r"\cvitem{%s}{%s}" % (
                e(s.get("category", "")), e(s.get("items", ""))))

    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def render_cover_tex(data: dict) -> str:
    """Render a moderncv cover letter to a .tex string.

    data keys: first, last, phone, email, style, color, company, recipient_address,
    date, opening, closing, paragraphs (list[str]), enclosure (str).
    """
    e = escape_latex
    lines = [
        r"\documentclass[11pt,a4paper,sans]{moderncv}",
        r"\moderncvstyle{%s}" % data.get("style", "banking"),
        r"\moderncvcolor{%s}" % data.get("color", "blue"),
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[scale=0.8]{geometry}",
        r"\name{%s}{%s}" % (e(data.get("first", "")), e(data.get("last", ""))),
    ]
    if data.get("phone"):
        lines.append(r"\phone[mobile]{%s}" % e(data["phone"]))
    if data.get("email"):
        lines.append(r"\email{%s}" % e(data["email"]))
    lines.append(r"\begin{document}")
    lines.append(r"\recipient{%s}{%s}" % (
        e(data.get("company", "Hiring Team")), e(data.get("recipient_address", ""))))
    lines.append(r"\date{%s}" % e(data.get("date", r"\today") if data.get("date") else r"\today"))
    lines.append(r"\opening{%s}" % e(data.get("opening", "Dear Hiring Manager,")))
    lines.append(r"\closing{%s}" % e(data.get("closing", "Sincerely,")))
    if data.get("enclosure"):
        lines.append(r"\enclosure[Enclosure]{%s}" % e(data["enclosure"]))
    lines.append(r"\makelettertitle")
    for para in (data.get("paragraphs") or []):
        lines.append(e(para))
        lines.append("")
    lines.append(r"\makeletterclosing")
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def compile_tex(tex_path: str, engine: str = "", timeout: int = 120) -> Optional[str]:
    """Compile a .tex file to PDF. Returns the PDF path, or None if no engine.

    Runs the engine twice (moderncv needs two passes for layout). Non-fatal:
    on any failure returns None and logs — the .tex remains for manual compile.
    """
    eng = available_engine(engine)
    if not eng:
        log.info("   No LaTeX engine installed — wrote .tex only (%s)", tex_path)
        return None
    tex_path = os.path.abspath(tex_path)
    workdir = os.path.dirname(tex_path)
    name = os.path.basename(tex_path)
    try:
        for _ in range(2):
            subprocess.run(
                [eng, "-interaction=nonstopmode", "-halt-on-error", name],
                cwd=workdir, timeout=timeout,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
        pdf = tex_path[:-4] + ".pdf" if tex_path.endswith(".tex") else tex_path + ".pdf"
        return pdf if os.path.exists(pdf) else None
    except Exception as exc:
        log.warning("   LaTeX compile failed (%s): %s", eng, exc)
        return None


class LaTeXDocsBuilder:
    """Render + compile a tailored CV and cover letter for one application."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.personal = cfg.get("personal", {})
        dc = cfg.get("latex_docs", {})
        self.enabled = dc.get("enabled", False)
        self.output_dir = dc.get("output_dir", "data/latex_docs")
        self.style = dc.get("cv_style", "banking")
        self.color = dc.get("cv_color", "blue")
        self.engine = dc.get("engine", "")

    def _identity(self) -> dict:
        p, qa = self.personal, self.cfg.get("question_answers", {})
        first = p.get("first_name") or (p.get("full_name", "").split(" ")[0] if p.get("full_name") else "")
        last = p.get("last_name") or (p.get("full_name", "").split(" ")[-1] if p.get("full_name") else "")
        return {
            "first": first, "last": last,
            "phone": p.get("phone", ""), "email": p.get("email", ""),
            "location": ", ".join(x for x in (p.get("city", ""), p.get("country", "")) if x),
            "linkedin": qa.get("linkedin", ""), "github": qa.get("github", ""),
            "style": self.style, "color": self.color,
        }

    def build_cv(self, cv_data: dict, basename: str = "cv") -> dict:
        """Render (and compile if possible) a CV. Returns {tex, pdf, engine}."""
        data = {**self._identity(), **cv_data}
        tex = render_cv_tex(data)
        return self._write_and_compile(tex, basename)

    def build_cover_letter(self, cover_data: dict, basename: str = "cover_letter") -> dict:
        data = {**self._identity(), **cover_data}
        tex = render_cover_tex(data)
        return self._write_and_compile(tex, basename)

    def _write_and_compile(self, tex: str, basename: str) -> dict:
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        tex_path = os.path.join(self.output_dir, f"{basename}.tex")
        with open(tex_path, "w", encoding="utf-8") as fh:
            fh.write(tex)
        pdf_path = compile_tex(tex_path, self.engine)
        return {"tex": tex_path, "pdf": pdf_path, "engine": available_engine(self.engine)}
