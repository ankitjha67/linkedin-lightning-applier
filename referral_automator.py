"""Referral Request Automator module.

When network_leverage finds 1st-degree connections at a target company,
this module auto-drafts and optionally sends referral request messages
via LinkedIn messaging.

Uses the `referral_requests` table defined in state.py:
  id, job_id, company, job_title, connection_name, connection_url,
  message_text, status, sent_at, created_at
"""

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger("lla.referral")

# LinkedIn connection-request notes are limited to 300 characters.
MAX_CONNECTION_NOTE_LENGTH = 300


class ReferralAutomator:
    """Drafts and sends referral request messages to LinkedIn connections."""

    def __init__(self, ai, cfg, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        rc = cfg.get("referral_automator", {})
        self.enabled = rc.get("enabled", False)
        self.daily_limit = rc.get("max_requests_per_day", rc.get("daily_limit", 5))
        self.auto_send = rc.get("auto_send", False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def draft_referral_request(self, job_id, company, job_title,
                               connection_name, connection_url,
                               is_first_degree=True):
        """AI-generate a referral request message and store it as a draft.

        For 1st-degree connections a full message is generated.
        For non-connected users the message is trimmed to 300 characters
        (LinkedIn connection-request note limit).

        Returns the draft dict or None on failure.
        """
        if not self.enabled:
            logger.debug("ReferralAutomator disabled; skipping draft.")
            return None

        try:
            message = self._generate_message(
                job_title, company, connection_name, is_first_degree
            )
            if not message:
                return None

            # Enforce length constraint for connection-request notes
            if not is_first_degree and len(message) > MAX_CONNECTION_NOTE_LENGTH:
                message = message[:MAX_CONNECTION_NOTE_LENGTH - 3].rsplit(" ", 1)[0] + "..."

            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                """INSERT INTO referral_requests
                   (job_id, company, job_title, connection_name, connection_url,
                    message_text, status, sent_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'draft', NULL, ?)""",
                (job_id, company, job_title, connection_name, connection_url,
                 message, now),
            )
            self.state.conn.commit()

            logger.info("Drafted referral request for %s at %s to %s.",
                        job_title, company, connection_name)
            return {
                "job_id": job_id,
                "company": company,
                "job_title": job_title,
                "connection_name": connection_name,
                "connection_url": connection_url,
                "is_first_degree": is_first_degree,
                "message_text": message,
                "status": "draft",
            }
        except Exception as exc:
            logger.error("Error drafting referral request: %s", exc)
            return None

    def send_referral_request(self, driver, connection_url, message_text):
        """Send a referral request message via LinkedIn messaging.

        Returns True if the message was sent successfully, False otherwise.
        """
        if not self.enabled or not connection_url:
            return False

        try:
            from linkedin import send_linkedin_message
            return send_linkedin_message(driver, connection_url, message_text)
        except Exception as exc:
            logger.error("Failed to send referral to %s: %s", connection_url, exc)
            return False

    def get_pending_requests(self):
        """Return all unsent (draft) referral requests."""
        try:
            rows = self.state.conn.execute(
                """SELECT id, job_id, company, job_title, connection_name,
                          connection_url, message_text, created_at
                   FROM referral_requests
                   WHERE status = 'draft'
                   ORDER BY created_at ASC"""
            ).fetchall()
            return [
                {
                    "id": r["id"], "job_id": r["job_id"], "company": r["company"],
                    "job_title": r["job_title"], "connection_name": r["connection_name"],
                    "connection_url": r["connection_url"], "message_text": r["message_text"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.error("Error fetching pending requests: %s", exc)
            return []

    def process_requests(self, driver):
        """Send pending referral requests, respecting the daily limit.

        Returns the number of messages successfully sent. Only sends when
        auto_send is enabled; otherwise drafts remain for manual review.
        """
        if not self.enabled or not self.auto_send:
            return 0

        pending = self.get_pending_requests()
        if not pending:
            return 0

        remaining = max(0, self.daily_limit - self._count_sent_today())
        if remaining == 0:
            logger.info("Daily referral request limit (%d) reached.", self.daily_limit)
            return 0

        sent_count = 0
        for request in pending[:remaining]:
            success = self.send_referral_request(
                driver, request["connection_url"], request["message_text"]
            )
            now = datetime.now(timezone.utc).isoformat()
            new_status = "sent" if success else "failed"
            self.state.conn.execute(
                "UPDATE referral_requests SET status = ?, sent_at = ? WHERE id = ?",
                (new_status, now if success else None, request["id"]),
            )
            self.state.conn.commit()
            if success:
                sent_count += 1
            time.sleep(3)  # avoid rate limits

        logger.info("Processed %d referral requests; %d sent.",
                    len(pending[:remaining]), sent_count)
        return sent_count

    def get_referral_stats(self):
        """Return counts by status."""
        try:
            def _count(where, params=()):
                row = self.state.conn.execute(
                    f"SELECT COUNT(*) as c FROM referral_requests WHERE {where}", params
                ).fetchone()
                return row["c"] if row else 0

            return {
                "draft": _count("status = 'draft'"),
                "sent": _count("status = 'sent'"),
                "failed": _count("status = 'failed'"),
            }
        except Exception as exc:
            logger.error("Error fetching referral stats: %s", exc)
            return {"draft": 0, "sent": 0, "failed": 0}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_message(self, job_title, company, connection_name, is_first_degree):
        """Generate a referral message via AI, with a graceful fallback."""
        first_name = connection_name.split()[0] if connection_name else "there"

        if not self.ai or not getattr(self.ai, "enabled", False):
            if is_first_degree:
                return (f"Hi {first_name}, I noticed {company} is hiring for a "
                        f"{job_title} role. Since you're there, would you be open to "
                        f"referring me? Happy to share a tailored resume. Thanks!")
            return (f"Hi {first_name}, I'm applying for the {job_title} role at "
                    f"{company} and would love to connect.")[:MAX_CONNECTION_NOTE_LENGTH]

        if is_first_degree:
            system = (
                "You write warm, professional LinkedIn referral-request messages. "
                "Personal, mention the specific role, under 120 words, mention a "
                "tailored resume is ready, make it easy to decline. No cliches. "
                "Return only the message text."
            )
        else:
            system = (
                f"You write concise LinkedIn connection-request notes under "
                f"{MAX_CONNECTION_NOTE_LENGTH} characters. Direct, personal, mention "
                f"the role briefly. Return only the note text."
            )
        user = (f"Recipient: {connection_name}\nRole: {job_title}\n"
                f"Company: {company}\n\n{getattr(self.ai, 'profile_context', '')[:1000]}")
        try:
            return self.ai._call_llm(system, user) or ""
        except Exception as exc:
            logger.debug("AI referral generation failed: %s", exc)
            return ""

    def _count_sent_today(self):
        """Count how many referral requests were sent today."""
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            row = self.state.conn.execute(
                "SELECT COUNT(*) as c FROM referral_requests WHERE sent_at LIKE ?",
                (f"{today}%",),
            ).fetchone()
            return row["c"] if row else 0
        except Exception as exc:
            logger.error("Error counting today's sent requests: %s", exc)
            return 0
