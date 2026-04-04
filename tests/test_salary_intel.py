"""Tests for salary_intel.py — SalaryIntel class."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from salary_intel import SalaryIntel
from state import State


class MockState:
    """Minimal state mock for tests that don't need DB."""
    pass


class TestParseSalary(unittest.TestCase):
    """Test parse_salary with various formats and currencies."""

    def setUp(self):
        self.si = SalaryIntel(state=MockState(), cfg={"salary_intelligence": {"enabled": True}})

    def test_usd_range_yearly(self):
        result = self.si.parse_salary("$120,000 - $150,000/yr")
        self.assertIsNotNone(result)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["min"], 120000)
        self.assertEqual(result["max"], 150000)
        self.assertEqual(result["period"], "yearly")

    def test_gbp_k_suffix(self):
        result = self.si.parse_salary("£85K-£110K")
        self.assertIsNotNone(result)
        self.assertEqual(result["currency"], "GBP")
        self.assertEqual(result["min"], 85000)
        self.assertEqual(result["max"], 110000)

    def test_inr_lpa(self):
        result = self.si.parse_salary("₹20-30 LPA")
        self.assertIsNotNone(result)
        self.assertEqual(result["currency"], "INR")
        self.assertEqual(result["min"], 2000000)
        self.assertEqual(result["max"], 3000000)

    def test_eur_range(self):
        result = self.si.parse_salary("€60,000 - €80,000")
        self.assertIsNotNone(result)
        self.assertEqual(result["currency"], "EUR")
        self.assertEqual(result["min"], 60000)
        self.assertEqual(result["max"], 80000)

    def test_usd_hourly(self):
        result = self.si.parse_salary("$50/hr")
        self.assertIsNotNone(result)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["period"], "hourly")
        self.assertEqual(result["min"], 50)

    def test_gbp_plain_number(self):
        result = self.si.parse_salary("80000 GBP")
        self.assertIsNotNone(result)
        self.assertEqual(result["currency"], "GBP")
        self.assertEqual(result["min"], 80000)

    def test_aud_k_suffix(self):
        result = self.si.parse_salary("A$90K-A$120K")
        self.assertIsNotNone(result)
        self.assertEqual(result["currency"], "AUD")
        self.assertEqual(result["min"], 90000)
        self.assertEqual(result["max"], 120000)

    def test_none_input(self):
        result = self.si.parse_salary(None)
        self.assertIsNone(result)

    def test_empty_string(self):
        result = self.si.parse_salary("")
        self.assertIsNone(result)

    def test_no_numbers(self):
        result = self.si.parse_salary("Competitive salary")
        self.assertIsNone(result)

    def test_default_currency_usd(self):
        result = self.si.parse_salary("120000 - 150000")
        self.assertIsNotNone(result)
        self.assertEqual(result["currency"], "USD")

    def test_monthly_period_detected(self):
        result = self.si.parse_salary("$8,000/month")
        self.assertIsNotNone(result)
        self.assertEqual(result["period"], "monthly")


class TestCurrencyDetection(unittest.TestCase):
    """Test currency detection for all supported symbols."""

    def setUp(self):
        self.si = SalaryIntel(state=MockState(), cfg={"salary_intelligence": {"enabled": True}})

    def test_dollar(self):
        result = self.si.parse_salary("$100,000")
        self.assertEqual(result["currency"], "USD")

    def test_pound(self):
        result = self.si.parse_salary("£80,000")
        self.assertEqual(result["currency"], "GBP")

    def test_euro(self):
        result = self.si.parse_salary("€70,000")
        self.assertEqual(result["currency"], "EUR")

    def test_rupee(self):
        result = self.si.parse_salary("₹1500000")
        self.assertEqual(result["currency"], "INR")

    def test_cad_code(self):
        result = self.si.parse_salary("90000 CAD")
        self.assertEqual(result["currency"], "CAD")

    def test_sgd_code(self):
        result = self.si.parse_salary("80000 SGD")
        self.assertEqual(result["currency"], "SGD")


class TestGetBenchmark(unittest.TestCase):
    """Test get_benchmark with real State backend."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.state = State(os.path.join(self.tmp_dir, "test.db"))
        self.si = SalaryIntel(state=self.state, cfg={"salary_intelligence": {"enabled": True}})

    def tearDown(self):
        self.state.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_benchmark_returns_correct_median(self):
        self.state.save_salary_data("j1", title="SWE", salary_min=100000,
                                    salary_max=150000, currency="USD")
        self.state.save_salary_data("j2", title="SWE", salary_min=120000,
                                    salary_max=170000, currency="USD")
        self.state.save_salary_data("j3", title="SWE", salary_min=140000,
                                    salary_max=190000, currency="USD")
        bench = self.si.get_benchmark("swe")
        self.assertEqual(bench["count"], 3)
        self.assertEqual(bench["median_min"], 120000)

    def test_benchmark_empty(self):
        bench = self.si.get_benchmark("nonexistent")
        self.assertEqual(bench["count"], 0)


if __name__ == "__main__":
    unittest.main()
