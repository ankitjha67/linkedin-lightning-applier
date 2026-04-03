"""
Company Intelligence Enrichment.

Before applying, enriches company data: Glassdoor rating, size, industry,
funding stage, headquarters. Factors into match scoring.
Can skip companies below a minimum rating threshold.
"""

import logging
import re
import time
from typing import Optional

log = logging.getLogger("lla.company_intel")


class CompanyIntel:
    """Enrich company data from multiple sources."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        ci_cfg = cfg.get("company_intel", {})
        self.enabled = ci_cfg.get("enabled", False)
        self.min_rating = ci_cfg.get("min_glassdoor_rating", 0)
        self.enrich_from_description = ci_cfg.get("enrich_from_description", True)
        self.cache_days = ci_cfg.get("cache_days", 30)

    def enrich(self, company: str, description: str = "",
               driver=None) -> dict:
        """
        Enrich company data. Returns dict with all available intel.
        Checks cache first, then tries multiple enrichment methods.
        """
        if not self.enabled:
            return {}

        # Check cache
        cached = self.state.get_company_intel(company)
        if cached and cached.get("glassdoor_rating", 0) > 0:
            return cached

        intel = {
            "company": company,
            "glassdoor_rating": 0,
            "company_size": "",
            "industry": "",
            "funding_stage": "",
            "headquarters": "",
            "description": "",
        }

        # Method 1: Extract from job description using AI
        if self.enrich_from_description and description:
            ai_intel = self._extract_from_description(company, description)
            intel.update({k: v for k, v in ai_intel.items() if v})

        # Method 2: Scrape from Glassdoor (if driver available)
        if driver:
            gd_intel = self._scrape_glassdoor(driver, company)
            intel.update({k: v for k, v in gd_intel.items() if v})

        # Method 3: Extract from LinkedIn company page
        if driver:
            li_intel = self._scrape_linkedin_company(driver, company)
            intel.update({k: v for k, v in li_intel.items() if v})

        # Save to cache
        if any(v for k, v in intel.items() if k != "company"):
            self.state.save_company_intel(**intel)

        return intel

    def should_skip(self, company: str) -> tuple[bool, str]:
        """Check if company should be skipped based on intel."""
        if not self.enabled or self.min_rating <= 0:
            return False, ""

        intel = self.state.get_company_intel(company)
        if not intel:
            return False, ""

        rating = intel.get("glassdoor_rating", 0)
        if rating > 0 and rating < self.min_rating:
            return True, f"low Glassdoor rating: {rating} (min: {self.min_rating})"

        return False, ""

    def _extract_from_description(self, company: str, description: str) -> dict:
        """Use AI to extract company info from job description."""
        if not self.ai or not self.ai.enabled:
            return {}

        system = """Extract company information from this job description.
Return ONLY a JSON object with these fields (empty string if unknown):
{"company_size": "", "industry": "", "funding_stage": "", "headquarters": "", "description": ""}

company_size: e.g. "1000-5000", "startup", "enterprise", "Fortune 500"
industry: e.g. "Financial Services", "Technology", "Healthcare"
funding_stage: e.g. "Series B", "Public", "Private", "Bootstrapped"
headquarters: city/country if mentioned
description: 1 sentence about what the company does"""

        user = f"Company: {company}\nJob Description:\n{description[:1500]}"

        try:
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 300
            result = self.ai._call_llm(system, user)
            self.ai.max_tokens = old_max

            if result:
                import json
                json_match = re.search(r'\{[^{}]+\}', result, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group())
                    return {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
        except Exception as e:
            log.debug(f"AI company intel extraction failed: {e}")

        return {}

    def _scrape_glassdoor(self, driver, company: str) -> dict:
        """Scrape Glassdoor for company rating and info."""
        from selenium.webdriver.common.by import By

        intel = {}
        original_url = driver.current_url

        try:
            # Search Glassdoor for the company
            company_slug = re.sub(r'[^\w]', '-', company.lower()).strip('-')
            search_url = f"https://www.glassdoor.com/Search/results.htm?keyword={company_slug}"

            driver.get(search_url)
            time.sleep(3)

            # Try to find company card with rating
            rating_els = driver.find_elements(By.CSS_SELECTOR,
                '[data-test="rating"], span[class*="rating"], div[class*="ratingNum"]')
            for el in rating_els:
                text = el.text.strip()
                try:
                    rating = float(text)
                    if 0 < rating <= 5:
                        intel["glassdoor_rating"] = rating
                        log.debug(f"  Glassdoor rating for {company}: {rating}")
                        break
                except (ValueError, TypeError):
                    continue

            # Company size
            for sel in ['[data-test="employer-size"]', 'span[class*="size"]']:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    intel["company_size"] = els[0].text.strip()
                    break

            # Industry
            for sel in ['[data-test="employer-industry"]', 'span[class*="industry"]']:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    intel["industry"] = els[0].text.strip()
                    break

        except Exception as e:
            log.debug(f"Glassdoor scrape failed for {company}: {e}")
        finally:
            try:
                driver.get(original_url)
                time.sleep(2)
            except Exception:
                pass

        return intel

    def _scrape_linkedin_company(self, driver, company: str) -> dict:
        """Extract company info from LinkedIn company page."""
        from selenium.webdriver.common.by import By

        intel = {}
        original_url = driver.current_url

        try:
            # Search LinkedIn for company
            search_url = (
                f"https://www.linkedin.com/search/results/companies/"
                f"?keywords={company}&origin=SWITCH_SEARCH_VERTICAL"
            )
            driver.get(search_url)
            time.sleep(3)

            # Click first company result
            company_links = driver.find_elements(By.CSS_SELECTOR,
                'a[href*="/company/"] span.entity-result__title-text')
            if company_links:
                parent_link = company_links[0].find_element(By.XPATH,
                    "./ancestor::a[contains(@href,'/company/')]")
                company_url = parent_link.get_attribute("href")
                if company_url:
                    # Go to about page
                    about_url = company_url.rstrip("/") + "/about/"
                    driver.get(about_url)
                    time.sleep(3)

                    # Extract details
                    for dt in driver.find_elements(By.TAG_NAME, "dt"):
                        label = dt.text.strip().lower()
                        try:
                            dd = dt.find_element(By.XPATH, "./following-sibling::dd[1]")
                            value = dd.text.strip()
                        except Exception:
                            continue

                        if "company size" in label or "employees" in label:
                            intel["company_size"] = value
                        elif "industry" in label:
                            intel["industry"] = value
                        elif "headquarters" in label:
                            intel["headquarters"] = value
                        elif "founded" in label:
                            intel["funding_stage"] = f"Founded {value}"

                    # Company description
                    for sel in ['section.org-about-company-module p',
                                'p[class*="about"]', '.org-about-us-organization-description__text']:
                        els = driver.find_elements(By.CSS_SELECTOR, sel)
                        if els and els[0].text.strip():
                            intel["description"] = els[0].text.strip()[:300]
                            break

        except Exception as e:
            log.debug(f"LinkedIn company scrape failed: {e}")
        finally:
            try:
                driver.get(original_url)
                time.sleep(2)
            except Exception:
                pass

        return intel

    def get_company_report(self, company: str) -> str:
        """Generate a company intelligence report."""
        intel = self.state.get_company_intel(company)
        if not intel:
            return f"No intelligence data for {company}"

        lines = [f"Company Intelligence: {company}", "-" * 40]
        if intel.get("glassdoor_rating"):
            lines.append(f"  Glassdoor Rating: {intel['glassdoor_rating']}/5.0")
        if intel.get("company_size"):
            lines.append(f"  Size: {intel['company_size']}")
        if intel.get("industry"):
            lines.append(f"  Industry: {intel['industry']}")
        if intel.get("funding_stage"):
            lines.append(f"  Stage: {intel['funding_stage']}")
        if intel.get("headquarters"):
            lines.append(f"  HQ: {intel['headquarters']}")
        if intel.get("description"):
            lines.append(f"  About: {intel['description'][:200]}")

        return "\n".join(lines)
