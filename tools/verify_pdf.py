#!/usr/bin/env python3
"""Check what a CV PDF actually looks like on the page.

    python tools/verify_pdf.py cv.pdf [--pages 2] [--tex cv.tex] [--fix]
    exit 0 = the layout is sound
    exit 1 = something a human reader would notice

`lla docs` checks that the *text* of a CV is right — the keywords, the ATS
text layer, the honesty of the claims. None of that looks at the rendered
page, and the failures that cost you an interview are usually visual:

  * a job title stranded at the foot of a page with its bullets overleaf,
    so a skimming reader sees a heading attached to nothing;
  * a two-page CV that spilled onto a third page carrying two lines;
  * a section heading as the last thing on a page;
  * text pushed outside the margins, which some ATS parsers drop entirely.

LaTeX will not warn about any of these. It typesets exactly what it was told
and moves on. This reads the finished PDF — the same artefact the employer
opens — and reports what a person would see.

With `--tex` it can also fix the orphans it finds, by inserting `\\needspace`
before the entries that broke, so the entry moves to the next page as a unit.
"""

import re
import subprocess
import sys
from pathlib import Path

# An entry heading: a year or year-range, which is what \cventry puts first.
YEAR_RE = re.compile(
    r"\b(19|20)\d{2}\s*(?:[-–—]{1,2}|\bto\b)\s*((19|20)\d{2}|present|current|now)\b",
    re.I)
SINGLE_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
BULLET_RE = re.compile(r"^\s*(?:[-•·▪◦*‣]|•)\s+\S")

# Section headings a CV actually uses. A heading alone at a page foot is the
# same defect as an orphaned entry, and just as visible.
SECTION_WORDS = {
    "experience", "work experience", "professional experience", "employment",
    "education", "skills", "technical skills", "projects", "publications",
    "certifications", "achievements", "summary", "profile", "interests",
    "languages", "references", "volunteering", "awards",
}

# Fractions of page height/width. Text below BOTTOM_BAND is "at the foot of
# the page"; anything outside the margins is off the page as far as a reader
# (or a strict ATS parser) is concerned.
BOTTOM_BAND = 0.12
TOP_BAND = 0.88
MARGIN_PT = 28.0          # ~10mm; tighter than any sane CV template
SHORT_PAGE_LINES = 4      # a final page with fewer lines than this is a widow


class Issue:
    def __init__(self, kind, message, page=None, fix="", severity="error"):
        self.kind = kind
        self.message = message
        self.page = page
        self.fix = fix
        self.severity = severity

    def as_dict(self):
        return {"kind": self.kind, "message": self.message, "page": self.page,
                "fix": self.fix, "severity": self.severity}

    def __repr__(self):
        where = f" p{self.page}" if self.page else ""
        return f"<{self.severity}{where}: {self.message}>"


class Page:
    """One rendered page: its size, and every piece of text with its position."""

    def __init__(self, number, width, height, items):
        self.number = number
        self.width = width
        self.height = height
        self.items = items            # [(x, y, text)] with y measured from the bottom

    @property
    def lines(self):
        return [t for _x, _y, t in self.items if t.strip()]

    def text(self):
        return "\n".join(self.lines)

    def lowest(self):
        """The item nearest the foot of the page."""
        real = [i for i in self.items if i[2].strip()]
        return min(real, key=lambda i: i[1]) if real else None

    def highest(self):
        real = [i for i in self.items if i[2].strip()]
        return max(real, key=lambda i: i[1]) if real else None


# ---------------------------------------------------------------------------
# Reading the PDF
# ---------------------------------------------------------------------------

def read_pages(pdf_path: str):
    """(pages, error). Positions come from pypdf; pdftotext is the fallback.

    Without coordinates the geometric checks cannot run, so the reader used is
    reported rather than quietly changing what gets checked.
    """
    pages, err = _read_with_pypdf(pdf_path)
    if pages:
        return pages, ""
    text_pages = _read_with_pdftotext(pdf_path)
    if text_pages:
        return text_pages, ""
    return [], err or ("no PDF reader available — install pypdf "
                       "(pip install pypdf) or poppler's pdftotext")


def _read_with_pypdf(pdf_path: str):
    try:
        import pypdf
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        # A broken native dependency can panic outside the Exception hierarchy.
        return [], f"pypdf unavailable ({exc})"
    try:
        reader = pypdf.PdfReader(pdf_path)
        pages = []
        for n, page in enumerate(reader.pages, 1):
            items = []

            def visit(text, _cm, tm, _font, _size, _items=items):
                if text and text.strip():
                    _items.append((round(tm[4], 1), round(tm[5], 1), text.strip()))

            page.extract_text(visitor_text=visit)
            box = page.mediabox
            pages.append(Page(n, float(box.width), float(box.height), items))
        return pages, ""
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        return [], f"could not read {pdf_path} ({type(exc).__name__}: {exc})"


def _read_with_pdftotext(pdf_path: str):
    """Text only — no coordinates, so the geometric checks are skipped."""
    import shutil
    if not shutil.which("pdftotext"):
        return []
    try:
        out = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                             capture_output=True, timeout=60, check=False)
        if out.returncode != 0 or not out.stdout:
            return []
    except Exception:
        return []
    chunks = out.stdout.decode("utf-8", errors="replace").split("\f")
    pages = []
    for n, chunk in enumerate([c for c in chunks if c.strip()], 1):
        # y is unknown; None marks "position not available".
        items = [(None, None, ln.strip()) for ln in chunk.splitlines() if ln.strip()]
        pages.append(Page(n, 0.0, 0.0, items))
    return pages


def has_positions(pages) -> bool:
    return any(i[1] is not None for p in pages for i in p.items)


# ---------------------------------------------------------------------------
# Recognising what a line is
# ---------------------------------------------------------------------------

def looks_like_entry_heading(line: str) -> bool:
    """A \\cventry heading: a date range, plus a role and employer."""
    text = (line or "").strip()
    if not text or BULLET_RE.match(text):
        return False
    if not (YEAR_RE.search(text) or SINGLE_YEAR_RE.search(text)):
        return False
    # Needs some name-like content, not just a bare date.
    words = [w for w in re.split(r"\s+", text) if w]
    capitals = sum(1 for w in words if w[:1].isupper())
    return len(words) >= 3 and capitals >= 2


def looks_like_section_heading(line: str) -> bool:
    text = re.sub(r"[^a-z ]", "", (line or "").strip().lower()).strip()
    return bool(text) and text in SECTION_WORDS


def looks_like_continuation(line: str) -> bool:
    """Content that belongs to an entry above it rather than starting one."""
    text = (line or "").strip()
    if not text:
        return False
    if BULLET_RE.match(text):
        return True
    return not (looks_like_entry_heading(text) or looks_like_section_heading(text))


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_page_count(pages, expected: int) -> list:
    n = len(pages)
    if n == 0:
        return [Issue("empty", "the PDF has no pages", fix="re-run the build")]
    if expected and n > expected:
        return [Issue(
            "page_count",
            f"{n} pages, but {expected} was asked for",
            fix="cut content, or raise --pages if the extra page is intended")]
    return []


def check_empty_pages(pages) -> list:
    return [Issue("empty_page", f"page {p.number} is blank", page=p.number,
                  fix="remove the stray page break that produced it")
            for p in pages if not p.lines]


def check_short_last_page(pages) -> list:
    """A final page carrying a couple of lines reads as a mistake."""
    if len(pages) < 2:
        return []
    last = pages[-1]
    if last.lines and len(last.lines) <= SHORT_PAGE_LINES:
        return [Issue(
            "widow_page",
            f"page {last.number} carries only {len(last.lines)} line(s): "
            f"{last.lines[0][:60]!r}",
            page=last.number, severity="warning",
            fix=r"tighten the page above, or \enlargethispage{\baselineskip} "
                "on it, so this content fits")]
    return []


def check_orphan_headings(pages) -> list:
    """A heading at the foot of a page whose content is on the next one.

    This is the defect that most changes how a CV reads: a skimming eye sees
    a job title with nothing under it, and the achievements that justify the
    role look like they belong to the next employer.
    """
    issues = []
    positioned = has_positions(pages)
    for idx, page in enumerate(pages[:-1]):
        nxt = pages[idx + 1]
        if not page.lines or not nxt.lines:
            continue

        if positioned:
            low = page.lowest()
            if low is None:
                continue
            at_foot = low[1] <= page.height * BOTTOM_BAND
            candidate = low[2]
        else:
            at_foot = True            # last line on the page, by definition
            candidate = page.lines[-1]
        if not at_foot:
            continue

        if looks_like_entry_heading(candidate) and looks_like_continuation(nxt.lines[0]):
            issues.append(Issue(
                "orphan_entry",
                f"page {page.number} ends with the entry {candidate[:60]!r} "
                f"and its detail starts on page {nxt.number}",
                page=page.number,
                fix=r"put \needspace{4\baselineskip} before this \cventry "
                    "so the entry moves as a unit"))
        elif looks_like_section_heading(candidate):
            issues.append(Issue(
                "orphan_section",
                f"page {page.number} ends with the heading {candidate[:40]!r}, "
                f"its content is on page {nxt.number}",
                page=page.number,
                fix=r"put \needspace{5\baselineskip} before this \section"))
    return issues


def check_margins(pages) -> list:
    """Text outside the printable area — invisible, clipped, or dropped by ATS."""
    if not has_positions(pages):
        return []
    issues = []
    for page in pages:
        if not page.width or not page.height:
            continue
        for x, y, text in page.items:
            if x is None or y is None or not text.strip():
                continue
            if x < MARGIN_PT or x > page.width - MARGIN_PT:
                issues.append(Issue(
                    "margin",
                    f"page {page.number}: {text[:40]!r} starts at x={x:.0f}, "
                    f"outside the margins of a {page.width:.0f}pt page",
                    page=page.number,
                    fix="widen the margins or shorten the line — some ATS "
                        "parsers drop text outside the text block"))
            elif y < MARGIN_PT or y > page.height - MARGIN_PT:
                issues.append(Issue(
                    "margin",
                    f"page {page.number}: {text[:40]!r} sits at y={y:.0f}, "
                    "beyond the top or bottom margin",
                    page=page.number,
                    fix=r"remove a line, or \enlargethispage to reflow the page"))
    # One example per page is enough to act on.
    seen, unique = set(), []
    for i in issues:
        if i.page not in seen:
            seen.add(i.page)
            unique.append(i)
    return unique


def check_expected_text(pages, expected) -> list:
    """Things that must survive to the rendered page — a name, an email."""
    if not expected:
        return []
    joined = "\n".join(p.text() for p in pages).lower()
    return [Issue("missing_text", f"{want!r} does not appear in the rendered PDF",
                  fix="check the template escaped it correctly")
            for want in expected if want.lower() not in joined]


def verify(pdf_path: str, expected_pages: int = 2, expected_text=None):
    """(issues, pages, error)."""
    pages, err = read_pages(pdf_path)
    if err and not pages:
        return [Issue("unreadable", err, fix="")], [], err
    issues = []
    issues += check_page_count(pages, expected_pages)
    issues += check_empty_pages(pages)
    issues += check_orphan_headings(pages)
    issues += check_short_last_page(pages)
    issues += check_margins(pages)
    issues += check_expected_text(pages, expected_text)
    return issues, pages, ""


# ---------------------------------------------------------------------------
# Fixing the .tex that produced it
# ---------------------------------------------------------------------------

NEEDSPACE_PKG = r"\usepackage{needspace}"


def add_needspace(tex: str, entry_space: int = 4, section_space: int = 5) -> tuple:
    """Guard every \\cventry and \\section against being split. (tex, n_changed).

    `needspace` asks LaTeX for a minimum amount of room before the material
    that follows; if there is not enough, the page breaks first and the entry
    starts the next page whole. Applying it to every entry is deliberate —
    fixing only the entries that broke this time leaves the next edit free to
    break a different one.
    """
    if not tex:
        return tex, 0
    out = tex
    if NEEDSPACE_PKG not in out:
        m = re.search(r"^\\documentclass.*$", out, re.M)
        if m:
            out = out[:m.end()] + "\n" + NEEDSPACE_PKG + out[m.end():]
        else:
            out = NEEDSPACE_PKG + "\n" + out

    changed = 0

    def guard(match, amount):
        nonlocal changed
        line = match.group(0)
        start = max(0, match.start() - 80)
        if r"\needspace" in out[start:match.start()]:
            return line                       # already guarded
        changed += 1
        return "\\needspace{%d\\baselineskip}\n%s" % (amount, line)

    out = re.sub(r"^\\cventry\b.*$", lambda m: guard(m, entry_space), out, flags=re.M)
    out = re.sub(r"^\\section\{.*$", lambda m: guard(m, section_space), out, flags=re.M)
    return out, changed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_issues(issues, pages, pdf_path) -> str:
    out = [f"  {pdf_path}", f"  {len(pages)} page(s)"]
    if pages and not has_positions(pages):
        out.append("  (text only — install pypdf for the margin and "
                   "page-position checks)")
    if not issues:
        out.append("\n  ✅ layout is sound")
        return "\n".join(out)

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity != "error"]
    out.append("")
    for group, label in ((errors, "❌"), (warnings, "⚠️ ")):
        for i in group:
            out.append(f"  {label} {i.message}")
            if i.fix:
                out.append(f"      fix: {i.fix}")
    out.append(f"\n  {len(errors)} problem(s), {len(warnings)} warning(s)")
    return "\n".join(out)


def main(argv):
    args, opts, i = [], {}, 0
    flags = {"--pages", "--tex", "--expect"}
    while i < len(argv):
        a = argv[i]
        if a in flags and i + 1 < len(argv):
            opts.setdefault(a, []).append(argv[i + 1])
            i += 2
            continue
        if a.startswith("--"):
            opts.setdefault(a, []).append(True)
            i += 1
            continue
        args.append(a)
        i += 1

    if not args:
        print("usage: python tools/verify_pdf.py cv.pdf [--pages 2] "
              "[--expect 'Ada Lovelace'] [--tex cv.tex --fix]")
        return 2

    pdf = args[0]
    if not Path(pdf).exists():
        print(f"  {pdf} does not exist")
        return 1
    try:
        expected_pages = int(opts.get("--pages", [2])[0])
    except (TypeError, ValueError):
        expected_pages = 2

    issues, pages, _err = verify(pdf, expected_pages, opts.get("--expect"))
    print(format_issues(issues, pages, pdf))

    tex = opts.get("--tex", [None])[0]
    if tex and opts.get("--fix"):
        path = Path(tex)
        if not path.exists():
            print(f"\n  {tex} does not exist — nothing fixed")
            return 1
        source = path.read_text(encoding="utf-8")
        fixed, n = add_needspace(source)
        if n:
            path.with_suffix(path.suffix + ".bak").write_text(source, encoding="utf-8")
            path.write_text(fixed, encoding="utf-8")
            print(f"\n  Guarded {n} entry/section(s) in {tex} with \\needspace")
            print(f"  (previous version saved as {tex}.bak)")
            print("  Rebuild and run this again to confirm the page breaks moved.")
        else:
            print(f"\n  {tex} is already guarded — nothing to change")
    elif tex:
        print(f"\n  Pass --fix as well to guard {tex} with \\needspace")

    return 1 if any(i.severity == "error" for i in issues) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
