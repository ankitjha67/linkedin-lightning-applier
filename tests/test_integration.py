"""
Integration Tests — End-to-End Pipeline.

Tests the full application pipeline with mocked browser interactions.
Verifies: config load → state init → scoring → tailoring → tracking → export.

Does NOT require Chrome or Selenium — all browser interactions are mocked.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestStateIntegration(unittest.TestCase):
    """Test that State creates ALL tables and they're queryable."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, "data"), exist_ok=True)
        from state import State
        self.state = State(os.path.join(self.tmpdir, "data", "test.db"))

    def tearDown(self):
        self.state.close()

    def test_all_tables_created(self):
        tables = [r["name"] for r in self.state.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        # Should have 40+ tables
        self.assertGreater(len(tables), 40, f"Only {len(tables)} tables: {sorted(tables)}")

    def test_core_tables_exist(self):
        core = ["applied_jobs", "skipped_jobs", "failed_jobs", "recruiters",
                "visa_sponsors", "daily_stats", "match_scores", "message_queue",
                "salary_data", "interview_prep", "google_jobs"]
        tables = [r["name"] for r in self.state.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for t in core:
            self.assertIn(t, tables, f"Missing core table: {t}")

    def test_new_feature_tables_exist(self):
        new = ["job_evaluations", "story_bank", "job_archetypes",
               "pipeline_states", "interview_sessions", "offers",
               "ghost_predictions", "market_snapshots", "employer_sla",
               "quality_scores", "career_simulations"]
        tables = [r["name"] for r in self.state.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for t in new:
            self.assertIn(t, tables, f"Missing feature table: {t}")


class TestFullApplicationPipeline(unittest.TestCase):
    """Test the complete job application pipeline (mocked browser)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, "data"), exist_ok=True)
        from state import State
        self.state = State(os.path.join(self.tmpdir, "data", "test.db"))
        self.cfg = {
            "search": {"search_terms": ["Developer"], "search_locations": ["NYC"]},
            "personal": {"first_name": "Test", "last_name": "User", "email": "test@test.com"},
            "application": {"years_of_experience": 5},
            "ai": {"enabled": False},
            "match_scoring": {"enabled": False},
            "resume_tailoring": {"enabled": False},
            "filters": {"blacklisted_companies": ["BadCorp"],
                        "bad_title_words": ["Intern"],
                        "visa_positive_keywords": ["visa sponsorship"],
                        "visa_negative_keywords": ["no sponsorship"]},
        }

    def tearDown(self):
        self.state.close()

    def test_mark_applied_with_all_fields(self):
        """Full application record with match score and resume version."""
        self.state.mark_applied(
            job_id="j1", title="Developer", company="ACME",
            location="NYC", match_score=85, resume_version="tailored_v1.pdf",
            salary_info="$120K", visa_sponsorship="yes",
            recruiter_name="Jane", search_term="Developer",
        )
        self.assertTrue(self.state.is_applied("j1"))
        row = self.state.conn.execute(
            "SELECT * FROM applied_jobs WHERE job_id='j1'"
        ).fetchone()
        self.assertEqual(row["match_score"], 85)
        self.assertEqual(row["resume_version"], "tailored_v1.pdf")

    def test_skip_with_match_score(self):
        """Skipped job records match score."""
        self.state.mark_skipped("j2", "PM", "Corp", reason="low score",
                                match_score=35)
        row = self.state.conn.execute(
            "SELECT * FROM skipped_jobs WHERE job_id='j2'"
        ).fetchone()
        self.assertEqual(row["match_score"], 35)

    def test_csv_export_includes_match_score(self):
        """CSV export includes match_score column."""
        self.state.mark_applied(job_id="j1", title="Dev", company="ACME",
                                match_score=90)
        export_dir = os.path.join(self.tmpdir, "export")
        self.state.export_csv(export_dir, self.cfg)

        csv_path = os.path.join(export_dir, "applied_jobs.csv")
        self.assertTrue(os.path.exists(csv_path))
        with open(csv_path) as f:
            header = f.readline()
            self.assertIn("match_score", header)

    def test_daily_stats_tracking(self):
        """Daily stats increment correctly."""
        self.state.mark_applied(job_id="j1", title="A", company="B")
        self.state.mark_applied(job_id="j2", title="C", company="D")
        self.state.mark_skipped("j3", "E", "F", reason="test")
        self.assertEqual(self.state.daily_applied_count(), 2)

    def test_recruiter_tracking(self):
        """Recruiters saved and deduplicated."""
        self.state.save_recruiter("Jane Smith", "VP", "ACME", "j1", "Dev")
        self.state.save_recruiter("Jane Smith", "VP", "ACME", "j1", "Dev")  # dupe
        self.state.save_recruiter("Bob Jones", "HR", "ACME", "j2", "PM")
        recs = self.state.get_all_recruiters()
        self.assertEqual(len(recs), 2)

    def test_visa_sponsor_tracking(self):
        """Visa sponsors tracked with evidence."""
        self.state.save_visa_sponsor("ACME", "H1B", "j1")
        self.state.save_visa_sponsor("ACME", "visa sponsorship", "j2")  # increment
        sponsors = self.state.get_all_visa_sponsors()
        self.assertEqual(len(sponsors), 1)
        self.assertEqual(sponsors[0]["times_seen"], 2)

    def test_session_summary(self):
        """Session summary includes all stats."""
        self.state.mark_applied(job_id="j1", title="A", company="B")
        summary = self.state.session_summary()
        self.assertIn("1A", summary)
        self.assertIn("Today:", summary)


class TestMatchScorerIntegration(unittest.TestCase):
    """Test match scorer with mocked AI."""

    def test_disabled_scorer_always_allows(self):
        from match_scorer import MatchScorer
        scorer = MatchScorer(None, {"match_scoring": {"enabled": False}})
        self.assertTrue(scorer.should_apply(0))
        self.assertTrue(scorer.should_apply(100))

    def test_enabled_scorer_respects_threshold(self):
        from match_scorer import MatchScorer
        scorer = MatchScorer(None, {"match_scoring": {"enabled": True, "minimum_score": 70}})
        self.assertTrue(scorer.should_apply(80))
        self.assertFalse(scorer.should_apply(60))

    def test_parse_score_valid_json(self):
        from match_scorer import MatchScorer
        scorer = MatchScorer(None, {"match_scoring": {"enabled": True}})
        result = scorer._parse_score('{"score": 85, "skill_matches": ["Python"], "missing_skills": ["Go"], "explanation": "Good match"}')
        self.assertEqual(result["score"], 85)
        self.assertEqual(result["skill_matches"], ["Python"])

    def test_parse_score_malformed(self):
        from match_scorer import MatchScorer
        scorer = MatchScorer(None, {"match_scoring": {"enabled": True}})
        result = scorer._parse_score("The score is 72 out of 100")
        self.assertEqual(result["score"], 72)


class TestDedupIntegration(unittest.TestCase):
    """Test dedup across a simulated multi-platform pipeline."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, "data"), exist_ok=True)
        from state import State
        self.state = State(os.path.join(self.tmpdir, "data", "test.db"))
        from dedup_engine import DedupEngine
        self.dedup = DedupEngine(self.state)

    def tearDown(self):
        self.state.close()

    def test_cross_platform_dedup(self):
        """Same job on LinkedIn and Indeed is detected."""
        self.dedup.register_job("li_123", "Software Engineer", "Google Inc.",
                                "Mountain View, CA", "linkedin")
        result = self.dedup.is_duplicate("Software Engineer", "Google",
                                         "Mountain View", "indeed")
        self.assertTrue(result["is_dup"])

    def test_different_jobs_not_deduped(self):
        """Different jobs are not falsely deduped."""
        self.dedup.register_job("li_123", "Software Engineer", "Google",
                                "NYC", "linkedin")
        result = self.dedup.is_duplicate("Data Scientist", "Meta",
                                         "Menlo Park", "indeed")
        self.assertFalse(result["is_dup"])


class TestConfigValidation(unittest.TestCase):
    """Test config validator catches issues."""

    def test_valid_config(self):
        from validate_config import ConfigValidator
        cfg = {
            "search": {"search_terms": ["Dev"], "search_locations": ["NYC"]},
            "personal": {"first_name": "Test"},
            "scheduling": {"max_applies_per_day": 30, "scan_interval_minutes": 10},
        }
        v = ConfigValidator(cfg)
        self.assertTrue(v.validate())

    def test_missing_search_terms(self):
        from validate_config import ConfigValidator
        cfg = {"search": {"search_terms": [], "search_locations": ["NYC"]},
               "personal": {"first_name": "Test"}}
        v = ConfigValidator(cfg)
        self.assertFalse(v.validate())
        self.assertTrue(any("search_terms" in e for e in v.errors))

    def test_conflicting_settings(self):
        from validate_config import ConfigValidator
        cfg = {
            "search": {"search_terms": ["Dev"], "search_locations": ["NYC"],
                        "easy_apply_only": True},
            "personal": {"first_name": "Test"},
            "external_apply": {"enabled": True},
        }
        v = ConfigValidator(cfg)
        v.validate()
        self.assertTrue(any("easy_apply_only" in w for w in v.warnings))


class TestApplyTimingIntegration(unittest.TestCase):
    """Test apply timing queue reordering."""

    def test_freshest_jobs_first(self):
        from apply_timing import ApplyTimingOptimizer
        opt = ApplyTimingOptimizer({"apply_timing": {"enabled": True,
                                                      "prioritize_fresh": True}})
        ids = ["old", "new", "mid"]
        times = {"old": "5 days ago", "new": "Just now", "mid": "6 hours ago"}
        result = opt.prioritize_jobs(ids, times)
        self.assertEqual(result[0], "new")
        self.assertEqual(result[-1], "old")


class TestPluginAPI(unittest.TestCase):
    """Test plugin registration and loading."""

    def test_register_ats(self):
        from plugin_api import PluginRegistry
        reg = PluginRegistry()
        reg.register_ats("bamboohr", object)
        self.assertIn("bamboohr", reg.get_all_ats())

    def test_register_hook(self):
        from plugin_api import PluginRegistry
        reg = PluginRegistry()
        called = []
        reg.register_hook("post_apply", lambda **kw: called.append(True))
        reg.fire_hook("post_apply", job_id="j1")
        self.assertEqual(len(called), 1)

    def test_register_notifier(self):
        from plugin_api import PluginRegistry
        reg = PluginRegistry()
        reg.register_notifier("webhook", lambda msg: True)
        self.assertIn("webhook", reg.get_all_notifiers())

    def test_plugin_loader_creates_dir(self):
        from plugin_api import PluginLoader
        tmpdir = tempfile.mkdtemp()
        plugins_dir = os.path.join(tmpdir, "plugins")
        loader = PluginLoader(plugins_dir=plugins_dir)
        loader.load_all()
        self.assertTrue(os.path.exists(plugins_dir))
        self.assertTrue(os.path.exists(os.path.join(plugins_dir, "example_plugin.py")))

    def test_register_plugin_metadata(self):
        from plugin_api import PluginRegistry
        reg = PluginRegistry()
        reg.register_plugin("test-plugin", "1.0.0", "Author", "Description")
        plugins = reg.get_loaded_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["name"], "test-plugin")


class TestMetricsCollector(unittest.TestCase):
    """Test metrics collection and Prometheus export."""

    def test_counter_increment(self):
        from metrics import MetricsCollector
        m = MetricsCollector()
        m.inc("test_counter")
        m.inc("test_counter", 5)
        output = m.to_prometheus()
        self.assertIn("test_counter", output)
        self.assertIn("6", output)

    def test_gauge_set(self):
        from metrics import MetricsCollector
        m = MetricsCollector()
        m.set_gauge("test_gauge", 42)
        output = m.to_prometheus()
        self.assertIn("test_gauge", output)
        self.assertIn("42", output)

    def test_histogram_observe(self):
        from metrics import MetricsCollector
        m = MetricsCollector()
        for v in [10, 20, 30, 40, 50]:
            m.observe("test_hist", v)
        output = m.to_prometheus()
        self.assertIn("test_hist_count", output)
        self.assertIn("5", output)


class TestCheckpointManager(unittest.TestCase):
    """Test crash recovery checkpoint."""

    def test_save_and_load(self):
        from checkpoint_manager import CheckpointManager
        tmpdir = tempfile.mkdtemp()
        cp_path = os.path.join(tmpdir, "data", "checkpoint.json")
        os.makedirs(os.path.dirname(cp_path), exist_ok=True)
        cp = CheckpointManager(checkpoint_path=cp_path)
        cp.save({"in_cycle": True, "search_term": "Dev", "job_index": 5,
                 "cycle_seen_ids": ["j1", "j2"]})
        loaded = cp.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["search_term"], "Dev")
        self.assertEqual(loaded["job_index"], 5)

    def test_clear(self):
        from checkpoint_manager import CheckpointManager
        tmpdir = tempfile.mkdtemp()
        cp_path = os.path.join(tmpdir, "data", "checkpoint.json")
        os.makedirs(os.path.dirname(cp_path), exist_ok=True)
        cp = CheckpointManager(checkpoint_path=cp_path)
        cp.save({"in_cycle": True})
        cp.clear()
        self.assertIsNone(cp.load())

    def test_resume_point(self):
        from checkpoint_manager import CheckpointManager
        tmpdir = tempfile.mkdtemp()
        cp_path = os.path.join(tmpdir, "data", "checkpoint.json")
        os.makedirs(os.path.dirname(cp_path), exist_ok=True)
        cp = CheckpointManager(checkpoint_path=cp_path)
        cp.save({"in_cycle": True, "search_term": "PM",
                 "location": "London", "job_index": 3,
                 "cycle_seen_ids": ["a", "b"]})
        resume = cp.get_resume_point()
        self.assertEqual(resume["search_term"], "PM")
        self.assertEqual(resume["job_index"], 3)
        self.assertIn("a", resume["cycle_seen_ids"])


class TestRateLimiter(unittest.TestCase):
    """Test dynamic rate limiting."""

    def test_normal_delay(self):
        from rate_limiter import RateLimiter
        rl = RateLimiter({"rate_limiter": {"enabled": True,
                                           "base_delay_min": 1,
                                           "base_delay_max": 2}})
        delay = rl.get_delay()
        self.assertGreaterEqual(delay, 1)
        self.assertLessEqual(delay, 4)  # includes jitter

    def test_escalation(self):
        from rate_limiter import RateLimiter
        rl = RateLimiter({"rate_limiter": {"enabled": True}})
        rl._escalate(3)
        self.assertEqual(rl.throttle_level, 3)
        delay = rl.get_delay()
        self.assertGreater(delay, 10)  # level 3 = 4x multiplier

    def test_should_pause(self):
        from rate_limiter import RateLimiter
        rl = RateLimiter({"rate_limiter": {"enabled": True}})
        self.assertFalse(rl.should_pause_cycle())
        rl._escalate(4)
        self.assertTrue(rl.should_pause_cycle())


if __name__ == "__main__":
    unittest.main()
