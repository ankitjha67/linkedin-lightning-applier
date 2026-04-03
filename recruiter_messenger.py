"""
Auto-Message Recruiters After Applying.

Sends personalized LinkedIn messages or InMail to the recruiter/hiring manager
identified in the job posting. AI generates the message based on the job + profile.
Configurable delay (e.g., 2 hours after applying).
"""

import logging
import time
import random
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("lla.messenger")


class RecruiterMessenger:
    """Queue and send personalized messages to recruiters after applying."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        msg_cfg = cfg.get("recruiter_messaging", {})
        self.enabled = msg_cfg.get("enabled", False)
        self.delay_minutes = msg_cfg.get("delay_minutes", 120)
        self.max_per_day = msg_cfg.get("max_messages_per_day", 10)
        self.message_template = msg_cfg.get("message_template", "")
        self.skip_no_url = msg_cfg.get("skip_if_no_profile_url", True)

    def queue_message(self, job_id: str, recruiter_name: str, profile_url: str,
                      company: str, job_title: str, description: str = ""):
        """Queue a message to be sent after the configured delay."""
        if not self.enabled:
            return

        if self.skip_no_url and not profile_url:
            log.debug(f"  Skipping message queue: no profile URL for {recruiter_name}")
            return

        if not recruiter_name or len(recruiter_name.strip()) < 2:
            return

        # Generate message text
        message = self.generate_message(job_title, company, recruiter_name, description)
        if not message:
            return

        # Calculate scheduled time
        scheduled = datetime.now() + timedelta(minutes=self.delay_minutes)
        scheduled_str = scheduled.strftime("%Y-%m-%d %H:%M:%S")

        self.state.queue_message(
            job_id=job_id,
            recruiter_name=recruiter_name,
            profile_url=profile_url,
            message_text=message,
            scheduled_at=scheduled_str,
            company=company,
            job_title=job_title,
        )
        log.info(f"   📨 Message queued for {recruiter_name} (send at {scheduled_str})")

    def generate_message(self, job_title: str, company: str,
                         recruiter_name: str, description: str = "") -> str:
        """Generate a personalized recruiter message using AI."""
        # Use template if provided
        if self.message_template:
            return self.message_template.format(
                recruiter_name=recruiter_name.split()[0],
                job_title=job_title,
                company=company,
            )

        if not self.ai or not self.ai.enabled:
            # Default template
            first_name = recruiter_name.split()[0] if recruiter_name else "there"
            return (
                f"Hi {first_name},\n\n"
                f"I just applied for the {job_title} position at {company} and wanted to "
                f"express my strong interest. My background in the field aligns well with "
                f"the role requirements, and I'd love the opportunity to discuss how I can "
                f"contribute to the team.\n\n"
                f"Would you be open to a brief conversation?\n\n"
                f"Thank you for your time!"
            )

        # AI-generated message
        system_prompt = f"""You write brief, personalized LinkedIn messages to recruiters after applying for a job.

RULES:
- Keep it 3-5 sentences maximum
- Sound genuine and professional, not robotic
- Reference the specific role and company
- Mention 1-2 relevant qualifications from the candidate's profile
- Don't be pushy — express interest and ask for a conversation
- Use the recruiter's first name
- Never mention you are an AI

{self.ai.profile_context}"""

        user_prompt = f"""Write a message to {recruiter_name} at {company} about the {job_title} role.
Job context: {description[:500] if description else 'N/A'}

Keep it short (3-5 sentences), genuine, and specific."""

        try:
            old_max = self.ai.max_tokens
            self.ai.max_tokens = 300
            result = self.ai._call_llm(system_prompt, user_prompt)
            self.ai.max_tokens = old_max
            return result if result else ""
        except Exception as e:
            log.warning(f"Message generation failed: {e}")
            return ""

    def process_queue(self, driver):
        """Send messages that are past their scheduled time."""
        if not self.enabled:
            return

        # Check daily limit
        if self.state.daily_message_count() >= self.max_per_day:
            log.info("  📨 Daily message limit reached")
            return

        pending = self.state.get_pending_messages()
        if not pending:
            return

        log.info(f"  📨 Processing {len(pending)} queued messages...")

        for msg in pending:
            if self.state.daily_message_count() >= self.max_per_day:
                break

            try:
                success = self._send_message(
                    driver,
                    profile_url=msg["profile_url"],
                    message_text=msg["message_text"],
                    recruiter_name=msg["recruiter_name"],
                )

                if success:
                    self.state.update_message_status(msg["id"], "sent")
                    log.info(f"   ✅ Sent message to {msg['recruiter_name']}")
                else:
                    self.state.update_message_status(msg["id"], "failed")
                    log.info(f"   ❌ Failed to message {msg['recruiter_name']}")

                # Delay between messages
                time.sleep(random.uniform(30, 90))

            except Exception as e:
                log.warning(f"   Message send error: {e}")
                self.state.update_message_status(msg["id"], "failed")

    def _send_message(self, driver, profile_url: str, message_text: str,
                      recruiter_name: str = "") -> bool:
        """Navigate to recruiter's profile and send a message."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.common.exceptions import (
            NoSuchElementException, TimeoutException,
            ElementClickInterceptedException,
        )

        if not profile_url:
            return False

        original_url = driver.current_url

        try:
            # Navigate to profile
            driver.get(profile_url)
            time.sleep(random.uniform(3, 5))

            # Find and click "Message" button
            msg_btn = None
            for sel in [
                'button[aria-label*="Message"]',
                'a[aria-label*="Message"]',
                'button.pvs-profile-actions__action[aria-label*="Message"]',
                '.pv-top-card-v2-ctas button:nth-child(1)',
            ]:
                btns = driver.find_elements(By.CSS_SELECTOR, sel)
                for btn in btns:
                    if btn.is_displayed() and ("message" in btn.text.lower() or
                                               "message" in (btn.get_attribute("aria-label") or "").lower()):
                        msg_btn = btn
                        break
                if msg_btn:
                    break

            if not msg_btn:
                # Try generic button matching
                for btn in driver.find_elements(By.TAG_NAME, "button"):
                    if "message" in btn.text.lower() and btn.is_displayed():
                        msg_btn = btn
                        break

            if not msg_btn:
                log.debug(f"  No message button found on {profile_url}")
                return False

            # Click message button
            try:
                msg_btn.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", msg_btn)
            time.sleep(random.uniform(2, 4))

            # Find message input
            msg_input = None
            for sel in [
                'div[role="textbox"][contenteditable="true"]',
                'div.msg-form__contenteditable',
                'textarea.msg-form__textarea',
                '[data-artdeco-is-focused] div[contenteditable]',
            ]:
                inputs = driver.find_elements(By.CSS_SELECTOR, sel)
                if inputs:
                    msg_input = inputs[-1]  # Use last (newest) message input
                    break

            if not msg_input:
                log.debug("  No message input found")
                return False

            # Type message
            msg_input.click()
            time.sleep(0.5)
            msg_input.send_keys(message_text)
            time.sleep(random.uniform(1, 2))

            # Click send
            send_btn = None
            for sel in [
                'button.msg-form__send-button',
                'button[type="submit"]',
                'button.msg-form__send-btn',
            ]:
                btns = driver.find_elements(By.CSS_SELECTOR, sel)
                for btn in btns:
                    if btn.is_displayed() and btn.is_enabled():
                        send_btn = btn
                        break
                if send_btn:
                    break

            if not send_btn:
                for btn in driver.find_elements(By.TAG_NAME, "button"):
                    if "send" in btn.text.lower() and btn.is_displayed():
                        send_btn = btn
                        break

            if send_btn:
                send_btn.click()
                time.sleep(2)
                return True
            else:
                log.debug("  No send button found")
                return False

        except Exception as e:
            log.warning(f"  Message send error: {e}")
            return False
        finally:
            # Return to original page
            try:
                driver.get(original_url)
                time.sleep(2)
            except Exception:
                pass
