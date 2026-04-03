"""
Time-of-Day Optimized Apply Queue

Instead of applying instantly, queues jobs and submits at the optimal time
for the target company's timezone. Jobs older than queue_max_age_hours are
auto-expired.
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


# Mapping of major cities / regions to timezone offsets (UTC hours).
# Covers the most common job locations without requiring pytz.
CITY_TIMEZONE_MAP = {
    # US
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "boston": "America/New_York",
    "philadelphia": "America/New_York",
    "washington": "America/New_York",
    "dc": "America/New_York",
    "atlanta": "America/New_York",
    "miami": "America/New_York",
    "charlotte": "America/New_York",
    "raleigh": "America/New_York",
    "pittsburgh": "America/New_York",
    "chicago": "America/Chicago",
    "dallas": "America/Chicago",
    "houston": "America/Chicago",
    "austin": "America/Chicago",
    "minneapolis": "America/Chicago",
    "nashville": "America/Chicago",
    "kansas city": "America/Chicago",
    "st. louis": "America/Chicago",
    "denver": "America/Denver",
    "phoenix": "America/Denver",
    "salt lake city": "America/Denver",
    "san francisco": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "portland": "America/Los_Angeles",
    "san jose": "America/Los_Angeles",
    "san diego": "America/Los_Angeles",
    "silicon valley": "America/Los_Angeles",
    # Canada
    "toronto": "America/Toronto",
    "vancouver": "America/Vancouver",
    "montreal": "America/Toronto",
    "calgary": "America/Denver",
    # UK / Europe
    "london": "Europe/London",
    "dublin": "Europe/Dublin",
    "edinburgh": "Europe/London",
    "berlin": "Europe/Berlin",
    "munich": "Europe/Berlin",
    "paris": "Europe/Paris",
    "amsterdam": "Europe/Amsterdam",
    "zurich": "Europe/Zurich",
    "stockholm": "Europe/Stockholm",
    "copenhagen": "Europe/Copenhagen",
    "madrid": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    # Asia-Pacific
    "bangalore": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "singapore": "Asia/Singapore",
    "tokyo": "Asia/Tokyo",
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Sydney",
    # Remote defaults
    "remote": "America/New_York",
}

# UTC offsets for timezone names (standard time, approximate)
TIMEZONE_UTC_OFFSETS = {
    "America/New_York": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Los_Angeles": -8,
    "America/Toronto": -5,
    "America/Vancouver": -8,
    "Europe/London": 0,
    "Europe/Dublin": 0,
    "Europe/Berlin": 1,
    "Europe/Paris": 1,
    "Europe/Amsterdam": 1,
    "Europe/Zurich": 1,
    "Europe/Stockholm": 1,
    "Europe/Copenhagen": 1,
    "Europe/Madrid": 1,
    "Asia/Kolkata": 5,
    "Asia/Singapore": 8,
    "Asia/Tokyo": 9,
    "Australia/Sydney": 11,
}


class ApplyScheduler:
    """Queues job applications and releases them at optimal local times."""

    DEFAULT_OPTIMAL_HOURS = [7, 8, 9, 10]
    DEFAULT_TIMEZONE = "America/New_York"

    def __init__(self, cfg, state):
        self.cfg = cfg
        self.state = state
        sched_cfg = cfg.get("apply_scheduler", {})
        self.enabled = sched_cfg.get("enabled", False)
        self.optimal_hours = sched_cfg.get("optimal_hours", self.DEFAULT_OPTIMAL_HOURS)
        self.timezone_detection = sched_cfg.get("timezone_detection", True)
        self.queue_max_age_hours = sched_cfg.get("queue_max_age_hours", 48)
        if self.enabled:
            logger.info(
                "ApplyScheduler enabled (optimal_hours=%s, max_age=%dh)",
                self.optimal_hours, self.queue_max_age_hours,
            )
        else:
            logger.debug("ApplyScheduler disabled")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def queue_job(self, job_id, title, company, location, job_url):
        """Determine optimal apply time and add job to the queue.

        Args:
            job_id: LinkedIn job ID.
            title: Job title.
            company: Company name.
            location: Job location string.
            job_url: Full URL to the job posting.

        Returns:
            Dict with 'queued' (bool), 'optimal_time' (ISO string), 'timezone'.
        """
        if not self.enabled:
            logger.debug("Job queuing skipped: module disabled")
            return {"queued": False, "optimal_time": None, "timezone": None}

        tz_name = self.detect_timezone(location) if self.timezone_detection else self.DEFAULT_TIMEZONE
        optimal_time = self.compute_optimal_time(tz_name)

        try:
            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                """INSERT OR IGNORE INTO apply_schedule
                   (job_id, title, company, location, job_url, timezone, optimal_time,
                    status, queued_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
                (
                    str(job_id), title, company, location, job_url,
                    tz_name, optimal_time.isoformat(), now,
                ),
            )
            self.state.conn.commit()
            logger.info(
                "Queued job %s (%s at %s) for %s (%s)",
                job_id, title, company, optimal_time.isoformat(), tz_name,
            )
            return {
                "queued": True,
                "optimal_time": optimal_time.isoformat(),
                "timezone": tz_name,
            }
        except Exception:
            logger.exception("Failed to queue job %s", job_id)
            return {"queued": False, "optimal_time": None, "timezone": None}

    def get_ready_jobs(self):
        """Get jobs whose optimal_time has passed and are still queued.

        Also auto-expires jobs older than queue_max_age_hours.

        Returns:
            List of dicts with job_id, title, company, location, job_url.
        """
        if not self.enabled:
            return []

        # First, expire old jobs
        self._expire_old_jobs()

        now = datetime.now(timezone.utc).isoformat()
        try:
            rows = self.state.conn.execute(
                """SELECT job_id, title, company, location, job_url, timezone, optimal_time
                   FROM apply_schedule
                   WHERE status = 'queued' AND optimal_time <= ?
                   ORDER BY optimal_time ASC""",
                (now,),
            ).fetchall()
            return [
                {
                    "job_id": r["job_id"],
                    "title": r["title"],
                    "company": r["company"],
                    "location": r["location"],
                    "job_url": r["job_url"],
                    "timezone": r["timezone"],
                    "optimal_time": r["optimal_time"],
                }
                for r in rows
            ]
        except Exception:
            logger.exception("Failed to fetch ready jobs")
            return []

    def mark_applied(self, job_id):
        """Mark a queued job as successfully applied.

        Args:
            job_id: Job ID to mark.

        Returns:
            True on success.
        """
        return self._update_status(job_id, "applied")

    def mark_expired(self, job_id):
        """Mark a queued job as expired (too old to apply).

        Args:
            job_id: Job ID to mark.

        Returns:
            True on success.
        """
        return self._update_status(job_id, "expired")

    def detect_timezone(self, location):
        """Map a location string to a timezone name.

        Searches the location string for known city names. Falls back to
        DEFAULT_TIMEZONE if no match is found.

        Args:
            location: Location string (e.g. "San Francisco, CA").

        Returns:
            Timezone string like "America/Los_Angeles".
        """
        if not location:
            return self.DEFAULT_TIMEZONE

        location_lower = location.lower().strip()

        # Direct city lookup
        for city, tz in CITY_TIMEZONE_MAP.items():
            if city in location_lower:
                logger.debug("Timezone for '%s' -> %s (matched '%s')", location, tz, city)
                return tz

        # Check for US state abbreviations
        state_tz = self._detect_us_state_timezone(location_lower)
        if state_tz:
            return state_tz

        logger.debug("No timezone match for '%s', using default %s", location, self.DEFAULT_TIMEZONE)
        return self.DEFAULT_TIMEZONE

    def compute_optimal_time(self, tz_name):
        """Compute the next occurrence of an optimal hour in the given timezone.

        Args:
            tz_name: Timezone string (e.g. "America/Los_Angeles").

        Returns:
            datetime in UTC representing the next optimal apply time.
        """
        utc_offset_hours = TIMEZONE_UTC_OFFSETS.get(tz_name, -5)
        now_utc = datetime.now(timezone.utc)

        # Current local time in the target timezone
        local_offset = timedelta(hours=utc_offset_hours)
        now_local = now_utc + local_offset

        # Find the next optimal hour
        for day_offset in range(3):  # Check today, tomorrow, day after
            candidate_date = now_local.date() + timedelta(days=day_offset)
            for hour in sorted(self.optimal_hours):
                candidate_local = datetime(
                    candidate_date.year, candidate_date.month, candidate_date.day,
                    hour, 0, 0,
                )
                # Convert back to UTC
                candidate_utc = candidate_local - local_offset
                candidate_utc = candidate_utc.replace(tzinfo=timezone.utc)
                if candidate_utc > now_utc:
                    return candidate_utc

        # Fallback: tomorrow at first optimal hour
        tomorrow = now_local.date() + timedelta(days=1)
        fallback_hour = self.optimal_hours[0] if self.optimal_hours else 8
        fallback_local = datetime(
            tomorrow.year, tomorrow.month, tomorrow.day,
            fallback_hour, 0, 0,
        )
        fallback_utc = (fallback_local - local_offset).replace(tzinfo=timezone.utc)
        return fallback_utc

    def get_queue_stats(self):
        """Get counts of pending, applied, and expired jobs in the queue.

        Returns:
            Dict with "queued", "applied", "expired", "total" keys.
        """
        stats = {"queued": 0, "applied": 0, "expired": 0, "total": 0}
        if not self.enabled:
            return stats

        try:
            rows = self.state.conn.execute(
                """SELECT status, COUNT(*) as cnt
                   FROM apply_schedule
                   GROUP BY status"""
            ).fetchall()
            for row in rows:
                status = row["status"]
                cnt = row["cnt"]
                stats[status] = cnt
                stats["total"] += cnt
        except Exception:
            logger.exception("Failed to fetch queue stats")

        return stats

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _update_status(self, job_id, status):
        """Update the status of a scheduled job."""
        if not self.enabled:
            return False
        try:
            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                """UPDATE apply_schedule
                   SET status = ?, created_at = datetime('now','localtime')
                   WHERE job_id = ? AND status = 'queued'""",
                (status, now, str(job_id)),
            )
            self.state.conn.commit()
            logger.debug("Marked job %s as %s", job_id, status)
            return True
        except Exception:
            logger.exception("Failed to update status for job %s", job_id)
            return False

    def _expire_old_jobs(self):
        """Auto-expire queued jobs older than queue_max_age_hours."""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self.queue_max_age_hours)
            now = datetime.now(timezone.utc).isoformat()
            result = self.state.conn.execute(
                """UPDATE apply_schedule
                   SET status = 'expired', created_at = datetime('now','localtime')
                   WHERE status = 'queued' AND queued_at < ?""",
                (now, cutoff.isoformat()),
            ).fetchone()
            self.state.conn.commit()
            expired_count = result.rowcount if hasattr(result, "rowcount") else 0
            if expired_count > 0:
                logger.info("Auto-expired %d job(s) older than %d hours", expired_count, self.queue_max_age_hours)
        except Exception:
            logger.exception("Failed to expire old queued jobs")

    def _detect_us_state_timezone(self, location_lower):
        """Detect timezone from US state abbreviations or names."""
        eastern = {
            "ny", "nj", "ct", "ma", "pa", "va", "md", "de", "ri", "nh", "vt",
            "me", "wv", "nc", "sc", "ga", "fl", "oh", "mi", "in",
            "new york", "new jersey", "connecticut", "massachusetts",
            "pennsylvania", "virginia", "maryland", "florida", "georgia",
            "north carolina", "south carolina", "ohio", "michigan",
        }
        central = {
            "il", "tx", "mn", "wi", "ia", "mo", "ar", "la", "ms", "al",
            "tn", "ky", "ok", "ks", "ne", "nd", "sd",
            "illinois", "texas", "minnesota", "wisconsin", "iowa", "missouri",
            "tennessee", "kentucky", "alabama", "louisiana", "oklahoma",
        }
        mountain = {
            "co", "az", "ut", "nm", "mt", "wy", "id",
            "colorado", "arizona", "utah", "new mexico", "montana", "idaho",
        }
        pacific = {
            "ca", "wa", "or", "nv",
            "california", "washington", "oregon", "nevada",
        }

        # Check state abbreviation at end: ", CA" pattern
        parts = [p.strip() for p in location_lower.split(",")]
        for part in parts:
            token = part.strip()
            if token in eastern:
                return "America/New_York"
            if token in central:
                return "America/Chicago"
            if token in mountain:
                return "America/Denver"
            if token in pacific:
                return "America/Los_Angeles"

        return None
