"""
Application Follow-Up Engine.

Multi-touch follow-up cadence after applying:
- Touch 1: Initial recruiter message (handled by recruiter_messenger.py)
- Touch 2: Follow-up at 5-7 days if no response
- Touch 3: Final follow-up at 14 days

Candidates who follow up are 2-3x more likely to get a response.
"""

import logging
import random
from datetime import datetime, timedelta

log = logging.getLogger("lla.follow_up")

# Default cadence: days after initial application
DEFAULT_CADENCE = [
    {"touch": 2, "days": 5, "type": "follow_up_1"},
    {"touch": 3, "days": 14, "type": "final_follow_up"},
]


class FollowUpEngine:
    """Schedule and execute multi-touch follow-up messages."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        fu_cfg = cfg.get("follow_up", {})
        self.enabled = fu_cfg.get("enabled", False)
        self.max_touches = fu_cfg.get("max_touches", 3)
        self.max_per_day = fu_cfg.get("max_follow_ups_per_day", 5)
        self.cadence = fu_cfg.get("cadence", DEFAULT_CADENCE)
        self.skip_if_responded = fu_cfg.get("skip_if_responded", True)

    def schedule_follow_ups(self, job_id: str, recruiter_name: str,
                            profile_url: str, company: str, job_title: str,
                            applied_at: str = ""):
        """Schedule follow-up messages based on the cadence."""
        if not self.enabled or not recruiter_name:
            return

        if not profile_url:
            return

        try:
            base_time = datetime.strptime(applied_at, "%Y-%m-%d %H:%M:%S") if applied_at \
                else datetime.now()
        except (ValueError, TypeError):
            base_time = datetime.now()

        for step in self.cadence:
            touch = step["touch"]
            if touch > self.max_touches:
                break

            # Check if this touch already scheduled
            existing = self.state.conn.execute("""
                SELECT 1 FROM follow_up_queue
                WHERE job_id=? AND touch_number=?
            """, (job_id, touch)).fetchone()
            if existing:
                continue

            days = step["days"]
            # Add some randomness to avoid patterns (+-6 hours)
            jitter_hours = random.uniform(-6, 6)
            scheduled = base_time + timedelta(days=days, hours=jitter_hours)

            # Don't schedule in the past
            if scheduled < datetime.now():
                scheduled = datetime.now() + timedelta(hours=random.uniform(1, 4))

            # Generate message
            message = self._generate_follow_up(
                job_title, company, recruiter_name, touch, step["type"]
            )
            if not message:
                continue

            scheduled_str = scheduled.strftime("%Y-%m-%d %H:%M:%S")
            self.state.queue_follow_up(
                job_id=job_id,
                recruiter_name=recruiter_name,
                profile_url=profile_url,
                company=company,
                job_title=job_title,
                message_text=message,
                scheduled_at=scheduled_str,
                touch_number=touch,
            )
            log.info(f"   Follow-up #{touch} scheduled for {recruiter_name} at {scheduled_str}")

    def process_follow_ups(self, driver):
        """Send follow-up messages that are due."""
        if not self.enabled:
            return

        # Count today's sent follow-ups
        today = datetime.now().strftime("%Y-%m-%d")
        sent_today = self.state.conn.execute("""
            SELECT COUNT(*) as c FROM follow_up_queue
            WHERE status='sent' AND date(sent_at)=?
        """, (today,)).fetchone()["c"]

        if sent_today >= self.max_per_day:
            log.debug("Daily follow-up limit reached")
            return

        pending = self.state.get_pending_follow_ups()
        if not pending:
            return

        log.info(f"Processing {len(pending)} follow-up messages...")

        for fu in pending:
            if sent_today >= self.max_per_day:
                break

            job_id = fu["job_id"]

            # Skip if already got a response
            if self.skip_if_responded:
                response = self.state.conn.execute("""
                    SELECT 1 FROM response_tracking
                    WHERE job_id=? AND response_type IN ('callback', 'interview', 'offer')
                """, (job_id,)).fetchone()
                if response:
                    self.state.update_follow_up_status(fu["id"], "skipped")
                    log.debug(f"  Skipping follow-up for {job_id} — already got response")
                    continue

                # Also skip if email response detected
                email_resp = self.state.conn.execute("""
                    SELECT 1 FROM email_responses
                    WHERE job_id=? AND response_type IN ('interview', 'assessment', 'positive')
                """, (job_id,)).fetchone()
                if email_resp:
                    self.state.update_follow_up_status(fu["id"], "skipped")
                    continue

            # Send the message
            success = self._send_follow_up(driver, fu)
            if success:
                self.state.update_follow_up_status(fu["id"], "sent")
                sent_today += 1
                log.info(f"  Sent follow-up #{fu['touch_number']} to {fu['recruiter_name']} "
                        f"for {fu['job_title']} @ {fu['company']}")
            else:
                self.state.update_follow_up_status(fu["id"], "failed")

            # Delay between messages
            import time
            time.sleep(random.uniform(30, 90))

    def _generate_follow_up(self, job_title: str, company: str,
                            recruiter_name: str, touch: int, touch_type: str) -> str:
        """Generate follow-up message text using AI."""
        first_name = recruiter_name.split()[0] if recruiter_name else "there"

        if not self.ai or not self.ai.enabled:
            if touch == 2:
                return (
                    f"Hi {first_name},\n\n"
                    f"I wanted to follow up on my application for the {job_title} "
                    f"position at {company}. I remain very interested in this opportunity "
                    f"and would welcome the chance to discuss how my experience aligns "
                    f"with what you're looking for.\n\n"
                    f"Would you have a few minutes for a quick conversation?\n\n"
                    f"Best regards"
                )
            else:  # touch 3 - final
                return (
                    f"Hi {first_name},\n\n"
                    f"I'm circling back one more time regarding the {job_title} role "
                    f"at {company}. I understand how busy things can get, so I wanted "
                    f"to reiterate my strong interest.\n\n"
                    f"If the timing isn't right, I'd still love to stay connected for "
                    f"future opportunities. Either way, thank you for your time!\n\n"
                    f"Best regards"
                )

        tone_map = {
            "follow_up_1": "polite and brief follow-up (3-4 sentences). Express continued interest. Don't be pushy.",
            "final_follow_up": "warm final follow-up (3-4 sentences). Acknowledge they may be busy. Offer to stay connected for future roles. Graceful close.",
        }
        tone = tone_map.get(touch_type, tone_map["follow_up_1"])

        system = f"""You write brief LinkedIn follow-up messages to recruiters.

RULES:
- {tone}
- Use the recruiter's first name
- Reference the specific role and company
- Sound genuine, not robotic
- Never mention you are an AI
- Maximum 4 sentences

{self.ai.profile_context}"""

        user = f"""Write follow-up #{touch} to {recruiter_name} about the {job_title} role at {company}.
This is a {'gentle follow-up' if touch == 2 else 'final follow-up'} message."""

        try:
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 250
            result = self.ai._call_llm(system, user)
            self.ai.max_tokens = old_max
            return result or ""
        except Exception as e:
            log.debug(f"Follow-up message generation failed: {e}")
            return ""

    def _send_follow_up(self, driver, follow_up: dict) -> bool:
        """Send a follow-up message via LinkedIn."""
        try:
            from linkedin import send_linkedin_message
            return send_linkedin_message(
                driver,
                profile_url=follow_up["profile_url"],
                message=follow_up["message_text"],
            )
        except Exception as e:
            log.warning(f"Follow-up send failed: {e}")
            return False

    def get_follow_up_stats(self) -> dict:
        """Get follow-up effectiveness stats."""
        total = self.state.conn.execute(
            "SELECT COUNT(*) as c FROM follow_up_queue WHERE status='sent'"
        ).fetchone()["c"]

        by_touch = self.state.conn.execute("""
            SELECT touch_number, COUNT(*) as sent,
                   SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) as delivered
            FROM follow_up_queue
            GROUP BY touch_number
        """).fetchall()

        # Check response rate after follow-ups
        responded_after = self.state.conn.execute("""
            SELECT COUNT(DISTINCT f.job_id) as c
            FROM follow_up_queue f
            JOIN response_tracking r ON f.job_id = r.job_id
            WHERE f.status='sent'
              AND r.response_type IN ('callback', 'interview', 'offer')
        """).fetchone()["c"]

        return {
            "total_sent": total,
            "by_touch": {r["touch_number"]: r["sent"] for r in by_touch},
            "responses_after_follow_up": responded_after,
            "response_rate": round(responded_after / max(total, 1) * 100, 1),
        }
