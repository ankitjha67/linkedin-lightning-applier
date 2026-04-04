"""Tests for jd_change_tracker.py — JDChangeTracker class."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jd_change_tracker import JDChangeTracker
from state import State


class TestCaptureSnapshot(unittest.TestCase):
    """Test capture_snapshot stores hash in DB."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))
        self.cfg = {"jd_tracking": {"enabled": True, "min_change_ratio": 0.05}}
        self.tracker = JDChangeTracker(self.cfg, self.state)

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_capture_returns_hash(self):
        h = self.tracker.capture_snapshot("j1", "We are looking for a SWE.")
        self.assertIsNotNone(h)
        self.assertEqual(len(h), 64)

    def test_capture_stores_in_db(self):
        self.tracker.capture_snapshot("j1", "We are looking for a SWE.")
        count = self.tracker.get_snapshot_count("j1")
        self.assertEqual(count, 1)

    def test_same_description_no_duplicate(self):
        desc = "We are looking for a SWE."
        self.tracker.capture_snapshot("j1", desc)
        self.tracker.capture_snapshot("j1", desc)
        count = self.tracker.get_snapshot_count("j1")
        self.assertEqual(count, 1)

    def test_disabled_returns_none(self):
        tracker = JDChangeTracker({"jd_tracking": {"enabled": False}}, self.state)
        h = tracker.capture_snapshot("j1", "description")
        self.assertIsNone(h)

    def test_empty_description_returns_none(self):
        h = self.tracker.capture_snapshot("j1", "")
        self.assertIsNone(h)


class TestCheckForChanges(unittest.TestCase):
    """Test check_for_changes detects modifications."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))
        self.cfg = {"jd_tracking": {"enabled": True, "min_change_ratio": 0.05}}
        self.tracker = JDChangeTracker(self.cfg, self.state)

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_detects_text_changes(self):
        old_desc = "We are looking for a Software Engineer with Python experience."
        new_desc = "We need a Machine Learning Engineer with TensorFlow and PyTorch expertise. Must have PhD."
        self.tracker.capture_snapshot("j1", old_desc)
        changes = self.tracker.check_for_changes("j1", new_desc)
        self.assertGreater(len(changes), 0)
        change_types = [c["type"] for c in changes]
        self.assertTrue(any(t in ("description_modified", "requirements_added", "requirements_removed")
                           for t in change_types))

    def test_detects_salary_changes(self):
        old_desc = "SWE role"
        self.tracker.capture_snapshot("j1", old_desc, salary_info="$100K-$120K")
        changes = self.tracker.check_for_changes("j1", old_desc + " updated",
                                                  new_salary="$120K-$150K")
        change_types = [c["type"] for c in changes]
        self.assertIn("salary_change", change_types)

    def test_no_changes_returns_empty(self):
        desc = "We are looking for a SWE with Python experience."
        self.tracker.capture_snapshot("j1", desc)
        changes = self.tracker.check_for_changes("j1", desc)
        self.assertEqual(len(changes), 0)

    def test_first_check_captures_baseline(self):
        # No snapshot exists yet; should capture and return empty
        changes = self.tracker.check_for_changes("j_new", "Some description")
        self.assertEqual(len(changes), 0)
        # Verify snapshot was captured
        count = self.tracker.get_snapshot_count("j_new")
        self.assertEqual(count, 1)

    def test_salary_added(self):
        desc = "SWE role description here."
        self.tracker.capture_snapshot("j1", desc, salary_info="")
        # Minor text change to bypass hash check
        changes = self.tracker.check_for_changes("j1", desc + " apply now",
                                                  new_salary="$150K")
        change_types = [c["type"] for c in changes]
        self.assertIn("salary_change", change_types)

    def test_disabled_returns_empty(self):
        tracker = JDChangeTracker({"jd_tracking": {"enabled": False}}, self.state)
        changes = tracker.check_for_changes("j1", "new desc")
        self.assertEqual(len(changes), 0)


class TestDetectChangeType(unittest.TestCase):
    """Test detect_change_type classifications."""

    def setUp(self):
        self.cfg = {"jd_tracking": {"enabled": True, "min_change_ratio": 0.05}}
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))
        self.tracker = JDChangeTracker(self.cfg, self.state)

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_salary_change_inline(self):
        old = "We offer $100,000 - $120,000/yr for this role."
        new = "We offer $130,000 - $150,000/yr for this role."
        changes = self.tracker.detect_change_type(old, new)
        types = [c["type"] for c in changes]
        self.assertIn("salary_change", types)

    def test_urgency_added(self):
        old = "We are looking for a software engineer to join our team."
        new = "URGENT: We need an immediate start software engineer. This is a critical hire."
        changes = self.tracker.detect_change_type(old, new)
        types = [c["type"] for c in changes]
        self.assertIn("urgency_added", types)

    def test_requirements_added(self):
        old = "Job description:\n- Python experience\n- Team player"
        new = "Job description:\n- Python experience\n- Team player\n- Must have AWS certification\n- 5+ years backend development"
        changes = self.tracker.detect_change_type(old, new)
        types = [c["type"] for c in changes]
        self.assertIn("requirements_added", types)

    def test_requirements_removed(self):
        old = "Requirements:\n- Python experience\n- AWS certification required\n- PhD in Computer Science"
        new = "Requirements:\n- Python experience"
        changes = self.tracker.detect_change_type(old, new)
        types = [c["type"] for c in changes]
        self.assertIn("requirements_removed", types)

    def test_no_change_below_threshold(self):
        old = "We are looking for a software engineer."
        new = "We are looking for a software engineer!"  # Trivial change
        changes = self.tracker.detect_change_type(old, new)
        self.assertEqual(len(changes), 0)

    def test_empty_old_description(self):
        changes = self.tracker.detect_change_type("", "New description here.")
        types = [c["type"] for c in changes]
        self.assertIn("description_modified", types)

    def test_both_none(self):
        changes = self.tracker.detect_change_type(None, None)
        self.assertEqual(len(changes), 0)


if __name__ == "__main__":
    unittest.main()
