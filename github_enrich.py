"""
GitHub signal enrichment — feature your best repos per application.

Candidate-side inversion of interviewstreet/hiring-agent's github.py (MIT):
their screener fetches an applicant's GitHub, separates true open-source
contributions from personal repos, and weighs the top projects. This module
does the same for YOUR profile so each application features the projects the
screener will value most for that specific posting — and warns you when your
GitHub would score poorly (e.g. all personal repos, nothing starred).

Deterministic core (fully testable without network):
  * ``classify_repo(repo)``      → "open_source" | "self_project" | "fork"
  * ``rank_projects(repos, jd)`` → repos scored by stars, recency, docs,
                                   language relevance to the JD
  * ``github_signal_summary``    → the screener-visible signal (would you cap
                                   at 10/35 on open-source?)

Network layer: ``fetch_repos(username)`` uses GitHub's public REST API
(unauthenticated, 60 req/h; set GITHUB_TOKEN for 5000/h). Fails soft.
"""

import logging
import os
import re
from datetime import datetime, timezone

log = logging.getLogger("lla.github_enrich")

_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# Fetch (network, fails soft)
# ---------------------------------------------------------------------------

def extract_username(github_url: str) -> str:
    """'https://github.com/ada-l/' or 'github.com/ada-l' or 'ada-l' → 'ada-l'."""
    if not github_url:
        return ""
    m = re.search(r"github\.com/([A-Za-z0-9-]+)", github_url)
    if m:
        return m.group(1)
    # bare username (no slashes/dots)
    if re.fullmatch(r"[A-Za-z0-9-]+", github_url.strip().strip("/")):
        return github_url.strip().strip("/")
    return ""


def fetch_repos(username: str, max_repos: int = 100) -> list:
    """Fetch public repos for a user via the GitHub REST API. [] on any failure."""
    if not username:
        return []
    try:
        import requests
        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": "linkedin-lightning-applier"}
        token = os.environ.get("GITHUB_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        out, page = [], 1
        while len(out) < max_repos:
            r = requests.get(f"{_API}/users/{username}/repos",
                             params={"per_page": 100, "page": page,
                                     "sort": "updated"},
                             headers=headers, timeout=20)
            if r.status_code != 200:
                log.debug("GitHub API %s for %s", r.status_code, username)
                break
            batch = r.json()
            if not batch:
                break
            out.extend(batch)
            page += 1
        return out[:max_repos]
    except Exception as exc:
        log.debug("fetch_repos failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Deterministic classification + ranking
# ---------------------------------------------------------------------------

def classify_repo(repo: dict) -> str:
    """Screener's distinction: forks and personal repos are NOT open source.

    "open_source" = a repo other people demonstrably use/contribute to
    (stars/forks from the community). Everything original-but-unnoticed is a
    "self_project"; plain forks are "fork".
    """
    if repo.get("fork"):
        return "fork"
    stars = repo.get("stargazers_count", 0) or 0
    forks = repo.get("forks_count", 0) or 0
    if stars >= 10 or forks >= 3:
        return "open_source"
    return "self_project"


def _jd_terms(jd_text: str) -> set:
    return {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9+#.]{1,}", jd_text or "")
            if len(t) > 2}


def score_repo(repo: dict, jd_terms: set) -> float:
    """Deterministic value score: community signal, docs, recency, JD relevance."""
    if repo.get("fork"):
        return 0.0
    score = 0.0
    stars = repo.get("stargazers_count", 0) or 0
    score += min(stars, 100) * 0.5            # community signal, capped
    score += min(repo.get("forks_count", 0) or 0, 20) * 1.0
    if repo.get("description"):
        score += 5                             # documented
    if repo.get("homepage"):
        score += 5                             # live demo → rubric bonus
    lang = (repo.get("language") or "").lower()
    if lang and lang in jd_terms:
        score += 15                            # matches the posting's stack
    topics = [t.lower() for t in (repo.get("topics") or [])]
    score += 5 * len(set(topics) & jd_terms)
    # recency: active in the last year beats abandoned
    pushed = repo.get("pushed_at") or ""
    try:
        dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - dt).days
        if age_days <= 365:
            score += 10 * (1 - age_days / 365)
    except (ValueError, TypeError):
        pass
    return round(score, 2)


def rank_projects(repos: list, jd_text: str = "", top: int = 7) -> list:
    """Return the top repos to feature, scored for this JD.

    Each item: {name, url, description, language, stars, type, score}.
    """
    terms = _jd_terms(jd_text)
    scored = []
    for r in repos or []:
        s = score_repo(r, terms)
        if s <= 0:
            continue
        scored.append({
            "name": r.get("name", ""),
            "url": r.get("html_url", ""),
            "description": (r.get("description") or "")[:200],
            "language": r.get("language") or "",
            "stars": r.get("stargazers_count", 0) or 0,
            "type": classify_repo(r),
            "score": s,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top]


def github_signal_summary(repos: list) -> dict:
    """How your GitHub reads to a screener — before they read a word of prose.

    Mirrors the rubric's hard rule: if everything is a self_project, the
    open-source category caps at ~10/35.
    """
    repos = repos or []
    kinds = [classify_repo(r) for r in repos]
    n_os = kinds.count("open_source")
    n_self = kinds.count("self_project")
    n_fork = kinds.count("fork")
    total_stars = sum((r.get("stargazers_count", 0) or 0) for r in repos)
    warnings = []
    if not repos:
        warnings.append("no public repos — engineering screeners score this 0-4/35")
    elif n_os == 0:
        warnings.append("all repos are personal/self projects — open-source "
                        "category will cap at ~10/35; contribute to established projects")
    undocumented = sum(1 for r in repos if not r.get("fork") and not r.get("description"))
    if undocumented:
        warnings.append(f"{undocumented} repo(s) have no description — add one-line "
                        "descriptions (screeners read them)")
    return {"repos": len(repos), "open_source": n_os, "self_projects": n_self,
            "forks": n_fork, "total_stars": total_stars, "warnings": warnings}
