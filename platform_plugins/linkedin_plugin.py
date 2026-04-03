"""
LinkedIn Platform Plugin.

Wraps existing linkedin.py functions into the JobPlatform interface.
This is the reference implementation for the plugin system.
"""

from typing import Optional
from .base import JobPlatform


class LinkedInPlugin(JobPlatform):
    """LinkedIn job platform implementation."""

    @property
    def name(self) -> str:
        return "linkedin"

    def create_browser(self, cfg: dict):
        from linkedin import create_browser
        return create_browser(cfg)

    def login(self, driver, cfg: dict) -> bool:
        from linkedin import login
        return login(driver, cfg)

    def build_search_url(self, cfg: dict, term: str, location: str) -> str:
        from linkedin import build_search_url
        return build_search_url(cfg, term, location)

    def navigate_to_search(self, driver, url: str):
        from linkedin import navigate_to_search
        navigate_to_search(driver, url)

    def get_job_cards(self, driver) -> list:
        from linkedin import get_job_cards
        return get_job_cards(driver)

    def extract_job_info(self, driver, card) -> Optional[dict]:
        from linkedin import extract_job_info
        return extract_job_info(driver, card)

    def get_job_description(self, driver) -> str:
        from linkedin import get_job_description
        return get_job_description(driver)

    def apply_to_job(self, driver, cfg: dict, ai=None, job_context: dict = None) -> bool:
        from linkedin import click_easy_apply, process_easy_apply
        if not click_easy_apply(driver):
            return False
        return process_easy_apply(driver, cfg, ai=ai, job_context=job_context)

    def get_salary_info(self, driver) -> str:
        from linkedin import get_salary_info
        return get_salary_info(driver)

    def extract_hiring_team(self, driver) -> list[dict]:
        from linkedin import extract_hiring_team
        return extract_hiring_team(driver)

    def detect_visa_sponsorship(self, description: str, cfg: dict) -> str:
        from linkedin import detect_visa_sponsorship
        return detect_visa_sponsorship(description, cfg)
