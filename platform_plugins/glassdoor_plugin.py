"""
Glassdoor Platform Plugin (scaffold).

Implements the JobPlatform interface for Glassdoor.com.
To be fully implemented when multi-platform support is activated.
"""

import logging
import re
import time
from typing import Optional
from urllib.parse import urlencode

from .base import JobPlatform

log = logging.getLogger("lla.glassdoor")


class GlassdoorPlugin(JobPlatform):
    """Glassdoor job platform implementation (scaffold)."""

    @property
    def name(self) -> str:
        return "glassdoor"

    def create_browser(self, cfg: dict):
        from linkedin import create_browser
        return create_browser(cfg)

    def login(self, driver, cfg: dict) -> bool:
        driver.get("https://www.glassdoor.com")
        time.sleep(3)
        return True

    def build_search_url(self, cfg: dict, term: str, location: str) -> str:
        loc_short = location.split(",")[0].strip()
        params = {"sc.keyword": term, "locT": "C", "locKeyword": loc_short}
        return f"https://www.glassdoor.com/Job/jobs.htm?{urlencode(params)}"

    def navigate_to_search(self, driver, url: str):
        driver.get(url)
        time.sleep(4)

    def get_job_cards(self, driver) -> list:
        from selenium.webdriver.common.by import By
        for sel in ['li.react-job-listing', 'li[data-test="jobListing"]',
                     'ul.job-list li']:
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if cards:
                return cards
        return []

    def extract_job_info(self, driver, card) -> Optional[dict]:
        from selenium.webdriver.common.by import By
        try:
            title_el = card.find_elements(By.CSS_SELECTOR, 'a[class*="jobTitle"], .job-title')
            company_el = card.find_elements(By.CSS_SELECTOR, '.job-search-key-l2hmii, .employer-name')
            location_el = card.find_elements(By.CSS_SELECTOR, '.job-search-key-1p8h1nm, .loc')

            title = title_el[0].text.strip() if title_el else ""
            company = company_el[0].text.strip() if company_el else ""
            location = location_el[0].text.strip() if location_el else ""

            job_id = card.get_attribute("data-id") or ""
            href = ""
            if title_el and title_el[0].tag_name == "a":
                href = title_el[0].get_attribute("href") or ""
            if not job_id and href:
                m = re.search(r'jobListingId=(\d+)', href)
                job_id = m.group(1) if m else ""

            return {
                "title": title, "company": company, "location": location,
                "job_id": f"glassdoor_{job_id}", "job_url": href,
            } if title else None
        except Exception:
            return None

    def get_job_description(self, driver) -> str:
        from selenium.webdriver.common.by import By
        time.sleep(1)
        for sel in ['#JobDescriptionContainer', '.desc', '[class*="jobDescriptionContent"]']:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                return els[0].text.strip()
        return ""

    def apply_to_job(self, driver, cfg: dict, ai=None, job_context: dict = None) -> bool:
        from selenium.webdriver.common.by import By
        # Glassdoor typically redirects to company's ATS
        btns = driver.find_elements(By.CSS_SELECTOR,
            'button[data-test="applyButton"], a.applyButton, button.apply-button')
        for btn in btns:
            if btn.is_displayed():
                btn.click()
                time.sleep(3)
                return False  # Returns False — ExternalApplier handles the ATS page
        return False
