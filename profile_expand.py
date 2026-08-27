"""Expand your profile from the public presence you already have.

    lla expand                 # discover sources from config + CV, report findings
    lla expand --apply         # also fill BLANK config fields with what it found

A CV is a summary written once and rarely updated. Meanwhile your GitHub has
the languages you actually write, your portfolio lists projects the CV had no
room for, and Scholar knows what you published. Applications keep asking for
exactly those things. This gathers them from sources you have already made
public, so answers come from evidence rather than from a model's imagination.

Three rules hold this together:

  1. **Every finding cites its source URL.** Nothing lands in your profile
     without a link you can click to check it. If it cannot be sourced, it is
     not a finding.
  2. **Nothing is overwritten.** `--apply` fills fields you left blank. What
     you wrote yourself always wins, and is never silently edited.
  3. **Every fetch honours robots.txt.** Enrichment reads other people's
     servers, so it goes through tools.robots_check and skips any URL the site
     has not permitted — including ones where permission could not be
     confirmed.

Sources: GitHub (via the API, no scraping), a personal site or portfolio,
Google Scholar, and Kaggle. Each is optional; whatever is missing is simply
reported as not found.
"""

import json
import logging
import re
from html import unescape
from urllib.parse import urlparse

log = logging.getLogger(__name__)

TIMEOUT = 20
USER_AGENT = "LightningApplier"
MAX_HTML = 400_000          # a profile page that big is not a profile page

# Skills we can recognise in prose with no model in the loop. Kept deliberately
# concrete: a term only counts as a skill if it is unambiguously one.
SKILL_TERMS = [
    "python", "java", "javascript", "typescript", "c++", "c#", "golang", "go",
    "rust", "ruby", "php", "scala", "kotlin", "swift", "r", "matlab", "sql",
    "bash", "powershell", "perl", "julia",
    "django", "flask", "fastapi", "spring", "react", "angular", "vue", "svelte",
    "node.js", "express", "rails", ".net", "laravel", "next.js",
    "pytorch", "tensorflow", "keras", "scikit-learn", "sklearn", "pandas",
    "numpy", "scipy", "xgboost", "lightgbm", "huggingface", "langchain",
    "spark", "hadoop", "kafka", "airflow", "dbt", "snowflake", "databricks",
    "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch",
    "cassandra", "dynamodb", "sqlite", "clickhouse",
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "ansible",
    "jenkins", "github actions", "gitlab ci", "circleci",
    "tableau", "power bi", "looker", "excel", "sas", "spss", "stata",
    "git", "linux", "graphql", "rest api", "grpc", "kafka", "rabbitmq",
]
# Longest first, so "github actions" is found before "git".
SKILL_TERMS = sorted(set(SKILL_TERMS), key=len, reverse=True)

# Terms that are also ordinary English (or a single letter). Matching these in
# prose puts skills you do not have into your applications — a Python style
# guide "mentions" R, and any page at all "mentions" Go. They only count when
# they sit in something that reads like a skills list: list punctuation on at
# least one side, and other recognised skills nearby. GitHub's language data
# is authoritative, so those still surface via expand_github().
AMBIGUOUS_TERMS = {"r", "go", "c#", "c++", "git", "excel", "swift", "rust",
                   "julia", "scala", "spark", "express", "dbt", "sas"}
_LIST_PUNCT = ",;|/•·\t\n()[]{}"
_CONTEXT_WINDOW = 80

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class Finding:
    """One fact, and the URL that proves it.

    `field` is the config key this could fill (or "" for informational
    findings). `confidence` is high/medium/low — it drives what `--apply`
    is willing to write without being asked.
    """

    def __init__(self, field: str, value, source: str, detail: str = "",
                 confidence: str = "high"):
        self.field = field
        self.value = value
        self.source = source
        self.detail = detail
        self.confidence = confidence

    def as_dict(self):
        return {"field": self.field, "value": self.value, "source": self.source,
                "detail": self.detail, "confidence": self.confidence}

    def __repr__(self):
        return f"<Finding {self.field or 'info'}={self.value!r} from {self.source}>"


class Report:
    """Findings, plus an honest account of what was skipped and why."""

    def __init__(self):
        self.findings = []
        self.skipped = []          # [(url, reason)]
        self.sources = []          # URLs actually read

    def add(self, finding):
        if finding is not None:
            self.findings.append(finding)

    def skip(self, url, reason):
        self.skipped.append((url, reason))

    def by_field(self):
        out = {}
        for f in self.findings:
            if f.field:
                out.setdefault(f.field, []).append(f)
        return out

    def as_dict(self):
        return {
            "sources_read": self.sources,
            "findings": [f.as_dict() for f in self.findings],
            "skipped": [{"url": u, "reason": r} for u, r in self.skipped],
        }


# ---------------------------------------------------------------------------
# Fetching — every request passes the robots.txt gate first
# ---------------------------------------------------------------------------

def robots_permits(url: str):
    """(allowed, reason). Import is local so robots stays an optional path."""
    try:
        from tools.robots_check import robots_allows
    except Exception as exc:                                  # pragma: no cover
        return False, f"robots checker unavailable ({exc})"
    try:
        v = robots_allows(url, USER_AGENT)
        return bool(v.allowed), (v.rule or v.reason)
    except Exception as exc:
        return False, f"robots check failed ({exc})"


def fetch_text(url: str, report: Report):
    """Fetch a page as plain text, or None with the reason recorded."""
    allowed, reason = robots_permits(url)
    if not allowed:
        report.skip(url, f"robots.txt: {reason}")
        return None
    try:
        import requests
        r = requests.get(url, timeout=TIMEOUT,
                         headers={"User-Agent": USER_AGENT}, allow_redirects=True)
    except Exception as exc:
        report.skip(url, f"fetch failed ({type(exc).__name__})")
        return None
    if r.status_code != 200:
        report.skip(url, f"HTTP {r.status_code}")
        return None
    report.sources.append(url)
    return r.text[:MAX_HTML]


def html_to_text(html: str) -> str:
    """Strip markup. Not a parser — enough to read prose out of a page."""
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    text = _ANY_TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", unescape(text)).strip()


def page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return _WS_RE.sub(" ", unescape(m.group(1))).strip() if m else ""


def _unambiguous_hits(low: str) -> list:
    out = []
    for term in SKILL_TERMS:
        if term in AMBIGUOUS_TERMS:
            continue
        # \b does not work around '+' or '.', so bound on non-word-ish chars.
        if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", low):
            out.append(term)
    return out


def _looks_like_a_skills_list(low: str, start: int, end: int, others: list) -> bool:
    """Is this occurrence sitting inside a list of skills, or just in a sentence?"""
    before = low[max(0, start - 2):start].strip()
    after = low[end:end + 2].strip()
    delimited = ((not before or before[-1] in _LIST_PUNCT) or
                 (not after or after[0] in _LIST_PUNCT))
    if not delimited:
        return False
    window = low[max(0, start - _CONTEXT_WINDOW):end + _CONTEXT_WINDOW]
    return any(o in window for o in others)


def find_skills(text: str) -> list:
    """Skill terms the text genuinely claims, in the order listed above.

    Ambiguous terms (see AMBIGUOUS_TERMS) must appear inside something that
    reads like a skills list, not merely somewhere on the page.
    """
    low = (text or "").lower()
    found = _unambiguous_hits(low)
    for term in SKILL_TERMS:
        if term not in AMBIGUOUS_TERMS:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        for m in re.finditer(pattern, low):
            if _looks_like_a_skills_list(low, m.start(), m.end(), found):
                found.append(term)
                break
    # Restore the canonical ordering (longest-first list order).
    return [t for t in SKILL_TERMS if t in set(found)]


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def expand_github(github_url: str, report: Report):
    """Languages you actually write, and the projects worth naming.

    Uses the GitHub REST API, which is published for this purpose — so it is
    not gated on robots.txt (that governs crawling the website, not the API).
    """
    from github_enrich import (
        extract_username,
        fetch_repos,
        github_signal_summary,
        rank_projects,
    )
    user = extract_username(github_url)
    if not user:
        return
    api = f"https://api.github.com/users/{user}/repos"
    repos = fetch_repos(user)
    if not repos:
        report.skip(api, "no public repos returned by the GitHub API")
        return
    report.sources.append(api)

    # Languages, most-used first, counted across original (non-fork) repos.
    counts = {}
    for r in repos:
        if r.get("fork"):
            continue
        lang = r.get("language")
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    langs = [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
    if langs:
        report.add(Finding(
            "skills", ", ".join(langs[:10]), api,
            f"languages across {len(repos)} public repos, most-used first"))

    summary = github_signal_summary(repos)
    report.add(Finding(
        "", summary, api,
        f"{summary['repos']} repos, {summary['open_source']} open-source, "
        f"{summary['total_stars']} stars", confidence="high"))

    top = rank_projects(repos, "", top=5)
    for r in top:
        name = r.get("name", "")
        desc = (r.get("description") or "").strip()
        stars = r.get("stargazers_count", 0) or 0
        report.add(Finding(
            "projects", name, r.get("html_url") or f"https://github.com/{user}/{name}",
            f"{desc or 'no description'} ({stars}★)",
            confidence="high" if desc else "medium"))


def expand_website(url: str, report: Report):
    """A personal site or portfolio: headline, and the stack it talks about."""
    html = fetch_text(url, report)
    if html is None:
        return
    title = page_title(html)
    if title:
        report.add(Finding("headline", title, url, "page title", confidence="medium"))
    text = html_to_text(html)
    skills = find_skills(text)
    if skills:
        report.add(Finding("skills", ", ".join(skills[:20]), url,
                           f"terms found on the page ({len(skills)} total)",
                           confidence="medium"))
    # Outbound project links worth a look, deduped, own-domain excluded.
    host = urlparse(url).netloc
    links = []
    for m in re.finditer(r'href=["\'](https?://[^"\']+)["\']', html or "", re.I):
        link = m.group(1)
        netloc = urlparse(link).netloc
        if netloc and netloc != host and any(
                k in netloc for k in ("github.com", "gitlab.com", "kaggle.com",
                                      "huggingface.co", "medium.com", "youtube.com")):
            if link not in links:
                links.append(link)
    if links:
        report.add(Finding("", links[:10], url, "project links found on your site"))


def expand_scholar(url: str, report: Report):
    """Google Scholar: publication count and citations, if the page is readable.

    Scholar disallows most automated access, so this usually — and correctly —
    reports as skipped rather than fetching anyway.
    """
    html = fetch_text(url, report)
    if html is None:
        return
    text = html_to_text(html)
    m = re.search(r"Cited by\s*([\d,]+)", text)
    if m:
        report.add(Finding("", f"{m.group(1)} citations", url,
                           "from the Scholar profile", confidence="medium"))
    titles = [unescape(t).strip() for t in
              re.findall(r'class="gsc_a_at"[^>]*>(.*?)</a>', html or "", re.S)]
    for t in titles[:10]:
        clean = _WS_RE.sub(" ", _ANY_TAG_RE.sub("", t)).strip()
        if clean:
            report.add(Finding("publications", clean, url, "Scholar publication"))


def expand_kaggle(url: str, report: Report):
    """Kaggle: competitions and notebooks are concrete, dated evidence."""
    html = fetch_text(url, report)
    if html is None:
        return
    text = html_to_text(html)
    for label in ("Competitions Grandmaster", "Competitions Master",
                  "Competitions Expert", "Notebooks Grandmaster",
                  "Notebooks Master", "Notebooks Expert",
                  "Datasets Master", "Datasets Expert"):
        if label in text:
            report.add(Finding("", label, url, "Kaggle tier", confidence="medium"))
    skills = find_skills(text)
    if skills:
        report.add(Finding("skills", ", ".join(skills[:15]), url,
                           "terms on the Kaggle profile", confidence="low"))


# ---------------------------------------------------------------------------
# Discovery + orchestration
# ---------------------------------------------------------------------------

SOURCE_MATCHERS = [
    ("github", re.compile(r"github\.com/[A-Za-z0-9-]+", re.I), expand_github),
    ("scholar", re.compile(r"scholar\.google\.[a-z.]+/citations", re.I), expand_scholar),
    ("kaggle", re.compile(r"kaggle\.com/[A-Za-z0-9_-]+", re.I), expand_kaggle),
]


def discover_sources(cfg: dict) -> list:
    """URLs to read, taken from config and the CV text. Deduped, order kept."""
    cfg = cfg or {}
    blobs = []
    qa = cfg.get("question_answers", {}) or {}
    personal = cfg.get("personal", {}) or {}
    for d in (qa, personal):
        for v in d.values():
            if isinstance(v, str) and "http" in v:
                blobs.append(v)
            elif isinstance(v, str) and ("github.com" in v or "kaggle.com" in v):
                blobs.append(v)
    for key in ("github", "portfolio", "website", "kaggle", "scholar"):
        for d in (qa, personal):
            v = d.get(key)
            if isinstance(v, str) and v.strip():
                blobs.append(v.strip())
    cv = (cfg.get("ai", {}) or {}).get("cv_text", "") or \
         (cfg.get("resume_tailoring", {}) or {}).get("master_resume_text", "")
    if cv:
        blobs.append(cv)

    urls, seen = [], set()
    for blob in blobs:
        for m in re.finditer(r"https?://[^\s,;)\]\"'<>]+", blob):
            u = m.group(0).rstrip(".,;)")
            if u not in seen and "linkedin.com" not in u.lower():
                seen.add(u)
                urls.append(u)
        # Bare github.com/user with no scheme is common in CVs.
        for m in re.finditer(r"(?<!//)\b((?:github|kaggle)\.com/[A-Za-z0-9_-]+)", blob, re.I):
            u = "https://" + m.group(1)
            if u not in seen:
                seen.add(u)
                urls.append(u)
    return urls


def source_kind(url: str) -> str:
    for name, rx, _ in SOURCE_MATCHERS:
        if rx.search(url):
            return name
    return "website"


def expand_profile(cfg: dict, extra_urls=None) -> Report:
    """Read every discovered source and return everything it could prove."""
    report = Report()
    urls = discover_sources(cfg) + [u for u in (extra_urls or []) if u]
    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        kind = source_kind(url)
        handler = next((h for n, _, h in SOURCE_MATCHERS if n == kind), expand_website)
        try:
            handler(url, report)
        except Exception as exc:
            report.skip(url, f"{kind} reader failed ({type(exc).__name__}: {exc})")
    return report


# ---------------------------------------------------------------------------
# Applying findings — blanks only, never an overwrite
# ---------------------------------------------------------------------------

APPLY_FIELDS = {
    "skills": ("question_answers", "skills"),
    "headline": ("question_answers", "headline"),
    "projects": ("question_answers", "projects"),
    "publications": ("question_answers", "publications"),
}


def apply_findings(cfg: dict, report: Report, min_confidence: str = "medium"):
    """Fill blank config fields from the findings. Returns (cfg, [changes]).

    A field already holding anything is left exactly as it is: this can only
    add what was missing, so re-running it is safe and repeatable.
    """
    order = {"low": 0, "medium": 1, "high": 2}
    floor = order.get(min_confidence, 1)
    cfg = dict(cfg or {})
    changes = []

    grouped = report.by_field()
    for field, (section, key) in APPLY_FIELDS.items():
        findings = [f for f in grouped.get(field, [])
                    if order.get(f.confidence, 0) >= floor]
        if not findings:
            continue
        sect = dict(cfg.get(section, {}) or {})
        if str(sect.get(key, "")).strip():
            continue                      # the user's own answer stands
        if field in ("projects", "publications"):
            value = "; ".join(str(f.value) for f in findings[:5])
        else:
            # Merge skill lists across sources, first mention wins, no repeats.
            parts, seen = [], set()
            for f in findings:
                for piece in str(f.value).split(","):
                    p = piece.strip()
                    if p and p.lower() not in seen:
                        seen.add(p.lower())
                        parts.append(p)
            value = ", ".join(parts[:25])
        if not value:
            continue
        sect[key] = value
        cfg[section] = sect
        changes.append((f"{section}.{key}", value,
                        [f.source for f in findings[:5]]))
    return cfg, changes


def _yaml_line(key: str, value, indent: str = "  ") -> str:
    """One correctly-quoted `key: value` line, produced by the YAML dumper."""
    import yaml
    dumped = yaml.safe_dump({key: value}, default_flow_style=False,
                            allow_unicode=True, sort_keys=False).rstrip("\n")
    return "\n".join(indent + ln for ln in dumped.splitlines())


def insert_into_yaml(text: str, section: str, key: str, value) -> str:
    """Add `section.key` to YAML source, keeping comments and layout intact.

    config.yaml is mostly documentation — the shipped template carries several
    hundred comment lines explaining every option. Round-tripping it through
    yaml.safe_dump would silently delete all of them, so additions are spliced
    in as text instead. Returns the text unchanged if the key already exists.
    """
    lines = text.splitlines()
    head = re.compile(r"^" + re.escape(section) + r"\s*:\s*(#.*)?$")
    start = next((i for i, ln in enumerate(lines) if head.match(ln)), None)

    if start is None:
        block = f"\n{section}:\n" + _yaml_line(key, value) + "\n"
        return text.rstrip("\n") + "\n" + block

    # The block runs until the next line at column 0 that starts a new key.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.strip() and not ln[0].isspace():
            end = i
            break

    body = lines[start + 1:end]
    existing = re.compile(r"^\s+" + re.escape(key) + r"\s*:")
    if any(existing.match(ln) for ln in body):
        return text                       # already present — never overwrite

    indent = next((re.match(r"^(\s+)", ln).group(1)
                   for ln in body if ln.strip() and re.match(r"^(\s+)", ln)), "  ")
    # Insert after the block's last non-blank line, so trailing blank lines and
    # the comments that follow them stay attached to whatever comes next.
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    lines.insert(at, _yaml_line(key, value, indent))
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def write_config_additions(path: str, changes, backup: bool = True):
    """Apply `changes` to the config file on disk. Returns (ok, message).

    The file is only replaced once the new text has been re-parsed and checked
    to contain exactly what we meant to add — a config this bot depends on is
    not worth corrupting for a convenience feature.
    """
    import yaml
    try:
        with open(path, encoding="utf-8") as fh:
            original = fh.read()
    except OSError as exc:
        return False, f"could not read {path}: {exc}"

    text = original
    for dotted, value, _sources in changes:
        section, _, key = dotted.partition(".")
        text = insert_into_yaml(text, section, key, value)

    try:
        parsed = yaml.safe_load(text) or {}
    except Exception as exc:
        return False, f"refused to write — the result would not parse ({exc})"
    for dotted, value, _sources in changes:
        section, _, key = dotted.partition(".")
        if (parsed.get(section) or {}).get(key) != value:
            return False, f"refused to write — {dotted} did not come out as expected"

    if backup:
        try:
            with open(path + ".bak", "w", encoding="utf-8") as fh:
                fh.write(original)
        except OSError as exc:
            return False, f"could not write the backup ({exc}) — nothing changed"
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        return False, f"could not write {path}: {exc}"
    return True, f"{path} updated (previous version saved as {path}.bak)"


def format_report(report: Report, changes=None) -> str:
    """The human-readable version — every line carries its source."""
    out = []
    grouped = report.by_field()
    info = [f for f in report.findings if not f.field]

    if not report.findings:
        out.append("  Nothing found. Add a GitHub, portfolio, Kaggle or Scholar")
        out.append("  URL to config.yaml (question_answers) or to your CV.")
    for field in sorted(grouped):
        out.append(f"\n  {field.upper()}")
        for f in grouped[field]:
            out.append(f"    • {f.value}")
            if f.detail:
                out.append(f"      {f.detail}")
            out.append(f"      source: {f.source}  [{f.confidence}]")
    if info:
        out.append("\n  CONTEXT")
        for f in info:
            value = f.value
            if isinstance(value, dict):
                value = ", ".join(f"{k}={v}" for k, v in value.items()
                                  if k != "warnings")
                for w in f.value.get("warnings", []):
                    out.append(f"    ! {w}")
            elif isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            out.append(f"    • {value}")
            out.append(f"      source: {f.source}")

    # A source that was read but proved nothing is worth saying out loud —
    # otherwise it looks like it was never tried.
    productive = {f.source for f in report.findings}
    empty = [u for u in report.sources if u not in productive]
    if empty:
        out.append("\n  READ, NOTHING FOUND")
        for url in empty:
            out.append(f"    - {url}")

    if report.skipped:
        out.append("\n  NOT READ")
        for url, reason in report.skipped:
            out.append(f"    - {url}")
            out.append(f"      {reason}")

    if changes:
        out.append("\n  APPLIED (blank fields only)")
        for key, value, sources in changes:
            shown = value if len(str(value)) <= 100 else str(value)[:97] + "..."
            out.append(f"    {key} = {shown}")
            for s in sources:
                out.append(f"      from: {s}")
    elif changes is not None:
        out.append("\n  Nothing applied — those fields already have answers.")
    return "\n".join(out)


def to_json(report: Report, changes=None) -> str:
    data = report.as_dict()
    if changes is not None:
        data["applied"] = [{"key": k, "value": v, "sources": s} for k, v, s in changes]
    return json.dumps(data, indent=2, default=str)
