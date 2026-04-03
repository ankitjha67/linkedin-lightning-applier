"""
Abstract base class for job platform plugins.

Each platform (LinkedIn, Indeed, Glassdoor, etc.) implements this interface.
The main orchestrator works through this abstraction, enabling multi-platform
job search and application from a single codebase.

Required methods must be implemented. Optional methods have sensible defaults.
"""

from abc import ABC, abstractmethod
from typing import Optional


class JobPlatform(ABC):
    """
    Abstract interface for job search platforms.

    Lifecycle:
        1. create_browser(cfg) → driver
        2. login(driver, cfg) → bool
        3. For each search:
            a. build_search_url(cfg, term, location) → url
            b. navigate_to_search(driver, url)
            c. get_job_cards(driver) → [card, ...]
            d. For each card:
                - extract_job_info(driver, card) → {title, company, ...}
                - get_job_description(driver) → str
                - apply_to_job(driver, cfg, ai, job_context) → bool
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Platform identifier (e.g., 'linkedin', 'indeed', 'glassdoor').
        Must be lowercase, alphanumeric."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable platform name. Defaults to capitalized name."""
        return self.name.capitalize()

    @property
    def requires_login(self) -> bool:
        """Whether this platform requires authentication to search/apply."""
        return True

    @property
    def supports_easy_apply(self) -> bool:
        """Whether this platform has a built-in quick-apply feature."""
        return False

    # ── Required Methods ──────────────────────────────────────

    @abstractmethod
    def create_browser(self, cfg: dict):
        """Create and configure a browser instance for this platform.

        Args:
            cfg: Full application config dict

        Returns:
            Selenium WebDriver instance
        """
        ...

    @abstractmethod
    def login(self, driver, cfg: dict) -> bool:
        """Authenticate with the platform.

        Args:
            driver: Selenium WebDriver
            cfg: Full config (contains credentials under platform-specific key)

        Returns:
            True if login successful, False otherwise
        """
        ...

    @abstractmethod
    def build_search_url(self, cfg: dict, term: str, location: str) -> str:
        """Build a search URL for the given term and location.

        Args:
            cfg: Full config (contains search settings)
            term: Job search keyword/title
            location: Location string (city, state, country)

        Returns:
            Full URL string for the search results page
        """
        ...

    @abstractmethod
    def navigate_to_search(self, driver, url: str):
        """Navigate browser to search results and wait for page load.

        Should wait for job cards to be present before returning.
        """
        ...

    @abstractmethod
    def get_job_cards(self, driver) -> list:
        """Get list of job card WebElements on the current page.

        Returns:
            List of Selenium WebElement objects representing job cards.
            Empty list if no results found.
        """
        ...

    @abstractmethod
    def extract_job_info(self, driver, card) -> Optional[dict]:
        """Extract job metadata from a card element.

        Args:
            driver: WebDriver (may need for scrolling/clicking)
            card: WebElement for the job card

        Returns:
            Dict with keys:
                - title: str (required)
                - company: str (required)
                - location: str
                - job_id: str (unique identifier, prefixed with platform name)
                - job_url: str (direct link to job posting)
                - posted_time: str (e.g., "2 hours ago")
                - applied: bool (already applied indicator)
                - work_style: str (remote/hybrid/onsite)
            Or None if extraction fails.
        """
        ...

    @abstractmethod
    def get_job_description(self, driver) -> str:
        """Get full job description text from the detail view.

        Called after clicking/selecting a job card.

        Returns:
            Plain text description, or empty string if unavailable.
        """
        ...

    @abstractmethod
    def apply_to_job(self, driver, cfg: dict, ai=None, job_context: dict = None) -> bool:
        """Apply to the currently viewed job.

        Args:
            driver: WebDriver positioned on the job detail page
            cfg: Full config
            ai: AIAnswerer instance for form filling (optional)
            job_context: Dict with title, company, description for AI context

        Returns:
            True if application was successfully submitted.
            False if application failed or was delegated to ExternalApplier.
        """
        ...

    # ── Optional Methods (override per platform) ──────────────

    def get_salary_info(self, driver) -> str:
        """Extract salary information from the job detail view.

        Returns:
            Salary string (e.g., "$120K-$150K/yr") or empty string.
        """
        return ""

    def extract_hiring_team(self, driver) -> list[dict]:
        """Extract recruiter/hiring team info from the job detail view.

        Returns:
            List of dicts with keys: name, title, profile_url
        """
        return []

    def detect_visa_sponsorship(self, description: str, cfg: dict) -> str:
        """Detect visa sponsorship from job description.

        Args:
            description: Full job description text
            cfg: Config with visa keyword lists

        Returns:
            "yes", "no", or "unknown"
        """
        if not description:
            return "unknown"
        desc_lower = description.lower()
        filters = cfg.get("filters", {})
        for kw in filters.get("visa_positive_keywords", []):
            if kw.lower() in desc_lower:
                return "yes"
        for kw in filters.get("visa_negative_keywords", []):
            if kw.lower() in desc_lower:
                return "no"
        return "unknown"

    def get_external_apply_url(self, driver) -> Optional[str]:
        """Get external application URL if job uses an ATS.

        Returns:
            URL string to external application form, or None.
        """
        return None

    def has_next_page(self, driver) -> bool:
        """Check if there are more pages of search results.

        Returns:
            True if a next page exists.
        """
        return False

    def go_to_next_page(self, driver) -> bool:
        """Navigate to the next page of search results.

        Returns:
            True if navigation successful.
        """
        return False

    def verify_session(self, driver) -> bool:
        """Quick check that the session is still valid.

        Returns:
            True if authenticated and session is active.
        """
        return True
