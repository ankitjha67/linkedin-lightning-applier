"""Tests for validate_config.py — ConfigValidator class."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from validate_config import ConfigValidator


def _minimal_valid_config():
    """Return a minimal config that passes all required checks."""
    return {
        "search": {
            "search_terms": ["Software Engineer"],
            "search_locations": ["New York"],
            "easy_apply_only": True,
        },
        "personal": {
            "email": "test@example.com",
            "first_name": "John",
        },
        "linkedin": {
            "email": "test@example.com",
            "password": "secret",
        },
        "ai": {
            "enabled": True,
            "provider": "ollama",
            "cv_text": "Experienced engineer.",
        },
        "scheduling": {
            "max_applies_per_day": 40,
            "max_applies_per_cycle": 15,
            "scan_interval_minutes": 10,
        },
        "match_scoring": {
            "minimum_score": 70,
        },
    }


class TestValidConfigPasses(unittest.TestCase):
    """Test that a valid config passes validation."""

    def test_valid_config_returns_true(self):
        cfg = _minimal_valid_config()
        v = ConfigValidator(cfg)
        result = v.validate()
        self.assertTrue(result)

    def test_valid_config_no_errors(self):
        cfg = _minimal_valid_config()
        v = ConfigValidator(cfg)
        v.validate()
        self.assertEqual(len(v.errors), 0)


class TestMissingFields(unittest.TestCase):
    """Test warnings/errors for missing fields."""

    def test_missing_email_warns(self):
        cfg = _minimal_valid_config()
        cfg["personal"]["email"] = ""
        cfg["linkedin"]["email"] = ""
        cfg["linkedin"]["password"] = ""
        v = ConfigValidator(cfg)
        v.validate()
        warning_text = " ".join(v.warnings)
        self.assertTrue(
            "email" in warning_text.lower() or "login" in warning_text.lower(),
            f"Expected email warning, got: {v.warnings}"
        )

    def test_missing_search_section_errors(self):
        cfg = _minimal_valid_config()
        del cfg["search"]
        v = ConfigValidator(cfg)
        result = v.validate()
        self.assertFalse(result)
        self.assertTrue(any("search" in e.lower() for e in v.errors))

    def test_missing_personal_section_errors(self):
        cfg = _minimal_valid_config()
        del cfg["personal"]
        v = ConfigValidator(cfg)
        result = v.validate()
        self.assertFalse(result)

    def test_empty_search_terms_errors(self):
        cfg = _minimal_valid_config()
        cfg["search"]["search_terms"] = []
        v = ConfigValidator(cfg)
        result = v.validate()
        self.assertFalse(result)
        self.assertTrue(any("search_terms" in e for e in v.errors))


class TestUnknownProvider(unittest.TestCase):
    """Test warning for unknown AI provider."""

    def test_unknown_provider_warns(self):
        cfg = _minimal_valid_config()
        cfg["ai"]["provider"] = "fake_provider_xyz"
        v = ConfigValidator(cfg)
        v.validate()
        self.assertTrue(any("unknown" in w.lower() and "provider" in w.lower()
                           for w in v.warnings),
                       f"Expected unknown provider warning, got: {v.warnings}")

    def test_known_provider_no_warning(self):
        cfg = _minimal_valid_config()
        cfg["ai"]["provider"] = "ollama"
        v = ConfigValidator(cfg)
        v.validate()
        provider_warnings = [w for w in v.warnings if "provider" in w.lower()]
        self.assertEqual(len(provider_warnings), 0)

    def test_disabled_ai_skips_provider_check(self):
        cfg = _minimal_valid_config()
        cfg["ai"]["enabled"] = False
        cfg["ai"]["provider"] = "nonexistent"
        v = ConfigValidator(cfg)
        v.validate()
        provider_warnings = [w for w in v.warnings if "provider" in w.lower()]
        self.assertEqual(len(provider_warnings), 0)


class TestNumericValues(unittest.TestCase):
    """Test validation of numeric configuration values."""

    def test_negative_max_applies_caught(self):
        cfg = _minimal_valid_config()
        cfg["scheduling"]["max_applies_per_day"] = -5
        v = ConfigValidator(cfg)
        v.validate()
        # Should have a warning about the value being outside range
        range_issues = [w for w in v.warnings if "max_applies_per_day" in w]
        self.assertGreater(len(range_issues), 0,
                          f"Expected numeric range warning, got warnings: {v.warnings}")

    def test_zero_port_caught(self):
        cfg = _minimal_valid_config()
        cfg["dashboard"] = {"port": 0}
        v = ConfigValidator(cfg)
        v.validate()
        port_issues = [w for w in v.warnings if "port" in w]
        self.assertGreater(len(port_issues), 0)

    def test_valid_port_no_warning(self):
        cfg = _minimal_valid_config()
        cfg["dashboard"] = {"port": 5000}
        v = ConfigValidator(cfg)
        v.validate()
        port_issues = [w for w in v.warnings if "port" in w]
        self.assertEqual(len(port_issues), 0)


class TestConflictingSettings(unittest.TestCase):
    """Test detection of conflicting configuration combinations."""

    def test_easy_apply_with_external_warns(self):
        cfg = _minimal_valid_config()
        cfg["search"]["easy_apply_only"] = True
        cfg["external_apply"] = {"enabled": True}
        v = ConfigValidator(cfg)
        v.validate()
        conflict_warnings = [w for w in v.warnings if "conflict" in w.lower()
                            or "easy_apply" in w.lower()]
        self.assertGreater(len(conflict_warnings), 0,
                          f"Expected conflict warning, got: {v.warnings}")

    def test_resume_tailoring_without_match_scoring_warns(self):
        cfg = _minimal_valid_config()
        cfg["resume_tailoring"] = {"enabled": True}
        cfg["match_scoring"]["enabled"] = False
        v = ConfigValidator(cfg)
        v.validate()
        relevant = [w for w in v.warnings if "resume_tailoring" in w.lower()]
        self.assertGreater(len(relevant), 0,
                          f"Expected tailoring warning, got: {v.warnings}")


class TestGetReport(unittest.TestCase):
    """Test report generation."""

    def test_report_is_string(self):
        cfg = _minimal_valid_config()
        v = ConfigValidator(cfg)
        v.validate()
        report = v.get_report()
        self.assertIsInstance(report, str)
        self.assertIn("Config Validation", report)

    def test_report_shows_errors(self):
        cfg = {}
        v = ConfigValidator(cfg)
        v.validate()
        report = v.get_report()
        self.assertIn("ERROR", report)


if __name__ == "__main__":
    unittest.main()
