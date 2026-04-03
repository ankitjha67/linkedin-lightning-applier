"""Referral Request Automator module.

When network_leverage finds 1st-degree connections at a target company,
this module auto-drafts and optionally sends referral request messages
via LinkedIn messaging.
"""

import logging
import json
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# LinkedIn connection-request notes are limited to 300 characters.
MAX_CONNECTION_NOTE_LENGTH = 300


class ReferralAutomator:
    """Drafts and sends referral request messages to LinkedIn connections."""

    def __init__(self, ai, cfg, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        self.enabled = cfg.get("referral_automator", {}).get("enabled", False)
        self.daily_limit = cfg.get("referral_automator", {}).get("daily_limit", 10)
        self._ensure_tables()

    def _ensure_tables(self):
        """Create the referral_requests table if it does not exist."""
        try:
            self.state.conn.execute(
                """CREATE TABLE IF NOT EXISTS referral_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    company TEXT,
                    job_title TEXT,
                    connection_name TEXT,
                    connection_url TEXT,
                    is_first_degree INTEGER DEFAULT 1,
                    message TEXT,
                    status TEXT DEFAULT 'draft',
                    sent_at TEXT,
                    responded INTEGER DEFAULT 0,
                    successful INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                )"""
            )
            self.state.conn.commit()
        except Exception as exc:
            logger.error("Failed to initialise referral_requests table: %s", exc)

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
            if is_first_degree:
                prompt = self._build_full_message_prompt(
                    job_title, company, connection_name
                )
            else:
                prompt = self._build_short_note_prompt(
                    job_title, company, connection_name
                )

            message = self.ai.generate(prompt)

            # Enforce length constraint for connection-request notes
            if not is_first_degree and len(message) > MAX_CONNECTION_NOTE_LENGTH:
                message = message[:MAX_CONNECTION_NOTE_LENGTH - 3].rsplit(" ", 1)[0] + "..."

            now = datetime.now(timezone.utc).isoformat()

            self.state.conn.execute(
                """INSERT INTO referral_requests
                   (job_id, company, job_title, connection_name, connection_url,
                    is_first_degree, message, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (job_id, company, job_title, connection_name, connection_url,
                 int(is_first_degree), message, now, now),
            )
            self.state.conn.commit()

            draft = {
                "job_id": job_id,
                "company": company,
                "job_title": job_title,
                "connection_name": connection_name,
                "connection_url": connection_url,
                "is_first_degree": is_first_degree,
                "message": message,
                "status": "draft",
            }
            logger.info(
                "Drafted referral request for %s at %s to %s.",
                job_title, company, connection_name,
            )
            return draft
        except Exception as exc:
            logger.error("Error drafting referral request: %s", exc)
            return None

    def send_referral_request(self, driver, connection_url, message):
        """Send a referral request message via LinkedIn messaging.

        Returns True if the message was sent successfully, False otherwise.
        """
        if not self.enabled:
            logger.debug("ReferralAutomator disabled; skipping send.")
            return False

        try:
            # Navigate to the connection's messaging page
            messaging_url = connection_url.rstrip("/") + "/overlay/message/"
            driver.get(messaging_url)
            time.sleep(2)

            # Find the message input area
            msg_box = driver.find_element(
                "css selector",
                "div.msg-form__contenteditable, textarea[name='message']"
            )
            msg_box.click()
            time.sleep(0.5)

            # Type the message
            msg_box.send_keys(message)
            time.sleep(0.5)

            # Click the send button
            send_btn = driver.find_element(
                "css selector",
                "button.msg-form__send-button, button[type='submit']"
            )
            send_btn.click()
            time.sleep(1)

            logger.info("Sent referral request to %s.", connection_url)
            return True
        except Exception as exc:
            logger.error("Failed to send message to %s: %s", connection_url, exc)
            return False

    def get_pending_requests(self):
        """Return all unsent (draft) referral requests."""
        try:
            rows = self.state.conn.execute(
                """SELECT id, job_id, company, job_title, connection_name,
                          connection_url, is_first_degree, message, created_at
                   FROM referral_requests
                   WHERE status = 'draft'
                   ORDER BY created_at ASC"""
            ).fetchall()
            return [
                {
                    "id": r[0], "job_id": r[1], "company": r[2],
                    "job_title": r[3], "connection_name": r[4],
                    "connection_url": r[5], "is_first_degree": bool(r[6]),
                    "message": r[7], "created_at": r[8],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.error("Error fetching pending requests: %s", exc)
            return []

    def process_requests(self, driver):
        """Send all pending referral requests, respecting the daily limit.

        Returns the number of messages successfully sent.
        """
        if not self.enabled:
            logger.debug("ReferralAutomator disabled; skipping process.")
            return 0

        pending = self.get_pending_requests()
        if not pending:
            logger.info("No pending referral requests to process.")
            return 0

        sent_today = self._count_sent_today()
        remaining = max(0, self.daily_limit - sent_today)

        if remaining == 0:
            logger.info("Daily referral request limit (%d) reached.", self.daily_limit)
            return 0

        sent_count = 0
        for request in pending[:remaining]:
            success = self.send_referral_request(
                driver, request["connection_url"], request["message"]
            )
            now = datetime.now(timezone.utc).isoformat()
            if success:
                self.state.conn.execute(
                    """UPDATE referral_requests
                       SET status = 'sent', sent_at = ?, updated_at = ?
                       WHERE id = ?""",
                    (now, now, request["id"]),
                )
                sent_count += 1
            else:
                self.state.conn.execute(
                    """UPDATE referral_requests
                       SET status = 'failed', updated_at = ?
                       WHERE id = ?""",
                    (now, request["id"]),
                )
            self.state.conn.commit()

            # Brief pause between messages to avoid triggering rate limits
            time.sleep(3)

        logger.info("Processed %d referral requests; %d sent.", len(pending[:remaining]), sent_count)
        return sent_count

    def get_referral_stats(self):
        """Return counts: sent, responded, successful."""
        try:
            total_sent = self.state.conn.execute(
                "SELECT COUNT(*) FROM referral_requests WHERE status = 'sent'"
            ).fetchone()[0]
            total_responded = self.state.conn.execute(
                "SELECT COUNT(*) FROM referral_requests WHERE responded = 1"
            ).fetchone()[0]
            total_successful = self.state.conn.execute(
                "SELECT COUNT(*) FROM referral_requests WHERE successful = 1"
            ).fetchone()[0]
            total_draft = self.state.conn.execute(
                "SELECT COUNT(*) FROM referral_requests WHERE status = 'draft'"
            ).fetchone()[0]

            return {
                "draft": total_draft,
                "sent": total_sent,
                "responded": total_responded,
                "successful": total_successful,
            }
        except Exception as exc:
            logger.error("Error fetching referral stats: %s", exc)
            return {"draft": 0, "sent": 0, "responded": 0, "successful": 0}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_full_message_prompt(self, job_title, company, connection_name):
        """Build AI prompt for a full referral request message (1st degree)."""
        return (
            f"Write a professional but warm LinkedIn message to {connection_name} "
            f"requesting a referral for the '{job_title}' role at {company}.\n\n"
            f"Guidelines:\n"
            f"- Be personal and mention the specific role\n"
            f"- Keep it under 500 words\n"
            f"- Mention that you have a tailored resume ready to share\n"
            f"- Express genuine interest in the company\n"
            f"- Be respectful of their time and make it easy to say no\n"
            f"- Do not be overly formal or use cliches\n"
            f"Return only the message text, no subject line or extras."
        )

    def _build_short_note_prompt(self, job_title, company, connection_name):
        """Build AI prompt for a short connection-request note (non-1st degree)."""
        return (
            f"Write a very concise LinkedIn connection request note to "
            f"{connection_name} at {company} about the '{job_title}' role.\n\n"
            f"STRICT LIMIT: Maximum {MAX_CONNECTION_NOTE_LENGTH} characters.\n"
            f"- Be direct and personal\n"
            f"- Mention the specific role briefly\n"
            f"- Express interest in connecting\n"
            f"Return only the note text."
        )

    def _count_sent_today(self):
        """Count how many referral requests were sent today."""
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            row = self.state.conn.execute(
                "SELECT COUNT(*) FROM referral_requests WHERE sent_at LIKE ?",
                (f"{today}%",),
            ).fetchone()
            return row[0] if row else 0
        except Exception as exc:
            logger.error("Error counting today's sent requests: %s", exc)
            return 0
