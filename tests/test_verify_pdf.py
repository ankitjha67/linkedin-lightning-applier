"""Rendered-layout verification — tools/verify_pdf.py.

`lla docs` checks a CV's text. These checks are about the page: a job title
stranded at the foot of one page with its achievements overleaf, a third page
carrying two lines, text pushed outside the margins. LaTeX reports none of it
— it typesets what it was told and moves on.

Where fpdf2 is installed the geometric tests build real PDFs and read them
back, so they exercise the actual reader rather than a mock of it.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import verify_pdf as vp  # noqa: E402

try:
    import pypdf  # noqa: F401
    from fpdf import FPDF
    CAN_RENDER = True
except Exception:
    CAN_RENDER = False


def page(number, lines, height=842.0, width=595.0, ys=None):
    """A synthetic page; ys gives each line's height from the page foot."""
    if ys is None:
        ys = [height - 60 - 14 * i for i in range(len(lines))]
    return vp.Page(number, width, height,
                   [(60.0, y, t) for y, t in zip(ys, lines)])


class TestLineRecognition(unittest.TestCase):
    def test_an_entry_heading_needs_a_date_and_a_name(self):
        self.assertTrue(vp.looks_like_entry_heading(
            "2021 - 2024   Senior Risk Manager   Monzo   London"))
        self.assertTrue(vp.looks_like_entry_heading(
            "2018–2021 Analyst Barclays London"))

    def test_present_counts_as_a_date(self):
        self.assertTrue(vp.looks_like_entry_heading(
            "2021 - Present   Lead Engineer   Wise   London"))

    def test_a_bare_date_is_not_an_entry(self):
        self.assertFalse(vp.looks_like_entry_heading("2021 - 2024"))

    def test_prose_is_not_an_entry(self):
        self.assertFalse(vp.looks_like_entry_heading(
            "Led the migration and reduced latency"))

    def test_a_bullet_is_never_an_entry_heading(self):
        self.assertFalse(vp.looks_like_entry_heading(
            "- Delivered the 2021 platform rewrite for Monzo Bank"))

    def test_section_headings_are_recognised(self):
        for h in ("Experience", "EDUCATION", "Technical Skills", "Publications"):
            self.assertTrue(vp.looks_like_section_heading(h), h)

    def test_ordinary_lines_are_not_section_headings(self):
        self.assertFalse(vp.looks_like_section_heading("Experience with Python"))
        self.assertFalse(vp.looks_like_section_heading(""))

    def test_bullets_are_continuations(self):
        for b in ("- Built it", "• Built it", "* Built it"):
            self.assertTrue(vp.looks_like_continuation(b), b)

    def test_a_new_entry_is_not_a_continuation(self):
        self.assertFalse(vp.looks_like_continuation(
            "2018 - 2021   Analyst   Barclays   London"))


class TestOrphanDetection(unittest.TestCase):
    """The defect that most changes how a CV reads."""

    def test_an_entry_at_the_page_foot_with_detail_overleaf_is_flagged(self):
        pages = [
            page(1, ["Ada Lovelace", "2021 - 2024 Senior Risk Manager Monzo London"],
                 ys=[780.0, 60.0]),
            page(2, ["- Built the risk engine", "- Led a team of six"]),
        ]
        kinds = [i.kind for i in vp.check_orphan_headings(pages)]
        self.assertIn("orphan_entry", kinds)

    def test_the_suggested_fix_names_needspace(self):
        pages = [
            page(1, ["Ada", "2021 - 2024 Senior Risk Manager Monzo London"],
                 ys=[780.0, 60.0]),
            page(2, ["- Built the risk engine"]),
        ]
        issue = vp.check_orphan_headings(pages)[0]
        self.assertIn("needspace", issue.fix)

    def test_an_entry_that_is_not_at_the_foot_is_fine(self):
        # Same heading, but halfway up the page — nothing is stranded.
        pages = [
            page(1, ["Ada", "2021 - 2024 Senior Risk Manager Monzo London"],
                 ys=[780.0, 500.0]),
            page(2, ["- Built the risk engine"]),
        ]
        self.assertEqual(vp.check_orphan_headings(pages), [])

    def test_a_section_heading_at_the_foot_is_flagged(self):
        pages = [page(1, ["Ada Lovelace", "Experience"], ys=[780.0, 55.0]),
                 page(2, ["2021 - 2024 Analyst Monzo London"])]
        kinds = [i.kind for i in vp.check_orphan_headings(pages)]
        self.assertIn("orphan_section", kinds)

    def test_a_page_ending_mid_prose_is_not_an_orphan(self):
        pages = [page(1, ["Ada", "delivered the migration on time"], ys=[780.0, 55.0]),
                 page(2, ["and reduced latency by half"])]
        self.assertEqual(vp.check_orphan_headings(pages), [])

    def test_an_entry_at_the_foot_of_the_last_page_is_not_an_orphan(self):
        # Nothing follows it, so nothing was stranded.
        pages = [page(1, ["2021 - 2024 Senior Risk Manager Monzo London"], ys=[55.0])]
        self.assertEqual(vp.check_orphan_headings(pages), [])

    def test_a_following_entry_means_the_first_one_ended(self):
        pages = [page(1, ["Ada", "2021 - 2024 Senior Risk Manager Monzo London"],
                      ys=[780.0, 55.0]),
                 page(2, ["2018 - 2021 Analyst Barclays London"])]
        self.assertEqual(vp.check_orphan_headings(pages), [])


class TestPageChecks(unittest.TestCase):
    def test_too_many_pages_is_an_error(self):
        issues = vp.check_page_count([page(i, ["x"]) for i in range(1, 4)], 2)
        self.assertEqual(issues[0].kind, "page_count")

    def test_the_expected_count_passes(self):
        self.assertEqual(vp.check_page_count([page(1, ["x"]), page(2, ["y"])], 2), [])

    def test_fewer_pages_than_expected_is_fine(self):
        self.assertEqual(vp.check_page_count([page(1, ["x"])], 2), [])

    def test_no_pages_at_all_is_an_error(self):
        self.assertEqual(vp.check_page_count([], 2)[0].kind, "empty")

    def test_a_blank_page_is_reported(self):
        issues = vp.check_empty_pages([page(1, ["x"]), vp.Page(2, 595, 842, [])])
        self.assertEqual(issues[0].page, 2)

    def test_a_final_page_with_two_lines_is_a_widow(self):
        issues = vp.check_short_last_page([page(1, ["a"] * 30), page(2, ["b", "c"])])
        self.assertEqual(issues[0].kind, "widow_page")
        self.assertEqual(issues[0].severity, "warning")

    def test_a_full_final_page_is_not_a_widow(self):
        self.assertEqual(
            vp.check_short_last_page([page(1, ["a"] * 30), page(2, ["b"] * 20)]), [])

    def test_a_single_page_document_is_never_a_widow(self):
        self.assertEqual(vp.check_short_last_page([page(1, ["only line"])]), [])


class TestMargins(unittest.TestCase):
    def test_text_past_the_right_margin_is_flagged(self):
        p = vp.Page(1, 595.0, 842.0, [(570.0, 700.0, "runs off the edge")])
        self.assertEqual(vp.check_margins([p])[0].kind, "margin")

    def test_text_before_the_left_margin_is_flagged(self):
        p = vp.Page(1, 595.0, 842.0, [(5.0, 700.0, "too far left")])
        self.assertTrue(vp.check_margins([p]))

    def test_text_below_the_bottom_margin_is_flagged(self):
        p = vp.Page(1, 595.0, 842.0, [(60.0, 10.0, "below the page")])
        self.assertTrue(vp.check_margins([p]))

    def test_text_inside_the_margins_is_fine(self):
        p = vp.Page(1, 595.0, 842.0, [(60.0, 700.0, "normal line")])
        self.assertEqual(vp.check_margins([p]), [])

    def test_only_one_example_per_page_is_reported(self):
        p = vp.Page(1, 595.0, 842.0,
                    [(570.0, 700.0, "a"), (575.0, 680.0, "b"), (580.0, 660.0, "c")])
        self.assertEqual(len(vp.check_margins([p])), 1)

    def test_margins_are_skipped_without_coordinates(self):
        # pdftotext gives no positions; the check must not invent them.
        p = vp.Page(1, 0.0, 0.0, [(None, None, "some text")])
        self.assertEqual(vp.check_margins([p]), [])


class TestExpectedText(unittest.TestCase):
    def test_missing_text_is_reported(self):
        pages = [page(1, ["Ada Lovelace"])]
        issues = vp.check_expected_text(pages, ["ada@example.com"])
        self.assertEqual(issues[0].kind, "missing_text")

    def test_present_text_passes(self):
        pages = [page(1, ["Ada Lovelace", "ada@example.com"])]
        self.assertEqual(vp.check_expected_text(pages, ["Ada Lovelace"]), [])

    def test_nothing_expected_checks_nothing(self):
        self.assertEqual(vp.check_expected_text([page(1, ["x"])], None), [])


class TestNeedspaceFix(unittest.TestCase):
    TEX = ("\\documentclass[11pt]{moderncv}\n"
           "\\begin{document}\n"
           "\\section{Experience}\n"
           "\\cventry{2021--2024}{Senior Risk Manager}{Monzo}{London}{}{body}\n"
           "\\cventry{2018--2021}{Analyst}{Barclays}{London}{}{body}\n"
           "\\end{document}\n")

    def test_the_package_is_added_after_documentclass(self):
        out, _n = vp.add_needspace(self.TEX)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("\\documentclass"))
        self.assertEqual(lines[1], vp.NEEDSPACE_PKG)

    def test_every_entry_and_section_is_guarded(self):
        out, n = vp.add_needspace(self.TEX)
        self.assertEqual(n, 3)                   # two entries, one section
        self.assertEqual(out.count("\\needspace{4\\baselineskip}"), 2)
        self.assertEqual(out.count("\\needspace{5\\baselineskip}"), 1)

    def test_the_guard_goes_before_the_entry(self):
        out, _ = vp.add_needspace(self.TEX)
        lines = out.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("\\cventry"):
                self.assertIn("needspace", lines[i - 1])

    def test_running_it_twice_changes_nothing(self):
        once, n1 = vp.add_needspace(self.TEX)
        twice, n2 = vp.add_needspace(once)
        self.assertEqual(n2, 0)
        self.assertEqual(once, twice)

    def test_the_package_is_not_added_twice(self):
        out, _ = vp.add_needspace(vp.add_needspace(self.TEX)[0])
        self.assertEqual(out.count(vp.NEEDSPACE_PKG), 1)

    def test_the_document_body_is_otherwise_unchanged(self):
        out, _ = vp.add_needspace(self.TEX)
        for line in self.TEX.splitlines():
            self.assertIn(line, out)

    def test_empty_input_is_safe(self):
        self.assertEqual(vp.add_needspace(""), ("", 0))


@unittest.skipUnless(CAN_RENDER, "fpdf2/pypdf not installed")
class TestAgainstRealPdfs(unittest.TestCase):
    """Build actual PDFs and read them back through the real reader."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def _pdf(self, name, pages):
        """pages: [[(x_mm, y_mm, text), ...], ...]"""
        doc = FPDF(format="A4")
        doc.set_auto_page_break(False)
        for items in pages:
            doc.add_page()
            doc.set_font("helvetica", size=11)
            for x, y, text in items:
                doc.set_xy(x, y)
                doc.cell(0, 6, text)
        path = str(self.dir / name)
        doc.output(path)
        return path

    def test_a_rendered_pdf_reports_its_pages_and_positions(self):
        path = self._pdf("t.pdf", [[(20, 20, "Ada Lovelace")], [(20, 20, "More")]])
        pages, err = vp.read_pages(path)
        self.assertEqual(err, "")
        self.assertEqual(len(pages), 2)
        self.assertTrue(vp.has_positions(pages))
        self.assertAlmostEqual(pages[0].width, 595.28, places=1)

    def test_a_real_orphan_is_caught(self):
        path = self._pdf("orphan.pdf", [
            [(20, 20, "Ada Lovelace   ada@example.com"),
             (20, 272, "2021 - 2024   Senior Risk Manager   Monzo   London")],
            [(20, 20, "- Built the risk engine"), (20, 28, "- Led a team of six")],
        ])
        issues, pages, _ = vp.verify(path, expected_pages=2)
        self.assertEqual(len(pages), 2)
        self.assertIn("orphan_entry", [i.kind for i in issues])

    def test_the_same_content_laid_out_well_has_no_errors(self):
        path = self._pdf("clean.pdf", [
            [(20, 20, "Ada Lovelace   ada@example.com"),
             (20, 30, "Experience"),
             (20, 40, "2018 - 2021   Analyst   Barclays   London"),
             (20, 48, "- Modelled credit exposure")],
            [(20, 20, "2021 - 2024   Senior Risk Manager   Monzo   London"),
             (20, 28, "- Built the risk engine"),
             (20, 36, "- Led a team of six"),
             (20, 44, "- Cut incident response time"),
             (20, 52, "- Owned the regulatory reporting")],
        ])
        issues, _pages, _ = vp.verify(path, expected_pages=2)
        errors = [i for i in issues if i.severity == "error"]
        self.assertEqual(errors, [], f"clean CV reported: {errors}")

    def test_a_real_margin_overflow_is_caught(self):
        path = self._pdf("over.pdf", [
            [(20, 20, "Ada Lovelace"), (200, 40, "This runs off the right edge")]])
        issues, _pages, _ = vp.verify(path, expected_pages=2)
        self.assertIn("margin", [i.kind for i in issues])

    def test_an_extra_page_is_caught(self):
        path = self._pdf("three.pdf", [[(20, 20, "One")], [(20, 20, "Two")],
                                       [(20, 20, "References on request")]])
        issues, _pages, _ = vp.verify(path, expected_pages=2)
        self.assertIn("page_count", [i.kind for i in issues])

    def test_expected_text_is_checked_against_the_rendered_page(self):
        path = self._pdf("t.pdf", [[(20, 20, "Ada Lovelace")]])
        issues, _pages, _ = vp.verify(path, 2, expected_text=["ada@example.com"])
        self.assertIn("missing_text", [i.kind for i in issues])

    def test_an_unreadable_file_is_reported_not_raised(self):
        bad = self.dir / "not.pdf"
        bad.write_text("this is not a PDF", encoding="utf-8")
        issues, pages, err = vp.verify(str(bad))
        self.assertEqual(pages, [])
        self.assertTrue(err)
        self.assertEqual(issues[0].kind, "unreadable")


class TestReporting(unittest.TestCase):
    def test_a_sound_layout_says_so(self):
        self.assertIn("layout is sound", vp.format_issues([], [page(1, ["x"])], "cv.pdf"))

    def test_errors_and_warnings_are_both_shown(self):
        issues = [vp.Issue("a", "broken", page=1, fix="do this"),
                  vp.Issue("b", "minor", page=2, severity="warning")]
        out = vp.format_issues(issues, [page(1, ["x"])], "cv.pdf")
        self.assertIn("broken", out)
        self.assertIn("minor", out)
        self.assertIn("do this", out)
        self.assertIn("1 problem(s), 1 warning(s)", out)

    def test_a_text_only_read_says_the_checks_were_limited(self):
        p = vp.Page(1, 0.0, 0.0, [(None, None, "line")])
        self.assertIn("text only", vp.format_issues([], [p], "cv.pdf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
