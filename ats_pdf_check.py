"""
ATS parseability + keyword-coverage check.

An ATS reads the PDF's embedded text layer, not the rendered page — and a
beautiful LaTeX CV can still extract as garbage (icon glyphs where the email
should be, interleaved lines from multi-column layouts). This module verifies a
CV the way a parser actually sees it, and scores how well it covers a job
posting's keywords. Inspired by MadsLorentzen/ai-job-search's ATS check.

Two layers:
  * `extract_pdf_text(path)` — pull the text layer via pdftotext (poppler) or
    pdfminer if installed; returns None if neither is available (graceful).
  * Pure-text checks — `check_parseability()`, `keyword_coverage()`,
    `ats_report()` — operate on a plain string, so they're fully testable with
    no binaries and work whether the text came from a PDF or straight from the
    LaTeX/plain source.

Honesty rule (from the source project): a keyword the profile does not support
is reported as a genuine gap, never silently "covered". This module only
measures coverage; it never invents keywords.
"""

import logging
import re
import shutil
import subprocess
from typing import Optional

log = logging.getLogger("lla.ats_pdf")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{6,}\d)")
# Unicode replacement / common "missing glyph" markers that signal a broken text layer
_GARBLE_RE = re.compile(r"[�□▯]")

_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "our", "are", "will", "have",
    "this", "that", "from", "who", "job", "role", "team", "work", "working",
    "candidate", "ideal", "looking", "join", "including", "etc", "such", "able",
    "strong", "good", "great", "excellent", "experience", "years", "year",
    "skills", "ability", "knowledge", "understanding", "requirements", "responsibilities",
    "we", "us", "a", "an", "of", "to", "in", "on", "as", "is", "be", "or", "at",
    "by", "it", "will", "must", "should", "within", "across", "plus", "per",
    "about", "into", "their", "they", "them", "his", "her", "he", "she",
    "new", "help", "make", "using", "use", "well", "more", "most", "all", "any",
}


def extract_pdf_text(pdf_path: str) -> Optional[str]:
    """Extract a PDF's text layer via pdftotext, then pdfminer. None if neither."""
    if shutil.which("pdftotext"):
        try:
            out = subprocess.run(
                ["pdftotext", "-layout", pdf_path, "-"],
                capture_output=True, timeout=60, check=False,
            )
            if out.returncode == 0 and out.stdout:
                return out.stdout.decode("utf-8", errors="replace")
        except Exception as exc:
            log.debug("pdftotext failed: %s", exc)
    try:
        from pdfminer.high_level import extract_text  # type: ignore
        return extract_text(pdf_path)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        # BaseException (not just Exception): a broken native dep (e.g. a
        # pyo3/cryptography panic) can raise outside the Exception hierarchy.
        log.debug("pdfminer unavailable/broken: %s", exc)
        return None


# A word: starts with a letter, may contain internal . or - joining alphanumerics
# (node.js, scikit-learn), and may end in + or # (c++, c#, f#). No trailing
# sentence punctuation is captured ("SQL." → "sql").
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[.\-][A-Za-z0-9]+)*[+#]*")


def _tokenize(text: str) -> list:
    return [t for t in _TOKEN_RE.findall((text or "").lower())]


def extract_keywords(jd_text: str, top: int = 30) -> list:
    """Pull likely 'must-have' keywords from a job description.

    Heuristic and deterministic: single tokens that aren't stopwords, ranked by
    frequency then length; capitalised/tech-looking terms get a small boost.
    """
    if not jd_text:
        return []
    raw = _tokenize(jd_text)
    freq = {}
    for tok in raw:
        if len(tok) < 3 or tok in _STOPWORDS:
            continue
        freq[tok] = freq.get(tok, 0) + 1
    # Boost terms that appear capitalised or contain tech punctuation in the source
    boosted = set(m.lower() for m in re.findall(r"\b[A-Z][A-Za-z0-9+.#]{2,}\b", jd_text))
    boosted |= set(m.lower() for m in re.findall(r"[A-Za-z]+[+.#][A-Za-z+.#]*", jd_text))

    def rank(item):
        w, c = item
        return (c + (1 if w in boosted else 0), len(w))

    ordered = sorted(freq.items(), key=rank, reverse=True)
    return [w for w, _ in ordered[:top]]


def keyword_coverage(cv_text: str, jd_text: str, top: int = 30) -> dict:
    """Score how many JD keywords appear in the CV text (as a parser sees it)."""
    keywords = extract_keywords(jd_text, top=top)
    cv_low = (cv_text or "").lower()
    present, missing = [], []
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw) + r"\b", cv_low):
            present.append(kw)
        else:
            missing.append(kw)
    total = len(keywords)
    pct = round(100 * len(present) / total) if total else 0
    return {"coverage_pct": pct, "present": present, "missing": missing,
            "keywords_checked": total}


def check_parseability(text: str, profile: dict = None) -> dict:
    """Verify the text layer the way an ATS parser sees it.

    Returns {ok, issues, email_found, phone_found, contact_in_top}.
    """
    profile = profile or {}
    issues = []
    text = text or ""
    if not text.strip():
        return {"ok": False, "issues": ["empty text layer — PDF has no extractable text"],
                "email_found": False, "phone_found": False, "contact_in_top": False}

    email_found = bool(_EMAIL_RE.search(text))
    phone_found = bool(_PHONE_RE.search(text))
    want_email = profile.get("email", "")
    if want_email and want_email.lower() not in text.lower():
        issues.append("configured email not present as literal text (icon glyph?)")
    if not email_found:
        issues.append("no email address found in text layer")
    if not phone_found:
        issues.append("no phone number found in text layer")

    # Contact details should sit near the top (first ~15 lines)
    head = "\n".join(text.splitlines()[:15]).lower()
    contact_in_top = (email_found and bool(_EMAIL_RE.search(head))) or \
                     (bool(want_email) and want_email.lower() in head)
    if email_found and not contact_in_top:
        issues.append("email not in the top of the document (reading-order problem?)")

    garble = len(_GARBLE_RE.findall(text))
    if garble:
        issues.append(f"{garble} garbled/missing-glyph character(s) in text layer")

    return {"ok": not issues, "issues": issues,
            "email_found": email_found, "phone_found": phone_found,
            "contact_in_top": contact_in_top, "garble_count": garble}


def ats_report(cv_text: str, jd_text: str, profile: dict = None,
               min_coverage: int = 60) -> dict:
    """Combined ATS report: parseability + keyword coverage + pass/fail."""
    parse = check_parseability(cv_text, profile)
    cover = keyword_coverage(cv_text, jd_text)
    passed = parse["ok"] and cover["coverage_pct"] >= min_coverage
    return {
        "passed": passed,
        "parseable": parse["ok"],
        "coverage_pct": cover["coverage_pct"],
        "missing_keywords": cover["missing"],
        "present_keywords": cover["present"],
        "issues": parse["issues"],
        "min_coverage": min_coverage,
    }
