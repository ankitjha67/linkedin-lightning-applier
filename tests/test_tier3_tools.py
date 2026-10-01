"""Skills linting, state reset, and CV templates.

Three small tools that guard things nothing else checks: documents Claude
executes, a database that holds the only record of where you applied, and a
template whose typo silently deletes a section of your CV.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv_templates as ct  # noqa: E402
import state_reset as sr  # noqa: E402
from state import State  # noqa: E402
from tools import lint_skills as ls  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════════════
# tools/lint_skills.py
# ═══════════════════════════════════════════════════════════════════════════

class TestFrontmatter(unittest.TestCase):
    def test_a_valid_block_parses(self):
        data, body, err = ls.split_frontmatter(
            "---\nname: a-skill\ndescription: Use when x\n---\nBody here")
        self.assertEqual(err, "")
        self.assertEqual(data["name"], "a-skill")
        self.assertIn("Body here", body)

    def test_a_missing_block_is_an_error(self):
        _d, _b, err = ls.split_frontmatter("# Just a heading")
        self.assertIn("no YAML frontmatter", err)

    def test_an_unclosed_block_is_an_error(self):
        _d, _b, err = ls.split_frontmatter("---\nname: x\nstill going")
        self.assertIn("never closed", err)

    def test_invalid_yaml_is_an_error(self):
        _d, _b, err = ls.split_frontmatter("---\nname: [unclosed\n---\nbody")
        self.assertIn("not valid YAML", err)

    def test_a_non_mapping_is_an_error(self):
        _d, _b, err = ls.split_frontmatter("---\n- a\n- b\n---\nbody")
        self.assertIn("mapping", err)


class TestPathReferences(unittest.TestCase):
    def test_backticked_module_paths_are_found(self):
        found = ls.referenced_paths("Use `cv_profile.py` and `tools/x.md`.")
        self.assertEqual(found, {"cv_profile.py", "tools/x.md"})

    def test_python_invocations_are_found(self):
        self.assertIn("tools/lint_skills.py",
                      ls.referenced_paths("Run python3 tools/lint_skills.py now"))

    def test_prose_is_not_mistaken_for_a_path(self):
        self.assertEqual(ls.referenced_paths("This is ordinary prose."), set())

    def test_gitignored_files_are_intentionally_absent(self):
        # config.yaml holds credentials, so it is never in a clone. A skill
        # may still legitimately tell Claude to read it.
        self.assertTrue(ls.is_intentionally_absent("config.yaml"))

    def test_generated_directories_are_intentionally_absent(self):
        self.assertTrue(ls.is_intentionally_absent("data/state.db"))
        self.assertTrue(ls.is_intentionally_absent("templates/cv-template.html"))

    def test_a_genuinely_missing_module_is_not_excused(self):
        self.assertFalse(ls.is_intentionally_absent("no_such_module.py"))


class TestShippedSkillsAreValid(unittest.TestCase):
    """The skills and commands this repo actually ships must pass."""

    def setUp(self):
        ls.problems.clear()
        ls.notes.clear()
        self.addCleanup(ls.problems.clear)

    def test_every_shipped_skill_is_clean(self):
        for path in sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")):
            ls.check_skill(path)
        self.assertEqual(ls.problems, [], f"skill problems: {ls.problems}")

    def test_every_shipped_command_is_clean(self):
        for path in sorted((ROOT / ".claude" / "commands").glob("*.md")):
            ls.check_command(path)
        self.assertEqual(ls.problems, [], f"command problems: {ls.problems}")


class TestSkillChecks(unittest.TestCase):
    def setUp(self):
        ls.problems.clear()
        self.addCleanup(ls.problems.clear)
        self.dir = Path(tempfile.mkdtemp())

    def _skill(self, dirname, text):
        d = self.dir / dirname
        d.mkdir(parents=True, exist_ok=True)
        p = d / "SKILL.md"
        p.write_text(text, encoding="utf-8")
        return p

    def _run(self, dirname, text):
        original = ls.ROOT
        ls.ROOT = self.dir
        self.addCleanup(setattr, ls, "ROOT", original)
        ls.check_skill(self._skill(dirname, text))
        return [m for _w, m, _f in ls.problems]

    def test_a_name_that_does_not_match_the_directory_is_caught(self):
        msgs = self._run("real-name",
                         "---\nname: other-name\ndescription: Use when x\n---\nBody")
        self.assertTrue(any("does not match the directory" in m for m in msgs))

    def test_a_description_with_no_trigger_is_caught(self):
        msgs = self._run("a-skill",
                         "---\nname: a-skill\ndescription: A toolkit for CVs.\n---\nBody")
        self.assertTrue(any("never says when to use" in m for m in msgs))

    def test_a_description_with_a_trigger_passes(self):
        msgs = self._run(
            "a-skill",
            "---\nname: a-skill\ndescription: Tailor CVs. Use when the user "
            "asks for a CV.\n---\nBody")
        self.assertEqual(msgs, [])

    def test_a_missing_description_is_caught(self):
        msgs = self._run("a-skill", "---\nname: a-skill\n---\nBody")
        self.assertTrue(any("no 'description'" in m for m in msgs))

    def test_an_uppercase_name_is_caught(self):
        msgs = self._run("a-skill",
                         "---\nname: A_Skill\ndescription: Use when x\n---\nBody")
        self.assertTrue(any("lowercase-with-hyphens" in m for m in msgs))

    def test_an_empty_body_is_caught(self):
        msgs = self._run("a-skill",
                         "---\nname: a-skill\ndescription: Use when x\n---\n")
        self.assertTrue(any("no body" in m for m in msgs))


# ═══════════════════════════════════════════════════════════════════════════
# state_reset.py
# ═══════════════════════════════════════════════════════════════════════════

def fresh_state():
    return State(db_path=str(Path(tempfile.mkdtemp()) / "s.db"))


class TestScopeCoverage(unittest.TestCase):
    """Every table must belong to exactly one scope.

    A table in no scope is never cleared and is invisible here; a table in two
    is a sign the scopes have stopped meaning anything. Both are the kind of
    drift that only shows up when someone is trying to wipe their data.
    """

    def setUp(self):
        self.state = fresh_state()

    def test_no_table_escapes_classification(self):
        missing = sr.unclassified_tables(self.state)
        self.assertEqual(missing, set(),
                         f"tables in the schema but in no reset scope: {sorted(missing)}")

    def test_no_scope_names_a_table_that_does_not_exist(self):
        extra = sr.known_tables() - sr.live_tables(self.state)
        self.assertEqual(extra, set(), f"scoped but not in the schema: {sorted(extra)}")

    def test_no_table_is_in_two_scopes(self):
        seen, dupes = {}, []
        for name, scope in sr.SCOPES.items():
            for table in scope["tables"]:
                if table in seen:
                    dupes.append((table, seen[table], name))
                seen[table] = name
        self.assertEqual(dupes, [])

    def test_every_scope_is_listed_in_the_order(self):
        self.assertEqual(set(sr.SCOPE_ORDER), set(sr.SCOPES))

    def test_all_covers_every_scope(self):
        self.assertEqual(set(sr.scope_tables("all")), sr.known_tables())


class TestReset(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.db = str(self.dir / "s.db")
        self.state = State(db_path=self.db)
        self.state.conn.execute(
            "INSERT INTO applied_jobs (job_id,title,company) VALUES ('j1','T','C')")
        self.state.conn.execute(
            "INSERT INTO response_tracking (job_id,response_type) VALUES ('j1','offer')")
        self.state.conn.commit()

    def _count(self, table):
        return self.state.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_a_dry_run_deletes_nothing(self):
        result = sr.reset(self.state, "outcomes", self.db, dry_run=True)
        self.assertFalse(result["cleared"])
        self.assertEqual(result["rows"], 1)
        self.assertEqual(self._count("response_tracking"), 1)

    def test_applying_clears_only_that_scope(self):
        sr.reset(self.state, "outcomes", self.db, dry_run=False)
        self.assertEqual(self._count("response_tracking"), 0)
        self.assertEqual(self._count("applied_jobs"), 1, "cleared the wrong scope")

    def test_a_backup_is_written_before_deleting(self):
        result = sr.reset(self.state, "outcomes", self.db, dry_run=False)
        self.assertTrue(result["backup"])
        self.assertTrue(Path(result["backup"]).exists())

    def test_the_backup_still_holds_the_deleted_rows(self):
        result = sr.reset(self.state, "outcomes", self.db, dry_run=False)
        restored = State(db_path=result["backup"])
        self.assertEqual(restored.conn.execute(
            "SELECT COUNT(*) FROM response_tracking").fetchone()[0], 1)

    def test_all_clears_everything(self):
        sr.reset(self.state, "all", self.db, dry_run=False)
        self.assertEqual(self._count("applied_jobs"), 0)
        self.assertEqual(self._count("response_tracking"), 0)

    def test_an_unknown_scope_is_refused(self):
        result = sr.reset(self.state, "everything", self.db, dry_run=False)
        self.assertIn("error", result)
        self.assertEqual(self._count("applied_jobs"), 1)

    def test_counts_only_include_tables_that_exist(self):
        got = sr.counts(self.state, ["applied_jobs", "no_such_table"])
        self.assertEqual(set(got), {"applied_jobs"})

    def test_the_plan_warns_before_clearing_applications(self):
        plan = sr.format_plan(sr.reset(self.state, "applications", self.db, True))
        self.assertIn("apply to those jobs again", plan)

    def test_an_empty_scope_says_so(self):
        sr.reset(self.state, "cache", self.db, dry_run=False)
        plan = sr.format_plan(sr.reset(self.state, "cache", self.db, True))
        self.assertIn("already empty", plan)

    def test_backing_up_a_missing_database_is_not_an_error(self):
        self.assertEqual(sr.backup_database(str(self.dir / "nope.db")), "")


# ═══════════════════════════════════════════════════════════════════════════
# cv_templates.py
# ═══════════════════════════════════════════════════════════════════════════

MINIMAL = ("<html><body><h1>{{FULL_NAME}}</h1><p>{{CONTACT_LINE}}</p>"
           "<p>{{SUMMARY}}</p>{{EXPERIENCE}}{{EDUCATION}}{{SKILLS}}"
           "{{CERTIFICATIONS}}</body></html>")


class TestTemplateChecks(unittest.TestCase):
    def test_a_complete_template_passes(self):
        report = ct.check_template(MINIMAL)
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(report.warnings, [])

    def test_a_misspelled_placeholder_is_an_error(self):
        report = ct.check_template(MINIMAL.replace("{{EXPERIENCE}}", "{{EXPERINCE}}"))
        self.assertFalse(report.ok)
        self.assertTrue(any("EXPERINCE" in m for m, _f in report.errors))

    def test_a_misspelling_gets_a_suggestion(self):
        report = ct.check_template(MINIMAL.replace("{{EXPERIENCE}}", "{{EXPERINCE}}"))
        self.assertTrue(any("did you mean {{EXPERIENCE}}" in m
                            for m, _f in report.errors))

    def test_a_missing_required_placeholder_is_an_error(self):
        report = ct.check_template(MINIMAL.replace("{{FULL_NAME}}", ""))
        self.assertFalse(report.ok)
        self.assertTrue(any("FULL_NAME" in m for m, _f in report.errors))

    def test_a_missing_optional_section_is_only_a_warning(self):
        report = ct.check_template(MINIMAL.replace("{{CERTIFICATIONS}}", ""))
        self.assertTrue(report.ok)
        self.assertTrue(any("CERTIFICATIONS" in m for m, _f in report.warnings))

    def test_a_script_tag_is_an_error(self):
        report = ct.check_template(MINIMAL.replace("<body>", "<body><script>x</script>"))
        self.assertFalse(report.ok)

    def test_a_remote_asset_is_a_warning(self):
        html = MINIMAL.replace("<body>", '<body><img src="https://x.com/logo.png">')
        report = ct.check_template(html)
        self.assertTrue(any("remote" in m for m, _f in report.warnings))

    def test_a_fragment_without_html_is_a_warning(self):
        report = ct.check_template("<h1>{{FULL_NAME}}</h1>{{EXPERIENCE}}")
        self.assertTrue(any("doctype" in m.lower() for m, _f in report.warnings))

    def test_an_empty_template_is_an_error(self):
        self.assertFalse(ct.check_template("").ok)

    def test_whitespace_inside_a_placeholder_still_matches(self):
        self.assertIn("FULL_NAME", ct.placeholders_in("{{ FULL_NAME }}"))


class TestTemplateRendering(unittest.TestCase):
    def test_a_complete_template_renders_with_sample_content(self):
        ok, msg = ct.check_renders(MINIMAL)
        self.assertTrue(ok, msg)

    def test_the_sample_name_reaches_the_output(self):
        self.assertIn("Ada Lovelace", ct._render_simple(MINIMAL))

    def test_experience_bullets_reach_the_output(self):
        self.assertIn("Wrote the first algorithm", ct._render_simple(MINIMAL))

    def test_an_unfilled_placeholder_fails_the_render_check(self):
        ok, msg = ct.check_renders(MINIMAL + "{{UNKNOWN_THING}}")
        self.assertFalse(ok)
        self.assertIn("UNKNOWN_THING", msg)

    def test_the_shipped_default_template_is_valid(self):
        # If the built-in template ever stopped satisfying its own contract,
        # every generated CV would be wrong.
        html = ct.default_template()
        report = ct.check_template(html)
        self.assertTrue(report.ok, report.errors)
        ok, msg = ct.check_renders(html)
        self.assertTrue(ok, msg)

    def test_the_known_placeholders_match_the_engine(self):
        import re as _re

        import cv_template_engine
        source = Path(cv_template_engine.__file__).read_text(encoding="utf-8")
        engine_tokens = {m.group(1) for m in
                         _re.finditer(r'replace\("\{\{([A-Z_]+)\}\}"', source)}
        self.assertTrue(engine_tokens)
        self.assertEqual(engine_tokens, ct.KNOWN_PLACEHOLDERS,
                         "cv_templates.KNOWN_PLACEHOLDERS has drifted from "
                         "cv_template_engine._render_html")


class TestInstalling(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.src = self.dir / "mine.html"
        self.src.write_text(MINIMAL, encoding="utf-8")

    def test_a_template_is_copied_into_place(self):
        dest = self.dir / "templates" / "cv.html"
        ok, msg = ct.install(str(self.src), str(dest))
        self.assertTrue(ok, msg)
        self.assertEqual(dest.read_text(encoding="utf-8"), MINIMAL)

    def test_an_existing_template_is_backed_up(self):
        dest = self.dir / "templates" / "cv.html"
        dest.parent.mkdir(parents=True)
        dest.write_text("<html>old</html>", encoding="utf-8")
        ok, _msg = ct.install(str(self.src), str(dest))
        self.assertTrue(ok)
        self.assertIn("old", (dest.parent / "cv.html.bak").read_text(encoding="utf-8"))

    def test_a_missing_source_is_reported(self):
        ok, msg = ct.install(str(self.dir / "nope.html"), str(self.dir / "d.html"))
        self.assertFalse(ok)
        self.assertIn("does not exist", msg)

    def test_installed_templates_are_listed(self):
        (self.dir / "a.html").write_text("x", encoding="utf-8")
        self.assertTrue(ct.installed_templates(str(self.dir)))

    def test_listing_a_missing_directory_is_empty(self):
        self.assertEqual(ct.installed_templates(str(self.dir / "nope")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
