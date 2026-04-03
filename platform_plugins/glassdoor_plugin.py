"""
Glassdoor Platform Plugin.

Implements the JobPlatform interface for Glassdoor.com.
Glassdoor typically redirects to company ATS for applications,
so the apply flow delegates to ExternalApplier.
"""

import logging
import re
import time
from typing import Optional
from urllib.parse import urlencode, quote_plus

from .base import JobPlatform

log = logging.getLogger("lla.glassdoor")

SELECTORS = {
    "job_cards": [
        "li.react-job-listing",
        'li[data-test="jobListing"]',
        "li[data-id]",
        "ul.job-list li",
    ],
    "title": [
        'a[data-test="job-title"]',
        "a.jobTitle",
        'a[class*="jobTitle"]',
        ".job-title",
    ],
    "company": [
        'span[class*="EmployerProfile"]',
        ".employer-name",
        'div[data-test="emp-name"]',
        'a[data-test="employer-short-name"]',
    ],
    "location": [
        'span[data-test="emp-location"]',
        ".loc",
        'span[class*="location"]',
    ],
    "salary": [
        'span[data-test="detailSalary"]',
        ".salary-estimate",
        'div[class*="SalaryEstimate"]',
    ],
    "description": [
        "#JobDescriptionContainer",
        ".desc",
        'div[class*="jobDescriptionContent"]',
        'div[data-test="jobDescriptionContent"]',
        'section[class*="JobDetails"]',
    ],
    "apply_button": [
        'button[data-test="applyButton"]',
        "button.apply-button",
        'a[data-test="applyButton"]',
        "a.applyButton",
    ],
}


class GlassdoorPlugin(JobPlatform):
    """Glassdoor job platform implementation."""

    @property
    def name(self) -> str:
        return "glassdoor"

    @property
    def display_name(self) -> str:
        return "Glassdoor"

    @property
    def requires_login(self) -> bool:
        return False  # Search works without login (may hit modal prompts)

    @property
    def supports_easy_apply(self) -> bool:
        return False  # Glassdoor always redirects to company ATS

    def create_browser(self, cfg: dict):
        from linkedin import create_browser
        return create_browser(cfg)

    def login(self, driver, cfg: dict) -> bool:
        """Navigate to Glassdoor. Login optional (dismiss prompts)."""
        try:
            driver.get("https://www.glassdoor.com")
            time.sleep(3)
            self._dismiss_popups(driver)
            return True
        except Exception as e:
            log.warning(f"Glassdoor navigation failed: {e}")
            return False

    def build_search_url(self, cfg: dict, term: str, location: str) -> str:
        loc_short = location.split(",")[0].strip()
        # Glassdoor uses a specific URL format
        term_slug = quote_plus(term)
        loc_slug = quote_plus(loc_short)
        params = {
            "sc.keyword": term,
            "locT": "C",
            "locKeyword": loc_short,
        }

        sc = cfg.get("search", {})
        dp = sc.get("date_posted", "Past 24 hours")
        date_map = {
            "Past 24 hours": "1",
            "Past week": "7",
            "Past month": "30",
        }
        if dp in date_map:
            params["fromAge"] = date_map[dp]

        return f"https://www.glassdoor.com/Job/jobs.htm?{urlencode(params)}"

    def navigate_to_search(self, driver, url: str):
        log.debug(f"Navigating: {url[:120]}")
        driver.get(url)
        time.sleep(4)
        self._dismiss_popups(driver)

        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    ", ".join(SELECTORS["job_cards"][:3])))
            )
        except Exception:
            log.debug("No Glassdoor job cards found after waiting")

    def get_job_cards(self, driver) -> list:
        from selenium.webdriver.common.by import By

        # Scroll to load content
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 500);")
            time.sleep(0.5)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)

        for sel in SELECTORS["job_cards"]:
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if cards:
                log.info(f"Found {len(cards)} Glassdoor cards via '{sel}'")
                return cards
        return []

    def extract_job_info(self, driver, card) -> Optional[dict]:
        from selenium.webdriver.common.by import By

        try:
            # Title
            title = ""
            title_el = None
            for sel in SELECTORS["title"]:
                els = card.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    title = els[0].text.strip()
                    title_el = els[0]
                    break
            if not title:
                return None

            # Company
            company = ""
            for sel in SELECTORS["company"]:
                els = card.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    company = els[0].text.strip()
                    break

            # Location
            location = ""
            for sel in SELECTORS["location"]:
                els = card.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    location = els[0].text.strip()
                    break

            # Job ID
            job_id = card.get_attribute("data-id") or ""
            href = ""
            if title_el and title_el.tag_name == "a":
                href = title_el.get_attribute("href") or ""
            if not job_id and href:
                m = re.search(r'jobListingId=(\d+)', href)
                if m:
                    job_id = m.group(1)
            is_real_id = bool(job_id)
            if not job_id:
                import hashlib
                job_id = hashlib.md5(f"{title}|{company}".encode()).hexdigest()[:12]

            # Salary
            salary = ""
            for sel in SELECTORS["salary"]:
                els = card.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    salary = els[0].text.strip()
                    break

            # Only build a real URL from real IDs — hash-based IDs won't resolve
            prefix = "glassdoor_" if is_real_id else "glassdoor_hash_"
            job_url = href if href else (
                f"https://www.glassdoor.com/job-listing/?jl={job_id}" if is_real_id else ""
            )

            return {
                "title": title,
                "company": company,
                "location": location,
                "job_id": f"{prefix}{job_id}",
                "job_url": job_url,
                "posted_time": "",
                "applied": False,
                "salary_info": salary,
                "work_style": "",
                "link": title_el,
            }

        except Exception as e:
            log.debug(f"Glassdoor card extraction failed: {e}")
            return None

    def get_job_description(self, driver) -> str:
        from selenium.webdriver.common.by import By
        time.sleep(1.5)

        for sel in SELECTORS["description"]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                return els[0].text.strip()
        return ""

    def get_salary_info(self, driver) -> str:
        from selenium.webdriver.common.by import By

        for sel in SELECTORS["salary"] + [
            'div[class*="salary"]',
            'span[class*="salary"]',
        ]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                txt = el.text.strip()
                if any(c in txt for c in ["$", "£", "€", "₹", "K", "salary"]):
                    return txt
        return ""

    def apply_to_job(self, driver, cfg: dict, ai=None, job_context: dict = None) -> bool:
        """
        Glassdoor always redirects to company ATS.
        Click the apply button and return False to signal ExternalApplier should handle it.
        """
        from selenium.webdriver.common.by import By

        for sel in SELECTORS["apply_button"]:
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            for btn in btns:
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    time.sleep(3)
                    # Glassdoor always opens external ATS
                    return False

        # Try any visible "Apply" button
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if "apply" in btn.text.lower() and btn.is_displayed():
                btn.click()
                time.sleep(3)
                return False

        return False

    def get_external_apply_url(self, driver) -> Optional[str]:
        """Glassdoor apply buttons usually link to external ATS."""
        from selenium.webdriver.common.by import By

        for sel in SELECTORS["apply_button"]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                href = el.get_attribute("href") or ""
                if href and "glassdoor.com" not in href:
                    return href

        # Check for "Apply on company site" links
        for a in driver.find_elements(By.TAG_NAME, "a"):
            href = a.get_attribute("href") or ""
            text = a.text.lower()
            if "apply" in text and href and "glassdoor.com" not in href:
                return href

        return None

    def has_next_page(self, driver) -> bool:
        from selenium.webdriver.common.by import By
        nav = driver.find_elements(By.CSS_SELECTOR,
            'button[data-test="pagination-next"], li.next a, a[aria-label="Next"]')
        for el in nav:
            if el.is_displayed() and self._is_element_enabled(el):
                return True
        return False

    def go_to_next_page(self, driver) -> bool:
        from selenium.webdriver.common.by import By
        try:
            old_url = driver.current_url
            for sel in ['button[data-test="pagination-next"]', 'li.next a', 'a[aria-label="Next"]']:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    if el.is_displayed() and self._is_element_enabled(el):
                        el.click()
                        time.sleep(3)
                        self._dismiss_popups(driver)
                        # Verify navigation actually occurred
                        if driver.current_url != old_url:
                            return True
                        # URL didn't change — check if results container refreshed
                        cards = self.get_job_cards(driver)
                        if cards:
                            return True
                        return False
        except Exception:
            pass
        return False

    @staticmethod
    def _is_element_enabled(el) -> bool:
        """Check if an element is truly enabled/actionable."""
        if el.get_attribute("aria-disabled") == "true":
            return False
        if el.get_attribute("disabled") is not None:
            return False
        classes = el.get_attribute("class") or ""
        if "disabled" in classes.lower():
            return False
        if el.get_attribute("tabindex") == "-1":
            return False
        return True

    def _dismiss_popups(self, driver):
        """Dismiss Glassdoor login/signup modals and cookie banners."""
        from selenium.webdriver.common.by import By

        for sel in [
            'button.e1r4hxna0',  # Close modal button
            'button[class*="CloseButton"]',
            'span.SVGInline.modal_closeIcon',
            'button[aria-label="Close"]',
            'div.modal_closeBtn',
            '#onetrust-accept-btn-handler',
        ]:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    if el.is_displayed():
                        el.click()
                        time.sleep(0.5)
            except Exception:
                continue

        # Also try pressing Escape to dismiss modals
        from selenium.webdriver.common.keys import Keys
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.3)
        except Exception:
            pass
