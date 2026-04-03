"""
Cross-Platform Duplicate Detection Engine

Prevents applying to the same job across LinkedIn, Indeed, Google Jobs,
and company career sites by computing normalized fingerprints.
"""

import hashlib
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class DedupEngine:
    """Detects duplicate job postings across platforms using normalized fingerprints."""

    # Words stripped when normalizing job titles
    SENIORITY_TOKENS = {
        "senior", "sr", "sr.", "junior", "jr", "jr.", "lead", "principal",
        "staff", "associate", "intern", "entry-level", "entry level",
        "mid-level", "mid level", "level", "i", "ii", "iii", "iv", "v",
    }

    # Suffixes stripped when normalizing company names
    COMPANY_SUFFIXES = [
        r",?\s+inc\.?$", r",?\s+llc\.?$", r",?\s+ltd\.?$", r",?\s+corp\.?$",
        r",?\s+corporation$", r",?\s+co\.?$", r",?\s+company$",
        r",?\s+incorporated$", r",?\s+limited$", r",?\s+plc\.?$",
        r",?\s+gmbh$", r",?\s+ag$", r",?\s+s\.?a\.?$",
    ]

    KNOWN_PLATFORMS = ("linkedin", "indeed", "google_jobs", "glassdoor", "company_site", "other")

    def __init__(self, state):
        self.state = state
        # Read enabled flag from config if accessible via state
        try:
            cfg = getattr(state, "cfg", None) or {}
            dedup_cfg = cfg.get("dedup", {}) if isinstance(cfg, dict) else {}
            self.enabled = dedup_cfg.get("enabled", True)
        except Exception:
            self.enabled = True
        self._suffix_patterns = [re.compile(p, re.IGNORECASE) for p in self.COMPANY_SUFFIXES]
        if self.enabled:
            logger.info("DedupEngine initialized")
        else:
            logger.debug("DedupEngine disabled")

    # ------------------------------------------------------------------
    # Fingerprint computation
    # ------------------------------------------------------------------

    def compute_fingerprint(self, title, company, location=""):
        """Produce a hex fingerprint from normalized title + company + location.

        Normalization strips seniority levels from titles, legal suffixes
        from company names, and standardizes location strings, then hashes
        the result with SHA-256.

        Args:
            title: Raw job title string.
            company: Raw company name.
            location: Raw location string (city, state, remote, etc.).

        Returns:
            64-char hex SHA-256 digest.
        """
        norm_title = self._normalize_title(title or "")
        norm_company = self._normalize_company(company or "")
        norm_location = self._normalize_location(location or "")
        combined = f"{norm_title}|{norm_company}|{norm_location}"
        fp = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        logger.debug(
            "Fingerprint: '%s' | '%s' | '%s' -> %s",
            norm_title, norm_company, norm_location, fp[:12],
        )
        return fp

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def is_duplicate(self, title, company, location="", platform="linkedin"):
        """Check whether a job has already been seen on any platform.

        Args:
            title: Job title.
            company: Company name.
            location: Location string.
            platform: The platform the caller is currently processing.

        Returns:
            dict with keys:
                "is_dup" (bool),
                "original_platform" (str or None),
                "original_job_id" (str or None),
                "fingerprint" (str).
        """
        fp = self.compute_fingerprint(title, company, location)
        result = {
            "is_dup": False,
            "original_platform": None,
            "original_job_id": None,
            "fingerprint": fp,
        }

        if not self.enabled:
            return result

        try:
            row = self.state.conn.execute(
                "SELECT job_id, platform FROM job_fingerprints WHERE fingerprint = ?",
                (fp,),
            ).fetchone()
            if row:
                result["is_dup"] = True
                result["original_platform"] = row["platform"]
                result["original_job_id"] = row["job_id"]
                if row["platform"] != platform:
                    logger.info(
                        "Cross-platform duplicate: '%s' at '%s' already on %s (job %s)",
                        title, company, row["platform"], row["job_id"],
                    )
                else:
                    logger.debug(
                        "Same-platform duplicate: '%s' at '%s' on %s",
                        title, company, platform,
                    )
        except Exception:
            logger.exception("Error checking duplicate for fingerprint %s", fp[:12])

        return result

    def register_job(self, job_id, title, company, location="", platform="linkedin"):
        """Register a job fingerprint so future duplicates are caught.

        Args:
            job_id: Platform-specific job identifier.
            title: Job title.
            company: Company name.
            location: Location string.
            platform: Platform name (one of KNOWN_PLATFORMS).

        Returns:
            The fingerprint string, or None on failure.
        """
        if not self.enabled:
            return None

        platform = platform if platform in self.KNOWN_PLATFORMS else "other"
        fp = self.compute_fingerprint(title, company, location)

        try:
            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                """INSERT OR IGNORE INTO job_fingerprints
                   (fingerprint, job_id, title, company, location, platform, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (fp, str(job_id), title, company, location, platform, now),
            )
            self.state.conn.commit()
            logger.debug("Registered fingerprint %s for job %s on %s", fp[:12], job_id, platform)
            return fp
        except Exception:
            logger.exception("Failed to register fingerprint for job %s", job_id)
            return None

    def get_duplicate_stats(self):
        """Return counts of registered fingerprints grouped by platform.

        Returns:
            dict mapping platform -> count, plus "total" and
            "duplicate_fingerprints" keys.
        """
        stats = {p: 0 for p in self.KNOWN_PLATFORMS}
        stats["total"] = 0
        stats["duplicate_fingerprints"] = 0

        if not self.enabled:
            return stats

        try:
            rows = self.state.conn.execute(
                "SELECT platform, COUNT(*) as cnt FROM job_fingerprints GROUP BY platform"
            )
            for row in rows:
                plat = row["platform"]
                cnt = row["cnt"]
                stats[plat] = cnt
                stats["total"] += cnt
        except Exception:
            logger.exception("Failed to fetch duplicate stats")
            return stats

        # Count fingerprints that appear on more than one platform
        try:
            dup_row = self.state.conn.execute(
                """SELECT COUNT(*) as cnt FROM (
                       SELECT fingerprint FROM job_fingerprints
                       GROUP BY fingerprint HAVING COUNT(DISTINCT platform) > 1
                   )"""
            )
            stats["duplicate_fingerprints"] = dup_row["cnt"] if dup_row else 0
        except Exception:
            logger.debug("Could not compute cross-platform duplicate count")

        return stats

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------

    def _normalize_title(self, title):
        """Lowercase, strip seniority tokens, collapse whitespace."""
        text = title.lower().strip()
        # Remove parenthetical content like (Remote) or (Contract)
        text = re.sub(r"\(.*?\)", "", text)
        # Remove special chars except spaces and hyphens
        text = re.sub(r"[^a-z0-9\s\-]", "", text)
        tokens = text.split()
        tokens = [t for t in tokens if t not in self.SENIORITY_TOKENS]
        return " ".join(tokens).strip()

    def _normalize_company(self, company):
        """Lowercase, strip legal suffixes, collapse whitespace."""
        text = company.lower().strip()
        for pat in self._suffix_patterns:
            text = pat.sub("", text)
        text = re.sub(r"[^a-z0-9\s]", "", text)
        return " ".join(text.split()).strip()

    def _normalize_location(self, location):
        """Lowercase, map remote synonyms, strip country detail."""
        text = location.lower().strip()
        # Normalize remote variants
        remote_patterns = ["remote", "work from home", "wfh", "anywhere", "distributed"]
        for rp in remote_patterns:
            if rp in text:
                return "remote"
        # Strip country if US
        text = re.sub(r",?\s*united states$", "", text)
        text = re.sub(r",?\s*usa?$", "", text)
        # Remove zip codes
        text = re.sub(r"\b\d{5}(-\d{4})?\b", "", text)
        # Keep city + state only
        text = re.sub(r"[^a-z0-9\s,]", "", text)
        parts = [p.strip() for p in text.split(",") if p.strip()]
        # Use only the city (first part) for fingerprinting to handle
        # "Mountain View, CA" vs "Mountain View" vs "Mountain View, California"
        return parts[0] if parts else ""

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def purge_old_fingerprints(self, days=90):
        """Remove fingerprints older than *days* to keep the table lean.

        Returns:
            Number of rows deleted.
        """
        if not self.enabled:
            return 0
        try:
            cutoff = datetime.now(timezone.utc).isoformat()
            result = self.state.conn.execute(
                """DELETE FROM job_fingerprints
                   WHERE first_seen < datetime(?, '-' || ? || ' days')""",
                (cutoff, days),
            )
            self.state.conn.commit()
            deleted = result.rowcount if hasattr(result, "rowcount") else 0
            logger.info("Purged %d fingerprints older than %d days", deleted, days)
            return deleted
        except Exception:
            logger.exception("Failed to purge old fingerprints")
            return 0
