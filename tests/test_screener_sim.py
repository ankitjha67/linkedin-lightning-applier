"""
Tests for the employer-side screener simulator (screener_sim) and GitHub
signal enrichment (github_enrich). Pure logic — AI mocked, no network.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import github_enrich as ge
import screener_sim as ss


class TestPickRubric(unittest.TestCase):
    def test_engineering_jd(self):
        self.assertEqual(ss.pick_rubric("Senior Python Backend Engineer"), "engineering")

    def test_professional_jd(self):
        self.assertEqual(ss.pick_rubric("Credit Risk Manager, Basel III IRB"), "professional")

    def test_empty(self):
        self.assertEqual(ss.pick_rubric(""), "professional")


class TestLintResume(unittest.TestCase):
    GOOD = (
        "Ada Lovelace  ada@x.com  +44 7700 900000\n"
        "https://linkedin.com/in/ada  https://github.com/ada\n"
        "Experience\n"
        "- Cut RWA compute time 40% across 12 portfolios (Python)\n"
        "- Led Basel III programme covering $2.1bn book\n"
        "- Delivered 3 regulatory submissions with 100% on-time record\n"
    ) + "word " * 150

    def test_clean_resume_few_issues(self):
        out = ss.lint_resume(self.GOOD)
        self.assertEqual(out["issues"], [])
        self.assertGreaterEqual(out["stats"]["urls"], 2)

    def test_no_links_flagged(self):
        out = ss.lint_resume("Ada ada@x.com\n- Did things\n" + "w " * 200)
        self.assertTrue(any("no links" in i for i in out["issues"]))

    def test_unquantified_bullets_flagged(self):
        text = ("ada@x.com https://linkedin.com/in/ada\n"
                "- Responsible for reporting\n- Managed stakeholders\n"
                "- Improved processes\n- Worked on models\n") + "w " * 200
        out = ss.lint_resume(text)
        self.assertTrue(any("quantified" in i for i in out["issues"]))

    def test_generic_project_name_flagged(self):
        out = ss.lint_resume(self.GOOD + "\nProjects: Weather App in React")
        self.assertTrue(any("weather app" in i for i in out["issues"]))

    def test_missing_email_and_short(self):
        out = ss.lint_resume("just a few words https://github.com/x")
        self.assertTrue(any("email" in i for i in out["issues"]))
        self.assertTrue(any("short" in i for i in out["issues"]))


class TestComputeTotal(unittest.TestCase):
    def test_ledger_math(self):
        ev = {"scores": {"relevant_experience": {"score": 30, "evidence": "e"},
                         "domain_expertise": {"score": 25, "evidence": "e"},
                         "impact_and_outcomes": {"score": 20, "evidence": "e"},
                         "skills_and_tools": {"score": 8, "evidence": "e"}},
              "bonus_points": {"total": 5}, "deductions": {"total": 3}}
        t = ss.compute_total(ev, "professional")
        self.assertEqual(t["category_total"], 83)
        self.assertEqual(t["final"], 85)
        self.assertEqual(t["max_possible"], 120)

    def test_clamps_over_cap_scores(self):
        ev = {"scores": {"open_source": {"score": 99}},  # cap is 35
              "bonus_points": {"total": 50},             # cap is 20
              "deductions": {"total": -5}}               # negative → 0
        t = ss.compute_total(ev, "engineering")
        self.assertEqual(t["categories"]["open_source"]["score"], 35)
        self.assertEqual(t["bonus"], 20)
        self.assertEqual(t["deductions"], 0)

    def test_garbage_input_safe(self):
        t = ss.compute_total({"scores": {"production": {"score": "lots"}}}, "engineering")
        self.assertEqual(t["categories"]["production"]["score"], 0)
        t2 = ss.compute_total(None, "professional")
        self.assertEqual(t2["final"], 0)

    def test_final_clamped_to_bounds(self):
        ev = {"scores": {}, "bonus_points": {"total": 0}, "deductions": {"total": 999}}
        self.assertEqual(ss.compute_total(ev)["final"], ss.MIN_FINAL)


class TestExtractJson(unittest.TestCase):
    def test_fenced(self):
        self.assertEqual(ss._extract_json('x ```json\n{"a": 1}\n``` y'), {"a": 1})

    def test_bare_and_invalid(self):
        self.assertEqual(ss._extract_json('noise {"a": 2} tail'), {"a": 2})
        self.assertIsNone(ss._extract_json("no json here"))
        self.assertIsNone(ss._extract_json(""))


class TestSimulate(unittest.TestCase):
    def test_without_ai_lint_only(self):
        sim = ss.ScreenerSimulator(None, {})
        res = sim.simulate("ada@x.com resume text " * 30, "Credit Risk Manager")
        self.assertEqual(res["rubric"], "professional")
        self.assertFalse(res["ai_used"])
        self.assertIsNone(res["total"])
        self.assertIn("issues", res["lint"])

    def test_with_ai_full_evaluation(self):
        ai = MagicMock()
        ai.enabled = True
        ai.generate.return_value = (
            '{"scores": {"relevant_experience": {"score": 30, "evidence": "e"},'
            '"domain_expertise": {"score": 28, "evidence": "e"},'
            '"impact_and_outcomes": {"score": 20, "evidence": "e"},'
            '"skills_and_tools": {"score": 9, "evidence": "e"}},'
            '"bonus_points": {"total": 4, "breakdown": "b"},'
            '"deductions": {"total": 2, "reasons": "r"},'
            '"key_strengths": ["s1"], "areas_for_improvement": ["a1"]}')
        sim = ss.ScreenerSimulator(ai, {"screener": {"pass_score": 65}})
        res = sim.simulate("resume", "Credit Risk Manager Basel III")
        self.assertTrue(res["ai_used"])
        self.assertEqual(res["total"]["final"], 89)
        self.assertTrue(res["passed"])

    def test_ai_garbage_falls_back(self):
        ai = MagicMock()
        ai.enabled = True
        ai.generate.return_value = "I cannot answer that."
        res = ss.ScreenerSimulator(ai, {}).simulate("r", "jd")
        self.assertFalse(res["ai_used"])
        self.assertIsNone(res["total"])


class TestGate(unittest.TestCase):
    """The shared pre-submit gate used by lla docs, lla apply, and the loop."""

    LONG_JD = "Credit Risk Manager Basel III IRB PD LGD EAD RWA regulatory. " * 5

    def _ai(self, final_score: int):
        ai = MagicMock()
        ai.enabled = True
        ai.generate.return_value = (
            '{"scores": {"relevant_experience": {"score": %d, "evidence": "e"},'
            '"domain_expertise": {"score": 0, "evidence": "e"},'
            '"impact_and_outcomes": {"score": 0, "evidence": "e"},'
            '"skills_and_tools": {"score": 0, "evidence": "e"}},'
            '"bonus_points": {"total": 0, "breakdown": "b"},'
            '"deductions": {"total": 0, "reasons": "r"},'
            '"key_strengths": ["s"], "areas_for_improvement": ["a"]}' % final_score)
        return ai

    def test_gate_off(self):
        sim = ss.ScreenerSimulator(self._ai(0), {"screener": {"gate": "off"}})
        self.assertEqual(sim.gate("cv", self.LONG_JD)["action"], "pass")

    def test_short_jd_skips(self):
        sim = ss.ScreenerSimulator(self._ai(0), {"screener": {"gate": "block"}})
        g = sim.gate("cv", "one-liner")
        self.assertEqual(g["action"], "skip")   # never blocks on unscoreable input

    def test_no_ai_skips(self):
        sim = ss.ScreenerSimulator(None, {"screener": {"gate": "block"}})
        self.assertEqual(sim.gate("cv", self.LONG_JD)["action"], "skip")

    def test_pass_at_threshold(self):
        sim = ss.ScreenerSimulator(self._ai(30), {"screener": {"pass_score": 30}})
        g = sim.gate("cv", self.LONG_JD)
        self.assertEqual(g["action"], "pass")
        self.assertEqual(g["final"], 30)

    def test_warn_mode_below_threshold(self):
        sim = ss.ScreenerSimulator(self._ai(10),
                                   {"screener": {"pass_score": 65, "gate": "warn"}})
        self.assertEqual(sim.gate("cv", self.LONG_JD)["action"], "warn")

    def test_block_mode_below_threshold(self):
        sim = ss.ScreenerSimulator(self._ai(10),
                                   {"screener": {"pass_score": 65, "gate": "block"}})
        g = sim.gate("cv", self.LONG_JD)
        self.assertEqual(g["action"], "block")
        self.assertIn("10 < 65", g["reason"])

    def test_garbage_ai_skips_not_blocks(self):
        ai = MagicMock()
        ai.enabled = True
        ai.generate.return_value = "not json"
        sim = ss.ScreenerSimulator(ai, {"screener": {"gate": "block"}})
        self.assertEqual(sim.gate("cv", self.LONG_JD)["action"], "skip")


class TestGitHubEnrich(unittest.TestCase):
    def _repo(self, **kw):
        base = {"name": "x", "html_url": "https://github.com/u/x", "fork": False,
                "stargazers_count": 0, "forks_count": 0, "description": "",
                "language": "", "topics": [], "pushed_at": ""}
        base.update(kw)
        return base

    def test_extract_username(self):
        self.assertEqual(ge.extract_username("https://github.com/ada-l/"), "ada-l")
        self.assertEqual(ge.extract_username("ada-l"), "ada-l")
        self.assertEqual(ge.extract_username("https://gitlab.com/x"), "")

    def test_classify(self):
        self.assertEqual(ge.classify_repo(self._repo(fork=True)), "fork")
        self.assertEqual(ge.classify_repo(self._repo(stargazers_count=50)), "open_source")
        self.assertEqual(ge.classify_repo(self._repo(stargazers_count=2)), "self_project")

    def test_rank_prefers_jd_relevant_starred(self):
        repos = [
            self._repo(name="risk-models", language="Python",
                       stargazers_count=40, description="IRB PD models"),
            self._repo(name="dotfiles", stargazers_count=1),
            self._repo(name="old-fork", fork=True, stargazers_count=500),
        ]
        ranked = ge.rank_projects(repos, "Python credit risk modelling", top=7)
        self.assertEqual(ranked[0]["name"], "risk-models")
        self.assertTrue(all(p["name"] != "old-fork" for p in ranked))  # forks score 0

    def test_signal_summary_warns_all_self(self):
        sig = ge.github_signal_summary([self._repo(), self._repo(name="y")])
        self.assertEqual(sig["open_source"], 0)
        self.assertTrue(any("cap at ~10/35" in w for w in sig["warnings"]))

    def test_signal_summary_empty(self):
        sig = ge.github_signal_summary([])
        self.assertTrue(any("no public repos" in w for w in sig["warnings"]))

    def test_fetch_repos_bad_user_soft(self):
        self.assertEqual(ge.fetch_repos(""), [])


if __name__ == "__main__":
    unittest.main()
