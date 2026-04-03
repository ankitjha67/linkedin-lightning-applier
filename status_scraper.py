"""ATS Portal Status Scraping module.

Periodically revisits ATS portals (Greenhouse, Workday, Lever, etc.)
to check and track application status changes.
"""

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Common ATS status patterns mapped to canonical statuses
STATUS_PATTERNS = {
    "offer": [
        re.compile(r"offer\s*(extended|letter)", re.IGNORECASE),
        re.compile(r"congratulations", re.IGNORECASE),
    ],
    "interview": [
        re.compile(r"interview\s*scheduled", re.IGNORECASE),
        re.compile(r"schedule.*interview", re.IGNORECASE),
        re.compile(r"next\s*steps", re.IGNORECASE),
    ],
    "reviewing": [
        re.compile(r"under\s*review", re.IGNORECASE),
        re.compile(r"being\s*reviewed", re.IGNORECASE),
        re.compile(r"in\s*progress", re.IGNORECASE),
        re.compile(r"screening", re.IGNORECASE),
    ],
    "rejected": [
        re.compile(r"not\s*(been\s*)?select", re.IGNORECASE),
        re.compile(r"position\s*(has\s*been\s*)?filled", re.IGNORECASE),
        re.compile(r"unfortunately", re.IGNORECASE),
        re.compile(r"no\s*longer\s*(being\s*)?consider", re.IGNORECASE),
        re.compile(r"rejected", re.IGNORECASE),
        re.compile(r"not\s*moving\s*forward", re.IGNORECASE),
    ],
    "applied": [
        re.compile(r"application\s*received", re.IGNORECASE),
        re.compile(r"successfully\s*(submitted|applied)", re.IGNORECASE),
        re.compile(r"thank\s*you\s*for\s*(applying|your\s*application)", re.IGNORECASE),
    ],
}


class ATSStatusScraper:
    """Scrapes ATS portals for application status updates."""

    def __init__(self, cfg, state):
        self.cfg = cfg
        self.state = state
        self.enabled = cfg.get("ats_status_scraper", {}).get("enabled", False)
        self._ensure_tables()

    def _ensure_tables(self):
        try:
            self.state.conn.execute(
                """CREATE TABLE IF NOT EXISTS ats_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    company TEXT,
                    title TEXT,
                    portal_url TEXT,
                    portal_type TEXT,
                    current_status TEXT DEFAULT 'applied',
                    previous_status TEXT,
                    last_checked TEXT,
                    status_changed_at TEXT,
                    created_at TEXT
                )"""
            )
            self.state.conn.commit()
        except Exception as exc:
            logger.error("Failed to initialise ats_status table: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_application(self, job_id, company, title, portal_url):
        """Register a new application for tracking."""
        if not self.enabled:
            logger.debug("ATSStatusScraper disabled; skipping registration.")
            return False

        try:
            existing = self.state.conn.execute(
                "SELECT id FROM ats_status WHERE job_id = ?", (job_id,)
            ).fetchone()
            if existing:
                logger.info("Application %s already tracked.", job_id)
                return True

            portal_type = self._detect_portal_type(portal_url)
            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                """INSERT INTO ats_status
                   (job_id, company, title, portal_url, portal_type,
                    current_status, last_checked, created_at)
                   VALUES (?, ?, ?, ?, ?, 'applied', ?, ?)""",
                (job_id, company, title, portal_url, portal_type, now, now),
            )
            self.state.conn.commit()
            logger.info("Registered application %s (%s) for ATS tracking.", job_id, company)
            return True
        except Exception as exc:
            logger.error("Error registering application %s: %s", job_id, exc)
            return False

    def check_all_statuses(self, driver):
        """Iterate all tracked applications and check each portal."""
        if not self.enabled:
            logger.debug("ATSStatusScraper disabled; skipping status check.")
            return []

        changes = []
        try:
            rows = self.state.conn.execute(
                "SELECT job_id, portal_url FROM ats_status WHERE current_status NOT IN ('rejected', 'offer')"
            ).fetchall()

            for job_id, portal_url in rows:
                try:
                    new_status = self.check_status(driver, job_id, portal_url)
                    if new_status:
                        changes.append({"job_id": job_id, "status": new_status})
                except Exception as inner_exc:
                    logger.warning("Failed to check status for %s: %s", job_id, inner_exc)

            logger.info("Checked %d applications; %d status changes detected.", len(rows), len(changes))
        except Exception as exc:
            logger.error("Error during bulk status check: %s", exc)
        return changes

    def check_status(self, driver, job_id, portal_url):
        """Visit a single portal URL, extract status, and update if changed."""
        try:
            row = self.state.conn.execute(
                "SELECT portal_type, current_status FROM ats_status WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                logger.warning("Job %s not found in tracking table.", job_id)
                return None

            portal_type, old_status = row

            driver.get(portal_url)

            if portal_type == "greenhouse":
                new_status = self._scrape_greenhouse_status(driver)
            elif portal_type == "workday":
                new_status = self._scrape_workday_status(driver)
            elif portal_type == "lever":
                new_status = self._scrape_lever_status(driver)
            else:
                new_status = self._scrape_generic_status(driver)

            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                "UPDATE ats_status SET last_checked = ? WHERE job_id = ?",
                (now, job_id),
            )

            if new_status and new_status != old_status:
                self.state.conn.execute(
                    """UPDATE ats_status
                       SET previous_status = current_status,
                           current_status = ?,
                           status_changed_at = ?
                       WHERE job_id = ?""",
                    (new_status, now, job_id),
                )
                logger.info("Status change for %s: %s -> %s", job_id, old_status, new_status)

            self.state.conn.commit()
            return new_status
        except Exception as exc:
            logger.error("Error checking status for %s: %s", job_id, exc)
            return None

    def get_status_changes(self):
        """Return recent status changes."""
        try:
            rows = self.state.conn.execute(
                """SELECT job_id, company, title, previous_status, current_status, status_changed_at
                   FROM ats_status
                   WHERE status_changed_at IS NOT NULL
                   ORDER BY status_changed_at DESC LIMIT 50"""
            ).fetchall()
            return [
                {
                    "job_id": r[0], "company": r[1], "title": r[2],
                    "from": r[3], "to": r[4], "changed_at": r[5],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.error("Error fetching status changes: %s", exc)
            return []

    def get_status_summary(self):
        """Return counts grouped by current_status."""
        try:
            rows = self.state.conn.execute(
                "SELECT current_status, COUNT(*) FROM ats_status GROUP BY current_status"
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        except Exception as exc:
            logger.error("Error fetching status summary: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # ATS-specific scrapers
    # ------------------------------------------------------------------

    def _scrape_greenhouse_status(self, driver):
        """Extract status from a Greenhouse application portal."""
        try:
            page_text = driver.find_element("tag name", "body").text
            return self._match_status_patterns(page_text)
        except Exception as exc:
            logger.warning("Greenhouse scrape failed: %s", exc)
            return None

    def _scrape_workday_status(self, driver):
        """Extract status from a Workday application portal."""
        try:
            # Workday often shows status in a specific data element
            status_elements = driver.find_elements("css selector", "[data-automation-id='statusLabel']")
            if status_elements:
                return self._match_status_patterns(status_elements[0].text)
            page_text = driver.find_element("tag name", "body").text
            return self._match_status_patterns(page_text)
        except Exception as exc:
            logger.warning("Workday scrape failed: %s", exc)
            return None

    def _scrape_lever_status(self, driver):
        """Extract status from a Lever application portal."""
        try:
            # Lever confirmation pages have a specific structure
            content_el = driver.find_elements("css selector", ".posting-headline, .application-confirmation")
            if content_el:
                return self._match_status_patterns(content_el[0].text)
            page_text = driver.find_element("tag name", "body").text
            return self._match_status_patterns(page_text)
        except Exception as exc:
            logger.warning("Lever scrape failed: %s", exc)
            return None

    def _scrape_generic_status(self, driver):
        """Fallback status extraction for unknown ATS portals."""
        try:
            page_text = driver.find_element("tag name", "body").text
            return self._match_status_patterns(page_text)
        except Exception as exc:
            logger.warning("Generic scrape failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _match_status_patterns(self, text):
        """Match page text against known status patterns, return canonical status."""
        if not text:
            return None
        # Check in priority order (most significant first)
        for status in ("offer", "interview", "rejected", "reviewing", "applied"):
            for pattern in STATUS_PATTERNS.get(status, []):
                if pattern.search(text):
                    return status
        return None

    @staticmethod
    def _detect_portal_type(url):
        """Detect ATS type from the portal URL."""
        if not url:
            return "unknown"
        url_lower = url.lower()
        if "greenhouse.io" in url_lower or "boards.greenhouse" in url_lower:
            return "greenhouse"
        if "myworkday" in url_lower or "workday.com" in url_lower:
            return "workday"
        if "lever.co" in url_lower or "jobs.lever" in url_lower:
            return "lever"
        return "unknown"
