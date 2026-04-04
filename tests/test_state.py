"""Tests for state.py — State class (SQLite persistence layer)."""

import csv
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from state import State


class TestStateInit(unittest.TestCase):
    """Test State initialization and table creation."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_creates_db_file(self):
        state = State(self.db_path)
        self.assertTrue(os.path.exists(self.db_path))
        state.close()

    def test_creates_parent_directory(self):
        nested_path = os.path.join(self.tmp_dir, "sub", "dir", "test.db")
        state = State(nested_path)
        self.assertTrue(os.path.exists(nested_path))
        state.close()

    def test_all_32_tables_created(self):
        state = State(self.db_path)
        rows = state.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_names = {r["name"] for r in rows}
        expected_tables = {
            "applied_jobs", "skipped_jobs", "failed_jobs", "recruiters",
            "visa_sponsors", "daily_stats", "match_scores", "message_queue",
            "salary_data", "interview_prep", "google_jobs", "response_tracking",
            "hiring_velocity", "follow_up_queue", "company_connections",
            "resume_variants", "skill_frequency", "company_intel",
            "email_responses", "profile_suggestions", "withdrawal_queue",
            "job_fingerprints", "jd_snapshots", "jd_changes",
            "recruiter_interactions", "recruiter_scores", "apply_schedule",
            "negotiation_briefs", "ats_status", "job_watchlist",
            "referral_requests",
        }
        for tbl in expected_tables:
            self.assertIn(tbl, table_names, f"Missing table: {tbl}")
        # Verify at least 31 tables (may have more)
        self.assertGreaterEqual(len(table_names), 31)
        state.close()

    def test_row_factory_is_sqlite_row(self):
        state = State(self.db_path)
        self.assertEqual(state.conn.row_factory, sqlite3.Row)
        state.close()

    def test_session_counters_initialized_to_zero(self):
        state = State(self.db_path)
        self.assertEqual(state.session_applied, 0)
        self.assertEqual(state.session_skipped, 0)
        self.assertEqual(state.session_failed, 0)
        state.close()


class TestStateAppliedSkippedFailed(unittest.TestCase):
    """Test mark_applied, mark_skipped, mark_failed and related queries."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.state = State(self.db_path)

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_mark_applied_basic(self):
        self.state.mark_applied("job1", title="SWE", company="Acme")
        self.assertTrue(self.state.is_applied("job1"))
        self.assertFalse(self.state.is_applied("job999"))

    def test_mark_applied_with_match_score_and_resume(self):
        self.state.mark_applied("job2", title="SWE", company="Acme",
                                match_score=85, resume_version="v2_tailored")
        row = self.state.conn.execute(
            "SELECT match_score, resume_version FROM applied_jobs WHERE job_id='job2'"
        ).fetchone()
        self.assertEqual(row["match_score"], 85)
        self.assertEqual(row["resume_version"], "v2_tailored")

    def test_mark_applied_increments_session_counter(self):
        self.state.mark_applied("j1")
        self.state.mark_applied("j2")
        self.assertEqual(self.state.session_applied, 2)

    def test_mark_skipped_basic(self):
        self.state.mark_skipped(job_id="skip1", title="PM", company="Corp",
                                reason="bad match", match_score=30)
        self.assertTrue(self.state.is_skipped("skip1"))
        self.assertFalse(self.state.is_skipped("skip999"))

    def test_mark_skipped_increments_session_counter(self):
        self.state.mark_skipped(job_id="s1", reason="test")
        self.assertEqual(self.state.session_skipped, 1)

    def test_mark_failed(self):
        self.state.mark_failed("f1", title="Eng", company="X", reason="timeout")
        self.assertEqual(self.state.session_failed, 1)
        row = self.state.conn.execute(
            "SELECT reason FROM failed_jobs WHERE job_id='f1'"
        ).fetchone()
        self.assertEqual(row["reason"], "timeout")

    def test_is_applied_returns_false_for_skipped(self):
        self.state.mark_skipped(job_id="sk1", reason="mismatch")
        self.assertFalse(self.state.is_applied("sk1"))

    def test_total_applied_count(self):
        for i in range(5):
            self.state.mark_applied(f"j{i}")
        self.assertEqual(self.state.total_applied(), 5)

    def test_mark_applied_stores_all_fields(self):
        self.state.mark_applied(
            "j99", title="Data Eng", company="Meta", location="NYC",
            work_style="Remote", job_url="http://example.com",
            salary_info="$150K", visa_sponsorship="yes",
        )
        row = self.state.conn.execute(
            "SELECT * FROM applied_jobs WHERE job_id='j99'"
        ).fetchone()
        self.assertEqual(row["title"], "Data Eng")
        self.assertEqual(row["location"], "NYC")
        self.assertEqual(row["work_style"], "Remote")
        self.assertEqual(row["visa_sponsorship"], "yes")

    def test_mark_applied_duplicate_replaces(self):
        self.state.mark_applied("dup1", title="V1")
        self.state.mark_applied("dup1", title="V2")
        row = self.state.conn.execute(
            "SELECT title FROM applied_jobs WHERE job_id='dup1'"
        ).fetchone()
        self.assertEqual(row["title"], "V2")


class TestStateMatchScores(unittest.TestCase):
    """Test save_match_score and get_match_score."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_and_get_match_score(self):
        self.state.save_match_score("j1", title="SWE", company="Google",
                                    score=88, skill_matches="python,go",
                                    missing_skills="rust",
                                    explanation="strong fit")
        result = self.state.get_match_score("j1")
        self.assertIsNotNone(result)
        self.assertEqual(result["score"], 88)
        self.assertEqual(result["skill_matches"], "python,go")
        self.assertEqual(result["missing_skills"], "rust")

    def test_get_match_score_nonexistent(self):
        result = self.state.get_match_score("nonexistent")
        self.assertIsNone(result)

    def test_match_score_upsert(self):
        self.state.save_match_score("j1", score=50)
        self.state.save_match_score("j1", score=90)
        result = self.state.get_match_score("j1")
        self.assertEqual(result["score"], 90)


class TestStateMessageQueue(unittest.TestCase):
    """Test message queue operations."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_queue_and_get_pending(self):
        past_time = "2020-01-01 00:00:00"
        self.state.queue_message("j1", "Alice", "http://li/alice",
                                 "Hi!", past_time, company="Acme", job_title="SWE")
        pending = self.state.get_pending_messages()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["recruiter_name"], "Alice")

    def test_future_message_not_pending(self):
        future_time = "2099-12-31 23:59:59"
        self.state.queue_message("j2", "Bob", "http://li/bob",
                                 "Hello!", future_time)
        pending = self.state.get_pending_messages()
        self.assertEqual(len(pending), 0)

    def test_update_message_status(self):
        self.state.queue_message("j1", "Alice", "url", "msg",
                                 "2020-01-01 00:00:00")
        pending = self.state.get_pending_messages()
        msg_id = pending[0]["id"]
        self.state.update_message_status(msg_id, "sent")
        # Should no longer be pending
        pending_after = self.state.get_pending_messages()
        self.assertEqual(len(pending_after), 0)

    def test_daily_message_count(self):
        # No sent messages yet
        self.assertEqual(self.state.daily_message_count(), 0)

    def test_multiple_messages_queued(self):
        for i in range(5):
            self.state.queue_message(f"j{i}", f"Recruiter{i}", "url", "msg",
                                     "2020-01-01 00:00:00")
        pending = self.state.get_pending_messages()
        self.assertEqual(len(pending), 5)


class TestStateSalaryData(unittest.TestCase):
    """Test salary data save and benchmark."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_and_benchmark(self):
        self.state.save_salary_data("j1", title="SWE", company="Google",
                                    location="NYC", salary_raw="$150K-$200K",
                                    salary_min=150000, salary_max=200000,
                                    currency="USD", period="yearly")
        self.state.save_salary_data("j2", title="SWE", company="Meta",
                                    location="NYC", salary_raw="$140K-$180K",
                                    salary_min=140000, salary_max=180000,
                                    currency="USD", period="yearly")
        bench = self.state.get_salary_benchmark("swe", "nyc")
        self.assertEqual(bench["count"], 2)
        self.assertGreater(bench["median_min"], 0)
        self.assertEqual(bench["currency"], "USD")

    def test_benchmark_no_data(self):
        bench = self.state.get_salary_benchmark("nonexistent")
        self.assertEqual(bench["count"], 0)

    def test_benchmark_returns_correct_median(self):
        for i, (mn, mx) in enumerate([(100, 200), (150, 250), (200, 300)]):
            self.state.save_salary_data(f"j{i}", title="Eng",
                                        salary_min=mn, salary_max=mx,
                                        currency="USD")
        bench = self.state.get_salary_benchmark("eng")
        self.assertEqual(bench["count"], 3)
        # Median of [100, 150, 200] = 150
        self.assertEqual(bench["median_min"], 150)

    def test_benchmark_min_max_salary(self):
        self.state.save_salary_data("j0", title="Eng", salary_min=80000,
                                    salary_max=120000, currency="USD")
        self.state.save_salary_data("j1", title="Eng", salary_min=100000,
                                    salary_max=160000, currency="USD")
        bench = self.state.get_salary_benchmark("eng")
        self.assertEqual(bench["min_salary"], 80000)
        self.assertEqual(bench["max_salary"], 160000)


class TestStateInterviewPrep(unittest.TestCase):
    """Test save/get interview prep."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_and_get(self):
        self.state.save_interview_prep("j1", company="Google", title="SWE",
                                       company_research="Founded 1998",
                                       likely_questions="System design",
                                       talking_points="Scalability")
        result = self.state.get_interview_prep("j1")
        self.assertIsNotNone(result)
        self.assertEqual(result["company"], "Google")
        self.assertEqual(result["company_research"], "Founded 1998")

    def test_get_nonexistent(self):
        result = self.state.get_interview_prep("nonexistent")
        self.assertIsNone(result)

    def test_upsert_interview_prep(self):
        self.state.save_interview_prep("j1", company="Google", title="SWE",
                                       company_research="V1")
        self.state.save_interview_prep("j1", company="Google", title="SWE",
                                       company_research="V2")
        result = self.state.get_interview_prep("j1")
        self.assertEqual(result["company_research"], "V2")


class TestStateGoogleJobs(unittest.TestCase):
    """Test Google Jobs save, get_by_status, update_status."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_and_get_by_status(self):
        self.state.save_google_job("g1", title="SWE", company="Acme",
                                   location="SF", source_platform="indeed")
        jobs = self.state.get_google_jobs_by_status("new")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "SWE")

    def test_update_status(self):
        self.state.save_google_job("g1", title="SWE", company="Acme")
        self.state.update_google_job_status("g1", "applied")
        jobs_new = self.state.get_google_jobs_by_status("new")
        jobs_applied = self.state.get_google_jobs_by_status("applied")
        self.assertEqual(len(jobs_new), 0)
        self.assertEqual(len(jobs_applied), 1)

    def test_duplicate_google_job_ignored(self):
        self.state.save_google_job("g1", title="SWE", company="Acme")
        self.state.save_google_job("g1", title="SWE Updated", company="Acme")
        jobs = self.state.get_google_jobs_by_status("new")
        # INSERT OR IGNORE means original title kept
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "SWE")

    def test_empty_status_returns_nothing(self):
        self.state.save_google_job("g1", title="SWE", company="Acme")
        jobs = self.state.get_google_jobs_by_status("applied")
        self.assertEqual(len(jobs), 0)


class TestStateCSVExport(unittest.TestCase):
    """Test CSV export creates files with correct headers."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))
        self.export_dir = os.path.join(self.tmp_dir, "export")

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_export_creates_csv_files(self):
        self.state.mark_applied("j1", title="SWE", company="Google")
        self.state.export_csv(self.export_dir, cfg={"export": {}})
        expected_files = [
            "applied_jobs.csv", "skipped_jobs.csv",
            "recruiters.csv", "visa_sponsors.csv",
        ]
        for fname in expected_files:
            path = os.path.join(self.export_dir, fname)
            self.assertTrue(os.path.exists(path), f"Missing CSV: {fname}")

    def test_applied_csv_has_correct_headers(self):
        self.state.mark_applied("j1", title="SWE", company="Google")
        self.state.export_csv(self.export_dir, cfg={"export": {}})
        with open(os.path.join(self.export_dir, "applied_jobs.csv")) as f:
            reader = csv.reader(f)
            headers = next(reader)
        self.assertIn("job_id", headers)
        self.assertIn("match_score", headers)
        self.assertIn("resume_version", headers)

    def test_applied_csv_has_data_rows(self):
        self.state.mark_applied("j1", title="SWE", company="Google")
        self.state.mark_applied("j2", title="PM", company="Meta")
        self.state.export_csv(self.export_dir, cfg={"export": {}})
        with open(os.path.join(self.export_dir, "applied_jobs.csv")) as f:
            reader = csv.reader(f)
            rows = list(reader)
        # 1 header + 2 data rows
        self.assertEqual(len(rows), 3)

    def test_export_creates_directory(self):
        deep_dir = os.path.join(self.tmp_dir, "a", "b", "c")
        self.state.mark_applied("j1", title="SWE", company="Google")
        self.state.export_csv(deep_dir, cfg={"export": {}})
        self.assertTrue(os.path.isdir(deep_dir))


class TestStateMigration(unittest.TestCase):
    """Test _migrate_tables adds columns to existing tables."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_migrate_tables_idempotent(self):
        # First init creates tables with columns
        state = State(self.db_path)
        state.close()
        # Second init should not fail (migration is idempotent)
        state2 = State(self.db_path)
        # Verify match_score column exists in applied_jobs
        row = state2.conn.execute(
            "SELECT match_score FROM applied_jobs LIMIT 0"
        ).fetchone()
        state2.close()

    def test_migrate_adds_missing_column(self):
        # Create a bare DB with applied_jobs missing match_score
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS applied_jobs (
            job_id TEXT PRIMARY KEY, title TEXT, company TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS skipped_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, reason TEXT)""")
        conn.commit()
        conn.close()
        # State init should migrate and add the columns
        state = State(self.db_path)
        # Should not raise
        state.conn.execute("SELECT match_score FROM applied_jobs LIMIT 0")
        state.close()


class TestStateSessionSummary(unittest.TestCase):
    """Test session_summary returns a string."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_session_summary_returns_string(self):
        summary = self.state.session_summary()
        self.assertIsInstance(summary, str)
        self.assertIn("Session:", summary)

    def test_session_summary_reflects_counts(self):
        self.state.mark_applied("j1")
        self.state.mark_skipped(job_id="s1", reason="test")
        summary = self.state.session_summary()
        self.assertIn("1A", summary)
        self.assertIn("1S", summary)

    def test_session_summary_includes_all_time(self):
        summary = self.state.session_summary()
        self.assertIn("All-time:", summary)


if __name__ == "__main__":
    unittest.main()
