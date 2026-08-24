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
import re
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
            if not self._authenticate(driver, job_context.get("url", "")):
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

    def _account(self, driver, job_url: str = "") -> dict:
        """Resolve credentials for this tenant.

        Precedence: explicit per-tenant config > flat config > the credential
        vault, which MINTS a unique strong password per tenant and saves it to
        the accounts sheet. That last path is what makes self-registration
        autonomous — nothing has to be pre-configured.

        The returned dict carries `status`: "new" (register) or "existing"
        (sign in with the stored password).
        """
        acct = dict(self.ats_accounts.get("workday", {}) or {})
        tenant = self._tenant(driver)
        by_tenant = acct.get("tenants", {}) or {}
        if tenant in by_tenant:
            acct.update(by_tenant[tenant])
        acct.setdefault("email", self.personal.get("email", ""))
        acct.setdefault("status", "existing")

        # A configured password is only usable if it meets ATS complexity rules.
        configured = acct.get("password", "")
        if configured:
            from credential_vault import password_is_strong
            if password_is_strong(configured):
                return acct
            log.warning("   workday: configured password is too weak for %s — "
                        "using a generated one", tenant)

        vault = self._vault()
        if vault and tenant:
            cred = vault.get_or_create(tenant, ats="workday", job_url=job_url,
                                       email=acct.get("email", ""))
            if cred:
                acct.update({"email": cred["email"], "password": cred["password"],
                             "status": cred.get("status", "new")})
        return acct

    def _vault(self):
        """Lazy credential vault (None when disabled or unavailable)."""
        if getattr(self, "_vault_obj", "unset") == "unset":
            self._vault_obj = None
            try:
                from credential_vault import CredentialVault
                v = CredentialVault(self.cfg)
                self._vault_obj = v if (v.enabled and v.auto_register) else None
            except Exception as exc:
                log.debug("   workday: vault unavailable (%s)", exc)
        return self._vault_obj

    @staticmethod
    def _any_visible(driver, selector: str) -> bool:
        from selenium.webdriver.common.by import By
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if el.is_displayed():
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _credential_inputs_present(self, driver) -> bool:
        """A place to TYPE credentials — the only honest sign we're still at auth.

        Deliberately excludes "Sign In" / "Create Account" LINKS: Workday keeps
        those in the header after a successful registration, and counting them
        made every successful account creation report as failed.
        """
        for aid in ("email", "password", "verifyPassword"):
            if self._any_visible(driver, f'[data-automation-id="{aid}"]'):
                return True
        return self._any_visible(driver, "input[type='password']")

    def _needs_auth(self, driver) -> bool:
        """Is there an auth wall to get past BEFORE applying?

        Here a link counts: a page offering only "Create Account" is still a
        wall. Use `_auth_complete()` to judge whether auth SUCCEEDED.
        """
        if self._credential_inputs_present(driver):
            return True
        for aid in ("createAccountLink", "signInLink", "createAccountCheckbox"):
            if self._any_visible(driver, f'[data-automation-id="{aid}"]'):
                return True
        return False

    def _auth_complete(self, driver) -> bool:
        """Did we get PAST auth? True when no credential input is left on screen."""
        return not self._credential_inputs_present(driver)

    def _authenticate(self, driver, job_url: str = "") -> bool:
        """Register on this tenant, or sign in when we already have an account.

        With the credential vault enabled this needs nothing pre-configured:
        a unique strong password is minted per tenant, the account is created,
        and the credential is written to the accounts sheet so you can log in
        yourself later.
        """
        tenant = self._tenant(driver)
        acct = self._account(driver, job_url=job_url)
        email = acct.get("email", "")
        password = acct.get("password", "")
        vault = self._vault()
        if not email or not password:
            log.warning("   workday: no credentials available for %s — set "
                        "personal.email or external_apply.ats_accounts.workday",
                        tenant or "this tenant")
            return False

        known = acct.get("status") == "existing"
        # A known account signs in first; an unknown one registers first.
        # NOTE: label each attempt explicitly — `attempt is self._try_register`
        # is always False, because attribute access mints a new bound method.
        attempts = [("signed_in", self._try_sign_in), ("registered", self._try_register)] \
            if known else [("registered", self._try_register), ("signed_in", self._try_sign_in)]
        for outcome, attempt in attempts:
            if attempt(driver, email, password):
                if vault:
                    vault.mark(tenant, outcome)
                log.info("   ✅ workday: %s on %s",
                         "registered" if outcome == "registered" else "signed in", tenant)
                return True
        if vault:
            vault.mark(tenant, "auth_failed",
                       "could not register or sign in — may need email verification")
        log.warning("   workday: could not register or sign in on %s", tenant)
        return False

    def _try_register(self, driver, email: str, password: str) -> bool:
        if not self._open_create_account(driver):
            return False
        if self._account_exists_error(driver):
            return False
        return self._submit_create_account(driver, email, password)

    def _try_sign_in(self, driver, email: str, password: str) -> bool:
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
        return clicked and self._auth_complete(driver)

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
        return self._auth_complete(driver) and not self._auth_error(driver)

    # Text that appears in role="alert" but is guidance, not failure. Treating
    # these as errors aborted account creation on tenants that show live
    # password-policy hints.
    _HINT_RE = re.compile(
        r"must (contain|be at least|include)|password (must|should)|"
        r"at least \d+ character|1 number|one number|special character|"
        r"case letter|requirements?:", re.I)
    _REAL_ERROR_RE = re.compile(
        r"already (exists|in use|registered)|invalid|incorrect|does not match|"
        r"could not|unable to|failed|try again|not recognized|required field", re.I)

    def _auth_error(self, driver) -> bool:
        """True only for a genuine failure, not for policy hints."""
        from selenium.webdriver.common.by import By
        try:
            for e in driver.find_elements(
                    By.CSS_SELECTOR,
                    '[data-automation-id="errorMessage"], [role="alert"]'):
                try:
                    if not e.is_displayed():
                        continue
                    txt = (e.text or "").strip()
                except Exception:
                    continue
                if not txt:
                    continue
                if self._REAL_ERROR_RE.search(txt):
                    log.info("   workday auth error: %s", txt[:120])
                    return True
                if self._HINT_RE.search(txt):
                    continue          # policy guidance — not a failure
                # An explicit errorMessage node with unclassified text is an error.
                if (e.get_attribute("data-automation-id") or "") == "errorMessage":
                    log.info("   workday auth error: %s", txt[:120])
                    return True
        except Exception:
            pass
        return False

    def _account_exists_error(self, driver) -> bool:
        """Specifically: this email is already registered on this tenant."""
        from selenium.webdriver.common.by import By
        try:
            for e in driver.find_elements(
                    By.CSS_SELECTOR,
                    '[data-automation-id="errorMessage"], [role="alert"]'):
                if e.is_displayed() and re.search(
                        r"already (exists|in use|registered)|account.*exists",
                        e.text or "", re.I):
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
