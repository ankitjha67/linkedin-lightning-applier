"""
Workday handler — the "dreaded" enterprise ATS.

Why Workday is hard, and how this handler wins:

  * Every company runs its own Workday *tenant* (e.g. ``nvidia.wd5.myworkdayjobs.com``)
    but they ALL share the same stable ``data-automation-id`` attributes. We target
    those instead of fragile CSS/text, so one handler drives every tenant.
  * Workday forces you to create an account (or sign in) before you can apply. We
    reuse a single email+password from config across every tenant: create the
    account the first time, sign in on return visits.
  * The form is a 5-7 page React wizard (My Information → My Experience →
    Application Questions → Voluntary Disclosures → Self Identify → Review →
    Submit) with custom dropdowns, typeahead selectors and date pickers — none
    of them native ``<select>``. The base class primitives handle those.

Nothing here hard-codes a company: give it any ``*.myworkdayjobs.com`` URL.
"""

import logging
import time
from urllib.parse import urlparse

from .base import ATSHandler

log = logging.getLogger("lla.ats.workday")


class WorkdayHandler(ATSHandler):
    name = "workday"
    needs_account = True

    def apply(self, driver, job_context: dict, resume_path: str = "") -> bool:
        time.sleep(3)  # Workday is slow to hydrate

        # 1. Kick off the application from the job posting.
        self.click_button(
            driver,
            labels=["apply", "apply now", "apply manually"],
            automation_ids=["jobPostingApplyButton", "apply", "adventureButton"],
        )
        time.sleep(2)

        # Workday sometimes offers "Autofill with Resume" vs "Apply Manually".
        # Manual is more reliable — we drive every field ourselves.
        self.click_button(driver, labels=["apply manually", "start your application"],
                          automation_ids=["applyManually"])
        time.sleep(1.5)

        # 2. Authenticate (create account or sign in) if the tenant demands it.
        if self._needs_auth(driver):
            if not self._authenticate(driver):
                log.warning("   workday: could not create/sign into account")
                return False
            time.sleep(2)
            # After auth Workday usually drops us back onto the apply flow.
            self.click_button(driver, labels=["apply manually", "apply"],
                              automation_ids=["applyManually", "jobPostingApplyButton"])
            time.sleep(1.5)

        # 3. Drive the wizard, with Workday-specific handling per page.
        return self.run_multistep(driver, job_context, resume_path,
                                  per_page=self._page)

    # ------------------------------------------------------------------
    # Account creation / sign-in
    # ------------------------------------------------------------------

    def _tenant(self, driver) -> str:
        """Return the tenant host, e.g. 'nvidia.wd5.myworkdayjobs.com'."""
        try:
            return urlparse(driver.current_url).netloc.lower()
        except Exception:
            return ""

    def _account(self, driver) -> dict:
        """Resolve credentials for this tenant.

        Looks up ``external_apply.ats_accounts.workday``. Supports either a flat
        ``{email, password}`` reused across all tenants, or a per-tenant map keyed
        by host. Falls back to the personal email so we can at least create one.
        """
        acct = dict(self.ats_accounts.get("workday", {}) or {})
        tenant = self._tenant(driver)
        # Per-tenant override wins if present.
        by_tenant = acct.get("tenants", {})
        if tenant in by_tenant:
            acct.update(by_tenant[tenant])
        acct.setdefault("email", self.personal.get("email", ""))
        return acct

    def _needs_auth(self, driver) -> bool:
        from selenium.webdriver.common.by import By
        for aid in ("email", "password", "createAccountLink", "signInLink",
                    "createAccountCheckbox"):
            if driver.find_elements(By.CSS_SELECTOR, f'[data-automation-id="{aid}"]'):
                return True
        # Text fallback
        try:
            body = driver.find_element(By.TAG_NAME, "body").text.lower()[:1500]
            return "create account" in body or "sign in" in body
        except Exception:
            return False

    def _authenticate(self, driver) -> bool:
        """Create an account, or sign in if one already exists for this tenant."""
        acct = self._account(driver)
        email = acct.get("email", "")
        password = acct.get("password", "")
        if not email or not password:
            log.warning("   workday: no ats_accounts.workday email/password configured")
            return False

        # Prefer creating an account (idempotent-ish: Workday tells us if it exists).
        if self._open_create_account(driver):
            if self._submit_create_account(driver, email, password):
                return True
            # Account probably already exists on this tenant → sign in instead.
            log.info("   workday: account exists, switching to sign-in")

        return self._sign_in(driver, email, password)

    def _open_create_account(self, driver) -> bool:
        return self.click_button(
            driver,
            labels=["create account", "create an account"],
            automation_ids=["createAccountLink", "createAccount"],
        ) or self._on_create_form(driver)

    def _on_create_form(self, driver) -> bool:
        from selenium.webdriver.common.by import By
        return bool(driver.find_elements(
            By.CSS_SELECTOR, '[data-automation-id="verifyPassword"]'))

    def _submit_create_account(self, driver, email: str, password: str) -> bool:
        from selenium.webdriver.common.by import By
        time.sleep(1)
        self.fill_by_automation_id(driver, "email", email)
        self.fill_by_automation_id(driver, "password", password)
        # Confirm-password field only exists on the create flow.
        if not self.fill_by_automation_id(driver, "verifyPassword", password):
            return False  # not a create form after all
        # Mandatory "I agree" checkbox.
        for cb in driver.find_elements(
                By.CSS_SELECTOR,
                '[data-automation-id="createAccountCheckbox"], '
                '[data-automation-id="createAccountCheckbox"] input'):
            try:
                if cb.get_attribute("type") == "checkbox" and not cb.is_selected():
                    self.safe_click(driver, cb)
            except Exception:
                pass
        clicked = self.click_button(
            driver,
            labels=["create account", "submit"],
            automation_ids=["createAccountSubmitButton", "click_filter"],
        )
        time.sleep(2.5)
        # Success = we left the auth screen and no duplicate-email error is shown.
        if self._auth_error(driver):
            return False
        return clicked and not self._needs_auth(driver)

    def _sign_in(self, driver, email: str, password: str) -> bool:
        # Make sure we're on the sign-in form, not create.
        self.click_button(driver, labels=["sign in", "already have an account"],
                          automation_ids=["signInLink"])
        time.sleep(1)
        self.fill_by_automation_id(driver, "email", email)
        self.fill_by_automation_id(driver, "password", password)
        self.click_button(driver, labels=["sign in"],
                          automation_ids=["signInSubmitButton", "click_filter"])
        time.sleep(2.5)
        return not self._needs_auth(driver) and not self._auth_error(driver)

    def _auth_error(self, driver) -> bool:
        from selenium.webdriver.common.by import By
        try:
            for e in driver.find_elements(
                    By.CSS_SELECTOR,
                    '[data-automation-id="errorMessage"], [role="alert"]'):
                if e.is_displayed() and e.text.strip():
                    return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # Per-page (wizard) handling
    # ------------------------------------------------------------------

    def _page(self, driver, job_context: dict):
        """Run before the generic sweep on every wizard page.

        Handles the Workday sections whose fields have distinctive
        data-automation-ids the generic label sweep would miss.
        """
        self._how_did_you_hear(driver)
        self._legal_name(driver)
        self._address(driver)
        self._phone(driver)
        self._source_and_disclosures(driver)

    def _legal_name(self, driver):
        p = self.personal
        self.fill_by_automation_id(driver, "legalNameSection_firstName", p.get("first_name", ""))
        self.fill_by_automation_id(driver, "legalNameSection_lastName", p.get("last_name", ""))
        # Preferred name section mirrors legal name on many tenants.
        self.fill_by_automation_id(driver, "preferredNameSection_firstName", p.get("first_name", ""))
        self.fill_by_automation_id(driver, "preferredNameSection_lastName", p.get("last_name", ""))

    def _address(self, driver):
        p = self.personal
        self.fill_by_automation_id(driver, "addressSection_addressLine1",
                                   p.get("address", ""))
        self.fill_by_automation_id(driver, "addressSection_city", p.get("city", ""))
        self.fill_by_automation_id(driver, "addressSection_postalCode",
                                   p.get("zip_code", ""))
        # Country + region are custom dropdowns.
        self._auto_dropdown(driver, "addressSection_countryRegion", p.get("state", ""))
        self._auto_dropdown(driver, "countryDropdown", p.get("country", ""))

    def _phone(self, driver):
        p = self.personal
        self.fill_by_automation_id(driver, "phone-number", p.get("phone", ""))
        self.fill_by_automation_id(driver, "phoneNumber", p.get("phone", ""))
        # Phone type ("Mobile") is a required dropdown on most tenants.
        self._auto_dropdown(driver, "phone-device-type", "Mobile")
        self._auto_dropdown(driver, "phoneType", "Mobile")

    def _how_did_you_hear(self, driver):
        """The mandatory 'How Did You Hear About Us?' source field."""
        src = self.application.get("source", "LinkedIn")
        self._auto_dropdown(driver, "source", src)
        self._auto_dropdown(driver, "sourceSection_source", src)

    def _source_and_disclosures(self, driver):
        """Voluntary self-ID (gender / ethnicity / veteran / disability).

        Default to the privacy-preserving 'I don't wish to answer' unless the
        user configured explicit values, so a required field never blocks submit.
        """
        decline = "decline"  # matches "I don't wish to answer" / "Decline to self-identify"
        for aid in ("gender", "ethnicity", "hispanicOrLatino", "veteranStatus",
                    "militaryStatus", "disabilityStatus"):
            cfg_val = self.application.get(aid)
            self._auto_dropdown(driver, aid, cfg_val or decline)

    def _auto_dropdown(self, driver, automation_id: str, value: str) -> bool:
        """Open a Workday custom dropdown identified by data-automation-id."""
        if not value:
            return False
        from selenium.webdriver.common.by import By
        for sel in (f'[data-automation-id="{automation_id}"]',
                    f'button[data-automation-id="{automation_id}"]',
                    f'[data-automation-id="{automation_id}"] button'):
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if el.is_displayed() and self.select_custom_dropdown(driver, el, value):
                        return True
                except Exception:
                    continue
        return False
