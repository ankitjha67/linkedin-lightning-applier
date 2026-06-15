"""
Careers-Page Scanner.

Scans a curated database of company careers pages (companies.json) for new
job postings, using free ATS JSON APIs (Greenhouse, Lever, Ashby) where
available and requests+BeautifulSoup scraping as a fallback.

This complements the LinkedIn/Google Jobs scrapers with a targeted,
company-first discovery approach — the same nightly-scan model used by
tools like autopilot-jobhunt, but with zero paid API dependency.

Discovered jobs are scored with MatchScorer, stored in the google_jobs
table (reused as a generic discovery table), and can trigger Telegram alerts.
"""

import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("lla.careers")


class CareersScanner:
    """Scan curated company careers pages via ATS APIs + scraping."""

    def __init__(self, cfg: dict, state, ai=None, scorer=None):
        self.cfg = cfg
        self.state = state
        self.ai = ai
        self.scorer = scorer
        cs_cfg = cfg.get("careers_scanner", {})
        self.enabled = cs_cfg.get("enabled", False)
        self.companies_file = cs_cfg.get("companies_file", "companies.json")
        self.max_companies_per_scan = cs_cfg.get("max_companies_per_scan", 30)
        self.min_score = cs_cfg.get("min_score", 60)
        self.request_delay = cs_cfg.get("request_delay_seconds", 2.0)
        self.regions = cs_cfg.get("regions", [])  # empty = all
        self.title_keywords = cs_cfg.get("title_keywords", [])  # empty = all

    def load_companies(self) -> list[dict]:
        """Load the curated companies database."""
        path = Path(self.companies_file)
        if not path.exists():
            log.warning(f"Companies file not found: {self.companies_file}")
            return []
        try:
            companies = json.loads(path.read_text())
            if self.regions:
                companies = [c for c in companies if c.get("region") in self.regions]
            return companies
        except Exception as e:
            log.warning(f"Failed to load companies: {e}")
            return []

    def scan(self, score_jobs: bool = True) -> list[dict]:
        """
        Scan all configured companies for new jobs.

        Returns list of new job dicts: {title, company, location, url, ats, score}
        """
        if not self.enabled:
            return []

        companies = self.load_companies()
        if not companies:
            return []

        log.info(f"Careers scan: {len(companies)} companies")
        all_jobs = []

        for company in companies[:self.max_companies_per_scan]:
            name = company.get("name", "Unknown")
            try:
                jobs = self._scan_company(company)
                if not jobs:
                    continue

                new_jobs = []
                for job in jobs:
                    # Title keyword filter
                    if self.title_keywords:
                        title_lower = job["title"].lower()
                        if not any(k.lower() in title_lower for k in self.title_keywords):
                            continue

                    # Dedup: skip if already in google_jobs by URL hash
                    import hashlib
                    gid = hashlib.md5(job["url"].encode()).hexdigest()[:16]
                    existing = self.state.conn.execute(
                        "SELECT 1 FROM google_jobs WHERE google_job_id=?", (gid,)
                    ).fetchone()
                    if existing:
                        continue

                    job["google_job_id"] = gid
                    new_jobs.append(job)

                # Score new jobs
                if score_jobs and self.scorer and self.scorer.enabled:
                    for job in new_jobs:
                        result = self.scorer.score_job(
                            job["title"], job["company"],
                            job.get("description", ""), job.get("location", "")
                        )
                        job["score"] = result.get("score", 0)
                        job["reason"] = result.get("explanation", "")

                # Persist to google_jobs table
                for job in new_jobs:
                    self.state.save_google_job(
                        google_job_id=job["google_job_id"],
                        title=job["title"], company=job["company"],
                        location=job.get("location", ""),
                        description=job.get("description", ""),
                        source_url=job["url"],
                        source_platform=job.get("ats", "careers"),
                    )

                if new_jobs:
                    log.info(f"  {name}: {len(new_jobs)} new jobs")
                    all_jobs.extend(new_jobs)

                time.sleep(self.request_delay)

            except Exception as e:
                log.debug(f"  {name}: scan failed — {e}")
                continue

        # Filter by min score for the return value
        if score_jobs:
            qualified = [j for j in all_jobs if j.get("score", 0) >= self.min_score]
            log.info(f"Careers scan complete: {len(all_jobs)} new, {len(qualified)} above score {self.min_score}")
            return sorted(qualified, key=lambda j: j.get("score", 0), reverse=True)

        return all_jobs

    def _scan_company(self, company: dict) -> list[dict]:
        """Scan a single company using its ATS API or scraping."""
        ats = company.get("ats", "").lower()
        company.get("name", "")

        if ats == "greenhouse":
            return self._scan_greenhouse(company)
        if ats == "lever":
            return self._scan_lever(company)
        if ats == "ashby":
            return self._scan_ashby(company)
        # Fallback: scrape the careers URL
        return self._scrape_careers(company)

    def _ats_slug(self, company: dict) -> str:
        """Derive an ATS board slug from company name or URL."""
        # Try explicit slug, else lowercase name with no spaces
        slug = company.get("ats_slug", "")
        if slug:
            return slug
        return re.sub(r"[^a-z0-9]", "", company.get("name", "").lower())

    def _scan_greenhouse(self, company: dict) -> list[dict]:
        """Greenhouse public board API (free, no auth)."""
        try:
            import requests
        except ImportError:
            return []
        slug = self._ats_slug(company)
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                return self._scrape_careers(company)
            data = resp.json()
            jobs = []
            for j in data.get("jobs", []):
                jobs.append({
                    "title": j.get("title", ""),
                    "company": company["name"],
                    "location": (j.get("location") or {}).get("name", ""),
                    "url": j.get("absolute_url", ""),
                    "description": self._strip_html(j.get("content", ""))[:2000],
                    "ats": "greenhouse",
                })
            return jobs
        except Exception:
            return self._scrape_careers(company)

    def _scan_lever(self, company: dict) -> list[dict]:
        """Lever public postings API (free, no auth)."""
        try:
            import requests
        except ImportError:
            return []
        slug = self._ats_slug(company)
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                return self._scrape_careers(company)
            data = resp.json()
            jobs = []
            for j in data:
                cats = j.get("categories", {})
                jobs.append({
                    "title": j.get("text", ""),
                    "company": company["name"],
                    "location": cats.get("location", ""),
                    "url": j.get("hostedUrl", ""),
                    "description": self._strip_html(j.get("descriptionPlain", j.get("description", "")))[:2000],
                    "ats": "lever",
                })
            return jobs
        except Exception:
            return self._scrape_careers(company)

    def _scan_ashby(self, company: dict) -> list[dict]:
        """Ashby public job board API (free, no auth)."""
        try:
            import requests
        except ImportError:
            return []
        slug = self._ats_slug(company)
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                return self._scrape_careers(company)
            data = resp.json()
            jobs = []
            for j in data.get("jobs", []):
                jobs.append({
                    "title": j.get("title", ""),
                    "company": company["name"],
                    "location": j.get("location", ""),
                    "url": j.get("jobUrl", ""),
                    "description": self._strip_html(j.get("descriptionPlain", ""))[:2000],
                    "ats": "ashby",
                })
            return jobs
        except Exception:
            return self._scrape_careers(company)

    def _scrape_careers(self, company: dict) -> list[dict]:
        """Fallback: scrape the careers page for job links."""
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        url = company.get("careers_url", "")
        if not url:
            return []

        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; JobScanner/1.0)"}
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")

            jobs = []
            seen = set()
            job_link_re = re.compile(
                r"/(job|jobs|opening|openings|position|positions|career|careers|role|roles|vacancy)/",
                re.IGNORECASE,
            )
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)
                if not text or len(text) < 5:
                    continue
                if job_link_re.search(href):
                    full_url = href if href.startswith("http") else self._join_url(url, href)
                    if full_url in seen:
                        continue
                    seen.add(full_url)
                    jobs.append({
                        "title": text[:120],
                        "company": company["name"],
                        "location": company.get("location", ""),
                        "url": full_url,
                        "description": "",
                        "ats": "scraped",
                    })
            return jobs[:50]
        except Exception:
            return []

    @staticmethod
    def _join_url(base: str, path: str) -> str:
        from urllib.parse import urljoin
        return urljoin(base, path)

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags from a string."""
        if not html:
            return ""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def get_scan_summary(self, jobs: list[dict]) -> str:
        """Format a scan summary for notifications."""
        if not jobs:
            return "No new matching jobs found."
        lines = [f"Careers Scan — {len(jobs)} matches\n"]
        for i, j in enumerate(jobs[:10], 1):
            score = j.get("score", "")
            score_str = f" [{score}/100]" if score else ""
            lines.append(f"#{i} {j['company']} — {j['title']}{score_str}")
            if j.get("location"):
                lines.append(f"   {j['location']}")
            if j.get("url"):
                lines.append(f"   {j['url']}")
        return "\n".join(lines)
