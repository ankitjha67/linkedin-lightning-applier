"""
Salary Intelligence Engine.

Aggregates salary data from every job processed (posted salary, parsed ranges).
Builds personal salary benchmarks by role and location. Exports to CSV.
"""

import logging
import re
from typing import Optional

log = logging.getLogger("lla.salary_intel")

# Currency patterns
CURRENCY_PATTERNS = {
    "$": "USD", "£": "GBP", "€": "EUR", "₹": "INR", "¥": "JPY",
    "A$": "AUD", "C$": "CAD", "S$": "SGD", "AED": "AED", "HK$": "HKD",
}


class SalaryIntel:
    """Parse, store, and benchmark salary data from job postings."""

    def __init__(self, state, ai=None, cfg: dict = None):
        self.state = state
        self.ai = ai
        self.cfg = cfg or {}
        si_cfg = self.cfg.get("salary_intelligence", {})
        self.enabled = si_cfg.get("enabled", False)
        self.export_csv = si_cfg.get("export_csv", True)

    def parse_and_store(self, job_id: str, title: str, company: str,
                        location: str, salary_raw: str, source: str = "linkedin"):
        """Parse salary string and store structured data."""
        if not self.enabled or not salary_raw:
            return

        parsed = self.parse_salary(salary_raw)
        if not parsed:
            return

        self.state.save_salary_data(
            job_id=job_id, title=title, company=company,
            location=location, salary_raw=salary_raw,
            salary_min=parsed["min"], salary_max=parsed["max"],
            currency=parsed["currency"], period=parsed["period"],
            source=source,
        )

        log.debug(f"  💰 Salary stored: {parsed['currency']} {parsed['min']:,.0f}-{parsed['max']:,.0f} {parsed['period']}")

    def parse_salary(self, text: str) -> Optional[dict]:
        """
        Parse salary text into structured data.

        Handles formats like:
            "$120,000 - $150,000/yr"
            "£85K-£110K per year"
            "₹20-30 LPA"
            "$50/hr"
            "80,000 - 100,000 GBP"
        """
        if not text:
            return None

        text = text.strip()

        # Detect currency (check multi-char symbols first to avoid
        # "$" matching before "A$", "C$", "S$", "HK$")
        currency = ""
        for symbol, code in sorted(CURRENCY_PATTERNS.items(),
                                    key=lambda x: len(x[0]), reverse=True):
            if symbol in text:
                currency = code
                break

        # Check for currency codes
        if not currency:
            for code in ["USD", "GBP", "EUR", "INR", "AED", "SGD", "CAD", "AUD", "HKD", "JPY"]:
                if code in text.upper():
                    currency = code
                    break

        if not currency:
            currency = "USD"  # Default assumption

        # Detect period
        period = "yearly"
        text_lower = text.lower()
        if any(x in text_lower for x in ["/hr", "per hour", "hourly", "/hour"]):
            period = "hourly"
        elif any(x in text_lower for x in ["/mo", "per month", "monthly", "/month"]):
            period = "monthly"

        # Extract numbers
        # Remove currency symbols and commas for parsing
        clean = re.sub(r'[£$€₹¥]', '', text)
        clean = clean.replace(',', '')

        # Look for ranges: "120000 - 150000" or "120K - 150K" or "120-150K"
        # Also handle LPA (Lakhs Per Annum) for India
        numbers = []

        # Handle K/k suffix (thousands)
        k_matches = re.findall(r'(\d+(?:\.\d+)?)\s*[Kk]', clean)
        if k_matches:
            numbers = [float(n) * 1000 for n in k_matches]

        # Handle LPA (Lakhs Per Annum)
        if not numbers:
            lpa_matches = re.findall(r'(\d+(?:\.\d+)?)\s*(?:[-–to\s]+(\d+(?:\.\d+)?)\s*)?(?:LPA|lpa|lakhs?)', clean)
            if lpa_matches:
                for match in lpa_matches:
                    numbers.append(float(match[0]) * 100000)
                    if match[1]:
                        numbers.append(float(match[1]) * 100000)

        # Handle plain numbers
        if not numbers:
            num_matches = re.findall(r'(\d{4,}(?:\.\d+)?)', clean)
            if num_matches:
                numbers = [float(n) for n in num_matches]

        # Handle small numbers (likely hourly or in thousands)
        if not numbers:
            small_matches = re.findall(r'(\d+(?:\.\d+)?)', clean)
            if small_matches:
                numbers = [float(n) for n in small_matches if float(n) > 0]

        if not numbers:
            return None

        # Convert hourly to yearly for comparison
        salary_min = min(numbers)
        salary_max = max(numbers) if len(numbers) > 1 else salary_min

        # Sanity check: if numbers are too small, likely in thousands
        if salary_max < 500 and period == "yearly":
            salary_min *= 1000
            salary_max *= 1000

        return {
            "min": salary_min,
            "max": salary_max,
            "currency": currency,
            "period": period,
        }

    def get_benchmark(self, title_pattern: str = "", location_pattern: str = "") -> dict:
        """Get salary benchmark for a role/location combination."""
        return self.state.get_salary_benchmark(title_pattern, location_pattern)

    def get_benchmark_report(self, title_pattern: str = "", location_pattern: str = "") -> str:
        """Generate a human-readable salary benchmark report."""
        data = self.get_benchmark(title_pattern, location_pattern)
        if data["count"] == 0:
            return f"No salary data available for '{title_pattern}' in '{location_pattern}'"

        return (
            f"Salary Benchmark: {title_pattern or 'All roles'} in {location_pattern or 'All locations'}\n"
            f"  Data points: {data['count']}\n"
            f"  Range: {data['currency']} {data['min_salary']:,.0f} - {data['max_salary']:,.0f}\n"
            f"  Median: {data['currency']} {data['median_min']:,.0f} - {data['median_max']:,.0f}\n"
        )

    def export_salary_csv(self, export_dir: str = "data"):
        """Export salary data to CSV (handled by state.export_csv)."""
        if self.enabled and self.export_csv:
            self.state.export_csv(export_dir, self.cfg)
