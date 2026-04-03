"""
External Apply — ATS Form Filling.

For jobs with external application links, open the company's ATS
(Greenhouse, Lever, Workday, Ashby, etc.), fill the form using AI, and submit.
Covers the ~60% of LinkedIn jobs that aren't Easy Apply.
"""

import logging
import os
import re
import time
import random
from typing import Optional

log = logging.getLogger("lla.external_apply")


class ExternalApplier:
    """Fill and submit external ATS application forms using AI."""

    def __init__(self, ai, cfg: dict):
        self.ai = ai
        self.cfg = cfg
        ea_cfg = cfg.get("external_apply", {})
        self.enabled = ea_cfg.get("enabled", False)
        self.supported_ats = set(ea_cfg.get("supported_ats", ["greenhouse", "lever", "workday", "ashby"]))
        self.max_per_cycle = ea_cfg.get("max_external_per_cycle", 5)
        self.timeout = ea_cfg.get("timeout_seconds", 120)
        self.applied_this_cycle = 0

    def can_apply(self) -> bool:
        return self.enabled and self.applied_this_cycle < self.max_per_cycle

    def detect_ats(self, url: str) -> Optional[str]:
        """Detect ATS platform from URL."""
        if not url:
            return None

        patterns = {
            "greenhouse": [r"boards\.greenhouse\.io", r"job-boards\.greenhouse\.io"],
            "lever": [r"jobs\.lever\.co"],
            "workday": [r"myworkday\.com", r"\.workday\.com", r"wd\d+\.myworkdayjobs\.com"],
            "ashby": [r"jobs\.ashbyhq\.com"],
        }

        for ats, pats in patterns.items():
            for pat in pats:
                if re.search(pat, url, re.IGNORECASE):
                    return ats if ats in self.supported_ats else None
        return None

    def apply_external(self, driver, apply_url: str, job_context: dict,
                       resume_path: str = "") -> bool:
        """
        Open ATS URL, fill the form, and submit.

        Args:
            driver: Selenium WebDriver
            apply_url: URL to the external application form
            job_context: {title, company, description, location}
            resume_path: Path to resume file for upload

        Returns:
            True if application was submitted successfully
        """
        if not self.can_apply():
            return False

        ats = self.detect_ats(apply_url)
        if not ats:
            log.info(f"   Unsupported ATS: {apply_url[:80]}")
            return False

        log.info(f"   🌐 External apply ({ats}): {apply_url[:80]}")

        # Save current window handle
        original_window = driver.current_window_handle
        original_url = driver.current_url

        try:
            # Open in new tab
            driver.execute_script(f"window.open('{apply_url}', '_blank');")
            time.sleep(2)

            # Switch to new tab
            new_window = [w for w in driver.window_handles if w != original_window]
            if not new_window:
                log.warning("   Failed to open new tab")
                return False

            driver.switch_to.window(new_window[0])
            time.sleep(3)

            # Route to ATS-specific handler
            success = False
            if ats == "greenhouse":
                success = self._fill_greenhouse(driver, job_context, resume_path)
            elif ats == "lever":
                success = self._fill_lever(driver, job_context, resume_path)
            elif ats == "workday":
                success = self._fill_workday(driver, job_context, resume_path)
            elif ats == "ashby":
                success = self._fill_ashby(driver, job_context, resume_path)

            if success:
                self.applied_this_cycle += 1

            return success

        except Exception as e:
            log.warning(f"   External apply error: {e}")
            return False
        finally:
            # Close the ATS tab and switch back
            try:
                if len(driver.window_handles) > 1:
                    driver.close()
                driver.switch_to.window(original_window)
                time.sleep(1)
            except Exception:
                pass

    def _fill_greenhouse(self, driver, job_context: dict, resume_path: str) -> bool:
        """Fill Greenhouse application form."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select

        log.debug("   Filling Greenhouse form...")
        time.sleep(2)

        personal = self.cfg.get("personal", {})
        app = self.cfg.get("application", {})

        try:
            # Greenhouse forms typically have: name, email, phone, resume, optional fields
            field_map = {
                "first_name": personal.get("first_name", ""),
                "last_name": personal.get("last_name", ""),
                "email": personal.get("email", ""),
                "phone": personal.get("phone", ""),
            }

            # Fill standard fields by name/id
            for field_name, value in field_map.items():
                if not value:
                    continue
                for selector in [f'input[name*="{field_name}"]', f'input[id*="{field_name}"]',
                                f'input[autocomplete*="{field_name}"]']:
                    try:
                        el = driver.find_element(By.CSS_SELECTOR, selector)
                        if not el.get_attribute("value"):
                            el.clear()
                            el.send_keys(value)
                            time.sleep(0.3)
                            break
                    except Exception:
                        continue

            # Resume upload
            if resume_path and os.path.exists(resume_path):
                self._upload_resume(driver, resume_path)

            # Fill any remaining text fields with AI
            self._fill_remaining_fields(driver, job_context)

            # Fill select dropdowns
            self._fill_select_fields(driver, job_context)

            # Submit
            return self._click_submit(driver)

        except Exception as e:
            log.warning(f"   Greenhouse fill error: {e}")
            return False

    def _fill_lever(self, driver, job_context: dict, resume_path: str) -> bool:
        """Fill Lever application form."""
        from selenium.webdriver.common.by import By

        log.debug("   Filling Lever form...")
        time.sleep(2)

        personal = self.cfg.get("personal", {})

        try:
            # Lever has a simpler form structure
            # Name, email, phone, resume, and custom questions
            inputs = driver.find_elements(By.CSS_SELECTOR, "input:not([type='hidden']):not([type='file'])")

            for inp in inputs:
                label = self._get_field_label(driver, inp)
                if not label:
                    continue

                label_lower = label.lower()
                value = ""

                if "name" in label_lower and "last" not in label_lower:
                    value = personal.get("full_name", "")
                elif "email" in label_lower:
                    value = personal.get("email", "")
                elif "phone" in label_lower:
                    value = personal.get("phone", "")
                elif "linkedin" in label_lower:
                    value = self.cfg.get("question_answers", {}).get("linkedin", "")
                elif "github" in label_lower or "website" in label_lower:
                    value = self.cfg.get("question_answers", {}).get("github", "")

                if not value and self.ai:
                    value = self.ai.answer(label,
                                          job_title=job_context.get("title", ""),
                                          company=job_context.get("company", ""))

                if value and not inp.get_attribute("value"):
                    inp.clear()
                    inp.send_keys(value)
                    time.sleep(0.2)

            # Resume upload
            if resume_path and os.path.exists(resume_path):
                self._upload_resume(driver, resume_path)

            # Textareas (cover letter, additional info)
            self._fill_textareas(driver, job_context)

            return self._click_submit(driver)

        except Exception as e:
            log.warning(f"   Lever fill error: {e}")
            return False

    def _fill_workday(self, driver, job_context: dict, resume_path: str) -> bool:
        """Fill Workday application form."""
        from selenium.webdriver.common.by import By

        log.debug("   Filling Workday form...")
        time.sleep(3)  # Workday is slow

        try:
            # Workday often requires clicking "Apply" first
            apply_btns = driver.find_elements(By.CSS_SELECTOR,
                'a[data-automation-id="jobPostingApplyButton"], '
                'button[data-automation-id="jobPostingApplyButton"]')
            if apply_btns:
                apply_btns[0].click()
                time.sleep(3)

            # Workday has multi-page forms — handle page by page
            for page in range(10):
                self._fill_remaining_fields(driver, job_context)
                self._fill_select_fields(driver, job_context)
                self._fill_textareas(driver, job_context)

                if resume_path and os.path.exists(resume_path):
                    self._upload_resume(driver, resume_path)

                # Look for Submit
                submit_btn = self._find_submit_button(driver)
                if submit_btn and "submit" in submit_btn.text.lower():
                    submit_btn.click()
                    time.sleep(3)
                    return True

                # Look for Next/Continue
                next_btn = None
                for text in ["Next", "Continue", "Save and Continue"]:
                    btns = driver.find_elements(By.XPATH,
                        f'//button[contains(text(), "{text}")] | //a[contains(text(), "{text}")]')
                    if btns:
                        next_btn = btns[0]
                        break

                if next_btn:
                    next_btn.click()
                    time.sleep(2)
                else:
                    break

            return False

        except Exception as e:
            log.warning(f"   Workday fill error: {e}")
            return False

    def _fill_ashby(self, driver, job_context: dict, resume_path: str) -> bool:
        """Fill Ashby application form."""
        log.debug("   Filling Ashby form...")
        time.sleep(2)

        try:
            # Ashby forms are React-based, similar to Lever
            self._fill_remaining_fields(driver, job_context)
            self._fill_select_fields(driver, job_context)
            self._fill_textareas(driver, job_context)

            if resume_path and os.path.exists(resume_path):
                self._upload_resume(driver, resume_path)

            return self._click_submit(driver)

        except Exception as e:
            log.warning(f"   Ashby fill error: {e}")
            return False

    def _get_field_label(self, driver, element) -> str:
        """Get the label text for a form field."""
        from selenium.webdriver.common.by import By

        # aria-label
        label = element.get_attribute("aria-label")
        if label:
            return label.strip()

        # placeholder
        placeholder = element.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()

        # Associated label
        el_id = element.get_attribute("id")
        if el_id:
            try:
                labels = driver.find_elements(By.CSS_SELECTOR, f'label[for="{el_id}"]')
                if labels:
                    return labels[0].text.strip()
            except Exception:
                pass

        # Parent label
        try:
            parent = element.find_element(By.XPATH, "./ancestor::label[1]")
            return parent.text.strip()
        except Exception:
            pass

        # Nearby label
        try:
            parent_div = element.find_element(By.XPATH, "./ancestor::div[1]")
            labels = parent_div.find_elements(By.TAG_NAME, "label")
            if labels:
                return labels[0].text.strip()
        except Exception:
            pass

        return element.get_attribute("name") or ""

    def _fill_remaining_fields(self, driver, job_context: dict):
        """Fill unfilled text input fields using config + AI."""
        from selenium.webdriver.common.by import By

        personal = self.cfg.get("personal", {})
        app = self.cfg.get("application", {})
        qa = self.cfg.get("question_answers", {})

        inputs = driver.find_elements(By.CSS_SELECTOR,
            "input[type='text'], input[type='email'], input[type='tel'], "
            "input[type='url'], input[type='number'], input:not([type])")

        for inp in inputs:
            try:
                if inp.get_attribute("value"):
                    continue
                if not inp.is_displayed():
                    continue

                label = self._get_field_label(driver, inp)
                if not label:
                    continue

                # Try keyword matching first
                value = self._keyword_match(label, personal, app, qa)

                # AI fallback
                if not value and self.ai:
                    value = self.ai.answer(label,
                                          job_title=job_context.get("title", ""),
                                          company=job_context.get("company", ""))

                if value:
                    inp.clear()
                    inp.send_keys(str(value))
                    time.sleep(0.2)

            except Exception:
                continue

    def _fill_select_fields(self, driver, job_context: dict):
        """Fill select dropdowns."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select

        personal = self.cfg.get("personal", {})
        app = self.cfg.get("application", {})
        qa = self.cfg.get("question_answers", {})

        for sel_el in driver.find_elements(By.TAG_NAME, "select"):
            try:
                if not sel_el.is_displayed():
                    continue

                select = Select(sel_el)
                # Skip if already has a non-default selection
                current = select.first_selected_option.text.strip().lower()
                if current and current not in ("select", "select an option", "choose", "--", ""):
                    continue

                label = self._get_field_label(driver, sel_el)
                if not label:
                    continue

                options = [o.text.strip() for o in select.options
                          if o.text.strip() and o.text.strip().lower() not in
                          ("select", "select an option", "choose", "--", "")]

                # Keyword match
                value = self._keyword_match(label, personal, app, qa)

                # AI fallback
                if not value and self.ai and options:
                    value = self.ai.answer(label, options=options,
                                          job_title=job_context.get("title", ""),
                                          company=job_context.get("company", ""))

                if value:
                    # Try to select matching option
                    for opt in select.options:
                        if value.lower() in opt.text.strip().lower() or opt.text.strip().lower() in value.lower():
                            select.select_by_visible_text(opt.text.strip())
                            time.sleep(0.2)
                            break

            except Exception:
                continue

    def _fill_textareas(self, driver, job_context: dict):
        """Fill textarea fields (cover letter, additional info)."""
        from selenium.webdriver.common.by import By

        for ta in driver.find_elements(By.TAG_NAME, "textarea"):
            try:
                if ta.get_attribute("value") or not ta.is_displayed():
                    continue

                label = self._get_field_label(driver, ta)
                if not label:
                    continue

                label_lower = label.lower()

                if self.ai:
                    if "cover" in label_lower or "letter" in label_lower:
                        value = self.ai.answer_cover_letter(
                            job_context.get("title", ""),
                            job_context.get("company", ""),
                            job_context.get("description", ""))
                    else:
                        value = self.ai.answer(label,
                                             job_title=job_context.get("title", ""),
                                             company=job_context.get("company", ""),
                                             job_description=job_context.get("description", ""))

                    if value:
                        ta.clear()
                        ta.send_keys(value)
                        time.sleep(0.3)

            except Exception:
                continue

    def _upload_resume(self, driver, resume_path: str):
        """Upload resume to file input."""
        from selenium.webdriver.common.by import By

        for fi in driver.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            try:
                fi.send_keys(os.path.abspath(resume_path))
                time.sleep(1)
                log.debug(f"   📎 Uploaded resume: {resume_path}")
                break
            except Exception:
                continue

    def _find_submit_button(self, driver):
        """Find the submit/apply button."""
        from selenium.webdriver.common.by import By

        for text in ["Submit Application", "Submit", "Apply", "Apply Now", "Send Application"]:
            btns = driver.find_elements(By.XPATH,
                f'//button[contains(text(), "{text}")] | //input[@value="{text}"]')
            for btn in btns:
                if btn.is_displayed() and btn.is_enabled():
                    return btn
        return None

    def _click_submit(self, driver) -> bool:
        """Find and click the submit button."""
        btn = self._find_submit_button(driver)
        if btn:
            try:
                btn.click()
                time.sleep(3)
                log.info("   ✅ External application submitted!")
                return True
            except Exception as e:
                log.warning(f"   Submit click failed: {e}")
        else:
            log.warning("   Submit button not found")
        return False

    def _keyword_match(self, label: str, personal: dict, app: dict, qa: dict) -> str:
        """Simple keyword matching for common fields."""
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
        if "linkedin" in l:
            return qa.get("linkedin", "")
        if "github" in l or "website" in l:
            return qa.get("github", qa.get("website", ""))
        if "salary" in l or "compensation" in l:
            return app.get("desired_salary", "")
        if "visa" in l or "sponsor" in l:
            return app.get("require_visa", "")
        if "relocat" in l:
            return app.get("willing_to_relocate", "")
        for k, v in qa.items():
            if k.lower() in l:
                return str(v)
        return ""
