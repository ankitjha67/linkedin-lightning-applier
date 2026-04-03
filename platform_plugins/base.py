"""
Abstract base class for job platform plugins.

Each platform (LinkedIn, Indeed, Glassdoor, etc.) implements this interface.
The main orchestrator works through this abstraction.
"""

from abc import ABC, abstractmethod
from typing import Optional


class JobPlatform(ABC):
    """Abstract interface for job search platforms."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Platform name (e.g., 'linkedin', 'indeed')."""
        ...

    @abstractmethod
    def create_browser(self, cfg: dict):
        """Create and configure browser for this platform."""
        ...

    @abstractmethod
    def login(self, driver, cfg: dict) -> bool:
        """Login to the platform. Returns True on success."""
        ...

    @abstractmethod
    def build_search_url(self, cfg: dict, term: str, location: str) -> str:
        """Build a job search URL for the given term and location."""
        ...

    @abstractmethod
    def navigate_to_search(self, driver, url: str):
        """Navigate to search results page."""
        ...

    @abstractmethod
    def get_job_cards(self, driver) -> list:
        """Get list of job card elements on the current search page."""
        ...

    @abstractmethod
    def extract_job_info(self, driver, card) -> Optional[dict]:
        """Extract job info from a card element.
        Returns: {title, company, location, job_id, job_url, ...}"""
        ...

    @abstractmethod
    def get_job_description(self, driver) -> str:
        """Get full job description from the detail view."""
        ...

    @abstractmethod
    def apply_to_job(self, driver, cfg: dict, ai=None, job_context: dict = None) -> bool:
        """Apply to the currently viewed job. Returns True on success."""
        ...

    def get_salary_info(self, driver) -> str:
        """Extract salary information. Override per platform."""
        return ""

    def extract_hiring_team(self, driver) -> list[dict]:
        """Extract recruiter/hiring team info. Override per platform."""
        return []

    def detect_visa_sponsorship(self, description: str, cfg: dict) -> str:
        """Detect visa sponsorship from description. Override per platform."""
        return "unknown"
