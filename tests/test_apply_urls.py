"""
Tests for the standalone batch external-apply runner (apply_urls.py).

Pure-logic / no-browser: input parsing (txt/csv/json), URL de-duplication,
stable per-URL job IDs, and the browserless `plan()` (ATS detection + applied
status). The actual browser submission path is exercised via the shared
ExternalApplier tests and can't run headless in CI.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apply_urls import BatchApplier, _job_id_for, load_jobs


class TestJobId(unittest.TestCase):
    def test_stable_and_unique(self):
        a1 = _job_id_for("https://boards.greenhouse.io/x/jobs/1")
        a2 = _job_id_for("https://boards.greenhouse.io/x/jobs/1")
        b = _job_id_for("https://boards.greenhouse.io/x/jobs/2")
        self.assertEqual(a1, a2)          # deterministic
        self.assertNotEqual(a1, b)        # url-specific
        self.assertTrue(a1.startswith("ext-"))


class TestLoadJobs(unittest.TestCase):
    def test_inline_urls_filter_and_dedupe(self):
        jobs = load_jobs([
            "https://jobs.lever.co/x/1",
            "https://jobs.lever.co/x/1",   # duplicate
            "not-a-url",                    # dropped
            "",                             # dropped
        ])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], "https://jobs.lever.co/x/1")

    def test_txt_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# a comment\n")
            f.write("https://boards.greenhouse.io/x/jobs/1\n")
            f.write("\n")
            f.write("https://jobs.ashbyhq.com/x/2\n")
            path = f.name
        try:
            jobs = load_jobs([], path)
            urls = [j["url"] for j in jobs]
            self.assertIn("https://boards.greenhouse.io/x/jobs/1", urls)
            self.assertIn("https://jobs.ashbyhq.com/x/2", urls)
            self.assertEqual(len(jobs), 2)  # comment + blank ignored
        finally:
            os.unlink(path)

    def test_csv_file_with_metadata(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("url,title,company,description\n")
            f.write("https://apply.workable.com/x/j/AB/,Data Scientist,Foo,ML\n")
            path = f.name
        try:
            jobs = load_jobs([], path)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["title"], "Data Scientist")
            self.assertEqual(jobs[0]["company"], "Foo")
            self.assertEqual(jobs[0]["description"], "ML")
        finally:
            os.unlink(path)

    def test_json_list_of_strings_and_objects(self):
        import json
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([
                "https://jobs.lever.co/x/1",
                {"url": "https://x.wd5.myworkdayjobs.com/j", "company": "Acme"},
                {"apply_url": "https://acme.bamboohr.com/careers/5"},
            ], f)
            path = f.name
        try:
            jobs = load_jobs([], path)
            urls = [j["url"] for j in jobs]
            self.assertEqual(len(jobs), 3)
            self.assertIn("https://x.wd5.myworkdayjobs.com/j", urls)
            self.assertIn("https://acme.bamboohr.com/careers/5", urls)
            acme = [j for j in jobs if j["company"] == "Acme"]
            self.assertEqual(len(acme), 1)
        finally:
            os.unlink(path)

    def test_inline_plus_file_merged_and_deduped(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("https://jobs.lever.co/x/1\n")   # also passed inline
            f.write("https://jobs.lever.co/x/2\n")
            path = f.name
        try:
            jobs = load_jobs(["https://jobs.lever.co/x/1"], path)
            self.assertEqual(len(jobs), 2)
        finally:
            os.unlink(path)


class TestPlan(unittest.TestCase):
    """plan() detects the ATS per URL and reports applied status — no browser."""

    def _runner(self):
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        self._dbs.append(db)
        cfg = {"state": {"db_path": db}, "personal": {}, "application": {},
               "external_apply": {}}
        return BatchApplier(cfg)

    def setUp(self):
        self._dbs = []

    def tearDown(self):
        for db in self._dbs:
            try:
                os.unlink(db)
            except OSError:
                pass

    def test_detects_and_flags_supported(self):
        r = self._runner()
        jobs = load_jobs([
            "https://boards.greenhouse.io/x/jobs/1",
            "https://x.wd5.myworkdayjobs.com/j",
            "https://example.com/nope",
        ])
        rows = r.plan(jobs)
        by_url = {job["url"]: (ats, already) for job, ats, already in rows}
        self.assertEqual(by_url["https://boards.greenhouse.io/x/jobs/1"][0], "greenhouse")
        self.assertEqual(by_url["https://x.wd5.myworkdayjobs.com/j"][0], "workday")
        self.assertIsNone(by_url["https://example.com/nope"][0])
        self.assertFalse(by_url["https://boards.greenhouse.io/x/jobs/1"][1])

    def test_applied_status_reflected(self):
        r = self._runner()
        url = "https://jobs.lever.co/x/1"
        r.state.mark_applied(job_id=_job_id_for(url), title="T", company="C")
        rows = r.plan(load_jobs([url]))
        _, ats, already = rows[0]
        self.assertEqual(ats, "lever")
        self.assertTrue(already)

    def test_applier_accepts_all_ats(self):
        # This tool applies to any handed-in URL regardless of config subset.
        r = self._runner()
        from ats_handlers import ALL_ATS
        self.assertTrue(r.applier.enabled)
        for name in ALL_ATS:
            self.assertIn(name, r.applier.supported_ats)


if __name__ == "__main__":
    unittest.main()
