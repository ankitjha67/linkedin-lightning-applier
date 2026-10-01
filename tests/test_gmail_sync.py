"""Recruiter email → recorded outcomes — gmail_sync.py.

The safety properties, in order of importance:

  * reading proposes, it never records;
  * a proposal that cannot be matched to an application is never applied;
  * an uncertain match is not applied by default, because a misattributed
    outcome teaches every downstream model something false; and
  * whatever is recorded says which email it came from.
"""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gmail_sync as gs  # noqa: E402
import outcomes as oc  # noqa: E402
from state import State  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"
JOBS = [
    ("j1", "Risk Manager", "Monzo", 60),
    ("j2", "Data Engineer", "Monzo", 50),
    ("j3", "Quant Analyst", "Revolut", 10),
    ("j4", "ML Engineer", "Wise", 3),
]


def seeded_state(jobs=JOBS):
    state = State(db_path=str(Path(tempfile.mkdtemp()) / "t.db"))
    now = datetime.now()
    for job_id, title, company, days in jobs:
        state.conn.execute(
            "INSERT INTO applied_jobs (job_id,title,company,applied_at) VALUES (?,?,?,?)",
            (job_id, title, company, (now - timedelta(days=days)).strftime(FMT)))
    state.conn.commit()
    return state


def email(eid, sender, subject, body, date="Mon, 18 Aug 2026 10:00:00 +0000"):
    return {"id": eid, "from": sender, "subject": subject, "body": body, "date": date}


class TestClassification(unittest.TestCase):
    def test_a_rejection_is_a_rejection(self):
        self.assertEqual(gs.classify_email(
            "Your application", "Unfortunately we are not moving forward."), "rejection")

    def test_a_polite_rejection_is_not_read_as_an_interview(self):
        # The hardest real case: rejection wrapped in interview vocabulary.
        self.assertEqual(gs.classify_email(
            "Thanks for speaking with us",
            "We enjoyed speaking with you and were impressed, but unfortunately "
            "we have gone with another candidate."), "rejection")

    def test_an_interview_invitation(self):
        self.assertEqual(gs.classify_email(
            "Next steps", "We would like to schedule a phone screen. "
                          "Please share your availability."), "interview")

    def test_a_take_home_is_an_assessment(self):
        self.assertEqual(gs.classify_email(
            "Exercise", "Please complete the following coding challenge on HackerRank."),
            "assessment")

    def test_an_offer_outranks_generic_positive_language(self):
        self.assertEqual(gs.classify_email(
            "Congratulations", "We are pleased to offer you the position. "
                               "Your offer letter is attached."), "offer")

    def test_unrelated_mail_classifies_as_nothing(self):
        self.assertEqual(gs.classify_email("Your invoice", "Payment received."), "")

    def test_empty_input_is_safe(self):
        self.assertEqual(gs.classify_email("", ""), "")
        self.assertEqual(gs.classify_email(None, None), "")


class TestSenderTokens(unittest.TestCase):
    def test_the_company_is_taken_from_the_domain(self):
        self.assertIn("monzo", gs.sender_tokens("careers@monzo.com"))

    def test_ats_and_mail_providers_are_not_companies(self):
        for addr in ("no-reply@greenhouse.io", "x@myworkday.com",
                     "y@gmail.com", "z@lever.co"):
            self.assertEqual(gs.sender_tokens(addr), [], addr)

    def test_subdomains_are_kept(self):
        self.assertIn("monzo", gs.sender_tokens("jobs@careers.monzo.com"))

    def test_a_malformed_address_is_empty(self):
        self.assertEqual(gs.sender_tokens("not-an-address"), [])
        self.assertEqual(gs.sender_tokens(""), [])


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.state = seeded_state()

    def test_the_sender_domain_identifies_a_unique_company(self):
        job_id, company, _t, conf, _why = gs.match_application(
            self.state, "careers@revolut.com", "Your application", "Hello")
        self.assertEqual(job_id, "j3")
        self.assertEqual(company, "Revolut")
        self.assertEqual(conf, "high")

    def test_the_title_disambiguates_two_roles_at_one_company(self):
        # Two Monzo applications; the email names which.
        job_id, _c, title, conf, _why = gs.match_application(
            self.state, "talent@monzo.com", "Take-home",
            "for the Risk Manager position")
        self.assertEqual(job_id, "j1")
        self.assertEqual(title, "Risk Manager")
        self.assertEqual(conf, "high")

    def test_an_unresolvable_company_stays_low_confidence(self):
        _j, _c, _t, conf, why = gs.match_application(
            self.state, "recruiting@monzo.com", "Thanks", "We enjoyed talking.")
        self.assertEqual(conf, "low")
        self.assertIn("verify", why)

    def test_the_company_named_in_the_body_matches_via_an_ats_sender(self):
        job_id, _c, _t, conf, _why = gs.match_application(
            self.state, "no-reply@greenhouse.io", "Wise — next steps",
            "The team at Wise would like to talk.")
        self.assertEqual(job_id, "j4")
        self.assertEqual(conf, "medium")

    def test_an_unrelated_email_matches_nothing(self):
        job_id, _c, _t, _conf, why = gs.match_application(
            self.state, "newsletter@techweekly.com", "10 jobs", "Interview tips.")
        self.assertEqual(job_id, "")
        self.assertIn("could not tell", why)

    def test_no_applications_matches_nothing(self):
        job_id, _c, _t, _conf, _why = gs.match_application(
            seeded_state([]), "careers@monzo.com", "Hi", "Hello")
        self.assertEqual(job_id, "")


class TestProposing(unittest.TestCase):
    def setUp(self):
        self.state = seeded_state()

    def test_reading_records_nothing(self):
        gs.propose_outcomes(self.state, [
            email("m1", "careers@revolut.com", "Application",
                  "Unfortunately we are not moving forward.")])
        count = self.state.conn.execute(
            "SELECT COUNT(*) c FROM response_tracking").fetchone()["c"]
        self.assertEqual(count, 0, "proposing wrote to the database")

    def test_unclassifiable_mail_is_not_proposed(self):
        self.assertEqual(gs.propose_outcomes(self.state, [
            email("m1", "billing@utility.com", "Invoice", "Payment received.")]), [])

    def test_an_unmatched_email_is_proposed_but_never_confident(self):
        [p] = gs.propose_outcomes(self.state, [
            email("m1", "newsletter@x.com", "Jobs", "Interview tips this week.")])
        self.assertEqual(p.job_id, "")
        self.assertEqual(p.confidence, "low")

    def test_an_already_recorded_outcome_is_not_proposed_again(self):
        oc.record_outcome(self.state, "j3", "rejection")
        self.assertEqual(gs.propose_outcomes(self.state, [
            email("m1", "careers@revolut.com", "Application",
                  "Unfortunately we are not moving forward.")]), [])

    def test_a_different_outcome_for_the_same_job_is_still_proposed(self):
        oc.record_outcome(self.state, "j3", "callback")
        props = gs.propose_outcomes(self.state, [
            email("m1", "careers@revolut.com", "Application",
                  "Unfortunately we are not moving forward.")])
        self.assertEqual([p.outcome for p in props], ["rejection"])

    def test_the_citation_names_sender_subject_and_date(self):
        [p] = gs.propose_outcomes(self.state, [
            email("m1", "careers@revolut.com", "Your application",
                  "Unfortunately we are not moving forward.")])
        for part in ("careers@revolut.com", "Your application", "2026", "m1"):
            self.assertIn(part, p.citation)


class TestApprovalGate(unittest.TestCase):
    def setUp(self):
        self.state = seeded_state()
        self.emails = [
            email("m1", "careers@revolut.com", "Your application",
                  "Unfortunately we are not moving forward."),           # high
            email("m2", "no-reply@greenhouse.io", "Wise — next steps",
                  "The team at Wise would like to schedule a call."),    # medium
            email("m3", "recruiting@monzo.com", "Thanks",
                  "We enjoyed speaking but have gone with another candidate."),  # low
            email("m4", "newsletter@x.com", "Jobs", "Interview tips."),  # unmatched
        ]
        self.props = gs.propose_outcomes(self.state, self.emails)

    def test_only_high_confidence_applies_by_default(self):
        written = gs.apply_proposals(self.state, self.props)
        self.assertEqual([p.email_id for p in written], ["m1"])

    def test_lowering_the_floor_includes_uncertain_matches(self):
        written = gs.apply_proposals(self.state, self.props, min_confidence="low")
        self.assertEqual({p.email_id for p in written}, {"m1", "m2", "m3"})

    def test_an_unmatched_proposal_is_never_applied(self):
        written = gs.apply_proposals(self.state, self.props, min_confidence="low")
        self.assertNotIn("m4", {p.email_id for p in written})
        unmatched = next(p for p in self.props if p.email_id == "m4")
        self.assertIn("not matched", unmatched.error)

    def test_explicit_approval_overrides_confidence(self):
        written = gs.apply_proposals(self.state, self.props, approve={"m3"})
        self.assertEqual([p.email_id for p in written], ["m3"])

    def test_approving_nothing_writes_nothing(self):
        self.assertEqual(gs.apply_proposals(self.state, self.props, approve=set()), [])
        self.assertEqual(self.state.conn.execute(
            "SELECT COUNT(*) c FROM response_tracking").fetchone()["c"], 0)

    def test_a_predicate_can_gate_approval(self):
        written = gs.apply_proposals(
            self.state, self.props, approve=lambda p: p.outcome == "interview")
        self.assertEqual([p.outcome for p in written], ["interview"])

    def test_what_is_written_cites_its_email(self):
        gs.apply_proposals(self.state, self.props)
        notes = self.state.conn.execute(
            "SELECT notes FROM response_tracking").fetchone()["notes"]
        self.assertIn("from email:", notes)
        self.assertIn("careers@revolut.com", notes)

    def test_the_outcome_is_dated_from_the_email_not_today(self):
        gs.apply_proposals(self.state, self.props)
        at = self.state.conn.execute(
            "SELECT response_at FROM response_tracking").fetchone()["response_at"]
        self.assertTrue(at.startswith("2026-08-18"), at)

    def test_applying_twice_does_not_duplicate(self):
        gs.apply_proposals(self.state, self.props)
        again = gs.propose_outcomes(self.state, self.emails)
        gs.apply_proposals(self.state, again)
        self.assertEqual(self.state.conn.execute(
            "SELECT COUNT(*) c FROM response_tracking").fetchone()["c"], 1)


class TestDates(unittest.TestCase):
    def test_rfc_2822_headers_parse(self):
        got = gs.parse_email_date("Mon, 18 Aug 2026 10:00:00 +0000")
        self.assertEqual(got.date(), datetime(2026, 8, 18).date())

    def test_a_plain_date_falls_back_to_the_outcome_parser(self):
        self.assertEqual(gs.parse_email_date("2026-08-18").date(),
                         datetime(2026, 8, 18).date())

    def test_an_unreadable_date_is_none(self):
        self.assertIsNone(gs.parse_email_date(""))
        self.assertIsNone(gs.parse_email_date("whenever"))


class TestPresentation(unittest.TestCase):
    def setUp(self):
        self.state = seeded_state()

    def test_nothing_to_do_says_so(self):
        self.assertIn("Nothing new", gs.format_proposals([]))

    def test_proposals_render_with_their_reason(self):
        props = gs.propose_outcomes(self.state, [
            email("m1", "careers@revolut.com", "Your application",
                  "Unfortunately we are not moving forward.")])
        out = gs.format_proposals(props)
        self.assertIn("REJECTION", out)
        self.assertIn("Revolut", out)
        self.assertIn("why:", out)
        self.assertIn("Nothing has been recorded", out)

    def test_the_summary_counts_what_matters(self):
        props = gs.propose_outcomes(self.state, [
            email("m1", "careers@revolut.com", "A",
                  "Unfortunately we are not moving forward."),
            email("m2", "newsletter@x.com", "B", "Interview tips.")])
        s = gs.summarise(props)
        self.assertEqual(s["proposed"], 2)
        self.assertEqual(s["matched"], 1)
        self.assertEqual(s["high_confidence"], 1)


class TestMcpToolLayer(unittest.TestCase):
    """The MCP surface must keep the same gate as the CLI."""

    def test_the_read_tool_and_write_tool_are_separate(self):
        import tools_layer
        self.assertIn("tool_propose_outcomes_from_email", tools_layer.ALL_TOOLS)
        self.assertIn("tool_apply_email_outcomes", tools_layer.ALL_TOOLS)

    def test_the_write_tool_requires_approved_ids(self):
        import tools_layer
        out = tools_layer.tool_apply_email_outcomes("[]", "[]")
        self.assertIn("No email ids approved", out)

    def test_bad_json_is_reported_not_raised(self):
        import tools_layer
        out = tools_layer.tool_propose_outcomes_from_email("{not json")
        self.assertIn("Could not read", out)

    def test_a_non_list_payload_is_rejected(self):
        import tools_layer
        out = tools_layer.tool_propose_outcomes_from_email('{"id": 1}')
        self.assertIn("Expected a JSON list", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
