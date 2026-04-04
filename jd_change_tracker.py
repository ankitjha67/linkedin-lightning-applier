"""
Job Description Change Tracking Module

Tracks edits to job descriptions after you have applied. Alerts on salary
changes, requirement additions/removals, and urgency signals.
"""

import hashlib
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class JDChangeTracker:
    """Captures JD snapshots and detects meaningful changes over time."""

    CHANGE_TYPES = (
        "salary_change",
        "requirements_added",
        "requirements_removed",
        "urgency_added",
        "title_change",
        "description_modified",
    )

    # Patterns that signal urgency was added
    URGENCY_PHRASES = [
        r"immediate\s+start",
        r"urgent(ly)?",
        r"asap",
        r"start\s+immediately",
        r"backfill",
        r"critical\s+hire",
        r"high\s+priority",
    ]

    # Requirement-like bullet patterns
    REQUIREMENT_PATTERN = re.compile(
        r"(?:^|\n)\s*[\u2022\-\*]\s*(.+)",
        re.MULTILINE,
    )

    # Salary / compensation patterns
    SALARY_PATTERN = re.compile(
        r"\$[\d,]+(?:\s*[-\u2013]\s*\$[\d,]+)?(?:\s*/\s*(?:yr|year|hr|hour|month))?",
        re.IGNORECASE,
    )

    def __init__(self, cfg, state):
        self.cfg = cfg
        self.state = state
        tracking_cfg = cfg.get("jd_tracking", {})
        self.enabled = tracking_cfg.get("enabled", False)
        self.min_change_ratio = tracking_cfg.get("min_change_ratio", 0.05)
        if self.enabled:
            logger.info("JDChangeTracker enabled (min_change_ratio=%.2f)", self.min_change_ratio)
        else:
            logger.debug("JDChangeTracker disabled")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def capture_snapshot(self, job_id, description, salary_info=None):
        """Store a snapshot of a job description for future comparison.

        Args:
            job_id: LinkedIn job ID.
            description: Full job description text.
            salary_info: Optional salary string extracted from the posting.

        Returns:
            The snapshot hash, or None on failure.
        """
        if not self.enabled:
            logger.debug("Snapshot capture skipped: module disabled")
            return None

        if not description:
            logger.warning("Empty description for job %s, skipping snapshot", job_id)
            return None

        snapshot_hash = self._hash_text(description)
        salary_hash = self._hash_text(salary_info) if salary_info else None

        try:
            now = datetime.now(timezone.utc).isoformat()
            # Check if we already have this exact hash (no change)
            existing = self.state.conn.execute(
                """SELECT id FROM jd_snapshots
                   WHERE job_id = ? AND snapshot_hash = ?
                   ORDER BY captured_at DESC LIMIT 1""",
                (str(job_id), snapshot_hash),
            ).fetchone()
            if existing:
                logger.debug("Snapshot unchanged for job %s (hash %s)", job_id, snapshot_hash[:12])
                return snapshot_hash

            self.state.conn.execute(
                """INSERT INTO jd_snapshots
                   (job_id, snapshot_hash, description, salary_info, salary_hash, captured_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(job_id), snapshot_hash, description, salary_info or "", salary_hash or "", now),
            )
            self.state.conn.commit()
            logger.info("Captured snapshot for job %s (hash %s)", job_id, snapshot_hash[:12])
            return snapshot_hash
        except Exception:
            logger.exception("Failed to capture snapshot for job %s", job_id)
            return None

    def check_for_changes(self, job_id, new_description, new_salary=None):
        """Compare a new description against the last snapshot.

        Args:
            job_id: LinkedIn job ID.
            new_description: Current job description text.
            new_salary: Current salary string (optional).

        Returns:
            List of change dicts, each with keys: "type", "summary", "old_value", "new_value".
            Empty list if no changes detected or module disabled.
        """
        if not self.enabled:
            return []

        if not new_description:
            return []

        # Get the most recent snapshot
        try:
            prev = self.state.conn.execute(
                """SELECT description, salary_info, snapshot_hash, salary_hash
                   FROM jd_snapshots
                   WHERE job_id = ?
                   ORDER BY captured_at DESC LIMIT 1""",
                (str(job_id),),
            ).fetchone()
        except Exception:
            logger.exception("Failed to fetch previous snapshot for job %s", job_id)
            return []

        if not prev:
            # No previous snapshot; capture this one as baseline
            self.capture_snapshot(job_id, new_description, new_salary)
            return []

        old_description = prev["description"]
        old_salary = prev["salary_info"]
        new_snapshot_hash = self._hash_text(new_description)

        # Quick hash check -- if identical, no changes
        if new_snapshot_hash == prev["snapshot_hash"]:
            return []

        changes = self.detect_change_type(old_description, new_description)

        # Check salary separately
        if new_salary and old_salary and new_salary.strip() != old_salary.strip():
            changes.append({
                "type": "salary_change",
                "summary": "Salary information changed",
                "old_value": old_salary,
                "new_value": new_salary,
            })
        elif new_salary and not old_salary:
            changes.append({
                "type": "salary_change",
                "summary": "Salary information added",
                "old_value": "",
                "new_value": new_salary,
            })
        elif not new_salary and old_salary:
            changes.append({
                "type": "salary_change",
                "summary": "Salary information removed",
                "old_value": old_salary,
                "new_value": "",
            })

        if changes:
            # Record changes and capture new snapshot
            self._record_changes(job_id, changes)
            self.capture_snapshot(job_id, new_description, new_salary)
            logger.info("Detected %d change(s) for job %s", len(changes), job_id)

        return changes

    def get_changed_jobs(self):
        """List all jobs that have detected changes.

        Returns:
            List of dicts with job_id, change_type, summary, detected_at.
        """
        if not self.enabled:
            return []

        try:
            rows = self.state.conn.execute(
                """SELECT job_id, change_type, summary, detected_at
                   FROM jd_changes
                   ORDER BY detected_at DESC"""
            ).fetchall()
            return [
                {
                    "job_id": r["job_id"],
                    "change_type": r["change_type"],
                    "summary": r["summary"],
                    "detected_at": r["detected_at"],
                }
                for r in rows
            ]
        except Exception:
            logger.exception("Failed to fetch changed jobs")
            return []

    def detect_change_type(self, old, new):
        """Classify the type of change between two description texts.

        Categories: salary_change, requirements_added, requirements_removed,
        urgency_added, description_modified.

        Args:
            old: Previous description text.
            new: New description text.

        Returns:
            List of change dicts with type, summary, old_value, new_value.
        """
        changes = []

        if not old or not new:
            if new and not old:
                changes.append({
                    "type": "description_modified",
                    "summary": "Description added (was empty)",
                    "old_value": "",
                    "new_value": new[:200],
                })
            return changes

        # Check overall similarity
        ratio = SequenceMatcher(None, old, new).ratio()

        # Always check salary changes even if overall text is very similar
        old_salaries = self.SALARY_PATTERN.findall(old)
        new_salaries = self.SALARY_PATTERN.findall(new)
        if old_salaries != new_salaries:
            changes.append({
                "type": "salary_change",
                "summary": "Inline salary information changed",
                "old_value": ", ".join(old_salaries) if old_salaries else "",
                "new_value": ", ".join(new_salaries) if new_salaries else "",
            })

        if ratio > (1.0 - self.min_change_ratio):
            # Changes are below the minimum threshold for non-salary changes
            return changes

        # Extract and compare requirements
        old_reqs = set(self._extract_requirements(old))
        new_reqs = set(self._extract_requirements(new))

        added_reqs = new_reqs - old_reqs
        removed_reqs = old_reqs - new_reqs

        if added_reqs:
            changes.append({
                "type": "requirements_added",
                "summary": f"{len(added_reqs)} requirement(s) added",
                "old_value": "",
                "new_value": "; ".join(sorted(added_reqs)[:5]),
            })

        if removed_reqs:
            changes.append({
                "type": "requirements_removed",
                "summary": f"{len(removed_reqs)} requirement(s) removed",
                "old_value": "; ".join(sorted(removed_reqs)[:5]),
                "new_value": "",
            })

        # Check for urgency phrases added
        old_urgency = self._has_urgency(old)
        new_urgency = self._has_urgency(new)
        if new_urgency and not old_urgency:
            changes.append({
                "type": "urgency_added",
                "summary": "Urgency language added to posting",
                "old_value": "",
                "new_value": new_urgency,
            })

        # General description modification if no specific category matched
        if not changes:
            changes.append({
                "type": "description_modified",
                "summary": f"Description changed (similarity {ratio:.0%})",
                "old_value": old[:200],
                "new_value": new[:200],
            })

        return changes

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hash_text(self, text):
        """Compute SHA-256 hash of text after whitespace normalization."""
        if not text:
            return ""
        normalized = " ".join(text.lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _extract_requirements(self, text):
        """Extract bullet-pointed requirements from description text."""
        matches = self.REQUIREMENT_PATTERN.findall(text)
        # Normalize each requirement
        reqs = []
        for m in matches:
            cleaned = m.strip().lower()
            cleaned = re.sub(r"\s+", " ", cleaned)
            if len(cleaned) > 10:  # Skip very short bullets
                reqs.append(cleaned)
        return reqs

    def _has_urgency(self, text):
        """Check if text contains urgency language. Returns matched phrase or empty string."""
        text_lower = text.lower()
        for pattern in self.URGENCY_PHRASES:
            match = re.search(pattern, text_lower)
            if match:
                return match.group(0)
        return ""

    def _record_changes(self, job_id, changes):
        """Persist detected changes to the jd_changes table."""
        now = datetime.now(timezone.utc).isoformat()
        for change in changes:
            try:
                self.state.conn.execute(
                    """INSERT INTO jd_changes
                       (job_id, change_type, summary, old_value, new_value, detected_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        str(job_id),
                        change["type"],
                        change["summary"],
                        change.get("old_value", ""),
                        change.get("new_value", ""),
                        now,
                    ),
                )
            except Exception:
                logger.exception("Failed to record change for job %s", job_id)
        try:
            self.state.conn.commit()
        except Exception:
            logger.exception("Failed to commit changes for job %s", job_id)

    def get_snapshot_count(self, job_id=None):
        """Return the number of snapshots, optionally filtered by job_id."""
        try:
            if job_id:
                row = self.state.conn.execute(
                    "SELECT COUNT(*) as cnt FROM jd_snapshots WHERE job_id = ?",
                    (str(job_id),),
                ).fetchone()
            else:
                row = self.state.conn.execute("SELECT COUNT(*) as cnt FROM jd_snapshots").fetchone()
            return row["cnt"] if row else 0
        except Exception:
            return 0
