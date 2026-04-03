"""
Indeed Platform Plugin (scaffold).

Implements the JobPlatform interface for Indeed.com.
To be fully implemented when multi-platform support is activated.
"""

import logging
import re
import time
from typing import Optional
from urllib.parse import urlencode

from .base import JobPlatform

log = logging.getLogger("lla.indeed")


class IndeedPlugin(JobPlatform):
    """Indeed job platform implementation."""

    @property
    def name(self) -> str:
        return "indeed"

    def create_browser(self, cfg: dict):
        # Reuse LinkedIn browser setup
        from linkedin import create_browser
        return create_browser(cfg)

    def login(self, driver, cfg: dict) -> bool:
        # Indeed doesn't require login for searching
        # But some features (save, apply) need an account
        driver.get("https://www.indeed.com")
        time.sleep(3)
        return True

    def build_search_url(self, cfg: dict, term: str, location: str) -> str:
        params = {
            "q": term,
            "l": location.split(",")[0].strip(),
            "sort": "date",
            "fromage": "1",  # Last 24 hours
        }
        sc = cfg.get("search", {})
        date_map = {
            "Past hour": "last",
            "Past 24 hours": "1",
            "Past week": "7",
            "Past month": "30",
        }
        dp = sc.get("date_posted", "Past 24 hours")
        params["fromage"] = date_map.get(dp, "1")

        return f"https://www.indeed.com/jobs?{urlencode(params)}"

    def navigate_to_search(self, driver, url: str):
        log.debug(f"Navigating: {url[:120]}")
        driver.get(url)
        time.sleep(4)

    def get_job_cards(self, driver) -> list:
        from selenium.webdriver.common.by import By

        for sel in [
            'div.job_seen_beacon',
            'div[class*="jobsearch-ResultsList"] div[class*="cardOutline"]',
            'td.resultContent',
            'div.slider_container .slider_item',
        ]:
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if cards:
                return cards
        return []

    def extract_job_info(self, driver, card) -> Optional[dict]:
        from selenium.webdriver.common.by import By

        try:
            # Title
            title_el = card.find_elements(By.CSS_SELECTOR,
                'h2.jobTitle a, a[data-jk], span[title]')
            title = title_el[0].text.strip() if title_el else ""

            # Job ID
            job_id = ""
            link_el = card.find_elements(By.CSS_SELECTOR, 'a[data-jk], a[href*="jk="]')
            if link_el:
                jk = link_el[0].get_attribute("data-jk") or ""
                if not jk:
                    href = link_el[0].get_attribute("href") or ""
                    m = re.search(r'jk=([a-f0-9]+)', href)
                    jk = m.group(1) if m else ""
                job_id = jk

            # Company
            company_el = card.find_elements(By.CSS_SELECTOR,
                'span[data-testid="company-name"], .companyName')
            company = company_el[0].text.strip() if company_el else ""

            # Location
            loc_el = card.find_elements(By.CSS_SELECTOR,
                'div[data-testid="text-location"], .companyLocation')
            location = loc_el[0].text.strip() if loc_el else ""

            if not title:
                return None

            return {
                "title": title,
                "company": company,
                "location": location,
                "job_id": f"indeed_{job_id}",
                "job_url": f"https://www.indeed.com/viewjob?jk={job_id}",
                "posted_time": "",
                "applied": False,
            }
        except Exception:
            return None

    def get_job_description(self, driver) -> str:
        from selenium.webdriver.common.by import By
        time.sleep(1)
        for sel in ['#jobDescriptionText', 'div.jobsearch-JobComponent-description',
                     '[class*="jobDescription"]']:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                return els[0].text.strip()
        return ""

    def apply_to_job(self, driver, cfg: dict, ai=None, job_context: dict = None) -> bool:
        from selenium.webdriver.common.by import By

        # Indeed has "Apply now" buttons that often redirect to company sites
        btns = driver.find_elements(By.CSS_SELECTOR,
            'button[id*="apply"], a[class*="apply"], button[class*="apply"]')
        for btn in btns:
            if "apply" in btn.text.lower() and btn.is_displayed():
                btn.click()
                time.sleep(3)
                # Check if it opened an external page or Indeed's apply flow
                if "indeed.com" in driver.current_url:
                    # Indeed's internal apply — fill form
                    return self._fill_indeed_form(driver, cfg, ai, job_context)
                else:
                    # External redirect — hand to ExternalApplier
                    return False
        return False

    def _fill_indeed_form(self, driver, cfg, ai, job_context) -> bool:
        """Fill Indeed's internal application form."""
        from selenium.webdriver.common.by import By

        personal = cfg.get("personal", {})

        for page in range(10):
            time.sleep(1)

            # Fill visible inputs
            for inp in driver.find_elements(By.CSS_SELECTOR,
                "input[type='text'], input[type='email'], input[type='tel']"):
                if inp.get_attribute("value"):
                    continue
                label = inp.get_attribute("aria-label") or inp.get_attribute("placeholder") or ""
                label_lower = label.lower()
                value = ""
                if "name" in label_lower:
                    value = personal.get("full_name", "")
                elif "email" in label_lower:
                    value = personal.get("email", "")
                elif "phone" in label_lower:
                    value = personal.get("phone", "")
                elif ai:
                    value = ai.answer(label,
                                     job_title=job_context.get("title", "") if job_context else "",
                                     company=job_context.get("company", "") if job_context else "")
                if value:
                    inp.clear()
                    inp.send_keys(value)

            # Resume upload
            resume_path = cfg.get("resume", {}).get("default_resume_path", "")
            if resume_path:
                import os
                for fi in driver.find_elements(By.CSS_SELECTOR, "input[type='file']"):
                    try:
                        fi.send_keys(os.path.abspath(resume_path))
                        time.sleep(1)
                    except Exception:
                        continue

            # Submit or Continue
            for btn_text in ["Submit", "Apply", "Continue", "Next"]:
                btns = driver.find_elements(By.XPATH,
                    f'//button[contains(text(), "{btn_text}")]')
                for btn in btns:
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(2)
                        if "submit" in btn_text.lower() or "apply" in btn_text.lower():
                            return True
                        break

        return False
