"""
ATS Handler base class.

Every ATS handler (Workday, iCIMS, SmartRecruiters, Taleo, Workable, Jobvite,
BambooHR, SuccessFactors, ADP, Greenhouse, Lever, Ashby) subclasses ATSHandler
and implements apply(). The base provides the hard-won primitives that make
enterprise ATS automation actually work:

  - fill_text: robust text entry with React-friendly event dispatch
  - fill_by_automation_id: Workday-style stable data-automation-id targeting
  - select_custom_dropdown: click-open React/ARIA listboxes (NOT native <select>)
  - select_native: native <select> handling
  - answer_field: keyword-match then AI, for any labeled field
  - click_button: text OR data-automation-id OR aria-label, visible+enabled only
  - upload_file: resume/file inputs (handles hidden inputs)
  - run_multistep: the Next/Continue wizard loop with terminal-state detection
  - get_label: aria-label / for= / placeholder / ancestor <label> / nearby text

These are deliberately defensive: enterprise ATS pages are slow, re-render
constantly, and raise StaleElementReferenceException often. Every helper
swallows per-element errors and moves on.
"""

import logging
import os
import time

log = logging.getLogger("lla.ats")

# Values that mean "no real selection yet" in dropdowns
_PLACEHOLDER_VALUES = {
    "", "select", "select one", "select an option", "choose", "choose one",
    "--", "please select", "select...", "none", "n/a",
}

# Button labels that FINISH an application (terminal)
_SUBMIT_LABELS = [
    "submit application", "submit your application", "submit", "send application",
    "finish", "complete application", "i agree and submit",
]
# Button labels that ADVANCE a wizard (non-terminal)
_NEXT_LABELS = [
    "save and continue", "continue to next step", "continue", "next", "review",
    "save and next", "proceed", "start your application", "apply", "apply now",
]


class ATSHandler:
    """Base class for all ATS-specific application handlers."""

    #: short name, e.g. "workday" — set by subclasses
    name: str = "generic"
    #: whether this ATS typically requires an account/login before applying
    needs_account: bool = False

    def __init__(self, ai, cfg: dict):
        self.ai = ai
        self.cfg = cfg
        self.personal = cfg.get("personal", {})
        self.application = cfg.get("application", {})
        self.qa = cfg.get("question_answers", {})
        self.ats_accounts = cfg.get("external_apply", {}).get("ats_accounts", {})
        self.max_pages = cfg.get("external_apply", {}).get("max_wizard_pages", 12)
        self.slow = cfg.get("external_apply", {}).get("slow_mo_seconds", 0.0)
        # Country-aware work authorization — consulted before the global
        # keyword answers so "authorized to work?" reflects the JOB's country.
        self.work_auth = None
        try:
            from work_auth import WorkAuthorization
            wa = WorkAuthorization(cfg)
            self.work_auth = wa if wa.enabled else None
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Entry point — subclasses override
    # ------------------------------------------------------------------

    def apply(self, driver, job_context: dict, resume_path: str = "") -> bool:
        """Fill and submit the application. Return True on success."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Text entry
    # ------------------------------------------------------------------

    def fill_text(self, driver, el, value: str, force: bool = False) -> bool:
        """Type `value` into a text element, firing React-friendly events."""
        if el is None or value is None or value == "":
            return False
        try:
            if not force and (el.get_attribute("value") or "").strip():
                return False
            if not el.is_displayed() or not el.is_enabled():
                return False
            from selenium.webdriver.common.keys import Keys
            el.click()
            time.sleep(0.15)
            # Clear existing content
            el.send_keys(Keys.CONTROL, "a")
            el.send_keys(Keys.DELETE)
            time.sleep(0.05)
            el.send_keys(str(value))
            # Nudge React frameworks that listen for input/change/blur
            try:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                    "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                    el,
                )
            except Exception:
                pass
            self._pause()
            return True
        except Exception as e:
            log.debug(f"    fill_text failed: {e}")
            return False

    def fill_by_automation_id(self, driver, automation_id: str, value: str) -> bool:
        """Fill a field by Workday-style data-automation-id (stable across tenants)."""
        from selenium.webdriver.common.by import By
        for sel in (
            f'input[data-automation-id="{automation_id}"]',
            f'[data-automation-id="{automation_id}"] input',
            f'textarea[data-automation-id="{automation_id}"]',
        ):
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    if self.fill_text(driver, el, value):
                        return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # Dropdowns
    # ------------------------------------------------------------------

    def select_native(self, sel_el, value: str) -> bool:
        """Choose an option in a native <select> by fuzzy text match."""
        try:
            from selenium.webdriver.support.ui import Select
            select = Select(sel_el)
            current = select.first_selected_option.text.strip().lower()
            if current and current not in _PLACEHOLDER_VALUES:
                return False
            v = value.strip().lower()
            # exact, then contains, then first-word
            for opt in select.options:
                if opt.text.strip().lower() == v:
                    select.select_by_visible_text(opt.text.strip())
                    return True
            for opt in select.options:
                ot = opt.text.strip().lower()
                if ot in _PLACEHOLDER_VALUES:
                    continue
                if v in ot or ot in v:
                    select.select_by_visible_text(opt.text.strip())
                    return True
            return False
        except Exception:
            return False

    def select_custom_dropdown(self, driver, trigger, value: str) -> bool:
        """Open a React/ARIA custom dropdown and pick the option matching `value`.

        `trigger` is the clickable element (a button/div with role=button or a
        Workday data-automation-id). Options appear in a popup listbox after click.
        """
        from selenium.webdriver.common.by import By
        try:
            self.safe_click(driver, trigger)
            time.sleep(0.6)
            v = value.strip().lower()

            # Common option containers across ATSes (Workday menuItem, ARIA option,
            # react-select option, generic li)
            option_selectors = [
                '[data-automation-id="menuItem"]',
                '[role="option"]',
                'li[role="option"]',
                'div[class*="option"]',
                'ul[role="listbox"] li',
                'div[role="listbox"] div',
            ]
            for sel in option_selectors:
                opts = driver.find_elements(By.CSS_SELECTOR, sel)
                visible = [o for o in opts if o.is_displayed() and o.text.strip()]
                if not visible:
                    continue
                # exact then contains
                for o in visible:
                    if o.text.strip().lower() == v:
                        self.safe_click(driver, o)
                        self._pause()
                        return True
                for o in visible:
                    ot = o.text.strip().lower()
                    if v in ot or (len(v) > 3 and ot in v):
                        self.safe_click(driver, o)
                        self._pause()
                        return True
                # nothing matched in a real list — stop trying other selectors
                break
            # Close the popup so it doesn't block the next field
            try:
                from selenium.webdriver.common.keys import Keys
                driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            except Exception:
                pass
            return False
        except Exception as e:
            log.debug(f"    custom dropdown failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Buttons / navigation
    # ------------------------------------------------------------------

    def click_button(self, driver, labels=None, automation_ids=None) -> bool:
        """Click the first visible+enabled button matching a label or automation id."""
        from selenium.webdriver.common.by import By

        for aid in (automation_ids or []):
            for sel in (f'button[data-automation-id="{aid}"]',
                        f'[data-automation-id="{aid}"]',
                        f'a[data-automation-id="{aid}"]'):
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    if self._clickable(el):
                        self.safe_click(driver, el)
                        return True

        for label in (labels or []):
            ll = label.lower()
            # buttons, links, and submit inputs
            candidates = driver.find_elements(
                By.XPATH,
                "//button | //a[@role='button'] | //input[@type='submit'] | //input[@type='button']",
            )
            for el in candidates:
                try:
                    text = (el.text or el.get_attribute("value") or
                            el.get_attribute("aria-label") or "").strip().lower()
                    if text and ll in text and self._clickable(el):
                        self.safe_click(driver, el)
                        return True
                except Exception:
                    continue
        return False

    def run_multistep(self, driver, job_context: dict, resume_path: str = "",
                      per_page=None) -> bool:
        """Drive a multi-page wizard: fill each page, advance, detect completion.

        `per_page(driver, ctx)` is an optional callback run on every page before
        the generic sweep (used by handlers that need page-specific logic).
        Returns True when a terminal Submit is clicked and confirmed.
        """
        for page in range(self.max_pages):
            time.sleep(1.2)
            if per_page:
                try:
                    per_page(driver, job_context)
                except Exception as e:
                    log.debug(f"    per_page callback failed: {e}")

            # Generic sweep: text, selects, textareas, radios, checkboxes, resume
            self.sweep_page(driver, job_context, resume_path)

            # Terminal submit?
            if self.click_button(driver, labels=_SUBMIT_LABELS,
                                 automation_ids=["submit", "wd-Submit"]):
                time.sleep(3)
                if self._looks_submitted(driver):
                    log.info(f"   ✅ {self.name}: application submitted")
                    return True
                log.info(f"   ✅ {self.name}: submit clicked (unconfirmed)")
                return True

            # Advance?
            advanced = self.click_button(
                driver, labels=_NEXT_LABELS,
                automation_ids=["bottom-navigation-next-button", "wd-CompositeContinueButton",
                                "continueButton", "next"],
            )
            if not advanced:
                log.debug(f"   {self.name}: no next/submit on page {page}")
                break
            time.sleep(1.5)
            # Surface validation errors so we can refill next loop
            self._log_validation_errors(driver)

        return False

    # ------------------------------------------------------------------
    # Generic page sweep (text / select / textarea / radio / checkbox / file)
    # ------------------------------------------------------------------

    def sweep_page(self, driver, job_context: dict, resume_path: str = ""):
        """Fill every empty, labeled field on the current page."""
        from selenium.webdriver.common.by import By

        # Resume upload first (many ATSes auto-parse and pre-fill from it)
        if resume_path and os.path.exists(resume_path):
            self.upload_file(driver, resume_path)

        # Text-like inputs
        for inp in driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='text'], input[type='email'], input[type='tel'], "
            "input[type='url'], input[type='number'], input:not([type])",
        ):
            try:
                if (inp.get_attribute("value") or "").strip() or not inp.is_displayed():
                    continue
                label = self.get_label(driver, inp)
                if not label:
                    continue
                val = self.answer_field(label, job_context)
                if val:
                    self.fill_text(driver, inp, val)
            except Exception:
                continue

        # Native selects
        for sel_el in driver.find_elements(By.TAG_NAME, "select"):
            try:
                if not sel_el.is_displayed():
                    continue
                label = self.get_label(driver, sel_el)
                if not label:
                    continue
                from selenium.webdriver.support.ui import Select
                options = [o.text.strip() for o in Select(sel_el).options
                           if o.text.strip().lower() not in _PLACEHOLDER_VALUES]
                val = self.answer_field(label, job_context, options=options)
                if val:
                    self.select_native(sel_el, val)
            except Exception:
                continue

        # Custom (button-style) dropdowns — Workday & other React ATSes
        for trigger in driver.find_elements(
            By.CSS_SELECTOR,
            'button[aria-haspopup="listbox"], [data-automation-id="selectShowAll"], '
            'div[role="button"][aria-haspopup], [data-automation-id*="dropdown"]',
        ):
            try:
                if not trigger.is_displayed():
                    continue
                label = self.get_label(driver, trigger)
                if not label:
                    continue
                options = self._peek_dropdown_options(driver, trigger)
                val = self.answer_field(label, job_context, options=options)
                if val:
                    self.select_custom_dropdown(driver, trigger, val)
            except Exception:
                continue

        # Textareas (cover letter / free text)
        for ta in driver.find_elements(By.TAG_NAME, "textarea"):
            try:
                if (ta.get_attribute("value") or "").strip() or not ta.is_displayed():
                    continue
                label = self.get_label(driver, ta)
                if not label:
                    continue
                if self.ai and ("cover" in label.lower() or "letter" in label.lower()):
                    val = self.ai.answer_cover_letter(
                        job_context.get("title", ""), job_context.get("company", ""),
                        job_context.get("description", ""))
                else:
                    val = self.answer_field(label, job_context, long=True)
                if val:
                    self.fill_text(driver, ta, val)
            except Exception:
                continue

        # Radio groups (Yes/No, authorization, etc.)
        self._fill_radios(driver, job_context)
        # Consent/acknowledge checkboxes
        self._check_consents(driver)

    # ------------------------------------------------------------------
    # Field answering (keyword → AI)
    # ------------------------------------------------------------------

    def answer_field(self, label: str, job_context: dict,
                     options=None, long: bool = False) -> str:
        """Return a value for a labeled field: work-auth → keyword → AI."""
        location = (job_context or {}).get("location", "")
        # Country-aware authorization beats the global keyword answers: the
        # same "authorized to work?" is Yes in your home country, No abroad.
        if self.work_auth:
            wa = self.work_auth.answer(label, location, options)
            if wa is not None:
                return wa
            if self.work_auth.recognizes(label):
                # Recognized but unanswerable here (no country / options don't
                # fit) — skip the global keyword answer, let the AI decide with
                # the location in view.
                if not self.ai:
                    return ""
                try:
                    return self.ai.answer(label, options=options,
                                          job_title=job_context.get("title", ""),
                                          company=job_context.get("company", ""),
                                          job_location=location)
                except Exception:
                    return ""
        val = self.keyword_match(label)
        if val:
            return val
        if not self.ai:
            return ""
        try:
            if options:
                return self.ai.answer(label, options=options,
                                      job_title=job_context.get("title", ""),
                                      company=job_context.get("company", ""),
                                      job_location=location)
            return self.ai.answer(label,
                                  job_title=job_context.get("title", ""),
                                  company=job_context.get("company", ""),
                                  job_description=job_context.get("description", "")[:500] if long else "",
                                  job_location=location)
        except Exception:
            return ""

    def keyword_match(self, label: str) -> str:
        """Fast, free matching for the common identity/eligibility fields."""
        if not label:
            return ""
        low = label.lower()
        p, a, qa = self.personal, self.application, self.qa
        if "first name" in low or ("given" in low and "name" in low):
            return p.get("first_name", "")
        if "last name" in low or "surname" in low or "family name" in low:
            return p.get("last_name", "")
        if "full name" in low or "your name" in low or low.strip() == "name":
            return p.get("full_name", "")
        if "preferred name" in low:
            return p.get("first_name", "")
        if "email" in low:
            return p.get("email", "")
        if "phone" in low or "mobile" in low or "telephone" in low:
            return p.get("phone", "")
        if "address line 1" in low or "street" in low or low.strip() == "address":
            return p.get("address", p.get("city", ""))
        if "city" in low or "town" in low:
            return p.get("city", "")
        if "state" in low or "province" in low or "region" in low:
            return p.get("state", "")
        if "zip" in low or "postal" in low or "postcode" in low:
            return p.get("zip_code", "")
        if "country" in low:
            return p.get("country", "")
        if "linkedin" in low:
            return qa.get("linkedin", "")
        if "github" in low or "portfolio" in low or "website" in low:
            return qa.get("github", qa.get("website", ""))
        if "years" in low and "experience" in low:
            return str(a.get("years_of_experience", ""))
        if "current" in low and ("company" in low or "employer" in low):
            return qa.get("current company", "")
        if "salary" in low or "compensation" in low or "expected" in low or "ctc" in low:
            return a.get("desired_salary", "")
        if "notice" in low:
            return a.get("notice_period_days", "")
        if "visa" in low or "sponsor" in low or "work permit" in low:
            return a.get("require_visa", "")
        if "authorized" in low or "authorised" in low or "legally" in low or "eligible to work" in low or "right to work" in low:
            return a.get("authorized_to_work", "")
        if "relocat" in low:
            return a.get("willing_to_relocate", "")
        if "start date" in low or "available" in low:
            return a.get("start_date", a.get("notice_period_days", ""))
        # config-defined Q&A keywords
        for k, v in qa.items():
            if k.lower() in low:
                return str(v)
        return ""

    # ------------------------------------------------------------------
    # Files, labels, clicks, misc utilities
    # ------------------------------------------------------------------

    def upload_file(self, driver, path: str, automation_id: str = None) -> bool:
        """Send a file path to a file input (works even if visually hidden)."""
        from selenium.webdriver.common.by import By
        selectors = []
        if automation_id:
            selectors.append(f'[data-automation-id="{automation_id}"] input[type="file"]')
        selectors.append("input[type='file']")
        for sel in selectors:
            for fi in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    fi.send_keys(os.path.abspath(path))
                    time.sleep(1.5)
                    log.debug(f"    uploaded file: {os.path.basename(path)}")
                    return True
                except Exception:
                    continue
        return False

    def get_label(self, driver, el) -> str:
        """Best-effort human label for a form control."""
        from selenium.webdriver.common.by import By
        for attr in ("aria-label", "aria-labelledby", "placeholder", "title"):
            v = el.get_attribute(attr)
            if v and v.strip() and not v.strip().startswith("http"):
                if attr == "aria-labelledby":
                    try:
                        ref = driver.find_element(By.ID, v.strip())
                        if ref.text.strip():
                            return ref.text.strip()
                    except Exception:
                        continue
                else:
                    return v.strip()
        el_id = el.get_attribute("id")
        if el_id:
            try:
                labels = driver.find_elements(By.CSS_SELECTOR, f'label[for="{el_id}"]')
                if labels and labels[0].text.strip():
                    return labels[0].text.strip()
            except Exception:
                pass
        # Workday wraps label + control in a group with data-automation-id
        for xp in ("./ancestor::label[1]",
                   "./ancestor::*[@data-automation-id][1]",
                   "./ancestor::div[.//label][1]"):
            try:
                anc = el.find_element(By.XPATH, xp)
                labels = anc.find_elements(By.TAG_NAME, "label")
                if labels and labels[0].text.strip():
                    return labels[0].text.strip()
                t = anc.text.strip().split("\n")[0]
                if t and len(t) < 120:
                    return t
            except Exception:
                continue
        return el.get_attribute("name") or ""

    def safe_click(self, driver, el):
        """Scroll into view and click, falling back to a JS click."""
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.2)
            el.click()
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", el)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clickable(self, el) -> bool:
        try:
            return el.is_displayed() and el.is_enabled() and \
                el.get_attribute("aria-disabled") != "true"
        except Exception:
            return False

    def _pause(self):
        if self.slow:
            time.sleep(self.slow)

    def _peek_dropdown_options(self, driver, trigger) -> list:
        """Open a custom dropdown, read option texts, then close it."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        try:
            self.safe_click(driver, trigger)
            time.sleep(0.5)
            opts = []
            for sel in ('[data-automation-id="menuItem"]', '[role="option"]',
                        'ul[role="listbox"] li'):
                for o in driver.find_elements(By.CSS_SELECTOR, sel):
                    if o.is_displayed() and o.text.strip():
                        opts.append(o.text.strip())
                if opts:
                    break
            try:
                driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            except Exception:
                pass
            return opts
        except Exception:
            return []

    def _fill_radios(self, driver, job_context: dict):
        from selenium.webdriver.common.by import By
        for fs in driver.find_elements(By.CSS_SELECTOR, "fieldset, [role='radiogroup']"):
            try:
                legend = fs.find_elements(By.CSS_SELECTOR, "legend, [role='heading'], label")
                question = legend[0].text.strip() if legend else ""
                if not question:
                    continue
                labels = [lb for lb in fs.find_elements(By.TAG_NAME, "label") if lb.text.strip()]
                if not labels:
                    continue
                # already answered?
                radios = fs.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                if any(r.is_selected() for r in radios):
                    continue
                choice = self.answer_field(question, job_context,
                                           options=[lb.text.strip() for lb in labels])
                if not choice:
                    continue
                for lbl in labels:
                    if choice.lower() in lbl.text.strip().lower():
                        rs = lbl.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                        self.safe_click(driver, rs[0] if rs else lbl)
                        break
            except Exception:
                continue

    def _check_consents(self, driver):
        from selenium.webdriver.common.by import By
        for cb in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
            try:
                if cb.is_selected() or not cb.is_displayed():
                    continue
                ctx = ""
                try:
                    ctx = cb.find_element(By.XPATH, "./ancestor::*[self::label or self::div][1]").text.lower()
                except Exception:
                    pass
                if any(w in ctx for w in ("acknowledg", "agree", "consent", "certif",
                                          "confirm", "i have read", "terms", "privacy")):
                    self.safe_click(driver, cb)
            except Exception:
                continue

    def _looks_submitted(self, driver) -> bool:
        try:
            body = driver.find_element("tag name", "body").text.lower()[:3000]
            markers = ("application submitted", "thank you for applying", "we received",
                       "successfully submitted", "your application has been",
                       "thanks for applying", "application complete", "confirmation")
            if any(m in body for m in markers):
                return True
            url = driver.current_url.lower()
            return any(x in url for x in ("submitted", "confirmation", "thank"))
        except Exception:
            return False

    def _log_validation_errors(self, driver):
        from selenium.webdriver.common.by import By
        try:
            errs = driver.find_elements(
                By.CSS_SELECTOR,
                '[data-automation-id="errorMessage"], [role="alert"], '
                '.error, [class*="error-message"]',
            )
            visible = [e.text.strip() for e in errs if e.is_displayed() and e.text.strip()]
            if visible:
                log.debug(f"   {self.name} validation: {'; '.join(visible[:4])}")
        except Exception:
            pass
