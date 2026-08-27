"""Profile expansion from public sources — profile_expand.py.

Two properties matter more than any individual extraction here:

  * nothing enters your profile without a source URL, and
  * nothing you wrote yourself is ever overwritten.

The skill-matching tests are the other half. A false positive is not a
cosmetic bug: it puts a language you do not know into a real job application.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import profile_expand as pe  # noqa: E402


class TestSkillMatching(unittest.TestCase):
    def test_unambiguous_terms_match_in_prose(self):
        found = pe.find_skills("I build services with Django and PostgreSQL.")
        self.assertIn("django", found)
        self.assertIn("postgresql", found)

    def test_english_words_are_not_skills(self):
        # "go" and "excel" here are verbs, not a language and a spreadsheet.
        self.assertEqual(pe.find_skills("You should go to the store and excel at it"), [])

    def test_single_letter_r_needs_a_list_context(self):
        self.assertNotIn("r", pe.find_skills("The letter R appears in this document"))

    def test_ambiguous_terms_count_inside_a_skills_list(self):
        found = pe.find_skills("Skills: Python, R, Go, SQL, Docker")
        for term in ("python", "r", "go", "sql", "docker"):
            self.assertIn(term, found)

    def test_pipe_separated_lists_work(self):
        found = pe.find_skills("Languages | Python | Go | Rust | Java")
        self.assertIn("go", found)
        self.assertIn("rust", found)

    def test_ambiguous_term_alone_is_not_enough(self):
        # No other recognised skill nearby, so the list context is unproven.
        self.assertEqual(pe.find_skills("Options: R, or something else entirely"), [])

    def test_longer_terms_win_over_their_prefixes(self):
        found = pe.find_skills("We use GitHub Actions for CI")
        self.assertIn("github actions", found)

    def test_plus_and_hash_are_matched_literally(self):
        found = pe.find_skills("Stack: C++, C#, Python, Docker")
        self.assertIn("c++", found)
        self.assertIn("c#", found)

    def test_substrings_of_other_words_do_not_match(self):
        self.assertEqual(pe.find_skills("The rusty gopher javascripted"), [])

    def test_empty_input_is_safe(self):
        self.assertEqual(pe.find_skills(""), [])
        self.assertEqual(pe.find_skills(None), [])


class TestHtmlHandling(unittest.TestCase):
    def test_title_is_extracted_and_unescaped(self):
        self.assertEqual(pe.page_title("<html><title>Ada &amp; Co</title></html>"),
                         "Ada & Co")

    def test_missing_title_is_empty(self):
        self.assertEqual(pe.page_title("<html></html>"), "")

    def test_script_and_style_content_is_dropped(self):
        html = "<style>.a{color:red}</style><script>var x=1</script><p>Hello</p>"
        text = pe.html_to_text(html)
        self.assertIn("Hello", text)
        self.assertNotIn("color", text)
        self.assertNotIn("var x", text)

    def test_entities_are_decoded(self):
        self.assertIn("R&D", pe.html_to_text("<p>R&amp;D</p>"))


class TestDiscovery(unittest.TestCase):
    def test_urls_are_found_in_config_answers(self):
        cfg = {"question_answers": {"github": "https://github.com/ada",
                                    "portfolio": "https://ada.dev"}}
        urls = pe.discover_sources(cfg)
        self.assertIn("https://github.com/ada", urls)
        self.assertIn("https://ada.dev", urls)

    def test_urls_are_found_in_cv_text(self):
        cfg = {"ai": {"cv_text": "Ada Lovelace — https://ada.dev — github.com/ada"}}
        urls = pe.discover_sources(cfg)
        self.assertIn("https://ada.dev", urls)
        self.assertIn("https://github.com/ada", urls)

    def test_linkedin_is_excluded(self):
        # LinkedIn is driven through the logged-in browser, not fetched here.
        cfg = {"question_answers": {"linkedin": "https://linkedin.com/in/ada"}}
        self.assertEqual(pe.discover_sources(cfg), [])

    def test_duplicates_are_collapsed(self):
        cfg = {"question_answers": {"a": "https://ada.dev", "b": "https://ada.dev"}}
        self.assertEqual(pe.discover_sources(cfg), ["https://ada.dev"])

    def test_trailing_punctuation_is_stripped(self):
        cfg = {"ai": {"cv_text": "See https://ada.dev, and more."}}
        self.assertIn("https://ada.dev", pe.discover_sources(cfg))

    def test_empty_config_finds_nothing(self):
        self.assertEqual(pe.discover_sources({}), [])
        self.assertEqual(pe.discover_sources(None), [])

    def test_source_kind_routing(self):
        self.assertEqual(pe.source_kind("https://github.com/ada"), "github")
        self.assertEqual(pe.source_kind("https://scholar.google.com/citations?user=x"),
                         "scholar")
        self.assertEqual(pe.source_kind("https://kaggle.com/ada"), "kaggle")
        self.assertEqual(pe.source_kind("https://ada.dev"), "website")


class TestRobotsGate(unittest.TestCase):
    """Enrichment reads other people's servers. It asks first."""

    def _robots(self, allowed, reason="test"):
        original = pe.robots_permits
        pe.robots_permits = lambda url: (allowed, reason)
        self.addCleanup(setattr, pe, "robots_permits", original)

    def test_a_disallowed_url_is_never_fetched(self):
        self._robots(False, "Disallow: /")
        fetched = []
        import requests
        original = requests.get
        requests.get = lambda *a, **k: fetched.append(a) or None
        self.addCleanup(setattr, requests, "get", original)

        report = pe.Report()
        self.assertIsNone(pe.fetch_text("https://x.com/p", report))
        self.assertEqual(fetched, [], "fetched a URL robots.txt disallowed")
        self.assertEqual(len(report.skipped), 1)
        self.assertIn("robots.txt", report.skipped[0][1])

    def test_an_allowed_url_is_fetched_and_recorded(self):
        self._robots(True)

        class R:
            status_code, text = 200, "<title>Hi</title>"
        import requests
        original = requests.get
        requests.get = lambda *a, **k: R()
        self.addCleanup(setattr, requests, "get", original)

        report = pe.Report()
        self.assertEqual(pe.fetch_text("https://x.com/p", report), "<title>Hi</title>")
        self.assertEqual(report.sources, ["https://x.com/p"])

    def test_non_200_is_recorded_as_skipped(self):
        self._robots(True)

        class R:
            status_code, text = 429, ""
        import requests
        original = requests.get
        requests.get = lambda *a, **k: R()
        self.addCleanup(setattr, requests, "get", original)

        report = pe.Report()
        self.assertIsNone(pe.fetch_text("https://x.com/p", report))
        self.assertIn("429", report.skipped[0][1])

    def test_a_network_error_is_recorded_not_raised(self):
        self._robots(True)
        import requests
        original = requests.get

        def boom(*a, **k):
            raise TimeoutError("nope")
        requests.get = boom
        self.addCleanup(setattr, requests, "get", original)

        report = pe.Report()
        self.assertIsNone(pe.fetch_text("https://x.com/p", report))
        self.assertIn("fetch failed", report.skipped[0][1])


FAKE_REPOS = [
    {"name": "pipeline", "language": "Python", "fork": False,
     "stargazers_count": 40, "forks_count": 9, "description": "ETL tool",
     "html_url": "https://github.com/ada/pipeline"},
    {"name": "notes", "language": "Python", "fork": False,
     "stargazers_count": 1, "forks_count": 0, "description": "",
     "html_url": "https://github.com/ada/notes"},
    {"name": "charts", "language": "TypeScript", "fork": False,
     "stargazers_count": 3, "forks_count": 0, "description": "d3 charts",
     "html_url": "https://github.com/ada/charts"},
    {"name": "forked-thing", "language": "Go", "fork": True,
     "stargazers_count": 0, "forks_count": 0, "description": "",
     "html_url": "https://github.com/ada/forked-thing"},
]


class TestGitHubSource(unittest.TestCase):
    """The GitHub API is published for this; it is not scraped."""

    def setUp(self):
        import github_enrich
        original = github_enrich.fetch_repos
        github_enrich.fetch_repos = lambda user, **k: list(FAKE_REPOS)
        self.addCleanup(setattr, github_enrich, "fetch_repos", original)

    def test_languages_come_from_original_repos_only(self):
        report = pe.Report()
        pe.expand_github("https://github.com/ada", report)
        skills = [f for f in report.findings if f.field == "skills"]
        self.assertEqual(len(skills), 1)
        # Python (2 repos) before TypeScript (1); Go is only in a fork.
        self.assertEqual(skills[0].value, "Python, TypeScript")

    def test_every_finding_carries_a_source(self):
        report = pe.Report()
        pe.expand_github("https://github.com/ada", report)
        self.assertTrue(report.findings)
        for f in report.findings:
            self.assertTrue(f.source.startswith("http"), f"no source on {f!r}")

    def test_projects_are_reported_with_their_repo_url(self):
        report = pe.Report()
        pe.expand_github("https://github.com/ada", report)
        projects = [f for f in report.findings if f.field == "projects"]
        self.assertTrue(projects)
        names = {f.value for f in projects}
        self.assertIn("pipeline", names)
        for f in projects:
            self.assertIn("github.com/ada/", f.source)

    def test_undescribed_repo_is_lower_confidence(self):
        report = pe.Report()
        pe.expand_github("https://github.com/ada", report)
        by_name = {f.value: f for f in report.findings if f.field == "projects"}
        if "notes" in by_name:
            self.assertEqual(by_name["notes"].confidence, "medium")

    def test_no_repos_is_reported_not_crashed(self):
        import github_enrich
        github_enrich.fetch_repos = lambda user, **k: []
        report = pe.Report()
        pe.expand_github("https://github.com/ada", report)
        self.assertEqual(report.findings, [])
        self.assertTrue(report.skipped)

    def test_unparseable_url_is_ignored(self):
        report = pe.Report()
        pe.expand_github("not a github url at all", report)
        self.assertEqual(report.findings, [])


class TestApplyFindings(unittest.TestCase):
    def _report(self):
        r = pe.Report()
        r.add(pe.Finding("skills", "python, docker", "https://github.com/ada"))
        r.add(pe.Finding("skills", "python, terraform", "https://ada.dev",
                         confidence="medium"))
        r.add(pe.Finding("headline", "Ada — Data Engineer", "https://ada.dev",
                         confidence="medium"))
        return r

    def test_blank_fields_are_filled(self):
        cfg, changes = pe.apply_findings({}, self._report())
        self.assertEqual(cfg["question_answers"]["headline"], "Ada — Data Engineer")
        self.assertIn("python", cfg["question_answers"]["skills"])
        self.assertTrue(changes)

    def test_existing_answers_are_never_overwritten(self):
        original = "Whatever I chose to write"
        cfg = {"question_answers": {"skills": original, "headline": "Mine"}}
        out, changes = pe.apply_findings(cfg, self._report())
        self.assertEqual(out["question_answers"]["skills"], original)
        self.assertEqual(out["question_answers"]["headline"], "Mine")
        self.assertEqual(changes, [])

    def test_skills_are_merged_and_deduped_across_sources(self):
        cfg, _ = pe.apply_findings({}, self._report())
        skills = cfg["question_answers"]["skills"]
        self.assertEqual(skills.lower().count("python"), 1)
        self.assertIn("docker", skills)
        self.assertIn("terraform", skills)

    def test_confidence_floor_is_respected(self):
        r = pe.Report()
        r.add(pe.Finding("skills", "cobol", "https://x.dev", confidence="low"))
        cfg, changes = pe.apply_findings({}, r, min_confidence="medium")
        self.assertEqual(changes, [])
        cfg, changes = pe.apply_findings({}, r, min_confidence="low")
        self.assertEqual(cfg["question_answers"]["skills"], "cobol")

    def test_every_change_records_where_it_came_from(self):
        _, changes = pe.apply_findings({}, self._report())
        for _key, _value, sources in changes:
            self.assertTrue(sources)
            for s in sources:
                self.assertTrue(s.startswith("http"))

    def test_applying_twice_changes_nothing_the_second_time(self):
        cfg, first = pe.apply_findings({}, self._report())
        _, second = pe.apply_findings(cfg, self._report())
        self.assertTrue(first)
        self.assertEqual(second, [])

    def test_input_config_is_not_mutated(self):
        cfg = {"question_answers": {}}
        pe.apply_findings(cfg, self._report())
        self.assertEqual(cfg["question_answers"], {})


COMMENTED = """\
# Lightning Applier configuration
personal:
  # your legal first name
  first_name: Ada      # as it appears on your passport
  email: ada@example.com

# Answers reused across application forms
question_answers:
  # what you tell them you do
  headline: Data Engineer

credentials:
  password: secret
"""


class TestCommentPreservingWrites(unittest.TestCase):
    """config.yaml is mostly documentation — a write must not eat it.

    The shipped template carries several hundred comment lines. Round-tripping
    it through yaml.safe_dump would delete every one of them.
    """

    def test_a_key_is_added_to_an_existing_section(self):
        out = pe.insert_into_yaml(COMMENTED, "question_answers", "skills", "python")
        import yaml
        self.assertEqual(yaml.safe_load(out)["question_answers"]["skills"], "python")

    def test_every_comment_survives(self):
        out = pe.insert_into_yaml(COMMENTED, "question_answers", "skills", "python")
        for comment in ("# Lightning Applier configuration",
                        "# your legal first name",
                        "# as it appears on your passport",
                        "# Answers reused across application forms",
                        "# what you tell them you do"):
            self.assertIn(comment, out, f"lost comment: {comment}")

    def test_only_one_line_is_added(self):
        out = pe.insert_into_yaml(COMMENTED, "question_answers", "skills", "python")
        self.assertEqual(len(out.splitlines()), len(COMMENTED.splitlines()) + 1)

    def test_other_sections_are_untouched(self):
        import yaml
        out = pe.insert_into_yaml(COMMENTED, "question_answers", "skills", "python")
        data = yaml.safe_load(out)
        self.assertEqual(data["personal"]["first_name"], "Ada")
        self.assertEqual(data["credentials"]["password"], "secret")

    def test_an_existing_key_is_never_rewritten(self):
        out = pe.insert_into_yaml(COMMENTED, "question_answers", "headline", "Something Else")
        self.assertEqual(out, COMMENTED)

    def test_a_missing_section_is_appended(self):
        import yaml
        out = pe.insert_into_yaml(COMMENTED, "brand_new", "key", "value")
        self.assertEqual(yaml.safe_load(out)["brand_new"]["key"], "value")
        self.assertIn("# Lightning Applier configuration", out)

    def test_indentation_of_the_section_is_matched(self):
        text = "block:\n    deep: 1\n"
        out = pe.insert_into_yaml(text, "block", "other", "2")
        self.assertIn("    other:", out)

    def test_values_needing_quotes_are_quoted(self):
        import yaml
        out = pe.insert_into_yaml(COMMENTED, "question_answers", "note", "yes: really # no")
        self.assertEqual(yaml.safe_load(out)["question_answers"]["note"],
                         "yes: really # no")

    def test_unicode_is_preserved(self):
        import yaml
        out = pe.insert_into_yaml(COMMENTED, "question_answers", "name", "Ada — Lovelace")
        self.assertEqual(yaml.safe_load(out)["question_answers"]["name"], "Ada — Lovelace")


class TestWriteConfigAdditions(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.path = str(Path(self.dir) / "config.yaml")
        Path(self.path).write_text(COMMENTED, encoding="utf-8")

    def _read(self):
        return Path(self.path).read_text(encoding="utf-8")

    def test_a_change_is_written_and_backed_up(self):
        ok, msg = pe.write_config_additions(
            self.path, [("question_answers.skills", "python", ["https://x.dev"])])
        self.assertTrue(ok, msg)
        self.assertIn("skills: python", self._read())
        self.assertEqual(Path(self.path + ".bak").read_text(encoding="utf-8"), COMMENTED)

    def test_comments_survive_a_real_write(self):
        pe.write_config_additions(
            self.path, [("question_answers.skills", "python", [])])
        self.assertIn("# what you tell them you do", self._read())

    def test_nothing_is_written_when_the_result_would_not_parse(self):
        Path(self.path).write_text("question_answers:\n  a: [unclosed\n", encoding="utf-8")
        before = self._read()
        ok, msg = pe.write_config_additions(
            self.path, [("question_answers.skills", "python", [])])
        self.assertFalse(ok)
        self.assertIn("refused", msg)
        self.assertEqual(self._read(), before, "config was modified despite the error")

    def test_a_missing_file_is_reported_not_raised(self):
        ok, msg = pe.write_config_additions(
            str(Path(self.dir) / "nope.yaml"), [("a.b", "c", [])])
        self.assertFalse(ok)
        self.assertIn("could not read", msg)

    def test_several_changes_apply_together(self):
        import yaml
        ok, _ = pe.write_config_additions(self.path, [
            ("question_answers.skills", "python", []),
            ("question_answers.projects", "pipeline", []),
        ])
        self.assertTrue(ok)
        data = yaml.safe_load(self._read())
        self.assertEqual(data["question_answers"]["skills"], "python")
        self.assertEqual(data["question_answers"]["projects"], "pipeline")


class TestReporting(unittest.TestCase):
    def test_sources_read_without_findings_are_shown(self):
        r = pe.Report()
        r.sources.append("https://empty.dev")
        self.assertIn("READ, NOTHING FOUND", pe.format_report(r))
        self.assertIn("https://empty.dev", pe.format_report(r))

    def test_skipped_sources_show_the_reason(self):
        r = pe.Report()
        r.skip("https://x.dev", "robots.txt: Disallow: /")
        out = pe.format_report(r)
        self.assertIn("NOT READ", out)
        self.assertIn("Disallow: /", out)

    def test_findings_render_with_their_source(self):
        r = pe.Report()
        r.add(pe.Finding("skills", "python", "https://ada.dev", "found on page"))
        out = pe.format_report(r)
        self.assertIn("python", out)
        self.assertIn("https://ada.dev", out)

    def test_empty_report_says_so(self):
        self.assertIn("Nothing found", pe.format_report(pe.Report()))

    def test_json_output_is_valid_and_complete(self):
        import json
        r = pe.Report()
        r.add(pe.Finding("skills", "python", "https://ada.dev"))
        r.skip("https://no.dev", "HTTP 500")
        data = json.loads(pe.to_json(r, [("question_answers.skills", "python",
                                          ["https://ada.dev"])]))
        self.assertEqual(data["findings"][0]["source"], "https://ada.dev")
        self.assertEqual(data["skipped"][0]["reason"], "HTTP 500")
        self.assertEqual(data["applied"][0]["key"], "question_answers.skills")


class TestOrchestration(unittest.TestCase):
    def test_a_failing_source_does_not_stop_the_others(self):
        calls = []

        def boom(url, report):
            raise ValueError("bad html")

        def ok(url, report):
            calls.append(url)
            report.add(pe.Finding("skills", "python", url))

        original = pe.SOURCE_MATCHERS[:]
        import re as _re
        pe.SOURCE_MATCHERS[:] = [("github", _re.compile(r"github\.com"), boom)]
        self.addCleanup(lambda: pe.SOURCE_MATCHERS.__setitem__(slice(None), original))

        original_site = pe.expand_website
        pe.expand_website = ok
        self.addCleanup(setattr, pe, "expand_website", original_site)

        report = pe.expand_profile({}, ["https://github.com/ada", "https://ada.dev"])
        self.assertEqual(calls, ["https://ada.dev"])
        self.assertTrue(any("github reader failed" in r for _, r in report.skipped))
        self.assertTrue(report.findings)

    def test_a_url_is_only_read_once(self):
        seen = []
        original = pe.expand_website
        pe.expand_website = lambda url, report: seen.append(url)
        self.addCleanup(setattr, pe, "expand_website", original)
        pe.expand_profile({}, ["https://ada.dev", "https://ada.dev"])
        self.assertEqual(seen, ["https://ada.dev"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
