"""
Per-platform ATS handlers.

Each class is deliberately thin: the real work lives in the base primitives and
the two shapes in generic.py. A handler only declares how its platform differs —
the button that starts the flow, the button that submits, and whether a login is
required. Add a new ATS by writing a class like these and registering it in
``__init__.py``.
"""

import logging
import time

from .generic import MultiStepHandler, SinglePageHandler

log = logging.getLogger("lla.ats")


# ---------------------------------------------------------------------------
# Modern single-page React forms
# ---------------------------------------------------------------------------

class GreenhouseHandler(SinglePageHandler):
    name = "greenhouse"
    submit_labels = ["submit application", "submit"]
    submit_automation_ids = ["submit_app"]

    def apply(self, driver, job_context: dict, resume_path: str = "") -> bool:
        # Greenhouse is sometimes embedded in an iframe on company career sites.
        self._enter_iframe(driver)
        return super().apply(driver, job_context, resume_path)

    def _enter_iframe(self, driver):
        from selenium.webdriver.common.by import By
        for f in driver.find_elements(By.CSS_SELECTOR,
                                      "iframe#grnhse_iframe, iframe[src*='greenhouse']"):
            try:
                driver.switch_to.frame(f)
                time.sleep(1)
                return
            except Exception:
                continue


class LeverHandler(SinglePageHandler):
    name = "lever"
    submit_labels = ["submit application", "submit"]


class AshbyHandler(SinglePageHandler):
    name = "ashby"
    start_labels = ["apply for this job", "application"]
    submit_labels = ["submit application", "submit"]


class SmartRecruitersHandler(SinglePageHandler):
    name = "smartrecruiters"
    # SmartRecruiters gates the form behind an "I'm interested" button.
    start_labels = ["i'm interested", "im interested", "apply"]
    submit_labels = ["submit application", "i'm interested", "submit", "apply"]


class WorkableHandler(SinglePageHandler):
    name = "workable"
    start_labels = ["apply for this job", "apply now"]
    submit_labels = ["submit application", "send application", "submit"]


class JobviteHandler(SinglePageHandler):
    name = "jobvite"
    start_labels = ["apply", "apply now", "apply for this job"]
    submit_labels = ["submit", "send application", "apply"]


class BambooHRHandler(SinglePageHandler):
    name = "bamboohr"
    start_labels = ["apply for this job", "apply now"]
    submit_labels = ["submit application", "submit"]


# ---------------------------------------------------------------------------
# Legacy / enterprise multi-step wizards (usually login-gated)
# ---------------------------------------------------------------------------

class ICIMSHandler(MultiStepHandler):
    name = "icims"
    needs_account = True
    account_key = "icims"
    start_labels = ["apply for this job", "apply", "apply now"]

    def apply(self, driver, job_context: dict, resume_path: str = "") -> bool:
        # iCIMS renders the form inside an iframe on most tenants.
        self._enter_iframe(driver)
        return super().apply(driver, job_context, resume_path)

    def _enter_iframe(self, driver):
        from selenium.webdriver.common.by import By
        for f in driver.find_elements(By.CSS_SELECTOR,
                                      "iframe#icims_content_iframe, iframe[src*='icims']"):
            try:
                driver.switch_to.frame(f)
                time.sleep(1)
                return
            except Exception:
                continue


class TaleoHandler(MultiStepHandler):
    name = "taleo"
    needs_account = True
    account_key = "taleo"
    start_labels = ["apply online", "apply", "apply now", "apply to job"]


class SuccessFactorsHandler(MultiStepHandler):
    name = "successfactors"
    needs_account = True
    account_key = "successfactors"
    start_labels = ["apply now", "apply", "start"]


class ADPHandler(MultiStepHandler):
    name = "adp"
    needs_account = True
    account_key = "adp"
    start_labels = ["apply", "apply now", "start application"]
