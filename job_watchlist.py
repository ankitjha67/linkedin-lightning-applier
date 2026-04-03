"""Smart Bookmarking with Reminders module.

Bookmark high-score jobs you are not ready to apply to yet.
Automatically checks whether bookmarked listings are still active.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Phrases that indicate a listing is no longer active
EXPIRED_INDICATORS = [
    "no longer accepting applications",
    "this job is no longer available",
    "position has been filled",
    "this posting has expired",
    "job has been removed",
    "this listing is closed",
    "this role has been filled",
    "sorry, this position is no longer open",
    "applications are closed",
    "this job posting is no longer active",
]


class JobWatchlist:
    """Manages a watchlist of bookmarked jobs with reminders and liveness checks."""

    def __init__(self, cfg, state):
        self.cfg = cfg
        self.state = state
        self.enabled = cfg.get("job_watchlist", {}).get("enabled", False)
        self._ensure_tables()

    def _ensure_tables(self):
        try:
            self.state.conn.execute(
                """CREATE TABLE IF NOT EXISTS job_watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL UNIQUE,
                    title TEXT,
                    company TEXT,
                    location TEXT,
                    job_url TEXT,
                    match_score REAL,
                    reason TEXT,
                    status TEXT DEFAULT 'active',
                    remind_at TEXT,
                    last_checked TEXT,
                    still_active INTEGER DEFAULT 1,
                    added_at TEXT,
                    updated_at TEXT
                )"""
            )
            self.state.conn.commit()
        except Exception as exc:
            logger.error("Failed to initialise job_watchlist table: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_to_watchlist(self, job_id, title, company, location, job_url,
                         match_score=None, reason=None, remind_days=7):
        """Bookmark a job with an optional reminder date."""
        if not self.enabled:
            logger.debug("JobWatchlist disabled; skipping add.")
            return False

        try:
            now = datetime.now(timezone.utc)
            remind_at = (now + timedelta(days=remind_days)).isoformat() if remind_days else None

            self.state.conn.execute(
                """INSERT OR REPLACE INTO job_watchlist
                   (job_id, title, company, location, job_url,
                    match_score, reason, status, remind_at, still_active,
                    added_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, 1, ?)""",
                (job_id, title, company, location, job_url,
                 match_score, reason, remind_at,
                 now.isoformat()),
            )
            self.state.conn.commit()
            logger.info(
                "Added job %s (%s at %s) to watchlist; reminder in %s days.",
                job_id, title, company, remind_days,
            )
            return True
        except Exception as exc:
            logger.error("Error adding job %s to watchlist: %s", job_id, exc)
            return False

    def check_active_listings(self, driver):
        """Visit each bookmarked job URL and check whether it is still posted."""
        if not self.enabled:
            logger.debug("JobWatchlist disabled; skipping active-listing check.")
            return []

        expired = []
        try:
            rows = self.state.conn.execute(
                "SELECT job_id, job_url FROM job_watchlist WHERE status = 'active'"
            ).fetchall()

            for job_id, job_url in rows:
                try:
                    still_active = self._is_listing_active(driver, job_url)
                    now = datetime.now(timezone.utc).isoformat()
                    self.state.conn.execute(
                        "UPDATE job_watchlist SET last_checked = ?, still_active = ? WHERE job_id = ?",
                        (now, int(still_active), now, job_id),
                    )
                    if not still_active:
                        expired.append(job_id)
                        logger.info("Watchlist job %s is no longer active.", job_id)
                except Exception as inner_exc:
                    logger.warning("Error checking listing %s: %s", job_id, inner_exc)

            self.state.conn.commit()
            logger.info(
                "Checked %d watchlist listings; %d expired.", len(rows), len(expired),
            )
        except Exception as exc:
            logger.error("Error checking active listings: %s", exc)
        return expired

    def get_due_reminders(self):
        """Return watchlist items whose remind_at date has passed."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            rows = self.state.conn.execute(
                """SELECT job_id, title, company, location, job_url, match_score, reason, remind_at
                   FROM job_watchlist
                   WHERE status = 'active' AND remind_at IS NOT NULL AND remind_at <= ?
                   ORDER BY remind_at ASC""",
                (now,),
            ).fetchall()
            return [
                {
                    "job_id": r[0], "title": r[1], "company": r[2],
                    "location": r[3], "job_url": r[4], "match_score": r[5],
                    "reason": r[6], "remind_at": r[7],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.error("Error fetching due reminders: %s", exc)
            return []

    def remove_from_watchlist(self, job_id):
        """Mark a watchlist item as removed."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                "UPDATE job_watchlist SET status = 'removed' WHERE job_id = ?",
                (now, job_id),
            )
            self.state.conn.commit()
            logger.info("Removed job %s from watchlist.", job_id)
            return True
        except Exception as exc:
            logger.error("Error removing job %s from watchlist: %s", job_id, exc)
            return False

    def get_watchlist(self, status="active"):
        """Get the current watchlist filtered by status."""
        try:
            rows = self.state.conn.execute(
                """SELECT job_id, title, company, location, job_url,
                          match_score, reason, status, remind_at, still_active, added_at
                   FROM job_watchlist
                   WHERE status = ?
                   ORDER BY added_at DESC""",
                (status,),
            ).fetchall()
            return [
                {
                    "job_id": r[0], "title": r[1], "company": r[2],
                    "location": r[3], "job_url": r[4], "match_score": r[5],
                    "reason": r[6], "status": r[7], "remind_at": r[8],
                    "still_active": bool(r[9]), "added_at": r[10],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.error("Error fetching watchlist: %s", exc)
            return []

    def auto_expire_filled(self):
        """Mark jobs as expired if they are no longer active."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            cursor = self.state.conn.execute(
                """UPDATE job_watchlist
                   SET status = 'expired'
                   WHERE status = 'active' AND still_active = 0""",
                (now,),
            )
            count = cursor.rowcount
            self.state.conn.commit()
            if count:
                logger.info("Auto-expired %d watchlist entries.", count)
            return count
        except Exception as exc:
            logger.error("Error auto-expiring listings: %s", exc)
            return 0

    def get_watchlist_stats(self):
        """Return counts of active, expired, removed, and applied items."""
        try:
            rows = self.state.conn.execute(
                "SELECT status, COUNT(*) FROM job_watchlist GROUP BY status"
            ).fetchall()
            stats = {r[0]: r[1] for r in rows}
            # Ensure expected keys exist
            for key in ("active", "expired", "removed", "applied"):
                stats.setdefault(key, 0)
            return stats
        except Exception as exc:
            logger.error("Error fetching watchlist stats: %s", exc)
            return {"active": 0, "expired": 0, "removed": 0, "applied": 0}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_listing_active(self, driver, job_url):
        """Navigate to the job URL and determine if the listing is still live."""
        try:
            driver.get(job_url)
            page_text = driver.find_element("tag name", "body").text.lower()

            for indicator in EXPIRED_INDICATORS:
                if indicator in page_text:
                    return False

            # If the page returned a 404-style message
            if "page not found" in page_text or "404" in page_text:
                return False

            return True
        except Exception as exc:
            logger.warning("Could not determine listing status for %s: %s", job_url, exc)
            # Assume still active if we cannot determine
            return True
