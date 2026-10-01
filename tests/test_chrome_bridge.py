"""The browser-agent bridge — chrome_bridge.py.

Claude for Chrome has the browser; the engine has the judgement. These tests
pin the three things that are deliberately enforced here rather than in a
prompt, because a prompt can be forgotten, truncated or summarised away
halfway through a long session:

  * the daily cap, counted from the database;
  * one application per job, via a claim;
  * no invented answers — an unsourceable field comes back `unanswered`.

The work-authorisation tests are the sharp end. A wrong answer there tells an
employer you cannot work for them, on every application, silently.
"""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chrome_bridge as cb  # noqa: E402
from state import State  # noqa: E402
from work_auth import WorkAuthorization  # noqa: E402

CFG = {
    "state": {"db_path": ":memory:"},
    "personal": {"first_name": "Ada", "last_name": "Lovelace"},
    "search": {"search_terms": ["Risk Manager"], "search_locations": ["London"]},
    "scheduling": {"max_applies_per_day": 3, "active_hours_start": 0,
                   "active_hours_end": 24},
    "work_authorization": {"citizenship": ["United Kingdom"], "visas": []},
    "question_answers": {"notice period": "1 month"},
}

LI_URL = "https://www.linkedin.com/jobs/view/4123456789/"


def fresh_state():
    return State(db_path=str(Path(tempfile.mkdtemp()) / "s.db"))


def cfg(**overrides):
    import copy
    out = copy.deepcopy(CFG)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


class TestJobIdentity(unittest.TestCase):
    def test_a_linkedin_url_yields_its_numeric_id(self):
        self.assertEqual(cb.job_id_for(LI_URL), "li-4123456789")

    def test_the_search_form_of_a_linkedin_url_works_too(self):
        self.assertEqual(
            cb.job_id_for("https://www.linkedin.com/jobs/search/?currentJobId=4123456789"),
            "li-4123456789")

    def test_the_same_posting_by_two_urls_is_one_job(self):
        a = cb.job_id_for(LI_URL)
        b = cb.job_id_for("https://linkedin.com/jobs/view/4123456789/?refId=x")
        self.assertEqual(a, b)

    def test_a_greenhouse_id_is_recognised(self):
        self.assertTrue(
            cb.job_id_for("https://boards.greenhouse.io/x/jobs?gh_jid=12345")
            .startswith("ats-"))

    def test_company_and_title_dedupe_an_unidentifiable_url(self):
        a = cb.job_id_for("https://careers.monzo.com/apply", "Risk Manager", "Monzo")
        b = cb.job_id_for("https://monzo.com/jobs/other", "Risk Manager", "Monzo")
        self.assertEqual(a, b, "the same role at one company got two ids")

    def test_different_roles_are_different_jobs(self):
        self.assertNotEqual(
            cb.job_id_for("https://x.com/a", "Risk Manager", "Monzo"),
            cb.job_id_for("https://x.com/b", "Data Engineer", "Monzo"))


class TestClaims(unittest.TestCase):
    """Two applications to one job look worse to an employer than none."""

    def setUp(self):
        self.state = fresh_state()

    def test_a_job_can_be_claimed(self):
        self.assertTrue(cb.claim(self.state, "j1", LI_URL, "Risk Manager", "Monzo"))

    def test_a_claimed_job_cannot_be_claimed_again(self):
        cb.claim(self.state, "j1")
        self.assertFalse(cb.claim(self.state, "j1"))

    def test_closing_a_claim_frees_nothing_once_applied(self):
        cb.claim(self.state, "j1")
        cb.close_claim(self.state, "j1", "applied")
        self.assertFalse(cb.claim(self.state, "j1"),
                         "an applied job was offered again")

    def test_a_failed_claim_can_be_retried(self):
        cb.claim(self.state, "j1")
        cb.close_claim(self.state, "j1", "failed", "form broke")
        self.assertTrue(cb.claim(self.state, "j1"))

    def test_a_stale_claim_is_released(self):
        # A closed tab or a crashed agent must not keep a job out of
        # circulation for ever.
        cb.claim(self.state, "j1")
        old = (datetime.now() - timedelta(minutes=cb.CLAIM_STALE_MINUTES + 10)
               ).strftime(cb.TIME_FMT)
        self.state.conn.execute(
            "UPDATE agent_claims SET claimed_at=? WHERE job_id=?", (old, "j1"))
        self.state.conn.commit()
        self.assertEqual(cb.release_stale_claims(self.state), 1)
        self.assertTrue(cb.claim(self.state, "j1"))

    def test_a_recent_claim_is_not_released(self):
        cb.claim(self.state, "j1")
        self.assertEqual(cb.release_stale_claims(self.state), 0)

    def test_open_claims_are_listed(self):
        cb.claim(self.state, "j1", title="Risk Manager", company="Monzo")
        cb.claim(self.state, "j2", title="Data Engineer", company="Wise")
        cb.close_claim(self.state, "j2", "applied")
        self.assertEqual([c["job_id"] for c in cb.open_claims(self.state)], ["j1"])


class TestConsider(unittest.TestCase):
    def setUp(self):
        self.state = fresh_state()

    def test_a_fresh_job_is_approved_and_claimed(self):
        out = cb.consider(self.state, cfg(), LI_URL, "Risk Manager", "Monzo")
        self.assertEqual(out["verdict"], "apply")
        self.assertFalse(cb.claim(self.state, out["job_id"]),
                         "consider() approved without claiming")

    def test_the_same_job_twice_is_refused(self):
        cb.consider(self.state, cfg(), LI_URL, "Risk Manager", "Monzo")
        second = cb.consider(self.state, cfg(), LI_URL, "Risk Manager", "Monzo")
        self.assertEqual(second["verdict"], "skip")
        self.assertIn("claimed", second["reason"])

    def test_an_already_applied_job_is_skipped(self):
        job_id = cb.job_id_for(LI_URL, "Risk Manager", "Monzo")
        self.state.mark_applied(job_id, title="Risk Manager", company="Monzo")
        out = cb.consider(self.state, cfg(), LI_URL, "Risk Manager", "Monzo")
        self.assertEqual(out["verdict"], "skip")
        self.assertIn("already applied", out["reason"])

    def test_an_already_skipped_job_is_not_reconsidered(self):
        job_id = cb.job_id_for(LI_URL, "Risk Manager", "Monzo")
        self.state.mark_skipped(job_id=job_id, reason="too junior")
        out = cb.consider(self.state, cfg(), LI_URL, "Risk Manager", "Monzo")
        self.assertEqual(out["verdict"], "skip")

    def test_the_daily_cap_stops_the_session(self):
        """Counted from the database, not from anything the agent tracks."""
        for n in range(3):
            self.state.mark_applied(f"j{n}", title=f"R{n}", company="Co")
        out = cb.consider(self.state, cfg(), LI_URL, "Risk Manager", "Monzo")
        self.assertEqual(out["verdict"], "stop")
        self.assertIn("daily cap", out["reason"])

    def test_the_cap_is_read_fresh_every_time(self):
        # So a long session cannot drift past it, however it is driven.
        self.assertEqual(cb.consider(self.state, cfg(), LI_URL, "R", "Monzo")["verdict"],
                         "apply")
        for n in range(3):
            self.state.mark_applied(f"k{n}", title="x", company="y")
        out = cb.consider(self.state, cfg(),
                          "https://www.linkedin.com/jobs/view/999888777/", "R2", "Co")
        self.assertEqual(out["verdict"], "stop")

    def test_outside_active_hours_stops_the_session(self):
        hour = datetime.now().hour
        # A window that certainly excludes now.
        start = (hour + 2) % 24
        narrow = cfg(scheduling={"active_hours_start": start,
                                 "active_hours_end": (start + 1) % 24 or 1})
        out = cb.consider(self.state, narrow, LI_URL, "Risk Manager", "Monzo")
        self.assertEqual(out["verdict"], "stop")
        self.assertIn("active hours", out["reason"])

    def test_a_low_score_skips_and_is_recorded(self):
        class Scorer:
            enabled = True

        import match_scorer
        original = match_scorer.MatchScorer
        match_scorer.MatchScorer = lambda ai, c: type(
            "S", (), {"score_job": lambda _s, *a, **k: {"score": 20,
                                                        "explanation": "wrong field"}})()
        self.addCleanup(setattr, match_scorer, "MatchScorer", original)

        low = cfg(matching={"min_match_score": 70})
        out = cb.consider(self.state, low, LI_URL, "Risk Manager", "Monzo",
                          description="a job", ai=Scorer())
        self.assertEqual(out["verdict"], "skip")
        self.assertIn("below", out["reason"])
        self.assertTrue(self.state.is_skipped(out["job_id"]),
                        "a skipped job was not recorded, so it would be reconsidered")

    def test_a_high_score_is_approved(self):
        class Scorer:
            enabled = True

        import match_scorer
        original = match_scorer.MatchScorer
        match_scorer.MatchScorer = lambda ai, c: type(
            "S", (), {"score_job": lambda _s, *a, **k: {"score": 88}})()
        self.addCleanup(setattr, match_scorer, "MatchScorer", original)

        out = cb.consider(self.state, cfg(matching={"min_match_score": 70}),
                          LI_URL, "Risk Manager", "Monzo",
                          description="a job", ai=Scorer())
        self.assertEqual(out["verdict"], "apply")
        self.assertEqual(out["score"], 88)

    def test_a_broken_scorer_does_not_block_the_session(self):
        class Scorer:
            enabled = True

        import match_scorer
        original = match_scorer.MatchScorer

        def boom(ai, c):
            raise RuntimeError("model down")
        match_scorer.MatchScorer = boom
        self.addCleanup(setattr, match_scorer, "MatchScorer", original)

        out = cb.consider(self.state, cfg(), LI_URL, "Risk Manager", "Monzo",
                          description="a job", ai=Scorer())
        self.assertEqual(out["verdict"], "apply")
        self.assertIsNone(out["score"])


class TestAnswers(unittest.TestCase):
    def setUp(self):
        self.state = fresh_state()

    def _ask(self, label, options=None, config=None, location="London"):
        out = cb.answers(self.state, config or cfg(),
                         [{"label": label, "options": options or []}],
                         job_location=location)
        return out["answers"][0]

    def test_work_authorisation_is_answered_from_citizenship(self):
        got = self._ask("Are you authorised to work in the United Kingdom?",
                        ["Yes", "No"])
        self.assertEqual(got["value"], "Yes")
        self.assertEqual(got["source"], "work_auth")

    def test_sponsorship_is_the_inverse(self):
        got = self._ask("Do you require visa sponsorship?", ["Yes", "No"])
        self.assertEqual(got["value"], "No")

    def test_a_country_you_cannot_work_in_answers_no(self):
        got = self._ask("Are you authorised to work in the United States?",
                        ["Yes", "No"])
        self.assertEqual(got["value"], "No")

    def test_a_configured_answer_is_used(self):
        got = self._ask("What is your notice period?")
        self.assertEqual(got["value"], "1 month")
        self.assertEqual(got["source"], "config")

    def test_an_unknown_question_is_unanswered_not_guessed(self):
        got = self._ask("Describe a time you influenced a senior stakeholder.")
        self.assertEqual(got["source"], "unanswered")
        self.assertEqual(got["value"], "")
        self.assertIn("ask me", got["note"])

    def test_a_consent_notice_is_left_alone(self):
        # This project has already shipped a notice period into a GDPR box once.
        got = self._ask("Keeping your data safe: we will retain your details "
                        "for 12 months in line with our privacy policy, and "
                        "you may withdraw consent at any time.")
        self.assertEqual(got["value"], "")
        self.assertIn("prose", got["note"])

    def test_a_field_with_no_label_is_not_answered(self):
        got = self._ask("")
        self.assertEqual(got["source"], "unanswered")

    def test_unanswered_labels_are_listed_for_the_person(self):
        out = cb.answers(self.state, cfg(), [
            {"label": "What is your notice period?"},
            {"label": "Why do you want to work here?"},
        ])
        self.assertEqual(out["answered"], 1)
        self.assertEqual(out["unanswered"], ["Why do you want to work here?"])

    def test_the_reminder_forbids_improvising(self):
        out = cb.answers(self.state, cfg(), [{"label": "x"}])
        self.assertIn("do not improvise", out["reminder"])

    def test_a_remembered_answer_comes_back(self):
        question = "Why do you want to work here?"
        cb.remember(self.state, cfg(),
                    [{"label": question, "value": "The risk models."}])
        got = self._ask(question)
        self.assertEqual(got["source"], "remembered")
        self.assertEqual(got["value"], "The risk models.")

    def test_remembering_an_empty_answer_is_ignored(self):
        self.assertEqual(
            cb.remember(self.state, cfg(), [{"label": "x", "value": ""}]), 0)


class TestProfileFields(unittest.TestCase):
    """Your own name, email and phone — the fields every form asks for.

    These resolved to `unanswered` at first, because only question_answers was
    consulted. That meant the agent had to stop and ask for the applicant's own
    email address on every single application, which makes the whole handoff
    pointless.
    """

    FULL = {
        "personal": {"first_name": "Ada", "last_name": "Lovelace",
                     "full_name": "Ada Lovelace", "email": "ada@example.com",
                     "phone": "+44 7000 000000", "city": "London",
                     "state": "Greater London", "zip_code": "EC1A",
                     "country": "United Kingdom",
                     "linkedin_headline": "Risk Manager @ Monzo"},
        "application": {"years_of_experience": 8, "notice_period_days": "30",
                        "desired_salary": "Negotiable", "current_ctc": "90000",
                        "willing_to_relocate": "Yes"},
    }

    CASES = [
        ("First name", "Ada"), ("Given name", "Ada"),
        ("Last name", "Lovelace"), ("Surname", "Lovelace"),
        ("Full legal name", "Ada Lovelace"),
        ("Email", "ada@example.com"), ("E-mail address", "ada@example.com"),
        ("Mobile phone number", "+44 7000 000000"), ("Telephone", "+44 7000 000000"),
        ("City", "London"), ("State / Province", "Greater London"),
        ("Postal code", "EC1A"), ("Zip code", "EC1A"),
        ("Country of residence", "United Kingdom"),
        ("Current job title", "Risk Manager @ Monzo"),
        ("Years of experience", "8"),
        ("How many years of experience do you have?", "8"),
        ("Notice period", "30"), ("Expected salary", "Negotiable"),
        ("Current CTC", "90000"), ("Willing to relocate?", "Yes"),
    ]

    def test_every_common_label_resolves(self):
        for label, expected in self.CASES:
            value, where = cb._profile_answer(self.FULL, label)
            self.assertEqual(value, expected, f"{label!r} -> {value!r} ({where})")

    def test_the_source_says_where_the_value_came_from(self):
        _value, where = cb._profile_answer(self.FULL, "Email")
        self.assertEqual(where, "personal.email")

    def test_a_field_with_no_configured_value_is_not_invented(self):
        value, _where = cb._profile_answer({"personal": {}}, "Email")
        self.assertEqual(value, "")

    def test_an_unrelated_label_matches_nothing(self):
        value, _where = cb._profile_answer(
            self.FULL, "Describe a conflict you resolved")
        self.assertEqual(value, "")

    def test_first_name_is_not_swallowed_by_the_name_pattern(self):
        # Ordering matters: "first name" must beat the bare-name pattern.
        self.assertEqual(cb._profile_answer(self.FULL, "First name")[1],
                         "personal.first_name")

    def test_profile_answers_come_through_the_bridge(self):
        state = fresh_state()
        config = cfg(**self.FULL)
        out = cb.answers(state, config, [{"label": "Email address"}])
        answer = out["answers"][0]
        self.assertEqual(answer["value"], "ada@example.com")
        self.assertEqual(answer["source"], "profile")
        self.assertIn("personal.email", answer["note"])

    def test_question_answers_still_win_over_the_profile(self):
        # An answer the user wrote by hand beats a derived one.
        state = fresh_state()
        config = cfg(question_answers={"notice period": "2 months"},
                     **self.FULL)
        out = cb.answers(state, config, [{"label": "What is your notice period?"}])
        self.assertEqual(out["answers"][0]["value"], "2 months")
        self.assertEqual(out["answers"][0]["source"], "config")

    def test_a_realistic_easy_apply_form_is_mostly_answered(self):
        state = fresh_state()
        config = cfg(**self.FULL)
        form = [
            {"label": "First name"}, {"label": "Last name"},
            {"label": "Email address"}, {"label": "Mobile phone number"},
            {"label": "City"}, {"label": "Country"},
            {"label": "Are you legally authorised to work in the United Kingdom?",
             "options": ["Yes", "No"]},
            {"label": "Will you now or in the future require sponsorship?",
             "options": ["Yes", "No"]},
            {"label": "What is your notice period?"},
            {"label": "Years of experience"},
            {"label": "Are you willing to relocate?", "options": ["Yes", "No"]},
        ]
        out = cb.answers(state, config, form, job_location="London")
        self.assertEqual(out["unanswered"], [],
                         f"a routine form left gaps: {out['unanswered']}")


class TestValidatorCatchesBadWorkAuth(unittest.TestCase):
    """`lla validate-config` and autopilot preflight must catch this shape."""

    def _validate(self, wa):
        from validate_config import ConfigValidator
        config = {"personal": {"first_name": "Ada"},
                  "search": {"search_terms": ["x"], "search_locations": ["London"]},
                  "work_authorization": wa}
        validator = ConfigValidator(config)
        return validator.validate(), validator.errors, validator.warnings

    def test_a_string_citizenship_is_valid(self):
        ok, errors, _warnings = self._validate({"citizenship": "United Kingdom"})
        self.assertTrue(ok, errors)

    def test_a_list_citizenship_is_valid(self):
        ok, errors, _warnings = self._validate({"citizenship": ["India"]})
        self.assertTrue(ok, errors)

    def test_an_unrecognised_country_fails_validation(self):
        ok, errors, _warnings = self._validate({"citizenship": ["Untied Kingdom"]})
        self.assertFalse(ok, "a typo'd country passed validation")
        self.assertTrue(any("work_authorization" in e for e in errors))

    def test_the_error_explains_the_consequence(self):
        _ok, errors, _warnings = self._validate({"citizenship": ["Elbonia"]})
        joined = " ".join(errors)
        self.assertIn("answer No", joined)

    def test_a_partly_unrecognised_list_is_only_a_warning(self):
        ok, _errors, warnings = self._validate({"citizenship": ["India", "Elbonia"]})
        self.assertTrue(ok)
        self.assertTrue(any("not recognised" in w for w in warnings))

    def test_an_unconfigured_section_is_not_an_error(self):
        ok, errors, _warnings = self._validate({})
        self.assertTrue(ok, errors)


class TestWorkAuthMisconfiguration(unittest.TestCase):
    """The sharpest edge in the whole system.

    `citizenship` is a list, but the key reads singular and YAML accepts a bare
    string. Iterated character by character it resolved nothing, so every
    "authorised to work in X?" answered No and every sponsorship question
    answered Yes — on every application, with only a per-letter warning.
    """

    def test_a_string_citizenship_resolves_to_one_country(self):
        auth = WorkAuthorization({"work_authorization": {"citizenship": "United Kingdom"}})
        self.assertEqual(auth.citizenship, {"united kingdom"})
        self.assertTrue(auth.usable)

    def test_a_string_citizenship_answers_correctly(self):
        auth = WorkAuthorization({"work_authorization": {"citizenship": "United Kingdom"}})
        self.assertEqual(
            auth.answer("Are you authorised to work in the United Kingdom?",
                        options=["Yes", "No"]), "Yes")

    def test_a_list_citizenship_still_works(self):
        auth = WorkAuthorization({"work_authorization": {"citizenship": ["India"]}})
        self.assertEqual(auth.citizenship, {"india"})

    def test_a_single_visa_dict_is_accepted(self):
        auth = WorkAuthorization({"work_authorization": {
            "citizenship": ["India"],
            "visas": {"country": "United Kingdom", "type": "Skilled Worker"}}})
        self.assertIn("united kingdom", auth.visa_countries)

    def test_an_unrecognised_country_is_unusable_rather_than_wrong(self):
        auth = WorkAuthorization({"work_authorization": {"citizenship": ["Elbonia"]}})
        self.assertTrue(auth.enabled)
        self.assertFalse(auth.usable)
        self.assertEqual(auth.unresolved, ["Elbonia"])

    def test_an_unusable_config_refuses_to_answer_through_the_bridge(self):
        # Better to hand it back than to tell an employer you cannot work.
        state = fresh_state()
        broken = cfg(work_authorization={"citizenship": ["Elbonia"], "visas": []})
        out = cb.answers(state, broken, [
            {"label": "Are you authorised to work in the United Kingdom?",
             "options": ["Yes", "No"]}])
        answer = out["answers"][0]
        self.assertEqual(answer["source"], "unanswered")
        self.assertEqual(answer["value"], "")
        self.assertIn("no country", answer["note"])

    def test_an_empty_config_is_not_enabled(self):
        auth = WorkAuthorization({})
        self.assertFalse(auth.enabled)


class TestReport(unittest.TestCase):
    def setUp(self):
        self.state = fresh_state()
        self.verdict = cb.consider(self.state, cfg(), LI_URL, "Risk Manager", "Monzo")

    def test_a_submitted_application_is_recorded(self):
        out = cb.report(self.state, cfg(), self.verdict["job_id"], submitted=True,
                        fields_filled=4)
        self.assertEqual(out["recorded"], "applied")
        self.assertTrue(self.state.is_applied(self.verdict["job_id"]))
        self.assertEqual(out["applied_today"], 1)

    def test_an_unsubmitted_application_is_recorded_as_failed(self):
        out = cb.report(self.state, cfg(), self.verdict["job_id"], submitted=False,
                        failed_reason="the form rejected the resume")
        self.assertEqual(out["recorded"], "failed")
        self.assertFalse(self.state.is_applied(self.verdict["job_id"]))

    def test_reporting_closes_the_claim(self):
        cb.report(self.state, cfg(), self.verdict["job_id"], submitted=True)
        self.assertEqual(cb.open_claims(self.state), [])

    def test_a_failure_frees_the_job_to_be_retried(self):
        cb.report(self.state, cfg(), self.verdict["job_id"], submitted=False,
                  failed_reason="browser crashed")
        again = cb.consider(self.state, cfg(), LI_URL, "Risk Manager", "Monzo")
        self.assertEqual(again["verdict"], "apply")

    def test_the_title_is_taken_from_the_claim_when_omitted(self):
        cb.report(self.state, cfg(), self.verdict["job_id"], submitted=True)
        row = self.state.conn.execute(
            "SELECT title, company FROM applied_jobs WHERE job_id=?",
            (self.verdict["job_id"],)).fetchone()
        self.assertEqual(row["title"], "Risk Manager")
        self.assertEqual(row["company"], "Monzo")

    def test_report_returns_how_long_to_wait(self):
        out = cb.report(self.state, cfg(), self.verdict["job_id"], submitted=True)
        self.assertIn(out["next_action"], ("continue", "break", "stop"))
        self.assertGreaterEqual(out["wait_seconds"], 0)

    def test_reaching_the_cap_tells_the_agent_to_stop(self):
        for n in range(2):
            self.state.mark_applied(f"pad{n}", title="x", company="y")
        out = cb.report(self.state, cfg(), self.verdict["job_id"], submitted=True)
        self.assertEqual(out["next_action"], "stop")
        self.assertIn("cap", out["why"])


class TestSessionAndRunbook(unittest.TestCase):
    def setUp(self):
        self.state = fresh_state()

    def test_status_reports_the_budget(self):
        status = cb.session_status(self.state, cfg())
        self.assertEqual(status["applied_today"], 0)
        self.assertEqual(status["cap"], 3)
        self.assertEqual(status["remaining"], 3)

    def test_status_surfaces_abandoned_claims(self):
        cb.claim(self.state, "j1", title="Risk Manager", company="Monzo")
        status = cb.session_status(self.state, cfg())
        self.assertEqual(len(status["open_claims"]), 1)

    def test_the_runbook_names_the_tools_the_agent_must_call(self):
        text = cb.render_runbook(cb.session_open(self.state, cfg()))
        for tool in ("consider_job", "answers_for_job", "report_application",
                     "remember_answers"):
            self.assertIn(tool, text)

    def test_the_runbook_forbids_clicking_submit(self):
        text = cb.render_runbook(cb.session_open(self.state, cfg()))
        self.assertIn("Never click it", text)

    def test_the_runbook_includes_the_pacing_protocol(self):
        text = cb.render_runbook(cb.session_open(self.state, cfg()))
        self.assertIn("Pacing", text)
        self.assertIn("Type, do not paste", text)

    def test_the_runbook_says_stop_on_a_captcha(self):
        text = cb.render_runbook(cb.session_open(self.state, cfg())).lower()
        self.assertIn("captcha", text)
        self.assertIn("do not retry", text)

    def test_the_runbook_states_the_remaining_budget(self):
        self.state.mark_applied("j0", title="x", company="y")
        text = cb.render_runbook(cb.session_open(self.state, cfg()))
        self.assertIn("1 of 3", text)

    def test_the_runbook_warns_when_the_cap_is_already_reached(self):
        for n in range(3):
            self.state.mark_applied(f"j{n}", title="x", company="y")
        text = cb.render_runbook(cb.session_open(self.state, cfg()))
        self.assertIn("daily cap is already reached", text)

    def test_the_runbook_warns_outside_active_hours(self):
        hour = datetime.now().hour
        start = (hour + 2) % 24
        narrow = cfg(scheduling={"active_hours_start": start,
                                 "active_hours_end": (start + 1) % 24 or 1})
        text = cb.render_runbook(cb.session_open(self.state, narrow))
        self.assertIn("outside the hours", text)

    def test_the_runbook_mentions_claims_left_over(self):
        cb.claim(self.state, "j1", title="Risk Manager", company="Monzo")
        text = cb.render_runbook(cb.session_open(self.state, cfg()))
        self.assertIn("never reported back", text)

    def test_the_runbook_carries_the_search_terms(self):
        text = cb.render_runbook(cb.session_open(self.state, cfg()))
        self.assertIn("Risk Manager", text)


class TestMcpSurface(unittest.TestCase):
    def test_every_bridge_tool_is_registered(self):
        import tools_layer
        for name in ("tool_chrome_session_open", "tool_consider_job",
                     "tool_answers_for_job", "tool_report_application",
                     "tool_remember_answers", "tool_chrome_session_status"):
            self.assertIn(name, tools_layer.ALL_TOOLS)

    def test_the_mcp_server_exposes_them(self):
        source = (Path(__file__).resolve().parent.parent / "mcp_server.py"
                  ).read_text(encoding="utf-8")
        for tool in ("def consider_job", "def answers_for_job",
                     "def report_application", "def chrome_session_open"):
            self.assertIn(tool, source)

    def test_a_single_field_sent_as_an_object_is_tolerated(self):
        import tools_layer
        out = tools_layer.tool_answers_for_job('{"label": "Notice period?"}')
        self.assertIn("answers", out)

    def test_a_scalar_payload_is_rejected_with_the_shape_it_wants(self):
        import tools_layer
        self.assertIn("Expected a JSON list",
                      tools_layer.tool_answers_for_job("5"))

    def test_unparseable_json_is_reported_not_raised(self):
        import tools_layer
        self.assertIn("Could not answer",
                      tools_layer.tool_answers_for_job("{not json"))

    def test_the_claims_table_belongs_to_a_reset_scope(self):
        # The reset scopes must cover every table, or `lla reset` silently
        # misses this one.
        import state_reset
        self.assertIn("agent_claims", state_reset.known_tables())


if __name__ == "__main__":
    unittest.main(verbosity=2)
