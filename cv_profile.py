"""
CV → candidate profile extraction.

So registration and form-filling can run "fully autonomously from the CV", this
derives the standard applicant fields straight out of the CV text: name, email,
phone, city/country, links, years of experience, current employer and title,
highest education and skills.

Deterministic and dependency-free — regex and small heuristics, no LLM call —
so it works offline and costs nothing. Anything explicitly set in
`config.personal` always wins; this only fills the blanks.

    from cv_profile import enrich_config_profile
    cfg = enrich_config_profile(cfg)     # personal.* now backfilled from the CV
"""

import logging
import re

log = logging.getLogger("lla.cv_profile")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# International-friendly: optional +CC, then 9-14 digits with separators.
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d[\d\s.-]{7,13}\d")
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+", re.I)
GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_-]+", re.I)
YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)

DEGREES = ["phd", "doctorate", "mba", "pgdm", "m.tech", "mtech", "msc", "m.sc",
           "masters", "master", "b.tech", "btech", "bsc", "b.sc", "be ",
           "bachelor", "bca", "mca", "diploma"]

# Cities the bot targets, mapped to their country (mirrors work_auth's map).
CITY_COUNTRY = {
    "gurugram": "India", "gurgaon": "India", "delhi": "India", "new delhi": "India",
    "mumbai": "India", "bangalore": "India", "bengaluru": "India", "pune": "India",
    "hyderabad": "India", "chennai": "India", "noida": "India", "kolkata": "India",
    "london": "United Kingdom", "manchester": "United Kingdom",
    "edinburgh": "United Kingdom", "dublin": "Ireland",
    "new york": "United States", "san francisco": "United States",
    "seattle": "United States", "boston": "United States", "chicago": "United States",
    "toronto": "Canada", "vancouver": "Canada", "sydney": "Australia",
    "melbourne": "Australia", "singapore": "Singapore",
    "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates",
    "frankfurt": "Germany", "berlin": "Germany", "amsterdam": "Netherlands",
    "zurich": "Switzerland", "hong kong": "Hong Kong",
}

SKILL_HINTS = [
    "python", "sql", "r", "java", "javascript", "typescript", "excel", "vba",
    "tableau", "power bi", "spss", "sas", "matlab", "scala", "c++", "aws",
    "azure", "gcp", "docker", "kubernetes", "spark", "hadoop", "airflow",
    "basel iii", "basel", "irb", "ifrs9", "ifrs 9", "ccar", "rwa", "ofsaa",
    "pd/lgd/ead", "credit risk", "market risk", "model validation", "stress testing",
    "machine learning", "pytorch", "tensorflow", "scikit-learn", "agile", "jira",
]


def _first_nonempty_line(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def extract_name(text: str) -> tuple:
    """(first, last, full). CVs put the name on line 1, often in caps."""
    line = _first_nonempty_line(text)
    # Strip a trailing role/company after a dash, comma or pipe.
    line = re.split(r"\s+[–—|]\s+|,\s", line)[0].strip()
    line = re.sub(r"[^A-Za-z .'-]", " ", line).strip()
    parts = [p for p in line.split() if len(p) > 1][:4]
    if not parts or len(parts) > 4:
        return "", "", ""
    # A name line shouldn't read like a sentence.
    if any(w.lower() in ("the", "and", "with", "for", "resume", "curriculum")
           for w in parts):
        return "", "", ""
    full = " ".join(w.capitalize() if w.isupper() else w for w in parts)
    return parts[0].capitalize(), parts[-1].capitalize(), full


def extract_location(text: str) -> tuple:
    """(city, country) from the first recognised city mention."""
    low = (text or "").lower()
    best, pos = None, len(low) + 1
    for city, country in CITY_COUNTRY.items():
        i = low.find(city)
        if i != -1 and i < pos:
            best, pos = (city.title(), country), i
    return best or ("", "")


def extract_years_experience(text: str):
    """Largest credible 'N years' figure in the CV."""
    vals = [int(m) for m in YEARS_RE.findall(text or "") if 0 < int(m) <= 50]
    return str(max(vals)) if vals else ""


def extract_current_role(text: str) -> tuple:
    """(company, title) for the present role — the line mentioning Present/Current."""
    for line in (text or "").splitlines():
        if re.search(r"\b(present|current|to date|till date)\b", line, re.I):
            cleaned = re.sub(r"\(?\b(19|20)\d{2}\b[^)]*\)?", "", line)
            cleaned = re.sub(r"\b(present|current|to date|till date)\b", "", cleaned, flags=re.I)
            cleaned = cleaned.strip(" .,:;-–—()")
            # "Company (dates): Title ..." or "Title, Company"
            if ":" in cleaned:
                left, right = cleaned.split(":", 1)
                return left.strip(" .,-"), right.strip().split(".")[0][:80]
            if "," in cleaned:
                a, b = [x.strip() for x in cleaned.split(",", 1)]
                return b[:80], a[:80]
            return cleaned[:80], ""
    return "", ""


def extract_education(text: str) -> str:
    low = (text or "").lower()
    for deg in DEGREES:
        i = low.find(deg)
        if i != -1:
            line = (text or "")[max(0, i - 40): i + 120].splitlines()
            for seg in line:
                if deg in seg.lower():
                    return seg.strip(" .,:;-")[:120]
    return ""


def extract_skills(text: str, limit: int = 15) -> str:
    low = (text or "").lower()
    found = [s for s in SKILL_HINTS if s in low]
    seen, out = set(), []
    for s in found:
        k = s.replace(" ", "")
        if k not in seen:
            seen.add(k)
            out.append(s.title() if s.islower() and " " in s else s.upper() if len(s) <= 4 else s.title())
    return ", ".join(out[:limit])


def extract_profile(cv_text: str) -> dict:
    """Everything we can derive from a CV, as config-shaped `personal` keys."""
    text = cv_text or ""
    first, last, full = extract_name(text)
    city, country = extract_location(text)
    company, title = extract_current_role(text)
    emails = EMAIL_RE.findall(text)
    # A phone match must contain enough digits to be real.
    phones = [p.strip() for p in PHONE_RE.findall(text)
              if len(re.sub(r"\D", "", p)) >= 10]
    li = LINKEDIN_RE.search(text)
    gh = GITHUB_RE.search(text)
    return {
        "first_name": first, "last_name": last, "full_name": full,
        "email": emails[0] if emails else "",
        "phone": phones[0] if phones else "",
        "city": city, "country": country,
        "linkedin": li.group(0) if li else "",
        "github": gh.group(0) if gh else "",
        "years_of_experience": extract_years_experience(text),
        "current_company": company, "current_title": title,
        "education": extract_education(text),
        "skills": extract_skills(text),
    }


def enrich_config_profile(cfg: dict) -> dict:
    """Backfill blank `personal` fields (and a few `question_answers`) from the CV.

    Explicit config always wins — this only fills what the user left empty, so
    a CV alone is enough to complete an application end to end.
    """
    cfg = cfg or {}
    cv = (cfg.get("ai", {}) or {}).get("cv_text", "") or \
         (cfg.get("resume_tailoring", {}) or {}).get("master_resume_text", "")
    if not cv:
        return cfg
    derived = extract_profile(cv)
    personal = dict(cfg.get("personal", {}) or {})
    filled = []
    for key in ("first_name", "last_name", "full_name", "email", "phone",
                "city", "country"):
        if not str(personal.get(key, "")).strip() and derived.get(key):
            personal[key] = derived[key]
            filled.append(key)
    cfg["personal"] = personal

    app = dict(cfg.get("application", {}) or {})
    if not str(app.get("years_of_experience", "")).strip() and derived["years_of_experience"]:
        app["years_of_experience"] = derived["years_of_experience"]
        filled.append("years_of_experience")
    cfg["application"] = app

    qa = dict(cfg.get("question_answers", {}) or {})
    for qa_key, dkey in (("linkedin", "linkedin"), ("github", "github"),
                         ("current company", "current_company"),
                         ("skills", "skills")):
        if not str(qa.get(qa_key, "")).strip() and derived.get(dkey):
            qa[qa_key] = derived[dkey]
            filled.append(qa_key)
    cfg["question_answers"] = qa

    if filled:
        log.info("   📄 CV profile: filled %s from the CV", ", ".join(filled))
    return cfg
