"""Tests for match_scorer.py — MatchScorer class."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from match_scorer import MatchScorer


class MockAI:
    """Mock AI that returns preset responses without calling any real LLM."""

    def __init__(self, response=""):
        self.enabled = True
        self.profile_context = "Experienced Python developer with 5 years."
        self.max_tokens = 200
        self._response = response

    def _call_llm(self, system_prompt, user_prompt):
        return self._response


class TestMatchScorerDisabled(unittest.TestCase):
    """Test scorer when disabled."""

    def test_disabled_scorer_returns_should_apply_true(self):
        scorer = MatchScorer(ai=None, cfg={"match_scoring": {"enabled": False}})
        self.assertTrue(scorer.should_apply(0))
        self.assertTrue(scorer.should_apply(100))
        self.assertTrue(scorer.should_apply(30))

    def test_disabled_scorer_score_job_returns_zero(self):
        scorer = MatchScorer(ai=None, cfg={"match_scoring": {"enabled": False}})
        result = scorer.score_job("SWE", "Google", "Build things")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["explanation"], "scoring disabled")

    def test_disabled_ai_returns_zero(self):
        mock_ai = MockAI()
        mock_ai.enabled = False
        scorer = MatchScorer(ai=mock_ai, cfg={"match_scoring": {"enabled": True}})
        result = scorer.score_job("SWE", "Google", "Build things")
        self.assertEqual(result["score"], 0)

    def test_disabled_scorer_returns_empty_lists(self):
        scorer = MatchScorer(ai=None, cfg={"match_scoring": {"enabled": False}})
        result = scorer.score_job("SWE", "Google", "Build things")
        self.assertEqual(result["skill_matches"], [])
        self.assertEqual(result["missing_skills"], [])


class TestParseScore(unittest.TestCase):
    """Test _parse_score with various inputs."""

    def setUp(self):
        self.scorer = MatchScorer(ai=None, cfg={"match_scoring": {"enabled": True}})

    def test_valid_json(self):
        raw = '{"score": 85, "skill_matches": ["python", "go"], "missing_skills": ["rust"], "explanation": "strong fit"}'
        result = self.scorer._parse_score(raw)
        self.assertEqual(result["score"], 85)
        self.assertEqual(result["skill_matches"], ["python", "go"])
        self.assertEqual(result["missing_skills"], ["rust"])
        self.assertEqual(result["explanation"], "strong fit")

    def test_json_with_markdown_wrapping(self):
        raw = '```json\n{"score": 72, "skill_matches": [], "missing_skills": [], "explanation": "ok"}\n```'
        result = self.scorer._parse_score(raw)
        self.assertEqual(result["score"], 72)

    def test_malformed_json_fallback_to_number(self):
        raw = "I would rate this candidate a 78 out of 100 for this role."
        result = self.scorer._parse_score(raw)
        self.assertEqual(result["score"], 78)

    def test_no_numbers_returns_50(self):
        raw = "Unable to determine a score for this candidate."
        result = self.scorer._parse_score(raw)
        self.assertEqual(result["score"], 50)
        self.assertIn("could not parse", result["explanation"])

    def test_score_clamped_to_100(self):
        raw = '{"score": 150, "skill_matches": [], "missing_skills": [], "explanation": "overflow"}'
        result = self.scorer._parse_score(raw)
        self.assertEqual(result["score"], 100)

    def test_score_clamped_to_0(self):
        raw = '{"score": -10, "skill_matches": [], "missing_skills": [], "explanation": "underflow"}'
        result = self.scorer._parse_score(raw)
        self.assertEqual(result["score"], 0)

    def test_fallback_ignores_numbers_over_100(self):
        raw = "The job requires 5000 hours of experience. Score: 65"
        result = self.scorer._parse_score(raw)
        # 5000 is > 100, so it should pick 65
        self.assertEqual(result["score"], 65)

    def test_json_missing_optional_fields(self):
        raw = '{"score": 60}'
        result = self.scorer._parse_score(raw)
        self.assertEqual(result["score"], 60)
        self.assertEqual(result["skill_matches"], [])
        self.assertEqual(result["missing_skills"], [])


class TestShouldApply(unittest.TestCase):
    """Test should_apply threshold logic."""

    def test_enabled_above_threshold(self):
        scorer = MatchScorer(ai=None, cfg={"match_scoring": {"enabled": True, "minimum_score": 70}})
        self.assertTrue(scorer.should_apply(70))
        self.assertTrue(scorer.should_apply(100))

    def test_enabled_below_threshold(self):
        scorer = MatchScorer(ai=None, cfg={"match_scoring": {"enabled": True, "minimum_score": 70}})
        self.assertFalse(scorer.should_apply(69))
        self.assertFalse(scorer.should_apply(0))

    def test_disabled_always_true(self):
        scorer = MatchScorer(ai=None, cfg={"match_scoring": {"enabled": False, "minimum_score": 70}})
        self.assertTrue(scorer.should_apply(0))
        self.assertTrue(scorer.should_apply(50))

    def test_exact_threshold(self):
        scorer = MatchScorer(ai=None, cfg={"match_scoring": {"enabled": True, "minimum_score": 50}})
        self.assertTrue(scorer.should_apply(50))
        self.assertFalse(scorer.should_apply(49))

    def test_custom_threshold(self):
        scorer = MatchScorer(ai=None, cfg={"match_scoring": {"enabled": True, "minimum_score": 90}})
        self.assertFalse(scorer.should_apply(89))
        self.assertTrue(scorer.should_apply(90))


class TestScoreJobWithMockAI(unittest.TestCase):
    """Test score_job with mock AI returning preset strings."""

    def test_score_job_with_valid_response(self):
        mock_ai = MockAI('{"score": 82, "skill_matches": ["python"], "missing_skills": [], "explanation": "good"}')
        scorer = MatchScorer(ai=mock_ai, cfg={"match_scoring": {"enabled": True}})
        result = scorer.score_job("SWE", "Google", "Build Python services")
        self.assertEqual(result["score"], 82)

    def test_score_job_empty_description(self):
        mock_ai = MockAI()
        scorer = MatchScorer(ai=mock_ai, cfg={"match_scoring": {"enabled": True}})
        result = scorer.score_job("SWE", "Google", "")
        self.assertEqual(result["score"], 50)
        self.assertIn("no description", result["explanation"])

    def test_score_job_no_ai(self):
        scorer = MatchScorer(ai=None, cfg={"match_scoring": {"enabled": True}})
        result = scorer.score_job("SWE", "Google", "Build things")
        self.assertEqual(result["score"], 0)

    def test_score_job_ai_returns_none(self):
        mock_ai = MockAI(None)
        scorer = MatchScorer(ai=mock_ai, cfg={"match_scoring": {"enabled": True}})
        result = scorer.score_job("SWE", "Google", "Build things")
        # Should return fallback score
        self.assertEqual(result["score"], 50)

    def test_score_job_ai_returns_plain_text(self):
        mock_ai = MockAI("This is a 75 percent match for the candidate.")
        scorer = MatchScorer(ai=mock_ai, cfg={"match_scoring": {"enabled": True}})
        result = scorer.score_job("SWE", "Google", "Build Python services")
        self.assertEqual(result["score"], 75)


if __name__ == "__main__":
    unittest.main()
