"""robots.txt compliance (RFC 9309) — tools/robots_check.py.

The point of these tests is that the checker fails CLOSED. Every ambiguity —
an unreadable robots.txt, a rule we cannot parse, a tie between Allow and
Disallow — has to come out as "not allowed", because the cost of being wrong
in the other direction is fetching a page a site has explicitly refused us.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import robots_check  # noqa: E402
from tools.robots_check import (  # noqa: E402
    Verdict,
    clear_cache,
    evaluate,
    parse_robots,
    robots_allows,
)

AGENT = "LightningApplier"


def verdict(text, path, agent=AGENT):
    return evaluate(parse_robots(text, agent), path)


class TestPathPrecedence(unittest.TestCase):
    """RFC 9309 §2.2.2: the longest matching rule wins; ties go to Disallow."""

    RULES = "User-agent: *\nDisallow: /jobs/\nAllow: /jobs/public/\n"

    def test_longer_allow_beats_shorter_disallow(self):
        self.assertTrue(verdict(self.RULES, "/jobs/public/eng"))

    def test_shorter_allow_does_not_rescue_deeper_disallow(self):
        self.assertFalse(verdict(self.RULES, "/jobs/private/eng"))

    def test_equal_length_tie_goes_to_disallow(self):
        v = verdict("User-agent: *\nAllow: /a/b\nDisallow: /a/b\n", "/a/b")
        self.assertFalse(v)
        self.assertIn("Disallow", v.rule)

    def test_unmatched_path_is_allowed(self):
        self.assertTrue(verdict(self.RULES, "/about"))

    def test_rule_order_does_not_matter(self):
        flipped = "User-agent: *\nAllow: /jobs/public/\nDisallow: /jobs/\n"
        self.assertTrue(verdict(flipped, "/jobs/public/eng"))
        self.assertFalse(verdict(flipped, "/jobs/other"))


class TestEmptyValues(unittest.TestCase):
    """An empty value means opposite things for Allow and Disallow."""

    def test_empty_disallow_allows_everything(self):
        self.assertTrue(verdict("User-agent: *\nDisallow:\n", "/anything/at/all"))

    def test_empty_allow_grants_nothing(self):
        # The bare "Allow:" must not override the Disallow that follows.
        self.assertFalse(verdict("User-agent: *\nAllow:\nDisallow: /x\n", "/x"))

    def test_empty_disallow_loses_to_a_real_rule(self):
        self.assertFalse(verdict("User-agent: *\nDisallow:\nDisallow: /x\n", "/x/1"))


class TestBlankLineInsideGroup(unittest.TestCase):
    """The reason this file exists instead of urllib.robotparser.

    The stdlib parser treats a blank line as ending the group and silently
    drops every rule after it — turning a Disallow into permission.
    """

    TEXT = "User-agent: *\nDisallow: /private/\n\nDisallow: /secret/\n"

    def test_rules_after_a_blank_line_still_apply(self):
        self.assertFalse(verdict(self.TEXT, "/secret/x"))
        self.assertFalse(verdict(self.TEXT, "/private/x"))

    def test_stdlib_really_does_fail_open_here(self):
        # If a future Python fixes this, we can drop the hand-rolled parser.
        import urllib.robotparser as rp
        p = rp.RobotFileParser()
        p.parse(self.TEXT.splitlines())
        self.assertTrue(p.can_fetch(AGENT, "/secret/x"),
                        "stdlib behaviour changed — re-evaluate parse_robots()")


class TestWildcards(unittest.TestCase):
    def test_star_matches_any_sequence(self):
        self.assertFalse(verdict("User-agent: *\nDisallow: /*/edit\n", "/a/b/edit"))

    def test_dollar_anchors_the_end(self):
        text = "User-agent: *\nDisallow: /*.pdf$\n"
        self.assertFalse(verdict(text, "/files/cv.pdf"))
        self.assertTrue(verdict(text, "/files/cv.pdf?dl=1"))

    def test_regex_metacharacters_are_literal(self):
        # '.' and '+' must not behave as regex operators.
        text = "User-agent: *\nDisallow: /a.b+c\n"
        self.assertFalse(verdict(text, "/a.b+c"))
        self.assertTrue(verdict(text, "/axbxc"))

    def test_prefix_match_is_not_required_to_be_a_whole_segment(self):
        self.assertFalse(verdict("User-agent: *\nDisallow: /jo\n", "/jobs"))


class TestAgentGroups(unittest.TestCase):
    TWO_GROUPS = ("User-agent: *\nDisallow: /\n\n"
                  "User-agent: LightningApplier\nDisallow: /admin/\n")

    def test_our_group_replaces_the_star_group(self):
        self.assertTrue(verdict(self.TWO_GROUPS, "/jobs"))
        self.assertFalse(verdict(self.TWO_GROUPS, "/admin/x"))

    def test_other_agents_fall_back_to_star(self):
        self.assertFalse(verdict(self.TWO_GROUPS, "/jobs", agent="SomeoneElse"))

    def test_version_suffix_still_matches_the_token(self):
        self.assertFalse(verdict(self.TWO_GROUPS, "/admin/x",
                                 agent="LightningApplier/2.9"))

    def test_prefix_must_end_on_a_token_boundary(self):
        # A group addressed to "li" must NOT capture "lightningapplier".
        text = "User-agent: li\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
        self.assertTrue(verdict(text, "/jobs"))

    def test_hyphenated_token_falls_back_to_its_base(self):
        text = "User-agent: lightning\nDisallow: /admin/\n"
        self.assertFalse(verdict(text, "/admin/x", agent="lightning-applier"))

    def test_most_specific_token_wins(self):
        text = ("User-agent: lightning\nDisallow: /\n\n"
                "User-agent: lightningapplier\nDisallow: /admin/\n")
        self.assertTrue(verdict(text, "/jobs"))
        self.assertFalse(verdict(text, "/admin/x"))

    def test_several_agents_share_one_group(self):
        text = "User-agent: foo\nUser-agent: LightningApplier\nDisallow: /no/\n"
        self.assertFalse(verdict(text, "/no/x"))

    def test_repeated_token_merges_its_groups(self):
        text = ("User-agent: lightningapplier\nDisallow: /a/\n\n"
                "User-agent: lightningapplier\nDisallow: /b/\n")
        self.assertFalse(verdict(text, "/a/1"))
        self.assertFalse(verdict(text, "/b/1"))

    def test_agent_matching_is_case_insensitive(self):
        text = "User-agent: LIGHTNINGAPPLIER\nDisallow: /x/\n"
        self.assertFalse(verdict(text, "/x/1", agent="lightningapplier"))


class TestSyntaxTolerance(unittest.TestCase):
    def test_comments_and_field_case_are_handled(self):
        text = "USER-AGENT: *   # everyone\nDISALLOW: /c/   # nope\n"
        self.assertFalse(verdict(text, "/c/1"))

    def test_unknown_fields_are_ignored(self):
        text = ("User-agent: *\nCrawl-delay: 10\nSitemap: https://x/s.xml\n"
                "Disallow: /c/\n")
        self.assertFalse(verdict(text, "/c/1"))

    def test_percent_encoded_paths_compare_decoded(self):
        self.assertFalse(verdict("User-agent: *\nDisallow: /a b/\n", "/a%20b/x"))

    def test_empty_file_allows_everything(self):
        self.assertTrue(robots_check.evaluate(parse_robots("", AGENT), "/x"))

    def test_rules_before_any_user_agent_are_ignored(self):
        # A stray Disallow with no group heading belongs to nobody.
        self.assertEqual(parse_robots("Disallow: /\n", AGENT), [])


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code, self.text = status_code, text


class TestNetworkFailureModes(unittest.TestCase):
    """Everything that is not a clean answer must fail closed."""

    def setUp(self):
        clear_cache()
        self.addCleanup(clear_cache)

    def _with_fetch(self, fn):
        original = robots_check._fetch_robots_uncached
        robots_check._fetch_robots_uncached = fn
        self.addCleanup(setattr, robots_check, "_fetch_robots_uncached", original)

    def _serving(self, resp=None, exc=None):
        import requests

        def fake_get(url, **kw):
            if exc:
                raise exc
            return resp
        original = requests.get
        requests.get = fake_get
        self.addCleanup(setattr, requests, "get", original)

    def test_404_means_no_policy_means_permission(self):
        self._serving(resp=_Resp(404))
        v = robots_allows("https://example.com/jobs")
        self.assertTrue(v)
        self.assertIn("no robots.txt", v.reason)

    def test_410_also_means_permission(self):
        self._serving(resp=_Resp(410))
        self.assertTrue(robots_allows("https://example.com/jobs"))

    def test_500_is_not_permission(self):
        self._serving(resp=_Resp(500))
        v = robots_allows("https://example.com/jobs")
        self.assertFalse(v)
        self.assertIn("not confirmed", v.reason)

    def test_403_is_not_permission(self):
        self._serving(resp=_Resp(403))
        self.assertFalse(robots_allows("https://example.com/jobs"))

    def test_a_timeout_is_not_permission(self):
        self._serving(exc=TimeoutError("timed out"))
        v = robots_allows("https://example.com/jobs")
        self.assertFalse(v)
        self.assertIn("not confirmed", v.reason)

    def test_relative_url_is_refused(self):
        v = robots_allows("/jobs/123")
        self.assertFalse(v)
        self.assertIn("absolute", v.reason)

    def test_200_with_rules_is_applied(self):
        self._serving(resp=_Resp(200, "User-agent: *\nDisallow: /jobs/\n"))
        self.assertFalse(robots_allows("https://example.com/jobs/1"))
        self.assertTrue(robots_allows("https://example.com/about"))

    def test_robots_is_fetched_once_per_host(self):
        calls = []

        def counting(base, agent):
            calls.append(base)
            return "User-agent: *\nDisallow: /x/\n", None
        self._with_fetch(counting)

        for path in ("/x/1", "/x/2", "/ok"):
            robots_allows("https://example.com" + path)
        self.assertEqual(len(calls), 1, "robots.txt refetched for the same host")

        robots_allows("https://other.example.com/x/1")
        self.assertEqual(len(calls), 2, "cache must be per-host")

    def test_cache_is_keyed_by_agent_too(self):
        calls = []

        def counting(base, agent):
            calls.append(agent)
            return "", None
        self._with_fetch(counting)
        robots_allows("https://example.com/a", agent="A")
        robots_allows("https://example.com/a", agent="B")
        self.assertEqual(calls, ["A", "B"])


class TestBatchAndCLI(unittest.TestCase):
    def setUp(self):
        clear_cache()
        self.addCleanup(clear_cache)
        original = robots_check._fetch_robots_uncached
        self.hosts = []

        def fake(base, agent):
            self.hosts.append(base)
            return "User-agent: *\nDisallow: /no/\n", None
        robots_check._fetch_robots_uncached = fake
        self.addCleanup(setattr, robots_check, "_fetch_robots_uncached", original)

    def test_check_urls_returns_a_verdict_per_url(self):
        urls = ["https://e.com/ok", "https://e.com/no/x"]
        results = robots_check.check_urls(urls)
        self.assertEqual([u for u, _ in results], urls)
        self.assertTrue(results[0][1])
        self.assertFalse(results[1][1])

    def test_check_urls_fetches_each_host_once(self):
        robots_check.check_urls(["https://e.com/a", "https://e.com/b",
                                 "https://f.com/a"])
        self.assertEqual(sorted(set(self.hosts)), ["https://e.com", "https://f.com"])
        self.assertEqual(len(self.hosts), 2)

    def test_cli_exit_code_reports_denial(self):
        import argparse

        import cli
        args = argparse.Namespace(urls=["https://e.com/ok"], file=None, agent=None)
        self.assertEqual(cli.cmd_robots(args), 0)
        args.urls = ["https://e.com/ok", "https://e.com/no/x"]
        self.assertEqual(cli.cmd_robots(args), 1)

    def test_cli_without_urls_is_a_usage_error(self):
        import argparse

        import cli
        args = argparse.Namespace(urls=[], file=None, agent=None)
        self.assertEqual(cli.cmd_robots(args), 2)


class TestVerdict(unittest.TestCase):
    def test_verdict_is_falsy_when_denied(self):
        self.assertFalse(bool(Verdict(False, "no")))
        self.assertTrue(bool(Verdict(True, "yes")))

    def test_repr_states_the_reason(self):
        self.assertIn("blocked", repr(Verdict(False, "blocked")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
