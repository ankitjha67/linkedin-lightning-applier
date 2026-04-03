"""
Indeed Platform Plugin.

Implements the JobPlatform interface for Indeed.com.
Handles job search, extraction, and application (both Indeed Apply and external).
"""

import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlencode

from .base import JobPlatform

log = logging.getLogger("lla.indeed")

# Indeed DOM selectors (updated periodically as Indeed changes their frontend)
SELECTORS = {
    "job_cards": [
        "div.job_seen_beacon",
        "div.slider_container .slider_item",
        "td.resultContent",
        "li.css-1ac2h1w",
        "div[class*='jobsearch-ResultsList'] div[class*='cardOutline']",
    ],
    "title": [
        "h2.jobTitle a span[title]",
        "h2.jobTitle a",
        "a[data-jk] span",
        "h2 a[id^='job_']",
    ],
    "company": [
        "span[data-testid='company-name']",
        "span.companyName",
        "span.css-92r8pb",
        "a[data-tn-element='companyName']",
    ],
    "location": [
        "div[data-testid='text-location']",
        "div.companyLocation",
        "span.css-1p0sjhy",
    ],
    "salary": [
        "div.salary-snippet-container",
        "div[class*='salary']",
        "span.estimated-salary",
        "div.metadata.salary-snippet-container",
    ],
    "description": [
        "#jobDescriptionText",
        "div.jobsearch-JobComponent-description",
        "div[id='jobDescriptionText']",
        "div[class*='jobDescription']",
    ],
    "apply_button": [
        "button#indeedApplyButton",
        "button[id*='indeedApply']",
        "a.indeed-apply-button",
        "button.css-1234qj",
    ],
    "posted_time": [
        "span.date",
        "span[class*='myJobsState']",
        "span.css-qvloho",
    ],
}

# Indeed date filter values
DATE_MAP = {
    "Past hour": "last",
    "Past 24 hours": "1",
    "Past week": "7",
    "Past month": "30",
    "Any time": "",
}


class IndeedPlugin(JobPlatform):
    """Indeed job platform implementation."""

    @property
    def name(self) -> str:
        return "indeed"

    @property
    def display_name(self) -> str:
        return "Indeed"

    @property
    def requires_login(self) -> bool:
        return False  # Indeed search works without login

    @property
    def supports_easy_apply(self) -> bool:
        return True  # Indeed has "Apply Now" for some jobs

    def create_browser(self, cfg: dict):
        from linkedin import create_browser
        return create_browser(cfg)

    def login(self, driver, cfg: dict) -> bool:
        """Indeed doesn't require login for searching. Navigate to homepage."""
        try:
            driver.get("https://www.indeed.com")
            time.sleep(3)
            # Dismiss cookie banner if present
            self._dismiss_popups(driver)
            return True
        except Exception as e:
            log.warning(f"Indeed navigation failed: {e}")
            return False

    def build_search_url(self, cfg: dict, term: str, location: str) -> str:
        sc = cfg.get("search", {})
        loc_short = location.split(",")[0].strip()

        params = {
            "q": term,
            "l": loc_short,
            "sort": "date",  # Most recent
        }

        dp = sc.get("date_posted", "Past 24 hours")
        fromage = DATE_MAP.get(dp, "1")
        if fromage:
            params["fromage"] = fromage

        # Experience level mapping
        el = sc.get("experience_level", [])
        level_map = {
            "Entry level": "entry_level",
            "Associate": "mid_level",
            "Mid-Senior level": "senior_level",
            "Director": "senior_level",
        }
        explvl = [level_map[e] for e in el if e in level_map]
        if explvl:
            params["explvl"] = explvl[0]

        # Job type
        jt = sc.get("job_type", [])
        type_map = {"Full-time": "fulltime", "Part-time": "parttime",
                    "Contract": "contract", "Temporary": "temporary"}
        jtype = [type_map[t] for t in jt if t in type_map]
        if jtype:
            params["jt"] = jtype[0]

        return f"https://www.indeed.com/jobs?{urlencode(params)}"

    def navigate_to_search(self, driver, url: str):
        log.debug(f"Navigating: {url[:120]}")
        driver.get(url)
        time.sleep(4)
        self._dismiss_popups(driver)

        # Wait for job cards
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    ", ".join(SELECTORS["job_cards"][:3])))
            )
        except Exception:
            log.debug("No job cards found after waiting")

    def get_job_cards(self, driver) -> list:
        from selenium.webdriver.common.by import By

        # Scroll to load lazy content
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 500);")
            time.sleep(0.5)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)

        for sel in SELECTORS["job_cards"]:
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if cards:
                log.info(f"Found {len(cards)} Indeed cards via '{sel}'")
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
                if els:
                    title = els[0].get_attribute("title") or els[0].text.strip()
                    title_el = els[0]
                    break
            if not title:
                return None

            # Job ID
            job_id = ""
            jk_el = card.find_elements(By.CSS_SELECTOR, "a[data-jk]")
            if jk_el:
                job_id = jk_el[0].get_attribute("data-jk") or ""
            if not job_id:
                # Try extracting from href
                for a in card.find_elements(By.TAG_NAME, "a"):
                    href = a.get_attribute("href") or ""
                    m = re.search(r'jk=([a-f0-9]+)', href)
                    if m:
                        job_id = m.group(1)
                        break
            if not job_id:
                # Generate from title hash
                import hashlib
                job_id = hashlib.md5(title.encode()).hexdigest()[:12]

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

            # Work style detection
            work_style = ""
            loc_lower = location.lower()
            if "remote" in loc_lower:
                work_style = "Remote"
            elif "hybrid" in loc_lower:
                work_style = "Hybrid"

            # Salary (if shown on card)
            salary = ""
            for sel in SELECTORS["salary"]:
                els = card.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    salary = els[0].text.strip()
                    break

            # Posted time
            posted_time = ""
            for sel in SELECTORS["posted_time"]:
                els = card.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    posted_time = els[0].text.strip()
                    break

            # Already applied check
            applied = False
            for el in card.find_elements(By.CSS_SELECTOR, "[class*='applied'], [class*='state']"):
                if "applied" in el.text.lower():
                    applied = True
                    break

            return {
                "title": title,
                "company": company,
                "location": location,
                "work_style": work_style,
                "job_id": f"indeed_{job_id}",
                "job_url": f"https://www.indeed.com/viewjob?jk={job_id}",
                "posted_time": posted_time,
                "applied": applied,
                "salary_info": salary,
                "link": title_el,
            }

        except Exception as e:
            log.debug(f"Indeed card extraction failed: {e}")
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
            "#salaryInfoAndJobType",
            "div[id='salaryInfoAndJobType']",
        ]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                txt = el.text.strip()
                if any(c in txt for c in ["$", "£", "€", "₹", "salary", "year", "hour"]):
                    return txt
        return ""

    def apply_to_job(self, driver, cfg: dict, ai=None, job_context: dict = None) -> bool:
        from selenium.webdriver.common.by import By

        # Look for Indeed Apply button
        for sel in SELECTORS["apply_button"]:
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            for btn in btns:
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    time.sleep(3)

                    # Check if it's Indeed's internal apply or external redirect
                    if "indeed.com" in driver.current_url:
                        return self._fill_indeed_form(driver, cfg, ai, job_context)
                    else:
                        # External redirect — hand back to caller
                        return False

        # Try generic "Apply" links
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if "apply" in btn.text.lower() and btn.is_displayed():
                btn.click()
                time.sleep(3)
                if "indeed.com" in driver.current_url:
                    return self._fill_indeed_form(driver, cfg, ai, job_context)
                return False

        return False

    def get_external_apply_url(self, driver) -> Optional[str]:
        """Check if the job uses an external application."""
        from selenium.webdriver.common.by import By

        for sel in ["a[class*='apply']", "a[href*='apply']"]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                href = el.get_attribute("href") or ""
                if href and "indeed.com" not in href:
                    return href
        return None

    def has_next_page(self, driver) -> bool:
        from selenium.webdriver.common.by import By
        nav = driver.find_elements(By.CSS_SELECTOR,
            'a[data-testid="pagination-page-next"], a[aria-label="Next Page"]')
        return len(nav) > 0

    def go_to_next_page(self, driver) -> bool:
        from selenium.webdriver.common.by import By
        try:
            nav = driver.find_elements(By.CSS_SELECTOR,
                'a[data-testid="pagination-page-next"], a[aria-label="Next Page"]')
            if nav:
                nav[0].click()
                time.sleep(3)
                return True
        except Exception:
            pass
        return False

    # ── Private Helpers ───────────────────────────────────────

    def _dismiss_popups(self, driver):
        """Dismiss cookie banners, notification prompts, etc."""
        from selenium.webdriver.common.by import By
        import time as _time

        for sel in [
            "button#onetrust-accept-btn-handler",
            "button[id*='accept']",
            "button.icl-CloseButton",
            "button[aria-label='close']",
            "div.popover-x-button-close button",
        ]:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    if el.is_displayed():
                        el.click()
                        _time.sleep(0.5)
            except Exception:
                continue

    def _fill_indeed_form(self, driver, cfg, ai, job_context) -> bool:
        """Fill Indeed's internal multi-step application form."""
        from selenium.webdriver.common.by import By

        personal = cfg.get("personal", {})
        qa = cfg.get("question_answers", {})
        jc = job_context or {}

        for page in range(10):
            time.sleep(1.5)

            # Fill text inputs
            for inp in driver.find_elements(By.CSS_SELECTOR,
                    "input[type='text'], input[type='email'], input[type='tel'], "
                    "input[type='url'], input[type='number'], input:not([type])"):
                try:
                    if inp.get_attribute("value") or not inp.is_displayed():
                        continue

                    label = self._get_label(driver, inp)
                    if not label:
                        continue

                    value = self._match_field(label, personal, qa)
                    if not value and ai:
                        value = ai.answer(label,
                                         job_title=jc.get("title", ""),
                                         company=jc.get("company", ""))
                    if value:
                        inp.clear()
                        inp.send_keys(str(value))
                        time.sleep(0.2)
                except Exception:
                    continue

            # Fill textareas
            for ta in driver.find_elements(By.TAG_NAME, "textarea"):
                try:
                    if ta.get_attribute("value") or not ta.is_displayed():
                        continue
                    label = self._get_label(driver, ta)
                    if label and ai:
                        if "cover" in label.lower():
                            value = ai.answer_cover_letter(
                                jc.get("title", ""), jc.get("company", ""),
                                jc.get("description", ""))
                        else:
                            value = ai.answer(label,
                                            job_title=jc.get("title", ""),
                                            company=jc.get("company", ""))
                        if value:
                            ta.clear()
                            ta.send_keys(value)
                            time.sleep(0.3)
                except Exception:
                    continue

            # Fill select dropdowns
            from selenium.webdriver.support.ui import Select
            for sel_el in driver.find_elements(By.TAG_NAME, "select"):
                try:
                    if not sel_el.is_displayed():
                        continue
                    select = Select(sel_el)
                    current = select.first_selected_option.text.strip().lower()
                    if current and current not in ("select", "select an option", "--", ""):
                        continue

                    label = self._get_label(driver, sel_el)
                    options = [o.text.strip() for o in select.options
                              if o.text.strip() and o.text.strip().lower() not in
                              ("select", "select an option", "--", "")]

                    value = self._match_field(label, personal, qa) if label else ""
                    if not value and ai and options:
                        value = ai.answer(label, options=options,
                                         job_title=jc.get("title", ""),
                                         company=jc.get("company", ""))
                    if value:
                        for opt in select.options:
                            if value.lower() in opt.text.strip().lower():
                                select.select_by_visible_text(opt.text.strip())
                                break
                except Exception:
                    continue

            # Radio buttons
            for fieldset in driver.find_elements(By.TAG_NAME, "fieldset"):
                try:
                    legend = fieldset.find_elements(By.CSS_SELECTOR, "legend, label")
                    if not legend:
                        continue
                    question = legend[0].text.strip()
                    radio_labels = [l.text.strip() for l in
                                   fieldset.find_elements(By.TAG_NAME, "label")
                                   if l.text.strip()]
                    if not radio_labels:
                        continue
                    value = self._match_field(question, personal, qa)
                    if not value and ai:
                        value = ai.answer(question, options=radio_labels,
                                         job_title=jc.get("title", ""),
                                         company=jc.get("company", ""))
                    if value:
                        for lbl in fieldset.find_elements(By.TAG_NAME, "label"):
                            if value.lower() in lbl.text.strip().lower():
                                radios = lbl.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                                if radios:
                                    radios[0].click()
                                else:
                                    lbl.click()
                                time.sleep(0.2)
                                break
                except Exception:
                    continue

            # Resume upload
            resume_path = cfg.get("resume", {}).get("default_resume_path", "")
            if resume_path and os.path.exists(resume_path):
                for fi in driver.find_elements(By.CSS_SELECTOR, "input[type='file']"):
                    try:
                        fi.send_keys(os.path.abspath(resume_path))
                        time.sleep(1)
                        break
                    except Exception:
                        continue

            # Submit or Continue
            submitted = False
            for btn_text in ["Submit your application", "Submit", "Apply", "Continue", "Next"]:
                btns = driver.find_elements(By.XPATH,
                    f'//button[contains(text(), "{btn_text}")] | '
                    f'//a[contains(text(), "{btn_text}")]')
                for btn in btns:
                    if btn.is_displayed() and btn.is_enabled():
                        btn.click()
                        time.sleep(2)
                        if btn_text.lower() in ("submit your application", "submit", "apply"):
                            log.info("   Indeed application submitted!")
                            return True
                        submitted = True
                        break
                if submitted:
                    break

            if not submitted:
                log.debug(f"Indeed form page {page}: no actionable button found")
                break

        return False

    def _get_label(self, driver, element) -> str:
        """Get label text for a form field."""
        from selenium.webdriver.common.by import By

        # aria-label
        label = element.get_attribute("aria-label")
        if label:
            return label.strip()

        # placeholder
        ph = element.get_attribute("placeholder")
        if ph:
            return ph.strip()

        # for= label
        el_id = element.get_attribute("id")
        if el_id:
            labels = driver.find_elements(By.CSS_SELECTOR, f'label[for="{el_id}"]')
            if labels:
                return labels[0].text.strip()

        # Parent label
        try:
            parent = element.find_element(By.XPATH, "./ancestor::label[1]")
            return parent.text.strip()
        except Exception:
            pass

        # Sibling label
        try:
            parent_div = element.find_element(By.XPATH, "./ancestor::div[1]")
            labels = parent_div.find_elements(By.TAG_NAME, "label")
            if labels:
                return labels[0].text.strip()
        except Exception:
            pass

        return element.get_attribute("name") or ""

    def _match_field(self, label: str, personal: dict, qa: dict) -> str:
        """Keyword matching for common form fields."""
        if not label:
            return ""
        l = label.lower()

        if "first name" in l:
            return personal.get("first_name", "")
        if "last name" in l:
            return personal.get("last_name", "")
        if "full name" in l or "your name" in l:
            return personal.get("full_name", "")
        if "email" in l:
            return personal.get("email", "")
        if "phone" in l or "mobile" in l:
            return personal.get("phone", "")
        if "city" in l:
            return personal.get("city", "")
        if "country" in l:
            return personal.get("country", "")

        # Check question_answers
        for k, v in qa.items():
            if k.lower() in l:
                return str(v)
        return ""
