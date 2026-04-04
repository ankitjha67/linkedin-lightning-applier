"""Tests for apply_timing.py — ApplyTimingOptimizer class."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apply_timing import ApplyTimingOptimizer


class TestParsePostedTime(unittest.TestCase):
    """Test parse_posted_time with various LinkedIn time strings."""

    def setUp(self):
        self.opt = ApplyTimingOptimizer(cfg={"apply_timing": {"enabled": True}})

    def test_just_now(self):
        hours = self.opt.parse_posted_time("Just now")
        self.assertIsNotNone(hours)
        self.assertLess(hours, 1)

    def test_hours_ago(self):
        hours = self.opt.parse_posted_time("3 hours ago")
        self.assertEqual(hours, 3.0)

    def test_days_ago(self):
        hours = self.opt.parse_posted_time("2 days ago")
        self.assertEqual(hours, 48.0)

    def test_week_ago(self):
        hours = self.opt.parse_posted_time("1 week ago")
        self.assertEqual(hours, 168.0)

    def test_minutes_ago(self):
        hours = self.opt.parse_posted_time("30 minutes ago")
        self.assertAlmostEqual(hours, 0.5, places=1)

    def test_reposted_prefix_stripped(self):
        hours = self.opt.parse_posted_time("Reposted 5 hours ago")
        self.assertEqual(hours, 5.0)

    def test_months_ago(self):
        hours = self.opt.parse_posted_time("2 months ago")
        self.assertEqual(hours, 1440.0)

    def test_none_input(self):
        self.assertIsNone(self.opt.parse_posted_time(None))

    def test_empty_string(self):
        self.assertIsNone(self.opt.parse_posted_time(""))

    def test_unparseable(self):
        self.assertIsNone(self.opt.parse_posted_time("yesterday"))


class TestGetFreshnessScore(unittest.TestCase):
    """Test freshness scoring curve."""

    def setUp(self):
        self.opt = ApplyTimingOptimizer(cfg={"apply_timing": {"enabled": True}})

    def test_zero_hours_perfect(self):
        self.assertEqual(self.opt.get_freshness_score(0), 1.0)

    def test_one_hour_perfect(self):
        self.assertEqual(self.opt.get_freshness_score(1), 1.0)

    def test_25_hours_moderate(self):
        score = self.opt.get_freshness_score(25)
        self.assertEqual(score, 0.3)  # 25h is in 24-48h bucket

    def test_200_hours_very_low(self):
        score = self.opt.get_freshness_score(200)
        self.assertLess(score, 0.15)

    def test_none_returns_neutral(self):
        self.assertEqual(self.opt.get_freshness_score(None), 0.5)

    def test_very_old_returns_minimal(self):
        score = self.opt.get_freshness_score(10000)
        self.assertLessEqual(score, 0.05)


class TestPrioritizeJobs(unittest.TestCase):
    """Test job reordering by freshness."""

    def setUp(self):
        self.opt = ApplyTimingOptimizer(cfg={
            "apply_timing": {"enabled": True, "prioritize_fresh": True}
        })

    def test_reorders_newest_first(self):
        job_ids = ["old", "new", "mid"]
        posted = {
            "old": "5 days ago",
            "new": "1 hour ago",
            "mid": "1 day ago",
        }
        result = self.opt.prioritize_jobs(job_ids, posted)
        self.assertEqual(result[0], "new")

    def test_disabled_preserves_order(self):
        opt = ApplyTimingOptimizer(cfg={
            "apply_timing": {"enabled": False, "prioritize_fresh": True}
        })
        job_ids = ["c", "b", "a"]
        result = opt.prioritize_jobs(job_ids, {})
        self.assertEqual(result, ["c", "b", "a"])

    def test_empty_list(self):
        result = self.opt.prioritize_jobs([], {})
        self.assertEqual(result, [])

    def test_single_job(self):
        result = self.opt.prioritize_jobs(["only"], {"only": "2 hours ago"})
        self.assertEqual(result, ["only"])


class TestShouldSkipStale(unittest.TestCase):
    """Test stale job detection."""

    def test_stale_job_skipped(self):
        opt = ApplyTimingOptimizer(cfg={
            "apply_timing": {
                "enabled": True,
                "skip_stale": True,
                "stale_threshold_hours": 168,
            }
        })
        skip, reason = opt.should_skip_stale(200)
        self.assertTrue(skip)
        self.assertIn("stale", reason)

    def test_fresh_job_not_skipped(self):
        opt = ApplyTimingOptimizer(cfg={
            "apply_timing": {
                "enabled": True,
                "skip_stale": True,
                "stale_threshold_hours": 168,
            }
        })
        skip, _ = opt.should_skip_stale(24)
        self.assertFalse(skip)

    def test_disabled_never_skips(self):
        opt = ApplyTimingOptimizer(cfg={
            "apply_timing": {"enabled": False, "skip_stale": True}
        })
        skip, _ = opt.should_skip_stale(10000)
        self.assertFalse(skip)

    def test_none_hours_not_skipped(self):
        opt = ApplyTimingOptimizer(cfg={
            "apply_timing": {
                "enabled": True,
                "skip_stale": True,
                "stale_threshold_hours": 168,
            }
        })
        skip, _ = opt.should_skip_stale(None)
        self.assertFalse(skip)


if __name__ == "__main__":
    unittest.main()
