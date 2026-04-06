"""
Application Pipeline State Machine.

Formal state machine for the full application lifecycle, from job discovery
through offer acceptance or rejection. Enforces valid transitions, tracks
history, and provides pipeline analytics.
"""

import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger("lla.pipeline_manager")

# All valid states in lifecycle order
STATES = [
    "discovered",
    "evaluated",
    "queued",
    "applied",
    "responded",
    "interviewing",
    "offer",
    "accepted",
    "rejected",
    "withdrawn",
    "discarded",
    "ghosted",
]

# Valid transitions: source -> set of allowed targets
VALID_TRANSITIONS = {
    "discovered":   {"evaluated", "discarded"},
    "evaluated":    {"queued", "discarded"},
    "queued":       {"applied", "discarded"},
    "applied":      {"responded", "interviewing", "rejected", "withdrawn", "ghosted"},
    "responded":    {"interviewing", "rejected", "withdrawn"},
    "interviewing": {"offer", "rejected", "withdrawn"},
    "offer":        {"accepted", "rejected", "withdrawn"},
}

# Terminal states (no outbound transitions)
TERMINAL_STATES = {"accepted", "rejected", "withdrawn", "discarded", "ghosted"}


class PipelineManager:
    """Manage the application pipeline state machine."""

    def __init__(self, cfg: dict, state):
        self.cfg = cfg
        self.state = state
        pm_cfg = cfg.get("pipeline_manager", {})
        self.enabled = pm_cfg.get("enabled", False)
        self.ghost_days = pm_cfg.get("ghost_days", 14)

    # ── State Transitions ────────────────────────────────────────

    def set_initial_state(self, job_id: str, company: str, title: str) -> bool:
        """
        Create a new pipeline entry at the 'discovered' state.

        Returns True if created, False if already exists.
        """
        if not self.enabled:
            log.debug("Pipeline manager disabled")
            return False

        # Check if already tracked
        existing = self.get_state(job_id)
        if existing:
            log.debug(f"Job {job_id} already in pipeline as '{existing}'")
            return False

        now = datetime.now().isoformat()
        history = json.dumps([{
            "state": "discovered",
            "timestamp": now,
            "notes": "Initial discovery",
        }])

        try:
            self.state.conn.execute(
                """INSERT INTO pipeline_states
                   (job_id, company, title, current_state, previous_state,
                    state_history, updated_at)
                   VALUES (?, ?, ?, 'discovered', '', ?, ?)""",
                (job_id, company, title, history, now),
            )
            self.state.conn.commit()
            log.info(f"Pipeline: {job_id} ({company} - {title}) -> discovered")
            return True
        except Exception as e:
            log.error(f"Failed to set initial state for {job_id}: {e}")
            return False

    def transition(self, job_id: str, new_state: str, notes: str = "") -> bool:
        """
        Move a job to a new state. Validates the transition is allowed.

        Returns True if transition succeeded, False otherwise.
        """
        if not self.enabled:
            log.debug("Pipeline manager disabled")
            return False

        if new_state not in STATES:
            log.error(f"Invalid state: {new_state}")
            return False

        # Get current state
        row = self._get_row(job_id)
        if not row:
            log.error(f"Job {job_id} not found in pipeline")
            return False

        current = row["current_state"]

        if not self.is_valid_transition(current, new_state):
            log.warning(
                f"Invalid transition: {current} -> {new_state} for {job_id}. "
                f"Allowed: {VALID_TRANSITIONS.get(current, set())}"
            )
            return False

        # Update history
        history = self._load_history(row["state_history"])
        now = datetime.now().isoformat()
        history.append({
            "state": new_state,
            "timestamp": now,
            "notes": notes,
            "from_state": current,
        })

        try:
            self.state.conn.execute(
                """UPDATE pipeline_states
                   SET current_state = ?, previous_state = ?,
                       state_history = ?, notes = ?, updated_at = ?
                   WHERE job_id = ?""",
                (new_state, current, json.dumps(history), notes, now, job_id),
            )
            self.state.conn.commit()
            log.info(f"Pipeline: {job_id} {current} -> {new_state}")
            return True
        except Exception as e:
            log.error(f"Failed to transition {job_id}: {e}")
            return False

    def bulk_transition(self, job_ids: list[str], new_state: str,
                        notes: str = "") -> dict:
        """
        Move multiple jobs to a new state.

        Returns dict with 'succeeded' and 'failed' lists.
        """
        succeeded = []
        failed = []
        for job_id in job_ids:
            if self.transition(job_id, new_state, notes):
                succeeded.append(job_id)
            else:
                failed.append(job_id)

        log.info(
            f"Bulk transition to '{new_state}': "
            f"{len(succeeded)} succeeded, {len(failed)} failed"
        )
        return {"succeeded": succeeded, "failed": failed}

    def is_valid_transition(self, current: str, target: str) -> bool:
        """Check if a state transition is allowed."""
        if current in TERMINAL_STATES:
            return False
        allowed = VALID_TRANSITIONS.get(current, set())
        return target in allowed

    # ── State Queries ────────────────────────────────────────────

    def get_state(self, job_id: str) -> str:
        """Get the current state for a job. Returns empty string if not found."""
        try:
            row = self.state.conn.execute(
                "SELECT current_state FROM pipeline_states WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return row["current_state"] if row else ""
        except Exception as e:
            log.error(f"Failed to get state for {job_id}: {e}")
            return ""

    def get_by_state(self, state: str) -> list[dict]:
        """Get all jobs in a given state."""
        if state not in STATES:
            log.warning(f"Unknown state: {state}")
            return []

        try:
            rows = self.state.conn.execute(
                """SELECT job_id, company, title, current_state, previous_state,
                          evaluation_grade, priority, notes, updated_at
                   FROM pipeline_states WHERE current_state = ?
                   ORDER BY priority DESC, updated_at ASC""",
                (state,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error(f"Failed to get jobs by state '{state}': {e}")
            return []

    def get_state_history(self, job_id: str) -> list[dict]:
        """Get the full transition history for a job."""
        row = self._get_row(job_id)
        if not row:
            return []
        return self._load_history(row["state_history"])

    def get_pipeline_summary(self) -> dict:
        """Get counts of jobs in each state."""
        summary = {s: 0 for s in STATES}
        try:
            rows = self.state.conn.execute(
                """SELECT current_state, COUNT(*) as cnt
                   FROM pipeline_states
                   GROUP BY current_state"""
            ).fetchall()
            for r in rows:
                state = r["current_state"]
                if state in summary:
                    summary[state] = r["cnt"]
        except Exception as e:
            log.error(f"Failed to get pipeline summary: {e}")
        return summary

    def get_priority_queue(self) -> list[dict]:
        """
        Get jobs sorted by priority for action.

        Orders: evaluated > queued first, then by priority desc,
        then by match grade.
        """
        try:
            rows = self.state.conn.execute(
                """SELECT job_id, company, title, current_state,
                          evaluation_grade, priority, notes, updated_at
                   FROM pipeline_states
                   WHERE current_state IN ('evaluated', 'queued')
                   ORDER BY
                       CASE current_state
                           WHEN 'queued' THEN 1
                           WHEN 'evaluated' THEN 2
                           ELSE 3
                       END,
                       priority DESC,
                       CASE evaluation_grade
                           WHEN 'A' THEN 1
                           WHEN 'B' THEN 2
                           WHEN 'C' THEN 3
                           WHEN 'D' THEN 4
                           WHEN 'F' THEN 5
                           ELSE 6
                       END,
                       updated_at ASC"""
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error(f"Failed to get priority queue: {e}")
            return []

    def get_pipeline_report(self) -> str:
        """Generate a formatted pipeline status report."""
        summary = self.get_pipeline_summary()
        total = sum(summary.values())

        lines = [
            "=" * 60,
            "APPLICATION PIPELINE REPORT",
            "=" * 60,
            f"Total tracked: {total}",
            "",
            "--- Active Pipeline ---",
        ]

        active_states = ["discovered", "evaluated", "queued", "applied",
                         "responded", "interviewing", "offer"]
        for s in active_states:
            count = summary.get(s, 0)
            if count > 0:
                bar = "#" * min(count, 40)
                lines.append(f"  {s:<15} {count:>4}  {bar}")

        lines.append("")
        lines.append("--- Outcomes ---")

        outcome_states = ["accepted", "rejected", "withdrawn", "discarded", "ghosted"]
        for s in outcome_states:
            count = summary.get(s, 0)
            if count > 0:
                lines.append(f"  {s:<15} {count:>4}")

        # Conversion metrics
        applied_count = summary.get("applied", 0)
        responded_count = summary.get("responded", 0)
        interviewing_count = summary.get("interviewing", 0)
        offer_count = summary.get("offer", 0)
        accepted_count = summary.get("accepted", 0)

        lines.append("")
        lines.append("--- Conversion Rates ---")

        if applied_count > 0:
            response_rate = ((responded_count + interviewing_count + offer_count +
                              accepted_count) / applied_count) * 100
            lines.append(f"  Response rate:    {response_rate:.1f}%")

        if responded_count + interviewing_count > 0:
            interview_pool = responded_count + interviewing_count
            interview_to_offer = ((offer_count + accepted_count) / interview_pool) * 100
            lines.append(f"  Interview->Offer: {interview_to_offer:.1f}%")

        lines.append("=" * 60)
        return "\n".join(lines)

    # ── Auto-Ghost ───────────────────────────────────────────────

    def auto_ghost(self, days: int = None) -> list[str]:
        """
        Move 'applied' jobs with no activity for N days to 'ghosted'.

        Returns list of ghosted job_ids.
        """
        if not self.enabled:
            return []

        days = days or self.ghost_days
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        try:
            rows = self.state.conn.execute(
                """SELECT job_id FROM pipeline_states
                   WHERE current_state = 'applied' AND updated_at < ?""",
                (cutoff,),
            ).fetchall()
        except Exception as e:
            log.error(f"Failed to query for ghosted jobs: {e}")
            return []

        ghosted = []
        for r in rows:
            job_id = r["job_id"]
            if self.transition(job_id, "ghosted",
                               f"Auto-ghosted after {days} days with no response"):
                ghosted.append(job_id)

        if ghosted:
            log.info(f"Auto-ghosted {len(ghosted)} jobs (>{days} days)")
        return ghosted

    # ── Internal Helpers ─────────────────────────────────────────

    def _get_row(self, job_id: str):
        """Fetch the full pipeline row for a job."""
        try:
            return self.state.conn.execute(
                """SELECT job_id, company, title, current_state, previous_state,
                          state_history, evaluation_grade, priority, notes, updated_at
                   FROM pipeline_states WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
        except Exception as e:
            log.error(f"Failed to fetch pipeline row for {job_id}: {e}")
            return None

    def _load_history(self, history_json: str) -> list[dict]:
        """Parse state_history JSON safely."""
        if not history_json:
            return []
        try:
            result = json.loads(history_json)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
