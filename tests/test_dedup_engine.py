"""Tests for dedup_engine.py — DedupEngine class."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dedup_engine import DedupEngine
from state import State


class TestComputeFingerprint(unittest.TestCase):
    """Test fingerprint computation and normalization."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))
        self.dedup = DedupEngine(self.state)

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_fingerprint_is_hex_string(self):
        fp = self.dedup.compute_fingerprint("Software Engineer", "Google", "NYC")
        self.assertEqual(len(fp), 64)  # SHA-256 hex
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))

    def test_strips_company_inc(self):
        fp1 = self.dedup.compute_fingerprint("SWE", "Google Inc.", "NYC")
        fp2 = self.dedup.compute_fingerprint("SWE", "Google", "NYC")
        self.assertEqual(fp1, fp2)

    def test_strips_company_ltd(self):
        fp1 = self.dedup.compute_fingerprint("SWE", "Acme Ltd", "London")
        fp2 = self.dedup.compute_fingerprint("SWE", "Acme", "London")
        self.assertEqual(fp1, fp2)

    def test_strips_seniority_senior(self):
        fp1 = self.dedup.compute_fingerprint("Senior Software Engineer", "Google", "NYC")
        fp2 = self.dedup.compute_fingerprint("Software Engineer", "Google", "NYC")
        self.assertEqual(fp1, fp2)

    def test_strips_seniority_lead(self):
        fp1 = self.dedup.compute_fingerprint("Lead Data Scientist", "Meta", "")
        fp2 = self.dedup.compute_fingerprint("Data Scientist", "Meta", "")
        self.assertEqual(fp1, fp2)

    def test_location_normalization_state(self):
        fp1 = self.dedup.compute_fingerprint("SWE", "Google", "Mountain View, CA")
        fp2 = self.dedup.compute_fingerprint("SWE", "Google", "Mountain View")
        self.assertEqual(fp1, fp2)

    def test_location_normalization_remote(self):
        fp1 = self.dedup.compute_fingerprint("SWE", "Google", "Remote")
        fp2 = self.dedup.compute_fingerprint("SWE", "Google", "Work From Home")
        self.assertEqual(fp1, fp2)

    def test_case_insensitive(self):
        fp1 = self.dedup.compute_fingerprint("SOFTWARE ENGINEER", "GOOGLE", "NYC")
        fp2 = self.dedup.compute_fingerprint("software engineer", "google", "nyc")
        self.assertEqual(fp1, fp2)

    def test_whitespace_normalization(self):
        fp1 = self.dedup.compute_fingerprint("Software  Engineer", "Google", "NYC")
        fp2 = self.dedup.compute_fingerprint("Software Engineer", "Google", "NYC")
        self.assertEqual(fp1, fp2)

    def test_different_jobs_different_fingerprints(self):
        fp1 = self.dedup.compute_fingerprint("Software Engineer", "Google", "NYC")
        fp2 = self.dedup.compute_fingerprint("Product Manager", "Google", "NYC")
        self.assertNotEqual(fp1, fp2)


class TestIsDuplicate(unittest.TestCase):
    """Test duplicate detection across platforms."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))
        self.dedup = DedupEngine(self.state)

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_no_duplicate_initially(self):
        result = self.dedup.is_duplicate("SWE", "Google", "NYC", "linkedin")
        self.assertFalse(result["is_dup"])

    def test_same_job_different_platform_is_duplicate(self):
        self.dedup.register_job("li123", "SWE", "Google", "NYC", "linkedin")
        result = self.dedup.is_duplicate("SWE", "Google", "NYC", "indeed")
        self.assertTrue(result["is_dup"])
        self.assertEqual(result["original_platform"], "linkedin")
        self.assertEqual(result["original_job_id"], "li123")

    def test_same_job_same_platform_is_duplicate(self):
        self.dedup.register_job("li123", "SWE", "Google", "NYC", "linkedin")
        result = self.dedup.is_duplicate("SWE", "Google", "NYC", "linkedin")
        self.assertTrue(result["is_dup"])

    def test_different_jobs_not_duplicate(self):
        self.dedup.register_job("li123", "SWE", "Google", "NYC", "linkedin")
        result = self.dedup.is_duplicate("Product Manager", "Meta", "SF", "indeed")
        self.assertFalse(result["is_dup"])

    def test_google_inc_matches_google(self):
        self.dedup.register_job("li123", "SWE", "Google Inc.", "NYC", "linkedin")
        result = self.dedup.is_duplicate("SWE", "Google", "NYC", "indeed")
        self.assertTrue(result["is_dup"])

    def test_senior_swe_matches_swe(self):
        self.dedup.register_job("li123", "Senior Software Engineer", "Google", "NYC", "linkedin")
        result = self.dedup.is_duplicate("Software Engineer", "Google", "NYC", "indeed")
        self.assertTrue(result["is_dup"])

    def test_result_includes_fingerprint(self):
        result = self.dedup.is_duplicate("SWE", "Google", "NYC")
        self.assertIn("fingerprint", result)
        self.assertEqual(len(result["fingerprint"]), 64)


class TestRegisterJob(unittest.TestCase):
    """Test register_job stores fingerprints in DB."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))
        self.dedup = DedupEngine(self.state)

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_register_returns_fingerprint(self):
        fp = self.dedup.register_job("j1", "SWE", "Google", "NYC", "linkedin")
        self.assertIsNotNone(fp)
        self.assertEqual(len(fp), 64)

    def test_register_stores_in_db(self):
        self.dedup.register_job("j1", "SWE", "Google", "NYC", "linkedin")
        row = self.state.conn.execute(
            "SELECT * FROM job_fingerprints WHERE job_id='j1'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["platform"], "linkedin")

    def test_register_unknown_platform_becomes_other(self):
        self.dedup.register_job("j1", "SWE", "Google", "NYC", "unknown_site")
        row = self.state.conn.execute(
            "SELECT platform FROM job_fingerprints WHERE job_id='j1'"
        ).fetchone()
        self.assertEqual(row["platform"], "other")

    def test_register_duplicate_fingerprint_ignored(self):
        self.dedup.register_job("j1", "SWE", "Google", "NYC", "linkedin")
        # Same normalized job, different job_id -- INSERT OR IGNORE
        self.dedup.register_job("j2", "SWE", "Google Inc.", "NYC", "indeed")
        rows = self.state.conn.execute(
            "SELECT COUNT(*) as c FROM job_fingerprints"
        ).fetchone()
        # Only one fingerprint stored (first one wins)
        self.assertEqual(rows["c"], 1)

    def test_disabled_engine_returns_none(self):
        self.dedup.enabled = False
        fp = self.dedup.register_job("j1", "SWE", "Google", "NYC", "linkedin")
        self.assertIsNone(fp)


if __name__ == "__main__":
    unittest.main()
