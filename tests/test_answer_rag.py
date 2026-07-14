"""
Tests for the semantic answer memory (answer_rag), its wiring into
AIAnswerer.answer(), the any-job-board generic fallback, and the new LLM
providers. Pure logic — sqlite in temp files, LLM calls mocked.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from answer_rag import AnswerRAG, tokenize


def _db():
    path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    return sqlite3.connect(path), path


class TestTokenize(unittest.TestCase):
    def test_drops_stopwords_keeps_tech(self):
        toks = tokenize("How many years of experience do you have with C++ and node.js?")
        self.assertIn("c++", toks)
        self.assertIn("node.js", toks)
        self.assertIn("years", toks)
        self.assertNotIn("how", toks)
        self.assertNotIn("of", toks)


class TestAnswerRAG(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = _db()
        self.rag = AnswerRAG(self.conn, {"rag": {"reuse_threshold": 0.9}})

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_save_and_exactish_reuse(self):
        self.rag.save("How many years of Python experience do you have?", "7")
        hit = self.rag.lookup("Years of Python experience?")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["answer"], "7")
        self.assertGreaterEqual(hit["similarity"], 0.9)

    def test_different_subject_not_reused(self):
        self.rag.save("How many years of Python experience do you have?", "7")
        # Same shape, different discriminating word — must NOT reuse.
        self.assertIsNone(self.rag.lookup("How many years of Java experience do you have?"))

    def test_country_us_not_swallowed_as_stopword(self):
        # Regression: "us" (pronoun) must not eat "US" (country) — a UK visa
        # answer must never be reused for a US visa question.
        self.rag.save("Do you require visa sponsorship to work in the UK?", "No")
        self.assertIsNone(
            self.rag.lookup("Do you require visa sponsorship to work in the US?"))

    def test_options_compatibility(self):
        self.rag.save("Are you authorized to work in the UK?", "Yes")
        hit = self.rag.lookup("Are you authorized to work in the UK?",
                              options=["Yes", "No"])
        self.assertEqual(hit["answer"], "Yes")
        # Cached answer not among the options → no reuse.
        self.assertIsNone(self.rag.lookup("Are you authorized to work in the UK?",
                                          options=["Definitely", "Never"]))

    def test_option_fuzzy_fit(self):
        self.rag.save("Do you require visa sponsorship?", "No")
        hit = self.rag.lookup("Do you require visa sponsorship?",
                              options=["No, I do not require sponsorship", "Yes"])
        self.assertEqual(hit["answer"], "No, I do not require sponsorship")

    def test_context_retrieval_excludes_reusable(self):
        self.rag.save("How many years of Python experience do you have?", "7")
        self.rag.save("What is your expected salary?", "150000")
        ctx = self.rag.retrieve_context("How many years of SQL experience do you have?")
        # Python question is similar-but-not-reusable → appears as context.
        self.assertTrue(any("Python" in q for q, a, s in ctx))
        block = self.rag.context_block("How many years of SQL experience do you have?")
        self.assertIn("stay consistent", block)

    def test_no_duplicate_storage(self):
        self.assertTrue(self.rag.save("Notice period in days?", "30"))
        self.assertFalse(self.rag.save("Notice period in days?", "30"))
        self.assertEqual(self.rag.stats()["stored"], 1)

    def test_persistence_across_instances(self):
        self.rag.save("Willing to relocate to Singapore?", "Yes")
        rag2 = AnswerRAG(self.conn, {})
        hit = rag2.lookup("Willing to relocate to Singapore?")
        self.assertEqual(hit["answer"], "Yes")

    def test_reuse_counted(self):
        self.rag.save("Notice period in days?", "30")
        self.rag.lookup("Notice period in days?")
        s = self.rag.stats()
        self.assertEqual(s["session_reuses"], 1)
        self.assertEqual(s["total_reuses"], 1)

    def test_disabled_is_inert(self):
        conn, path = _db()
        try:
            rag = AnswerRAG(conn, {"rag": {"enabled": False}})
            self.assertFalse(rag.save("Q?", "A"))
            self.assertIsNone(rag.lookup("Q?"))
            self.assertEqual(rag.retrieve_context("Q?"), [])
        finally:
            conn.close()
            os.unlink(path)


class TestAIAnswererRAGWiring(unittest.TestCase):
    """answer(): exact cache → RAG reuse (no LLM) → LLM+context → RAG save."""

    def _ai(self):
        from ai import AIAnswerer
        conn, self.path = _db()
        return AIAnswerer({"ai": {"enabled": True, "provider": "ollama"},
                           "rag": {"reuse_threshold": 0.9}}, db_conn=conn)

    def test_rag_hit_skips_llm(self):
        ai = self._ai()
        self.assertIsNotNone(ai.rag)
        ai.rag.save("How many years of Python experience do you have?", "7")
        with patch.object(ai, "_call_llm") as mock_llm:
            out = ai.answer("Years of Python experience?")
        self.assertEqual(out, "7")
        mock_llm.assert_not_called()

    def test_llm_answer_saved_to_rag_with_context(self):
        ai = self._ai()
        ai.rag.save("How many years of Python experience do you have?", "7")
        with patch.object(ai, "_call_llm", return_value="5") as mock_llm:
            out = ai.answer("How many years of SQL experience do you have?")
        self.assertEqual(out, "5")
        # Similar (non-reusable) Q&A was injected as prompt context.
        user_prompt = mock_llm.call_args[0][1]
        self.assertIn("stay consistent", user_prompt)
        # And the fresh answer is now reusable.
        self.assertIsNotNone(ai.rag.lookup("Years of SQL experience?"))

    def test_works_without_db(self):
        from ai import AIAnswerer
        ai = AIAnswerer({"ai": {"enabled": True, "provider": "ollama"}})
        self.assertIsNone(ai.rag)
        with patch.object(ai, "_call_llm", return_value="ok"):
            self.assertEqual(ai.answer("Anything?"), "ok")


class TestNewProviders(unittest.TestCase):
    def test_provider_urls_and_models(self):
        from ai import DEFAULT_MODELS, PROVIDER_URLS
        for p in ("xai", "mistral", "custom"):
            self.assertIn(p, PROVIDER_URLS)
            self.assertIn(p, DEFAULT_MODELS)

    def test_env_keys(self):
        from ai import AIAnswerer
        with patch.dict(os.environ, {"XAI_API_KEY": "xk", "MISTRAL_API_KEY": "mk",
                                     "LLM_API_KEY": "ck"}):
            self.assertEqual(AIAnswerer._key_from_env("xai"), "xk")
            self.assertEqual(AIAnswerer._key_from_env("mistral"), "mk")
            self.assertEqual(AIAnswerer._key_from_env("custom"), "ck")

    def test_validator_accepts(self):
        from validate_config import VALID_AI_PROVIDERS
        for p in ("xai", "mistral", "custom"):
            self.assertIn(p, VALID_AI_PROVIDERS)

    def test_custom_local_no_key_no_warning_path(self):
        from ai import AIAnswerer
        ai = AIAnswerer({"ai": {"enabled": True, "provider": "custom",
                                "base_url": "http://localhost:8000/v1"}})
        self.assertEqual(ai.base_url, "http://localhost:8000/v1")
        self.assertEqual(ai.api_key, "")


class TestGenericFallback(unittest.TestCase):
    def test_generic_handler_has_account_support(self):
        from ats_handlers.generic import AccountMixin, GenericHandler
        self.assertTrue(issubclass(GenericHandler, AccountMixin))
        h = GenericHandler(None, {"external_apply": {"ats_accounts": {
            "generic": {"email": "a@b.com", "password": "pw"}}}, "personal": {}})
        self.assertEqual(h.account_key, "generic")
        email, pw = h._creds()
        self.assertEqual((email, pw), ("a@b.com", "pw"))

    def test_external_applier_falls_back(self):
        from external_apply import ExternalApplier
        ea = ExternalApplier(None, {"external_apply": {"enabled": True}})
        self.assertTrue(ea.allow_generic_fallback)
        # Unknown URL still yields None from detect (planning), but apply path
        # will route to generic — verified via the flag + handler registry.
        self.assertIsNone(ea.detect_ats("https://jobs.example-board.io/123"))
        from ats_handlers import get_handler
        self.assertIsNotNone(get_handler("generic", None, {}))

    def test_fallback_can_be_disabled(self):
        from external_apply import ExternalApplier
        ea = ExternalApplier(None, {"external_apply": {
            "enabled": True, "allow_generic_fallback": False}})
        self.assertFalse(ea.allow_generic_fallback)


if __name__ == "__main__":
    unittest.main()
