"""
Tests for the professional application-document pipeline:
latex_docs, ats_pdf_check, relevance_cutter, doc_reviewer.

All pure-logic — no LaTeX/pdf binaries and no live LLM (AI is mocked). The
binary-dependent paths (compile, pdf extraction) are asserted to degrade
gracefully when the tools are absent.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ats_pdf_check as ats
import latex_docs
import relevance_cutter as rc
from doc_reviewer import DocumentReviewer


class TestLatexDocs(unittest.TestCase):
    def test_escape_latex(self):
        self.assertEqual(latex_docs.escape_latex("A & B 50% #1 $x_y"),
                         r"A \& B 50\% \#1 \$x\_y")
        self.assertEqual(latex_docs.escape_latex(None), "")

    def test_render_cv_contains_core_parts(self):
        tex = latex_docs.render_cv_tex({
            "first": "Ada", "last": "Lovelace", "email": "ada@x.com",
            "title": "Risk Manager",
            "profile": "Credit risk specialist with 7+ years.",
            "competencies": ["Basel III & IRB", "Python, SQL"],
            "experience": [{"years": "2021--Now", "title": "Manager",
                            "company": "EY", "location": "London",
                            "bullets": ["Led RWA transformation"]}],
            "skills": [{"category": "Tech", "items": "Python, SQL, R"}],
        })
        self.assertIn(r"\documentclass[11pt,a4paper,sans]{moderncv}", tex)
        self.assertIn(r"\name{Ada}{Lovelace}", tex)
        self.assertIn(r"\section{Core Competencies}", tex)
        self.assertIn(r"\cventry{2021--Now}{Manager}{EY}{London}", tex)
        self.assertIn(r"Basel III \& IRB", tex)          # escaped ampersand
        self.assertTrue(tex.strip().endswith(r"\end{document}"))

    def test_render_cover_letter(self):
        tex = latex_docs.render_cover_tex({
            "first": "Ada", "last": "Lovelace", "email": "ada@x.com",
            "company": "Monzo", "opening": "Dear Hiring Team,",
            "paragraphs": ["I am excited to apply.", "My background fits."],
        })
        self.assertIn(r"\recipient{Monzo}{}", tex)
        self.assertIn(r"\opening{Dear Hiring Team,}", tex)
        self.assertIn(r"\makelettertitle", tex)
        self.assertIn("I am excited to apply.", tex)

    def test_compile_degrades_without_engine(self):
        # No LaTeX engine in CI → available_engine None, compile returns None,
        # but the .tex is still written.
        with tempfile.TemporaryDirectory() as d:
            cfg = {"personal": {"first_name": "Ada", "last_name": "Lovelace",
                                "email": "ada@x.com"},
                   "latex_docs": {"output_dir": d}}
            b = latex_docs.LaTeXDocsBuilder(cfg)
            res = b.build_cv({"profile": "x", "competencies": ["Python"]})
            self.assertTrue(os.path.exists(res["tex"]))
            if latex_docs.available_engine() is None:
                self.assertIsNone(res["pdf"])


class TestATSCheck(unittest.TestCase):
    JD = ("We seek a Credit Risk Manager with Basel III, IRB, PD LGD EAD modelling, "
          "Python and SQL. Regulatory reporting and RWA experience required.")

    def test_extract_keywords(self):
        kws = ats.extract_keywords(self.JD)
        self.assertIn("basel", kws)
        self.assertIn("python", kws)
        self.assertNotIn("with", kws)   # stopword

    def test_keyword_coverage(self):
        cv = "Credit risk manager. Basel III, IRB, PD LGD EAD models in Python."
        cov = ats.keyword_coverage(cv, self.JD)
        self.assertIn("basel", cov["present"])
        self.assertIn("sql", cov["missing"])       # not in the CV
        self.assertGreater(cov["coverage_pct"], 0)
        self.assertLessEqual(cov["coverage_pct"], 100)

    def test_parseability_good(self):
        text = "Ada Lovelace\nada@x.com  +44 7700 900000\nLondon\n\nExperience..."
        r = ats.check_parseability(text, {"email": "ada@x.com"})
        self.assertTrue(r["ok"])
        self.assertTrue(r["email_found"] and r["phone_found"] and r["contact_in_top"])

    def test_parseability_flags_missing_and_garble(self):
        r = ats.check_parseability("Some body text with a � glyph and no contacts.")
        self.assertFalse(r["ok"])
        self.assertTrue(any("email" in i for i in r["issues"]))
        self.assertEqual(r["garble_count"], 1)

    def test_parseability_empty(self):
        r = ats.check_parseability("")
        self.assertFalse(r["ok"])

    def test_ats_report_pass_fail(self):
        good_cv = ("Ada Lovelace ada@x.com +44 7700 900000 London. Credit risk "
                   "manager: Basel III, IRB, PD LGD EAD, RWA, regulatory reporting, "
                   "Python, SQL modelling.")
        rep = ats.ats_report(good_cv, self.JD, {"email": "ada@x.com"}, min_coverage=40)
        self.assertTrue(rep["passed"])
        weak = ats.ats_report("Hi I like jobs.", self.JD, min_coverage=60)
        self.assertFalse(weak["passed"])

    def test_extract_pdf_text_missing_tools(self):
        # No pdftotext/pdfminer in CI → None, not a crash.
        self.assertIsNone(ats.extract_pdf_text("/nonexistent/file.pdf"))


class TestRelevanceCutter(unittest.TestCase):
    JD = "Credit risk Basel III IRB Python SQL RWA regulatory capital modelling"

    def test_no_trim_when_under_budget(self):
        lines = ["Basel III credit risk", "Python SQL models"]
        out = rc.trim_to(lines, 5, self.JD)
        self.assertEqual(out["kept"], lines)
        self.assertEqual(out["dropped"], [])

    def test_trims_lowest_relevance_first(self):
        lines = [
            "Led Basel III IRB credit risk RWA capital modelling programme",  # high relevance
            "Enjoy hiking and photography on weekends",                       # irrelevant
            "Built Python SQL regulatory reporting pipelines",                # high relevance
        ]
        out = rc.trim_to(lines, 2, self.JD)
        self.assertEqual(len(out["kept"]), 2)
        self.assertIn("Enjoy hiking and photography on weekends", out["dropped"])

    def test_preserves_original_order(self):
        lines = ["Basel III risk", "Python SQL", "capital modelling"]
        out = rc.trim_to(lines, 2, self.JD)
        # kept lines appear in their original relative order
        idxs = [lines.index(x) for x in out["kept"]]
        self.assertEqual(idxs, sorted(idxs))

    def test_cover_dependency_boost(self):
        jd = "generic role"
        cover = "In my cover letter I highlight my Kaggle competition wins."
        lines = ["Kaggle competition grandmaster winner", "Made coffee daily"]
        out = rc.trim_to(lines, 1, jd, cover)
        self.assertIn("Kaggle competition grandmaster winner", out["kept"])


class TestDocReviewer(unittest.TestCase):
    def _ai(self, reply):
        ai = MagicMock()
        ai.enabled = True
        ai.generate.return_value = reply
        return ai

    def test_review_returns_critique(self):
        r = DocumentReviewer(self._ai("- Add SQL\n- Too generic"))
        out = r.review("draft cv", "CV", "Risk Mgr", "Monzo", "needs SQL")
        self.assertIn("Add SQL", out)

    def test_revise_applies_and_falls_back(self):
        r = DocumentReviewer(self._ai("REVISED TEXT"))
        self.assertEqual(r.revise("old", "fix it", "CV"), "REVISED TEXT")
        # empty critique → unchanged
        self.assertEqual(r.revise("old", "", "CV"), "old")

    def test_honesty_none(self):
        r = DocumentReviewer(self._ai("NONE"))
        self.assertEqual(r.check_honesty("doc", "profile"), [])

    def test_honesty_flags(self):
        r = DocumentReviewer(self._ai("- Claims PhD not in profile\n- 10y exp overstated"))
        flags = r.check_honesty("doc", "profile")
        self.assertEqual(len(flags), 2)
        self.assertIn("Claims PhD not in profile", flags)

    def test_degrades_without_ai(self):
        r = DocumentReviewer(None)
        self.assertEqual(r.review("d", "CV", "t", "c", "jd"), "")
        self.assertEqual(r.revise("d", "crit", "CV"), "d")
        self.assertEqual(r.check_honesty("d", "p"), [])

    def test_full_loop(self):
        ai = MagicMock()
        ai.enabled = True
        ai.generate.side_effect = ["- fix framing", "REVISED", "NONE"]
        r = DocumentReviewer(ai)
        out = r.draft_review_revise("draft", "CV", "Risk Mgr", "Monzo", "jd", "profile")
        self.assertEqual(out["revised"], "REVISED")
        self.assertEqual(out["critique"], "- fix framing")
        self.assertEqual(out["honesty_flags"], [])


if __name__ == "__main__":
    unittest.main()
