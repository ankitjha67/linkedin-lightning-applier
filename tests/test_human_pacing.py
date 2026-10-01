"""Humanised pacing — human_pacing.py and the Claude-for-Chrome protocol.

Two drivers share this browser: the Selenium bot and Claude for Chrome reading
a prompt. They should behave the same, so the numbers live in one place and a
test fails when the document stops agreeing with the code.

The behavioural properties worth pinning: delays scale the right way (a
"careful" run must be slower than a "fast" one, which it was not), long prose
is typed rather than pasted, and a page asking whether we are a person stops
the run instead of being retried.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import human_pacing as hp  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CHROME_DOC = REPO / "tests" / "e2e" / "CLAUDE_IN_CHROME.md"

ESSAY = ("I'm drawn to this role because the team owns the risk models end to "
         "end, which is what I did at my last job. I would bring the same "
         "approach here.")


class TestPatienceScalesTheRightWay(unittest.TestCase):
    """`careful` must be slower than `normal`, which must be slower than `fast`.

    This was inverted: the factor was named `speed` and divided where it should
    have multiplied, so "careful" was quietly the fastest setting of the three.
    """

    def test_the_profiles_are_ordered(self):
        self.assertGreater(hp.PROFILES["careful"], hp.PROFILES["normal"])
        self.assertGreater(hp.PROFILES["normal"], hp.PROFILES["fast"])

    def test_more_patience_means_longer_delays(self):
        quick = hp.pause(3, 8, hp.PROFILES["fast"], sleep=False)
        slow = hp.pause(3, 8, hp.PROFILES["careful"], sleep=False)
        # Ranges overlap, so compare the bounds rather than two samples.
        self.assertEqual(hp._scaled(3, 8, 2.0), (6.0, 16.0))
        self.assertGreater(hp._scaled(3, 8, 1.6)[0], hp._scaled(3, 8, 0.65)[0])
        self.assertGreaterEqual(slow, 3 * hp.PROFILES["careful"])
        self.assertLessEqual(quick, 8 * hp.PROFILES["fast"])

    def test_more_patience_means_longer_reading(self):
        text = "word " * 600
        self.assertGreater(hp.dwell_for_text(text, 1.6),
                           hp.dwell_for_text(text, 0.65))

    def test_more_patience_means_slower_typing(self):
        self.assertGreater(hp.estimate_typing_seconds(ESSAY, 1.6),
                           hp.estimate_typing_seconds(ESSAY, 0.65))

    def test_more_patience_means_longer_breaks(self):
        self.assertGreater(hp.break_seconds(2.0), hp.break_seconds(0.5))

    def test_the_rendered_profiles_are_ordered_too(self):
        def first_gap(profile):
            text = " ".join(hp.render_protocol(profile).split())
            m = re.search(r"between jobs\.\*\* (\d+)-(\d+) seconds", text)
            self.assertIsNotNone(m, f"no gap figure in the {profile} protocol")
            return int(m.group(1))
        self.assertGreater(first_gap("careful"), first_gap("normal"))
        self.assertGreater(first_gap("normal"), first_gap("fast"))


class TestReadingDwell(unittest.TestCase):
    def test_longer_postings_take_longer_to_read(self):
        self.assertGreater(hp.dwell_for_text("word " * 800),
                           hp.dwell_for_text("word " * 100))

    def test_even_empty_text_takes_a_moment(self):
        self.assertGreaterEqual(hp.dwell_for_text(""), hp.MIN_DWELL)
        self.assertGreaterEqual(hp.dwell_for_text(None), hp.MIN_DWELL)

    def test_the_cap_does_not_contradict_the_documented_example(self):
        """MAX_DWELL was 45s while the protocol promised 164s for 600 words."""
        documented = 600 / hp.SKIM_WPM * 60 * hp.PROFILES["careful"]
        self.assertGreaterEqual(
            hp.MAX_DWELL, documented,
            "MAX_DWELL caps reading below the time render_protocol() quotes")

    def test_dwell_is_capped(self):
        # Nobody stares at one posting for ten minutes.
        self.assertLessEqual(hp.dwell_for_text("word " * 100_000), hp.MAX_DWELL)

    def test_a_600_word_posting_takes_a_believable_time(self):
        samples = [hp.dwell_for_text("word " * 600) for _ in range(50)]
        self.assertTrue(all(60 < s < hp.MAX_DWELL for s in samples),
                        f"unbelievable dwell range: {min(samples)}-{max(samples)}")

    def test_reading_is_not_a_constant_rate(self):
        samples = {round(hp.dwell_for_text("word " * 300), 4) for _ in range(20)}
        self.assertGreater(len(samples), 1, "dwell time never varies")


class TestTyping(unittest.TestCase):
    def test_every_character_gets_a_delay(self):
        pairs = list(hp.typing_delays("hello"))
        self.assertEqual("".join(c for c, _d in pairs), "hello")
        self.assertTrue(all(d > 0 for _c, d in pairs))

    def test_a_sentence_end_pauses_longer_than_a_letter(self):
        # Averaged, because each delay is jittered.
        def mean_for(text, char):
            runs = [d for _ in range(60)
                    for c, d in hp.typing_delays(text) if c == char]
            return sum(runs) / len(runs)
        self.assertGreater(mean_for("ab. cd", "."), mean_for("ab. cd", "a"))

    def test_an_essay_takes_the_better_part_of_a_minute(self):
        seconds = hp.estimate_typing_seconds(ESSAY)
        self.assertGreater(seconds, 20, "an essay answer typed too fast to be human")
        self.assertLess(seconds, 200, "implausibly slow")

    def test_the_estimate_matches_what_sampling_produces(self):
        # The estimate is what the documentation quotes, so it has to be honest.
        sampled = [hp.typing_seconds(ESSAY) for _ in range(300)]
        mean = sum(sampled) / len(sampled)
        estimate = hp.estimate_typing_seconds(ESSAY)
        self.assertAlmostEqual(estimate, mean, delta=mean * 0.15)

    def test_the_estimate_is_deterministic(self):
        self.assertEqual(hp.estimate_typing_seconds(ESSAY),
                         hp.estimate_typing_seconds(ESSAY))

    def test_empty_text_types_instantly(self):
        self.assertEqual(hp.estimate_typing_seconds(""), 0.0)
        self.assertEqual(list(hp.typing_delays("")), [])
        self.assertEqual(list(hp.typing_delays(None)), [])


class FakeField:
    def __init__(self):
        self.sent = []

    def send_keys(self, value):
        self.sent.append(value)

    @property
    def text(self):
        return "".join(self.sent)


class TestTypeLikeHuman(unittest.TestCase):
    def test_the_whole_string_arrives(self):
        field = FakeField()
        hp.type_like_human(field, "Hello there", patience=0.001)
        self.assertEqual(field.text, "Hello there")

    def test_it_is_sent_character_by_character(self):
        field = FakeField()
        hp.type_like_human(field, "abcdef", patience=0.001)
        self.assertEqual(len(field.sent), 6,
                         "the value arrived in fewer events than characters")

    def test_a_time_budget_still_delivers_the_whole_value(self):
        # The budget must never truncate the answer — a half-typed reply to an
        # employer is worse than a fast one.
        field = FakeField()
        hp.type_like_human(field, "x" * 400, patience=1.0, max_seconds=0.05)
        self.assertEqual(field.text, "x" * 400)

    def test_empty_text_sends_nothing(self):
        field = FakeField()
        hp.type_like_human(field, "", patience=0.001)
        self.assertEqual(field.sent, [])


class TestLongAnswersAreTypedByTheBot(unittest.TestCase):
    """The Selenium path had the same tell: one send_keys for a whole essay."""

    def test_linkedin_routes_long_values_through_human_typing(self):
        source = (REPO / "linkedin.py").read_text(encoding="utf-8")
        self.assertIn("type_like_human", source,
                      "_fill_field no longer types long answers like a person")
        self.assertIn("HUMAN_TYPING_MIN_CHARS", source)

    def test_short_fields_are_still_filled_directly(self):
        # A name or an email typed character-by-character buys nothing and
        # costs time on every single application. Read from source rather than
        # imported: linkedin.py needs undetected_chromedriver, which is not a
        # test dependency.
        source = (REPO / "linkedin.py").read_text(encoding="utf-8")
        match = re.search(r"^HUMAN_TYPING_MIN_CHARS\s*=\s*(\d+)", source, re.M)
        self.assertIsNotNone(match, "HUMAN_TYPING_MIN_CHARS is gone")
        self.assertGreaterEqual(int(match.group(1)), 40)


class TestSessionShape(unittest.TestCase):
    def test_a_break_is_needed_after_enough_applications(self):
        self.assertTrue(hp.needs_break(99))

    def test_no_break_is_needed_at_the_start(self):
        self.assertFalse(hp.needs_break(0))

    def test_breaks_are_minutes_not_seconds(self):
        self.assertGreaterEqual(hp.break_seconds(), hp.BREAK_MINUTES[0] * 60)

    def test_the_hourly_rate_is_below_the_daily_cap(self):
        # Otherwise the "take breaks" advice is never reachable.
        self.assertLess(hp.MAX_APPLICATIONS_PER_HOUR, 40)


class TestChallengeDetection(unittest.TestCase):
    """A page asking whether we are a person ends the run."""

    def test_captcha_wording_is_caught(self):
        for text in ("Please complete the CAPTCHA",
                     "Verify you are human",
                     "Are you a robot?",
                     "We've restricted your account",
                     "Unusual activity detected on your account",
                     "checkpoint/challenge"):
            self.assertTrue(hp.looks_like_a_challenge(text), text)

    def test_ordinary_pages_are_not_challenges(self):
        for text in ("Apply for this job",
                     "Senior Risk Manager — Monzo",
                     "Your application has been submitted",
                     ""):
            self.assertFalse(hp.looks_like_a_challenge(text), text)

    def test_detection_is_case_insensitive(self):
        self.assertTrue(hp.looks_like_a_challenge("ReCaPtChA required"))

    def test_the_protocol_says_stop_rather_than_retry(self):
        protocol = " ".join(hp.render_protocol().lower().split())
        self.assertIn("do not wait and retry", protocol)
        self.assertIn("do not attempt to solve a captcha", protocol)


class TestActiveHoursWarning(unittest.TestCase):
    """Per-click pacing cannot disguise a schedule no person keeps."""

    def test_round_the_clock_is_flagged(self):
        warning = hp.active_hours_warning(
            {"scheduling": {"active_hours_start": 0, "active_hours_end": 24}})
        self.assertIn("3am", warning)

    def test_a_normal_working_day_is_not_flagged(self):
        self.assertEqual(hp.active_hours_warning(
            {"scheduling": {"active_hours_start": 8, "active_hours_end": 23}}), "")

    def test_an_implausibly_long_day_is_flagged(self):
        self.assertIn("longer day", hp.active_hours_warning(
            {"scheduling": {"active_hours_start": 3, "active_hours_end": 23}}))

    def test_a_missing_config_defaults_to_the_round_the_clock_warning(self):
        # The shipped default really is 0-24, so silence here would be wrong.
        self.assertTrue(hp.active_hours_warning({}))
        self.assertTrue(hp.active_hours_warning(None))

    def test_the_shipped_config_default_is_what_the_warning_describes(self):
        import yaml
        cfg = yaml.safe_load(
            (REPO / "config.example.yaml").read_text(encoding="utf-8"))
        self.assertTrue(hp.active_hours_warning(cfg),
                        "config.example.yaml no longer ships 0-24; update the "
                        "warning or drop it")


class TestProtocolText(unittest.TestCase):
    def test_it_is_deterministic(self):
        self.assertEqual(hp.render_protocol(), hp.render_protocol())

    def test_it_covers_every_rule(self):
        protocol = " ".join(hp.render_protocol().lower().split())
        for topic in ("read before you act", "type, do not paste",
                      "leave gaps", "scroll to what you click",
                      "one tab", "take breaks", "human hours"):
            self.assertIn(topic, protocol)

    def test_it_forbids_faking_mistakes(self):
        # Inventing typos to look human would corrupt a real application.
        protocol = " ".join(hp.render_protocol().lower().split())
        self.assertIn("never fake mistakes", protocol)

    def test_it_states_that_slower_is_the_point(self):
        protocol = " ".join(hp.render_protocol().lower().split())
        self.assertIn("slower and fewer", protocol)

    def test_an_unknown_profile_falls_back_to_normal(self):
        self.assertEqual(hp.render_protocol("nonsense"), hp.render_protocol("normal"))

    def test_lines_are_readable_when_pasted(self):
        for line in hp.render_protocol().splitlines():
            self.assertLessEqual(len(line), 100, f"line too long to paste: {line}")


class TestDocStaysInSyncWithTheCode(unittest.TestCase):
    """The generated block in CLAUDE_IN_CHROME.md must match the generator.

    The whole point of generating it is that the prompt Claude reads and the
    delays the bot uses cannot drift apart. Regenerate with `lla pacing --write`.
    """

    PATTERN = re.compile(
        r"<!-- BEGIN GENERATED PACING[^>]*-->\n(.*?)\n<!-- END GENERATED PACING -->",
        re.S)

    def test_the_document_has_the_generated_block(self):
        self.assertTrue(CHROME_DOC.exists())
        self.assertIsNotNone(
            self.PATTERN.search(CHROME_DOC.read_text(encoding="utf-8")))

    def test_the_block_matches_the_generator_exactly(self):
        match = self.PATTERN.search(CHROME_DOC.read_text(encoding="utf-8"))
        self.assertEqual(
            match.group(1), hp.render_protocol(),
            "the pacing block has drifted from human_pacing.render_protocol(); "
            "regenerate it with `lla pacing --write`")

    def test_the_document_tells_the_reader_to_paste_pacing_first(self):
        text = CHROME_DOC.read_text(encoding="utf-8")
        self.assertIn("paste this FIRST", text)

    def test_the_document_still_forbids_clicking_submit(self):
        # The pacing section must not have displaced the safety rule.
        text = CHROME_DOC.read_text(encoding="utf-8").lower()
        self.assertIn("never click", text)
        self.assertIn("submit", text)


class TestCliSurface(unittest.TestCase):
    def test_pacing_is_registered(self):
        import cli
        self.assertIn("pacing", cli.COMMAND_MAP)

    def test_write_is_idempotent(self):
        import argparse

        import cli
        before = CHROME_DOC.read_text(encoding="utf-8")
        args = argparse.Namespace(profile="normal", write=True, quiet=True,
                                  config="config.yaml")
        self.assertEqual(cli.cmd_pacing(args), 0)
        self.addCleanup(CHROME_DOC.write_text, before, "utf-8")
        self.assertEqual(CHROME_DOC.read_text(encoding="utf-8"), before,
                         "`lla pacing --write` changed a document already in sync")


if __name__ == "__main__":
    unittest.main(verbosity=2)
