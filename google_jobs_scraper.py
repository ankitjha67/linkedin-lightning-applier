"""
Google Jobs Scraper.

Discovers jobs from Google Jobs search (google.com/search?q=...&ibp=htl;jobs).
Feeds discovered jobs back into the main pipeline:
- LinkedIn jobs → navigate directly to linkedin.com/jobs/view/{id}
- ATS jobs (Greenhouse, Lever, Workday) → hand to ExternalApplier
- Other platforms → store for future processing
"""

import hashlib
import logging
import re
import time
import random
from typing import Optional
from urllib.parse import urlencode, quote_plus

log = logging.getLogger("lla.google_jobs")

# Platform detection patterns
PLATFORM_PATTERNS = {
    "linkedin": [r"linkedin\.com/jobs/view/(\d+)", r"linkedin\.com/jobs/.*currentJobId=(\d+)"],
    "greenhouse": [r"boards\.greenhouse\.io", r"greenhouse\.io"],
    "lever": [r"jobs\.lever\.co", r"lever\.co"],
    "workday": [r"myworkday\.com", r"workday\.com"],
    "ashby": [r"jobs\.ashbyhq\.com", r"ashbyhq\.com"],
    "indeed": [r"indeed\.com/viewjob", r"indeed\.com/rc/clk"],
    "glassdoor": [r"glassdoor\.com/job-listing", r"glassdoor\.com/partner"],
    "ziprecruiter": [r"ziprecruiter\.com/c"],
    "angellist": [r"angel\.co/company", r"wellfound\.com"],
}

# Google Jobs date filter mapping
DATE_FILTERS = {
    "today": "date_posted:today",
    "3days": "date_posted:3days",
    "week": "date_posted:week",
    "month": "date_posted:month",
}


class GoogleJobsScraper:
    """Scrape Google Jobs for job discovery across all platforms."""

    def __init__(self, cfg: dict, state):
        self.cfg = cfg
        self.state = state
        gj_cfg = cfg.get("google_jobs", {})
        self.enabled = gj_cfg.get("enabled", False)
        self.use_serpapi = gj_cfg.get("use_serpapi", False)
        self.serpapi_key = gj_cfg.get("serpapi_key", "")
        self.max_results = gj_cfg.get("max_results_per_query", 50)
        self.search_queries = gj_cfg.get("search_queries", [])
        self.country_code = gj_cfg.get("country_code", "uk")
        self.date_posted = gj_cfg.get("date_posted", "today")
        self.deduplicate = gj_cfg.get("deduplicate_with_linkedin", True)

        # Auto-generate queries from main search config if none specified
        if not self.search_queries:
            search_cfg = cfg.get("search", {})
            terms = search_cfg.get("search_terms", [])
            locs = search_cfg.get("search_locations", [])
            for term in terms[:5]:  # Limit to top 5 terms
                for loc in locs[:3]:  # Limit to top 3 locations
                    loc_short = loc.split(",")[0].strip()
                    self.search_queries.append(f"{term} jobs in {loc_short}")

    def scrape_jobs(self, driver=None) -> list[dict]:
        """
        Scrape Google Jobs. Uses Selenium if driver provided, else SerpAPI or requests.

        Returns list of job dicts with keys:
            title, company, location, description, apply_url, source_platform,
            linkedin_job_id, google_job_id, salary_raw
        """
        if not self.enabled:
            return []

        all_jobs = []
        for query in self.search_queries:
            try:
                if self.use_serpapi and self.serpapi_key:
                    jobs = self._scrape_with_serpapi(query)
                elif driver:
                    jobs = self._scrape_with_selenium(driver, query)
                else:
                    jobs = self._scrape_with_requests(query)

                # Deduplicate and store
                for job in jobs:
                    job_id = job.get("google_job_id", "")
                    if not job_id:
                        # Generate ID from title + company
                        raw = f"{job.get('title', '')}|{job.get('company', '')}"
                        job_id = hashlib.md5(raw.encode()).hexdigest()[:16]
                        job["google_job_id"] = job_id

                    # Check for LinkedIn job ID
                    linkedin_id = self._extract_linkedin_job_id(job.get("apply_url", ""))
                    job["linkedin_job_id"] = linkedin_id or ""

                    # Skip if already applied on LinkedIn
                    if self.deduplicate and linkedin_id and self.state.is_applied(linkedin_id):
                        continue

                    # Detect source platform
                    if not job.get("source_platform"):
                        job["source_platform"] = self._detect_source_platform(job.get("apply_url", ""))

                    # Save to DB
                    self.state.save_google_job(
                        google_job_id=job_id,
                        title=job.get("title", ""),
                        company=job.get("company", ""),
                        location=job.get("location", ""),
                        description=job.get("description", ""),
                        salary_raw=job.get("salary_raw", ""),
                        source_url=job.get("apply_url", ""),
                        source_platform=job.get("source_platform", ""),
                        linkedin_job_id=job.get("linkedin_job_id", ""),
                    )
                    all_jobs.append(job)

                log.info(f"   🔍 Google Jobs: '{query}' → {len(jobs)} found, {len(all_jobs)} new total")
                time.sleep(random.uniform(2, 5))  # Rate limit

            except Exception as e:
                log.warning(f"Google Jobs scrape failed for '{query}': {e}")

        return all_jobs

    def _scrape_with_selenium(self, driver, query: str) -> list[dict]:
        """Use existing browser to scrape Google Jobs (handles JS rendering)."""
        jobs = []

        # Build Google Jobs URL
        date_filter = DATE_FILTERS.get(self.date_posted, "")
        params = {"q": query, "ibp": "htl;jobs"}
        if date_filter:
            params["chips"] = date_filter
        if self.country_code:
            params["gl"] = self.country_code

        url = f"https://www.google.com/search?{urlencode(params)}"
        log.debug(f"  Google Jobs URL: {url[:120]}")

        # Save current URL to return later
        original_url = driver.current_url

        try:
            driver.get(url)
            time.sleep(3)

            from selenium.webdriver.common.by import By

            # Google Jobs renders in a special widget
            # Look for job cards within the jobs panel
            job_cards = driver.find_elements(By.CSS_SELECTOR,
                'li.iFjolb, div.PwjeAc, div[data-hveid] div.nJlQNd, div.gws-plugins-horizon-jobs__li-ed')

            if not job_cards:
                # Try alternative selectors
                job_cards = driver.find_elements(By.CSS_SELECTOR,
                    '[jscontroller] li, div[data-ved] div[jsaction*="click"]')

            for i, card in enumerate(job_cards[:self.max_results]):
                try:
                    # Click the card to load details
                    card.click()
                    time.sleep(0.8)

                    job = self._extract_google_job_details(driver, card)
                    if job and job.get("title"):
                        jobs.append(job)
                except Exception:
                    continue

        except Exception as e:
            log.warning(f"Selenium Google Jobs scrape error: {e}")
        finally:
            # Return to original page
            try:
                driver.get(original_url)
                time.sleep(2)
            except Exception:
                pass

        return jobs

    def _extract_google_job_details(self, driver, card) -> dict:
        """Extract job details from a Google Jobs card + detail panel."""
        from selenium.webdriver.common.by import By

        job = {"title": "", "company": "", "location": "", "description": "",
               "apply_url": "", "salary_raw": "", "source_platform": ""}

        # Title
        for sel in ['h2', '.BjJfJf', '.PUpOsf', '[role="heading"]', '.nJlQNd']:
            try:
                el = card.find_element(By.CSS_SELECTOR, sel)
                if el.text.strip():
                    job["title"] = el.text.strip()
                    break
            except Exception:
                continue

        # Company
        for sel in ['.vNEEBe', '.nJlQNd + div', '.company-name']:
            try:
                el = card.find_element(By.CSS_SELECTOR, sel)
                if el.text.strip():
                    job["company"] = el.text.strip().split("\n")[0]
                    break
            except Exception:
                continue

        # Location
        for sel in ['.Qk80Jf', '.location']:
            try:
                el = card.find_element(By.CSS_SELECTOR, sel)
                if el.text.strip():
                    job["location"] = el.text.strip()
                    break
            except Exception:
                continue

        # Detail panel — description and apply link
        try:
            # Description from detail panel
            for desc_sel in ['.HBvzbc', '.YgLbBe', '#gws-plugins-horizon-jobs__job_details_page',
                            '[class*="job-details"]', '.pjBnF']:
                descs = driver.find_elements(By.CSS_SELECTOR, desc_sel)
                if descs:
                    job["description"] = descs[0].text.strip()[:2000]
                    break

            # Apply button/link
            for link_sel in ['a.pMhGee', 'a[data-ved*="apply"]', '.KDMqBe a',
                            'a[href*="apply"]', '.pjBnF a']:
                links = driver.find_elements(By.CSS_SELECTOR, link_sel)
                for link in links:
                    href = link.get_attribute("href") or ""
                    if href and "google.com" not in href:
                        job["apply_url"] = href
                        break
                if job["apply_url"]:
                    break

            # Salary
            for sal_sel in ['.SuWscb', '.salary']:
                sals = driver.find_elements(By.CSS_SELECTOR, sal_sel)
                if sals and sals[0].text.strip():
                    job["salary_raw"] = sals[0].text.strip()
                    break

        except Exception:
            pass

        return job

    def _scrape_with_serpapi(self, query: str) -> list[dict]:
        """Use SerpAPI Google Jobs endpoint for reliable structured data."""
        try:
            from serpapi import GoogleSearch
        except ImportError:
            log.warning("serpapi not installed. Run: pip install google-search-results")
            return self._scrape_with_requests(query)

        jobs = []
        try:
            params = {
                "engine": "google_jobs",
                "q": query,
                "api_key": self.serpapi_key,
                "gl": self.country_code,
            }
            if self.date_posted in DATE_FILTERS:
                params["chips"] = DATE_FILTERS[self.date_posted]

            search = GoogleSearch(params)
            results = search.get_dict()

            for item in results.get("jobs_results", [])[:self.max_results]:
                apply_links = item.get("apply_options", [])
                apply_url = apply_links[0].get("link", "") if apply_links else ""

                jobs.append({
                    "title": item.get("title", ""),
                    "company": item.get("company_name", ""),
                    "location": item.get("location", ""),
                    "description": item.get("description", "")[:2000],
                    "apply_url": apply_url,
                    "salary_raw": item.get("detected_extensions", {}).get("salary", ""),
                    "google_job_id": item.get("job_id", ""),
                    "source_platform": "",
                })

        except Exception as e:
            log.warning(f"SerpAPI error: {e}")

        return jobs

    def _scrape_with_requests(self, query: str) -> list[dict]:
        """Headless scraping with requests + BeautifulSoup (less reliable for Google Jobs)."""
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            log.warning("requests/beautifulsoup4 not installed. Google Jobs scraping unavailable.")
            return []

        jobs = []
        try:
            params = {"q": query, "ibp": "htl;jobs"}
            if self.country_code:
                params["gl"] = self.country_code

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }

            url = f"https://www.google.com/search?{urlencode(params)}"
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Google Jobs data is often in script tags as JSON
            for script in soup.find_all("script"):
                text = script.string or ""
                if "job" in text.lower() and "title" in text.lower():
                    # Try to extract job data from embedded JSON
                    self._extract_jobs_from_script(text, jobs)

            # Also try HTML cards
            for card in soup.select("li.iFjolb, div.PwjeAc"):
                title_el = card.select_one("h2, .BjJfJf")
                company_el = card.select_one(".vNEEBe")
                location_el = card.select_one(".Qk80Jf")

                if title_el:
                    jobs.append({
                        "title": title_el.get_text(strip=True),
                        "company": company_el.get_text(strip=True) if company_el else "",
                        "location": location_el.get_text(strip=True) if location_el else "",
                        "description": "",
                        "apply_url": "",
                        "salary_raw": "",
                        "source_platform": "",
                    })

        except Exception as e:
            log.warning(f"Requests-based Google Jobs scrape failed: {e}")

        return jobs

    def _extract_jobs_from_script(self, script_text: str, jobs: list):
        """Try to extract job data from Google's embedded script data."""
        import json

        # Google often embeds job data in JavaScript arrays
        # This is fragile but catches some cases
        try:
            # Look for JSON-like structures with job fields
            matches = re.findall(r'\{[^{}]*"title"[^{}]*"company"[^{}]*\}', script_text)
            for match in matches[:self.max_results]:
                try:
                    data = json.loads(match)
                    if data.get("title"):
                        jobs.append({
                            "title": data.get("title", ""),
                            "company": data.get("company", data.get("company_name", "")),
                            "location": data.get("location", ""),
                            "description": data.get("description", "")[:2000],
                            "apply_url": data.get("apply_link", data.get("url", "")),
                            "salary_raw": data.get("salary", ""),
                            "source_platform": "",
                        })
                except (json.JSONDecodeError, TypeError):
                    continue
        except Exception:
            pass

    def _detect_source_platform(self, url: str) -> str:
        """Detect which platform/ATS the apply URL points to."""
        if not url:
            return "unknown"

        for platform, patterns in PLATFORM_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    return platform

        return "other"

    def _extract_linkedin_job_id(self, url: str) -> Optional[str]:
        """Extract LinkedIn job ID from URL if it's a LinkedIn job."""
        if not url:
            return None

        for pattern in PLATFORM_PATTERNS.get("linkedin", []):
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    def get_queued_linkedin_jobs(self) -> list[dict]:
        """Get Google-discovered jobs that link to LinkedIn, ready for processing."""
        jobs = self.state.get_google_jobs_by_status("new")
        return [j for j in jobs if j.get("source_platform") == "linkedin" and j.get("linkedin_job_id")]

    def get_queued_ats_jobs(self) -> list[dict]:
        """Get Google-discovered jobs that link to external ATS."""
        jobs = self.state.get_google_jobs_by_status("new")
        ats_platforms = {"greenhouse", "lever", "workday", "ashby"}
        return [j for j in jobs if j.get("source_platform") in ats_platforms]

    def get_queued_other_jobs(self) -> list[dict]:
        """Get Google-discovered jobs from other platforms."""
        jobs = self.state.get_google_jobs_by_status("new")
        known = {"linkedin", "greenhouse", "lever", "workday", "ashby"}
        return [j for j in jobs if j.get("source_platform") not in known]
