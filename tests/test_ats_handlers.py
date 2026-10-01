"""
Tests for the ATS handler framework (ats_handlers/) and ExternalApplier routing.

These are pure-logic tests — no browser. They cover URL detection across all
supported platforms, the registry, handler wiring (names, account flags,
credential resolution), and the keyword-matching field logic.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ats_handlers as A
from ats_handlers.generic import MultiStepHandler, SinglePageHandler
from ats_handlers.handlers import ICIMSHandler
from ats_handlers.workday import WorkdayHandler
from external_apply import ExternalApplier


class _FakeDriver:
    """Minimal driver stand-in for URL-only logic (tenant resolution)."""

    def __init__(self, url):
        self.current_url = url


class TestATSDetection(unittest.TestCase):
    """Every supported platform maps from a representative URL."""

    CASES = {
        "https://nvidia.wd5.myworkdayjobs.com/en-US/careers/job/x": "workday",
        "https://acme.wd1.myworkdayjobs.com/External/job/y": "workday",
        "https://boards.greenhouse.io/acme/jobs/123": "greenhouse",
        "https://job-boards.greenhouse.io/acme/jobs/9": "greenhouse",
        "https://jobs.lever.co/acme/abc-123": "lever",
        "https://jobs.ashbyhq.com/acme/xyz": "ashby",
        "https://jobs.smartrecruiters.com/Acme/74000": "smartrecruiters",
        "https://apply.workable.com/acme/j/ABCDEF/": "workable",
        "https://jobs.jobvite.com/acme/job/oXYZ": "jobvite",
        "https://acme.bamboohr.com/careers/42": "bamboohr",
        "https://careers-acme.icims.com/jobs/1234/job": "icims",
        "https://acme.taleo.net/careersection/jobdetail.ftl": "taleo",
        "https://career5.successfactors.com/career?company=Acme": "successfactors",
        "https://myjobs.adp.com/acmecareers/cx/job/123": "adp",
    }

    def test_all_platforms_detected(self):
        for url, expected in self.CASES.items():
            self.assertEqual(A.detect_ats(url), expected, url)

    def test_unknown_and_empty(self):
        self.assertIsNone(A.detect_ats("https://example.com/careers/apply"))
        self.assertIsNone(A.detect_ats(""))
        self.assertIsNone(A.detect_ats(None))

    def test_case_insensitive(self):
        self.assertEqual(
            A.detect_ats("https://Boards.Greenhouse.IO/Acme/Jobs/1"), "greenhouse")

    def test_all_ats_list_complete(self):
        for name in ("workday", "greenhouse", "lever", "ashby", "smartrecruiters",
                     "workable", "jobvite", "bamboohr", "icims", "taleo",
                     "successfactors", "adp"):
            self.assertIn(name, A.ALL_ATS)
        self.assertNotIn("generic", A.ALL_ATS)


class TestRegistry(unittest.TestCase):
    """get_handler / handler_for_url wiring."""

    CFG = {"personal": {}, "application": {}, "external_apply": {}}

    def test_get_handler_by_name(self):
        for name in A.ALL_ATS + ["generic"]:
            h = A.get_handler(name, None, self.CFG)
            self.assertIsNotNone(h, name)
            self.assertEqual(h.name, name)

    def test_get_handler_unknown(self):
        self.assertIsNone(A.get_handler("monster", None, self.CFG))
        self.assertIsNone(A.get_handler("", None, self.CFG))

    def test_handler_for_url(self):
        h = A.handler_for_url("https://jobs.lever.co/acme/1", None, self.CFG)
        self.assertEqual(h.name, "lever")
        self.assertIsNone(A.handler_for_url("https://example.com", None, self.CFG))

    def test_generic_is_single_page(self):
        self.assertTrue(issubclass(A.GenericHandler, SinglePageHandler))


class TestHandlerShapes(unittest.TestCase):
    """Handlers declare the right shape and account requirements."""

    def test_login_gated_platforms(self):
        for name in ("icims", "taleo", "successfactors", "adp"):
            h = A.get_handler(name, None, {})
            self.assertTrue(h.needs_account, name)
            self.assertIsInstance(h, MultiStepHandler)

    def test_open_platforms_no_account(self):
        for name in ("greenhouse", "lever", "ashby", "smartrecruiters",
                     "workable", "jobvite", "bamboohr"):
            h = A.get_handler(name, None, {})
            self.assertFalse(h.needs_account, name)
            self.assertIsInstance(h, SinglePageHandler)

    def test_workday_needs_account(self):
        h = A.get_handler("workday", None, {})
        self.assertTrue(h.needs_account)
        self.assertIsInstance(h, WorkdayHandler)


class TestWorkdayAccount(unittest.TestCase):
    """Per-tenant credential resolution for Workday."""

    def _handler(self, ats_accounts, personal=None):
        import tempfile
        cfg = {
            "personal": personal or {},
            "application": {},
            "external_apply": {"ats_accounts": ats_accounts},
            # Isolate the credential vault so unit tests never touch data/.
            "credential_vault": {"output_dir": tempfile.mkdtemp()},
        }
        return WorkdayHandler(None, cfg)

    def test_tenant_parsed_from_url(self):
        h = self._handler({})
        d = _FakeDriver("https://nvidia.wd5.myworkdayjobs.com/en-US/x/job/y")
        self.assertEqual(h._tenant(d), "nvidia.wd5.myworkdayjobs.com")

    def test_flat_credentials_reused(self):
        # A STRONG configured password is used verbatim across tenants.
        h = self._handler({"workday": {"email": "a@b.com", "password": "Str0ng!Pass99"}})
        acct = h._account(_FakeDriver("https://x.wd1.myworkdayjobs.com/j"))
        self.assertEqual(acct["email"], "a@b.com")
        self.assertEqual(acct["password"], "Str0ng!Pass99")

    def test_weak_configured_password_is_replaced(self):
        # A weak one would be rejected by the ATS, so the vault mints a real one.
        from credential_vault import password_is_strong
        h = self._handler({"workday": {"email": "a@b.com", "password": "pw123"}})
        acct = h._account(_FakeDriver("https://x.wd1.myworkdayjobs.com/j"))
        self.assertNotEqual(acct["password"], "pw123")
        self.assertTrue(password_is_strong(acct["password"]))

    def test_personal_email_fallback(self):
        h = self._handler({"workday": {"password": "Str0ng!Pass99"}},
                          personal={"email": "me@personal.com"})
        acct = h._account(_FakeDriver("https://x.wd1.myworkdayjobs.com/j"))
        self.assertEqual(acct["email"], "me@personal.com")

    def test_per_tenant_override(self):
        h = self._handler({"workday": {
            "email": "default@b.com", "password": "Default!Pass99",
            "tenants": {"special.wd5.myworkdayjobs.com":
                        {"email": "special@b.com", "password": "Special!Pass99"}},
        }})
        acct = h._account(_FakeDriver("https://special.wd5.myworkdayjobs.com/j"))
        self.assertEqual(acct["email"], "special@b.com")
        self.assertEqual(acct["password"], "Special!Pass99")


class TestAccountMixin(unittest.TestCase):
    """Credential lookup for login-gated multi-step platforms."""

    def test_creds_from_account_key(self):
        cfg = {"personal": {"email": "me@x.com"},
               "external_apply": {"ats_accounts": {"icims": {"password": "pw"}}}}
        h = ICIMSHandler(None, cfg)
        email, pw = h._creds()
        self.assertEqual(email, "me@x.com")  # falls back to personal
        self.assertEqual(pw, "pw")


class TestKeywordMatch(unittest.TestCase):
    """The free, fast identity/eligibility field matcher."""

    def setUp(self):
        cfg = {
            "personal": {
                "first_name": "Ada", "last_name": "Lovelace",
                "full_name": "Ada Lovelace", "email": "ada@x.com",
                "phone": "555-0100", "city": "London", "state": "England",
                "zip_code": "EC1", "country": "UK", "address": "1 Analytical St",
            },
            "application": {
                "years_of_experience": 7, "desired_salary": "150000",
                "authorized_to_work": "Yes", "require_visa": "No",
                "willing_to_relocate": "Yes",
            },
            "question_answers": {"linkedin": "linkedin.com/in/ada",
                                 "github": "github.com/ada"},
        }
        self.h = A.get_handler("greenhouse", None, cfg)

    def test_identity_fields(self):
        self.assertEqual(self.h.keyword_match("First Name"), "Ada")
        self.assertEqual(self.h.keyword_match("Last Name"), "Lovelace")
        self.assertEqual(self.h.keyword_match("Email Address"), "ada@x.com")
        self.assertEqual(self.h.keyword_match("Mobile Phone"), "555-0100")
        self.assertEqual(self.h.keyword_match("City"), "London")
        self.assertEqual(self.h.keyword_match("Country"), "UK")

    def test_eligibility_fields(self):
        self.assertEqual(self.h.keyword_match("Are you authorized to work?"), "Yes")
        self.assertEqual(self.h.keyword_match("Do you require visa sponsorship?"), "No")
        self.assertEqual(self.h.keyword_match("Willing to relocate?"), "Yes")

    def test_links_and_experience(self):
        self.assertEqual(self.h.keyword_match("LinkedIn Profile"), "linkedin.com/in/ada")
        self.assertEqual(self.h.keyword_match("GitHub URL"), "github.com/ada")
        self.assertEqual(self.h.keyword_match("Years of experience"), "7")

    def test_no_match_returns_empty(self):
        self.assertEqual(self.h.keyword_match("Describe your ideal team"), "")
        self.assertEqual(self.h.keyword_match(""), "")

    def test_answer_field_without_ai(self):
        # keyword hit works with no AI; miss returns empty (no AI to call)
        self.assertEqual(self.h.answer_field("Email", {}), "ada@x.com")
        self.assertEqual(self.h.answer_field("Favorite color", {}), "")


class TestExternalApplierRouting(unittest.TestCase):
    """ExternalApplier public API and supported_ats filtering stay intact."""

    def test_default_supports_all(self):
        ea = ExternalApplier(None, {"external_apply": {"enabled": True}})
        self.assertTrue(ea.enabled)
        for name in A.ALL_ATS:
            self.assertIn(name, ea.supported_ats)

    def test_detect_respects_supported_filter(self):
        ea = ExternalApplier(None, {"external_apply": {
            "enabled": True, "supported_ats": ["greenhouse"]}})
        self.assertEqual(ea.detect_ats("https://boards.greenhouse.io/x/jobs/1"),
                         "greenhouse")
        # Workday is real but not in this user's supported list → None
        self.assertIsNone(ea.detect_ats("https://x.wd5.myworkdayjobs.com/j"))

    def test_can_apply_respects_cap(self):
        ea = ExternalApplier(None, {"external_apply": {
            "enabled": True, "max_external_per_cycle": 2}})
        self.assertTrue(ea.can_apply())
        ea.applied_this_cycle = 2
        self.assertFalse(ea.can_apply())

    def test_disabled_blocks_apply(self):
        ea = ExternalApplier(None, {"external_apply": {"enabled": False}})
        self.assertFalse(ea.can_apply())


if __name__ == "__main__":
    unittest.main()
