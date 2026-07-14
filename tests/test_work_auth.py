"""
Tests for country-aware work authorization (work_auth.py) and its wiring into
AIAnswerer, the ATS handlers, and the LinkedIn Easy Apply keyword matcher.

The core scenario: an Indian citizen is authorized in India with no visa, and
requires sponsorship everywhere else — unless a held visa covers that country.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from work_auth import WorkAuthorization

INDIAN = {"work_authorization": {"citizenship": ["India"]}}
INDIAN_UK_VISA = {"work_authorization": {
    "citizenship": ["India"],
    "visas": [{"country": "United Kingdom", "type": "Skilled Worker"}],
}}


class TestCoreScenario(unittest.TestCase):
    """Indian passport: authorized in India, sponsorship needed elsewhere."""

    def setUp(self):
        self.wa = WorkAuthorization(INDIAN)

    def test_home_country_authorized(self):
        self.assertEqual(self.wa.answer(
            "Are you legally authorized to work in India?"), "Yes")

    def test_other_country_not_authorized(self):
        self.assertEqual(self.wa.answer(
            "Are you authorized to work in the United Kingdom?"), "No")
        self.assertEqual(self.wa.answer(
            "Are you authorized to work in Singapore?"), "No")

    def test_sponsorship_inverted(self):
        # Home country: no sponsorship needed.
        self.assertEqual(self.wa.answer(
            "Do you require visa sponsorship?", job_location="Gurugram, India"), "No")
        # Abroad: sponsorship needed — the exact behavior requested.
        self.assertEqual(self.wa.answer(
            "Will you require visa sponsorship?", job_location="London, UK"), "Yes")

    def test_job_location_used_when_question_has_no_country(self):
        self.assertEqual(self.wa.answer(
            "Are you legally authorized to work?", job_location="Toronto, Ontario, Canada"), "No")
        self.assertEqual(self.wa.answer(
            "Are you legally authorized to work?", job_location="Mumbai, Maharashtra, India"), "Yes")

    def test_question_country_beats_job_location(self):
        # Question names the US explicitly even though the job is in India.
        self.assertEqual(self.wa.answer(
            "Are you authorized to work in the United States?",
            job_location="Gurugram, India"), "No")

    def test_no_country_anywhere_falls_through(self):
        self.assertIsNone(self.wa.answer("Are you legally authorized to work?"))

    def test_non_auth_question_ignored(self):
        self.assertIsNone(self.wa.answer("How many years of Python experience?"))
        self.assertFalse(self.wa.recognizes("How many years of Python experience?"))


class TestVisaHeld(unittest.TestCase):
    """Holding a UK Skilled Worker visa flips the UK answers only."""

    def setUp(self):
        self.wa = WorkAuthorization(INDIAN_UK_VISA)

    def test_visa_country_authorized(self):
        self.assertEqual(self.wa.answer(
            "Are you authorized to work in the UK?"), "Yes")
        self.assertEqual(self.wa.answer(
            "Do you require sponsorship to work in the United Kingdom?"), "No")

    def test_other_countries_still_no(self):
        self.assertEqual(self.wa.answer(
            "Are you authorized to work in Singapore?"), "No")
        self.assertEqual(self.wa.answer(
            "Do you require visa sponsorship?", job_location="Dubai, United Arab Emirates"), "Yes")

    def test_citizenship_question_ignores_visas(self):
        # A visa is not citizenship.
        self.assertEqual(self.wa.answer("Are you a UK citizen?"), "No")
        self.assertEqual(self.wa.answer("Are you a citizen of India?"), "Yes")

    def test_authorized_countries_list(self):
        self.assertEqual(self.wa.authorized_countries(),
                         ["india", "united kingdom"])


class TestCountryDetection(unittest.TestCase):
    def setUp(self):
        self.wa = WorkAuthorization(INDIAN)

    def test_aliases(self):
        for text, expect in [
            ("London, England, United Kingdom", "united kingdom"),
            ("Great Britain", "united kingdom"),
            ("Dubai, United Arab Emirates", "united arab emirates"),
            ("Hong Kong SAR", "hong kong"),
            ("Frankfurt, Hessen, Germany", "germany"),
            ("Sydney, New South Wales, Australia", "australia"),
        ]:
            self.assertEqual(self.wa.country_from_text(text), expect, text)

    def test_city_fallback(self):
        self.assertEqual(self.wa.country_from_text("Bengaluru"), "india")
        self.assertEqual(self.wa.country_from_text("New York"), "united states")

    def test_ambiguous_short_codes_need_uppercase(self):
        # "in the US" → US matches (uppercase); "in the office" must not → India.
        self.assertEqual(self.wa.country_from_text("authorized to work in the US"),
                         "united states")
        self.assertEqual(self.wa.country_from_text("work in the office daily"), "")

    def test_options_fitting(self):
        wa = self.wa
        self.assertEqual(
            wa.answer("Do you require sponsorship to work in the UK?",
                      options=["Yes", "No"]), "Yes")
        self.assertEqual(
            wa.answer("Do you require sponsorship to work in India?",
                      options=["No, I do not require sponsorship", "Yes, I will"]),
            "No, I do not require sponsorship")
        # Options that can't express the truthful answer → None (no lie).
        self.assertIsNone(
            wa.answer("Do you require sponsorship to work in the UK?",
                      options=["H-1B", "Green Card"]))


class TestUnconfigured(unittest.TestCase):
    def test_disabled_without_config(self):
        wa = WorkAuthorization({})
        self.assertFalse(wa.enabled)
        self.assertIsNone(wa.answer("Are you authorized to work in India?"))


class TestAIAnswererWiring(unittest.TestCase):
    """work_auth runs before cache/RAG and its answers are never stored."""

    def _ai(self, cfg_extra=None):
        from ai import AIAnswerer
        conn = sqlite3.connect(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
        cfg = {"ai": {"enabled": True, "provider": "ollama"}, **INDIAN}
        cfg.update(cfg_extra or {})
        return AIAnswerer(cfg, db_conn=conn)

    def test_deterministic_answer_no_llm(self):
        ai = self._ai()
        with patch.object(ai, "_call_llm") as mock_llm:
            self.assertEqual(
                ai.answer("Are you authorized to work in India?"), "Yes")
            self.assertEqual(
                ai.answer("Do you require visa sponsorship?",
                          job_location="London, UK"), "Yes")
        mock_llm.assert_not_called()

    def test_stale_cache_never_leaks_across_countries(self):
        ai = self._ai()
        # Poison both cache layers with a wrong global "Yes" (pre-feature state).
        q = "Are you legally authorized to work?"
        ai._save_cache(q, "Yes")
        ai.rag.save(q, "Yes")
        # UK job: the work-auth layer must answer No, ignoring cache and RAG.
        self.assertEqual(ai.answer(q, job_location="London, UK"), "No")

    def test_wa_llm_answers_not_saved(self):
        ai = self._ai()
        # Unresolvable country → falls to LLM, but must NOT be cached/RAG-saved.
        q = "Do you have the right to work in Elbonia?"
        with patch.object(ai, "_call_llm", return_value="No"):
            self.assertEqual(ai.answer(q), "No")
        self.assertIsNone(ai._check_cache(q, None))
        self.assertIsNone(ai.rag.lookup(q))


class TestHandlerWiring(unittest.TestCase):
    def test_ats_answer_field_uses_job_country(self):
        from ats_handlers import get_handler
        cfg = {**INDIAN, "application": {"authorized_to_work": "Yes",
                                         "require_visa": "Yes"}}
        h = get_handler("greenhouse", None, cfg)
        # UK job: per-country No beats the global keyword "Yes".
        self.assertEqual(
            h.answer_field("Are you legally authorized to work?",
                           {"location": "London, UK"}), "No")
        # India job: Yes.
        self.assertEqual(
            h.answer_field("Are you legally authorized to work?",
                           {"location": "Gurugram, India"}), "Yes")

    def test_linkedin_find_answer_uses_work_auth(self):
        try:
            from linkedin import _find_answer
        except ImportError:
            self.skipTest("selenium/undetected_chromedriver not installed")
        wa = WorkAuthorization(INDIAN)
        app = {"authorized_to_work": "Yes", "require_visa": "Yes"}
        self.assertEqual(
            _find_answer("are you authorized to work in the uk?", {}, app, {},
                         work_auth=wa, location=""), "No")
        # Recognized but no country → blocks the wrong global fallback.
        self.assertEqual(
            _find_answer("are you legally authorized to work?", {}, app, {},
                         work_auth=wa, location=""), "")
        # Legacy behavior intact when work_auth absent.
        self.assertEqual(
            _find_answer("are you legally authorized to work?", {}, app, {}), "Yes")


if __name__ == "__main__":
    unittest.main()
