"""
Regression tests for the prose-label keyword collision.

Found by running the extension against the GENUINE live Greenhouse application
form for Monzo's "Credit Risk Manager, Portfolio Management": the consent
question

    "🔐 Keeping your data safe is really important to us. Please take a moment
     to read our privacy notice …"

was being answered with the notice PERIOD ("30 days") because the label happens
to contain the substring "notice". The same loose substring rules existed in all
three answering layers (browser extension, ats_handlers/base.py, linkedin.py).

These tests pin the fix: sentence-like / consent labels are never answered from
the short keyword map, while genuine short field labels still are.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ats_handlers.base import ATSHandler, is_prose_label

# The exact label from the live Monzo form that triggered the bug.
LIVE_CONSENT_LABEL = (
    "🔐 Keeping your data safe is really important to us. Please take a moment "
    "to read our privacy notice and confirm you're happy for us to process your "
    "application."
)

CFG = {
    "personal": {"first_name": "Ankit", "last_name": "Kumar", "city": "Gurugram",
                 "email": "a@b.com", "phone": "+91 90000 00000"},
    "application": {"notice_period_days": "30 days", "desired_salary": "Negotiable"},
    "question_answers": {},
}


class TestIsProseLabel(unittest.TestCase):
    def test_live_consent_label_is_prose(self):
        self.assertTrue(is_prose_label(LIVE_CONSENT_LABEL))

    def test_consent_keywords_flagged_even_when_short(self):
        for label in ("Privacy policy", "I consent to data processing",
                      "GDPR acknowledgement", "Terms and conditions"):
            self.assertTrue(is_prose_label(label), label)

    def test_long_essay_prompt_is_prose(self):
        self.assertTrue(is_prose_label(
            "Tell us about a time you had to influence a senior stakeholder "
            "without formal authority, and what the outcome was."))

    def test_real_field_labels_are_not_prose(self):
        for label in ("First Name", "Last Name", "Email", "Phone",
                      "Notice period", "City", "Salary expectations",
                      "Years of experience", "LinkedIn Profile",
                      "What is your notice period?"):
            self.assertFalse(is_prose_label(label), label)

    def test_empty(self):
        self.assertFalse(is_prose_label(""))
        self.assertFalse(is_prose_label(None))


class TestKeywordMatchGuard(unittest.TestCase):
    def setUp(self):
        self.h = ATSHandler(None, CFG)

    def test_consent_label_gets_no_keyword_answer(self):
        # THE BUG: this used to return "30 days".
        self.assertEqual(self.h.keyword_match(LIVE_CONSENT_LABEL), "")

    def test_genuine_notice_period_still_answered(self):
        self.assertEqual(self.h.keyword_match("Notice period"), "30 days")
        self.assertEqual(self.h.keyword_match("What is your notice period?"), "30 days")

    def test_standard_fields_unaffected(self):
        self.assertEqual(self.h.keyword_match("First Name"), "Ankit")
        self.assertEqual(self.h.keyword_match("Last Name"), "Kumar")
        self.assertEqual(self.h.keyword_match("City"), "Gurugram")
        self.assertEqual(self.h.keyword_match("Email"), "a@b.com")

    def test_city_not_matched_inside_a_sentence(self):
        # "city" as a substring of prose must not fill the city field.
        self.assertEqual(self.h.keyword_match(
            "Describe how you would build credit strategy for a city-based "
            "lending portfolio serving diverse customers."), "")


class TestLinkedInFindAnswerGuard(unittest.TestCase):
    def test_easy_apply_matcher_guards_prose(self):
        try:
            from linkedin import _find_answer
        except ImportError:
            self.skipTest("selenium not installed")
        app = {"notice_period_days": "30 days", "desired_salary": "Negotiable"}
        # Bug: prose containing "notice" returned the notice period.
        self.assertEqual(_find_answer(LIVE_CONSENT_LABEL.lower(), {}, app, {}), "")
        # Genuine label still answered.
        self.assertEqual(_find_answer("notice period", {}, app, {}), "30 days")


if __name__ == "__main__":
    unittest.main()
