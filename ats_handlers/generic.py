"""
Generic handler behaviours shared by most ATS platforms.

Two shapes cover almost every non-Workday ATS:

  * ``SinglePageHandler`` — one long form (Greenhouse, Lever, Ashby,
    SmartRecruiters, Workable, Jobvite, BambooHR): sweep the page, click submit.
  * ``MultiStepHandler`` — a paged wizard, often behind a login (iCIMS, Taleo,
    SuccessFactors, ADP): optionally register/sign-in, then run the Next/Submit
    loop from the base class.

Per-platform handlers subclass these and only declare their start/submit button
hints and (for logins) their credential key. ``AccountMixin`` provides a
best-effort register-or-sign-in flow driven by ``external_apply.ats_accounts``.
"""

import logging
import time

from .base import _SUBMIT_LABELS, ATSHandler

log = logging.getLogger("lla.ats.generic")


class AccountMixin:
    """Best-effort 'create account or sign in' for ATSes that gate applications."""

    #: config key under external_apply.ats_accounts (defaults to handler name)
    account_key: str = ""

    def _creds(self):
        key = self.account_key or self.name
        acct = self.ats_accounts.get(key, {}) or {}
        email = acct.get("email") or self.personal.get("email", "")
        return email, acct.get("password", "")

    def _auth_present(self, driver) -> bool:
        from selenium.webdriver.common.by import By
        pw = driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
        return any(p.is_displayed() for p in pw)

    def ensure_account(self, driver) -> bool:
        """If a login/registration wall is showing, get past it. Return True if clear."""
        if not self._auth_present(driver):
            return True
        email, password = self._creds()
        if not email or not password:
            log.warning(f"   {self.name}: login required but no ats_accounts.{self.account_key or self.name} configured")
            return False

        from selenium.webdriver.common.by import By
        # Fill email/username + password (+ confirm password if a register form).
        for sel in ("input[type='email']", "input[name*='email' i]",
                    "input[id*='email' i]", "input[name*='user' i]"):
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                if self.fill_text(driver, el, email):
                    break
        pws = [p for p in driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
               if p.is_displayed()]
        for p in pws:
            self.fill_text(driver, p, password, force=True)

        self.click_button(
            driver,
            labels=["create account", "register", "sign in", "log in", "login",
                    "continue", "submit"],
            automation_ids=["createAccount", "signIn", "login", "register"],
        )
        time.sleep(2.5)
        return not self._auth_present(driver)


class SinglePageHandler(ATSHandler):
    """One-page application form: fill everything, then submit."""

    #: optional "Apply for this job" gate before the form is shown
    start_labels: list = []
    start_automation_ids: list = []
    #: submit button text hints (defaults to the shared terminal labels)
    submit_labels: list = _SUBMIT_LABELS
    submit_automation_ids: list = []

    def apply(self, driver, job_context: dict, resume_path: str = "") -> bool:
        time.sleep(2)
        if self.start_labels or self.start_automation_ids:
            if self.click_button(driver, labels=self.start_labels,
                                 automation_ids=self.start_automation_ids):
                time.sleep(2)

        # Two passes: React forms often reveal conditional fields after the first.
        self.sweep_page(driver, job_context, resume_path)
        self.sweep_page(driver, job_context, resume_path)

        if self.click_button(driver, labels=self.submit_labels,
                             automation_ids=self.submit_automation_ids):
            time.sleep(3)
            if self._looks_submitted(driver):
                log.info(f"   ✅ {self.name}: application submitted")
            else:
                self._log_validation_errors(driver)
                log.info(f"   ✅ {self.name}: submit clicked")
            return True
        log.warning(f"   {self.name}: submit button not found")
        return False


class MultiStepHandler(AccountMixin, ATSHandler):
    """Paged wizard, optionally behind a login."""

    start_labels: list = ["apply", "apply now", "start application"]
    start_automation_ids: list = []

    def apply(self, driver, job_context: dict, resume_path: str = "") -> bool:
        time.sleep(2)
        if self.start_labels or self.start_automation_ids:
            if self.click_button(driver, labels=self.start_labels,
                                 automation_ids=self.start_automation_ids):
                time.sleep(2)
        if self.needs_account and not self.ensure_account(driver):
            return False
        return self.run_multistep(driver, job_context, resume_path)


class GenericHandler(SinglePageHandler):
    """Fallback for an unknown ATS: treat it as a single-page form."""

    name = "generic"
