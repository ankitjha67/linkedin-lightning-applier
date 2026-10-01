"""Recording application outcomes — outcomes.py.

This is the table eight other modules learn from. The properties that matter:
an outcome is attached to the right application, dated when it actually
happened rather than when it was typed in, and a terminal outcome genuinely
closes the application.
"""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import outcomes as oc  # noqa: E402
from state import State  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"


def seeded_state(jobs):
    """jobs: [(job_id, title, company, days_ago)]"""
    tmp = tempfile.mkdtemp()
    state = State(db_path=str(Path(tmp) / "t.db"))
    now = datetime.now()
    for job_id, title, company, days_ago in jobs:
        state.conn.execute(
            "INSERT INTO applied_jobs (job_id,title,company,applied_at) "
            "VALUES (?,?,?,?)",
            (job_id, title, company, (now - timedelta(days=days_ago)).strftime(FMT)))
    state.conn.commit()
    return state


DEFAULT_JOBS = [
    ("j1", "Risk Manager", "Monzo", 60),
    ("j2", "Data Engineer", "Monzo", 50),
    ("j3", "Quant Analyst", "Revolut", 10),
    ("j4", "ML Engineer", "Wise", 3),
]


class TestVocabulary(unittest.TestCase):
    def test_aliases_resolve_to_canonical_keys(self):
        for typed, expected in [("rejected", "rejection"), ("Rejected", "rejection"),
                                ("take-home", "assessment"), ("onsite", "interview"),
                                ("hired", "offer"), ("ghost", "ghosted"),
                                ("phone", "callback")]:
            self.assertEqual(oc.normalise_type(typed), expected, typed)

    def test_canonical_keys_pass_through(self):
        for key in oc.ALL_TYPES:
            self.assertEqual(oc.normalise_type(key), key)

    def test_unknown_words_are_rejected(self):
        self.assertEqual(oc.normalise_type("banana"), "")
        self.assertEqual(oc.normalise_type(""), "")
        self.assertEqual(oc.normalise_type(None), "")

    def test_assessment_counts_as_engagement(self):
        # A take-home test means they engaged; it is not a neutral event.
        self.assertIn("assessment", oc.POSITIVE_TYPES)

    def test_terminal_types_end_an_application(self):
        self.assertEqual(set(oc.TERMINAL_TYPES),
                         {"offer", "rejection", "withdrawn", "ghosted"})

    def test_no_type_is_both_positive_and_a_rejection(self):
        for t in oc.OUTCOME_TYPES:
            if t.key in ("rejection", "ghosted", "withdrawn"):
                self.assertFalse(t.positive, t.key)


class TestSqlVocabularyStaysInSync(unittest.TestCase):
    """The positive-outcome set is written out by hand in ten SQL queries.

    `assessment` was missing from all of them, so a take-home test — one of
    the strongest signals an application produced — did not count as a
    response anywhere. This fails if that set drifts from outcomes.py again.
    """

    FILES = ["smart_scheduler.py", "follow_up_engine.py", "success_tracker.py"]

    def test_every_positive_response_query_lists_every_positive_type(self):
        import re
        root = Path(__file__).resolve().parent.parent
        pattern = re.compile(r"response_type\s+IN\s*\(([^)]*)\)", re.I)
        checked = 0
        for name in self.FILES:
            text = (root / name).read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                listed = {v.strip().strip("'\"") for v in match.group(1).split(",")}
                # Only the "did they engage?" queries — not rejection lookups.
                if "callback" not in listed:
                    continue
                checked += 1
                missing = set(oc.POSITIVE_TYPES) - listed
                self.assertFalse(
                    missing,
                    f"{name}: query omits {sorted(missing)} — "
                    f"those outcomes will not count as a response")
        self.assertGreater(checked, 0, "found no positive-response queries to check")

    def test_success_tracker_accepts_every_canonical_type(self):
        import success_tracker
        for key in oc.ALL_TYPES:
            self.assertIn(key, success_tracker.RESPONSE_TYPES,
                          f"success_tracker would coerce '{key}' to callback")

    def test_email_monitor_persists_the_assessment_class_it_detects(self):
        # It classifies take-homes; it must also store them.
        root = Path(__file__).resolve().parent.parent
        text = (root / "email_monitor.py").read_text(encoding="utf-8")
        line = [ln for ln in text.splitlines()
                if 'if job_id and response_type in' in ln]
        self.assertTrue(line)
        self.assertIn("assessment", line[0],
                      "email_monitor detects assessments but never records them")


class TestParseWhen(unittest.TestCase):
    def test_empty_is_none_not_now(self):
        # `parse_when(a) or parse_when(b)` fallbacks depend on this: answering
        # "now" for a missing date reports every application as fresh.
        self.assertIsNone(oc.parse_when(""))
        self.assertIsNone(oc.parse_when(None))

    def test_relative_days(self):
        got = oc.parse_when("5 days ago")
        self.assertAlmostEqual((datetime.now() - got).days, 5, delta=1)
        self.assertAlmostEqual((datetime.now() - oc.parse_when("3d")).days, 3, delta=1)

    def test_yesterday_and_today(self):
        self.assertAlmostEqual((datetime.now() - oc.parse_when("yesterday")).days,
                               1, delta=1)
        self.assertAlmostEqual((datetime.now() - oc.parse_when("today")).days,
                               0, delta=1)

    def test_date_formats(self):
        self.assertEqual(oc.parse_when("2026-08-14").date(),
                         datetime(2026, 8, 14).date())
        self.assertEqual(oc.parse_when("14/08/2026").date(),
                         datetime(2026, 8, 14).date())
        self.assertEqual(oc.parse_when("2026-08-14 09:30:00"),
                         datetime(2026, 8, 14, 9, 30))

    def test_nonsense_is_none(self):
        self.assertIsNone(oc.parse_when("sometime last spring"))


class TestFinding(unittest.TestCase):
    def setUp(self):
        self.state = seeded_state(DEFAULT_JOBS)

    def test_exact_job_id_wins(self):
        found = oc.find_applications(self.state, "j1")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["title"], "Risk Manager")

    def test_company_substring_matches_several(self):
        found = oc.find_applications(self.state, "Monzo")
        self.assertEqual(len(found), 2)

    def test_matches_are_newest_first(self):
        found = oc.find_applications(self.state, "Monzo")
        self.assertEqual(found[0]["job_id"], "j2")   # applied 50d ago, not 60

    def test_title_substring_matches(self):
        self.assertEqual(len(oc.find_applications(self.state, "Quant")), 1)

    def test_no_match_is_empty(self):
        self.assertEqual(oc.find_applications(self.state, "Nowhere Inc"), [])

    def test_empty_query_is_empty(self):
        self.assertEqual(oc.find_applications(self.state, ""), [])


class TestRecording(unittest.TestCase):
    def setUp(self):
        self.state = seeded_state(DEFAULT_JOBS)

    def test_an_outcome_is_stored_against_the_application(self):
        ok, msg = oc.record_outcome(self.state, "j1", "interview")
        self.assertTrue(ok, msg)
        rows = oc.existing_outcomes(self.state, "j1")
        self.assertEqual([r["response_type"] for r in rows], ["interview"])

    def test_days_to_response_uses_when_it_happened(self):
        # Applied 60 days ago, interviewed 5 days ago = 55 days, not 60.
        oc.record_outcome(self.state, "j1", "interview",
                          when=datetime.now() - timedelta(days=5))
        days = self.state.conn.execute(
            "SELECT days_to_response FROM response_tracking WHERE job_id='j1'"
        ).fetchone()["days_to_response"]
        self.assertAlmostEqual(days, 55, delta=1.5)

    def test_the_recorded_date_is_when_it_happened(self):
        when = datetime.now() - timedelta(days=5)
        oc.record_outcome(self.state, "j1", "interview", when=when)
        at = self.state.conn.execute(
            "SELECT response_at FROM response_tracking WHERE job_id='j1'"
        ).fetchone()["response_at"]
        self.assertEqual(at[:10], when.strftime("%Y-%m-%d"))

    def test_an_application_progresses_through_several_outcomes(self):
        for t in ("callback", "assessment", "interview", "offer"):
            ok, msg = oc.record_outcome(self.state, "j1", t)
            self.assertTrue(ok, msg)
        self.assertEqual(len(oc.existing_outcomes(self.state, "j1")), 4)

    def test_the_same_outcome_twice_is_refused(self):
        oc.record_outcome(self.state, "j1", "interview")
        ok, msg = oc.record_outcome(self.state, "j1", "interview")
        self.assertFalse(ok)
        self.assertIn("already recorded", msg)

    def test_force_allows_a_repeat(self):
        oc.record_outcome(self.state, "j1", "interview")
        ok, _ = oc.record_outcome(self.state, "j1", "interview", allow_duplicate=True)
        self.assertTrue(ok)
        self.assertEqual(len(oc.existing_outcomes(self.state, "j1")), 2)

    def test_an_unknown_type_is_refused(self):
        ok, msg = oc.record_outcome(self.state, "j1", "banana")
        self.assertFalse(ok)
        self.assertIn("not an outcome", msg)
        self.assertEqual(oc.existing_outcomes(self.state, "j1"), [])

    def test_an_unknown_job_is_refused(self):
        ok, msg = oc.record_outcome(self.state, "nope", "interview")
        self.assertFalse(ok)
        self.assertIn("no application", msg)

    def test_aliases_are_stored_canonically(self):
        oc.record_outcome(self.state, "j1", "rejected")
        self.assertEqual(oc.existing_outcomes(self.state, "j1")[0]["response_type"],
                         "rejection")

    def test_notes_are_kept(self):
        oc.record_outcome(self.state, "j1", "offer", notes="£85k, starts Sept")
        self.assertIn("85k", oc.existing_outcomes(self.state, "j1")[0]["notes"])

    def test_a_future_date_does_not_produce_negative_days(self):
        oc.record_outcome(self.state, "j1", "interview",
                          when=datetime.now() - timedelta(days=999))
        days = self.state.conn.execute(
            "SELECT days_to_response FROM response_tracking WHERE job_id='j1'"
        ).fetchone()["days_to_response"]
        self.assertGreaterEqual(days, 0)


class TestPending(unittest.TestCase):
    def setUp(self):
        self.state = seeded_state(DEFAULT_JOBS)

    def test_everything_is_open_to_begin_with(self):
        self.assertEqual(len(oc.pending_applications(self.state)), 4)

    def test_days_quiet_is_measured_from_the_application(self):
        rows = {r["job_id"]: r for r in oc.pending_applications(self.state)}
        self.assertAlmostEqual(rows["j1"]["days_quiet"], 60, delta=1)
        self.assertAlmostEqual(rows["j4"]["days_quiet"], 3, delta=1)

    def test_longest_silence_comes_first(self):
        rows = oc.pending_applications(self.state)
        self.assertEqual(rows[0]["job_id"], "j1")

    def test_a_terminal_outcome_closes_the_application(self):
        oc.record_outcome(self.state, "j1", "offer")
        oc.record_outcome(self.state, "j3", "rejection")
        open_ids = {r["job_id"] for r in oc.pending_applications(self.state)}
        self.assertEqual(open_ids, {"j2", "j4"})

    def test_a_non_terminal_outcome_keeps_it_open(self):
        oc.record_outcome(self.state, "j1", "interview")
        open_ids = {r["job_id"] for r in oc.pending_applications(self.state)}
        self.assertIn("j1", open_ids)

    def test_days_quiet_resets_after_an_update(self):
        oc.record_outcome(self.state, "j1", "callback")
        row = next(r for r in oc.pending_applications(self.state)
                   if r["job_id"] == "j1")
        self.assertLess(row["days_quiet"], 1)
        self.assertEqual(row["responses"], 1)

    def test_quiet_days_filters(self):
        rows = oc.pending_applications(self.state, quiet_days=45)
        self.assertEqual({r["job_id"] for r in rows}, {"j1", "j2"})


class TestGhostSweep(unittest.TestCase):
    def setUp(self):
        self.state = seeded_state(DEFAULT_JOBS)

    def test_dry_run_changes_nothing(self):
        stale = oc.sweep_ghosted(self.state, after_days=45, dry_run=True)
        self.assertEqual({r["job_id"] for r in stale}, {"j1", "j2"})
        self.assertEqual(
            self.state.conn.execute(
                "SELECT COUNT(*) c FROM response_tracking").fetchone()["c"], 0)

    def test_applying_marks_them_ghosted(self):
        done = oc.sweep_ghosted(self.state, after_days=45, dry_run=False)
        self.assertEqual(len(done), 2)
        types = {r["job_id"]: oc.existing_outcomes(self.state, r["job_id"])[0]
                 for r in done}
        for row in types.values():
            self.assertEqual(row["response_type"], "ghosted")

    def test_applications_with_any_response_are_left_alone(self):
        # They replied once; silence since then is a different problem.
        oc.record_outcome(self.state, "j1", "callback",
                          when=datetime.now() - timedelta(days=50))
        done = oc.sweep_ghosted(self.state, after_days=45, dry_run=False)
        self.assertEqual({r["job_id"] for r in done}, {"j2"})

    def test_the_threshold_is_respected(self):
        self.assertEqual(oc.sweep_ghosted(self.state, after_days=365), [])

    def test_a_swept_application_is_no_longer_open(self):
        oc.sweep_ghosted(self.state, after_days=45, dry_run=False)
        open_ids = {r["job_id"] for r in oc.pending_applications(self.state)}
        self.assertEqual(open_ids, {"j3", "j4"})


class TestSummary(unittest.TestCase):
    def setUp(self):
        self.state = seeded_state(DEFAULT_JOBS)

    def test_an_empty_funnel_reads_as_empty(self):
        s = oc.outcome_summary(self.state)
        self.assertEqual(s["applied"], 4)
        self.assertEqual(s["engaged"], 0)
        self.assertEqual(s["pending"], 4)
        self.assertIn("No outcomes recorded yet", oc.format_summary(s))

    def test_the_funnel_counts_each_stage(self):
        oc.record_outcome(self.state, "j1", "interview")
        oc.record_outcome(self.state, "j1", "offer")
        oc.record_outcome(self.state, "j3", "rejection")
        s = oc.outcome_summary(self.state)
        self.assertEqual(s["interviews"], 1)
        self.assertEqual(s["offers"], 1)
        self.assertEqual(s["rejections"], 1)
        self.assertEqual(s["pending"], 2)

    def test_an_application_is_counted_once_however_many_outcomes(self):
        for t in ("callback", "assessment", "interview", "offer"):
            oc.record_outcome(self.state, "j1", t)
        self.assertEqual(oc.outcome_summary(self.state)["engaged"], 1)

    def test_an_assessment_counts_as_engagement(self):
        oc.record_outcome(self.state, "j1", "assessment")
        self.assertEqual(oc.outcome_summary(self.state)["engaged"], 1)

    def test_engagement_rate_is_a_percentage_of_applications(self):
        oc.record_outcome(self.state, "j1", "interview")
        self.assertEqual(oc.outcome_summary(self.state)["engagement_rate"], 25.0)

    def test_no_applications_does_not_divide_by_zero(self):
        state = seeded_state([])
        s = oc.outcome_summary(state)
        self.assertEqual(s["engagement_rate"], 0.0)
        self.assertIn("No applications", oc.format_summary(s))

    def test_formatting_never_crashes_on_a_real_funnel(self):
        oc.record_outcome(self.state, "j1", "offer")
        self.assertIn("Applied", oc.format_summary(oc.outcome_summary(self.state)))
        self.assertIn("still open", oc.format_pending(
            oc.pending_applications(self.state)))

    def test_empty_pending_says_so(self):
        self.assertIn("Nothing outstanding", oc.format_pending([]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
