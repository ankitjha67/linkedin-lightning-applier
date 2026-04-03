"""
Application Withdrawal / Retraction Module

Auto-withdraws pending applications when user gets an offer or decides against a company.
Navigates LinkedIn to perform actual withdrawal actions.
"""

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ApplicationWithdrawer:
    """Manages queuing and executing application withdrawals on LinkedIn."""

    VALID_REASONS = (
        "accepted_offer",
        "declined_company",
        "position_filled",
        "changed_mind",
        "duplicate",
        "other",
    )

    def __init__(self, cfg, state):
        self.cfg = cfg
        self.state = state
        withdrawal_cfg = cfg.get("application_withdrawal", {})
        self.enabled = withdrawal_cfg.get("enabled", False)
        self.batch_size = withdrawal_cfg.get("batch_size", 5)
        self.delay_between_withdrawals = withdrawal_cfg.get("delay_seconds", 3)
        self.dry_run = withdrawal_cfg.get("dry_run", False)
        if self.enabled:
            logger.info("ApplicationWithdrawer enabled (batch_size=%d)", self.batch_size)
        else:
            logger.debug("ApplicationWithdrawer disabled")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def queue_withdrawal(self, job_id, company, title, reason="other"):
        """Schedule a withdrawal for a specific job application.

        Args:
            job_id: LinkedIn job ID.
            company: Company name.
            title: Job title.
            reason: One of VALID_REASONS explaining why withdrawing.

        Returns:
            True if queued successfully, False otherwise.
        """
        if not self.enabled:
            logger.debug("Withdrawal queuing skipped: module disabled")
            return False

        if reason not in self.VALID_REASONS:
            logger.warning("Invalid withdrawal reason '%s', defaulting to 'other'", reason)
            reason = "other"

        try:
            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                """INSERT OR IGNORE INTO withdrawal_queue
                   (job_id, company, title, reason, status, queued_at)
                   VALUES (?, ?, ?, ?, 'pending', ?)""",
                (str(job_id), company, title, reason, now),
            )
            self.state.conn.commit()
            logger.info(
                "Queued withdrawal: job_id=%s company=%s reason=%s",
                job_id, company, reason,
            )
            return True
        except Exception:
            logger.exception("Failed to queue withdrawal for job %s", job_id)
            return False

    def withdraw_application(self, driver, job_id):
        """Navigate to a LinkedIn job page and click withdraw.

        Navigates to /jobs/view/{job_id}, opens the '...' menu, and clicks
        'Withdraw application'.

        Args:
            driver: Selenium WebDriver instance.
            job_id: LinkedIn job ID to withdraw from.

        Returns:
            True if withdrawal succeeded, False otherwise.
        """
        if not self.enabled:
            logger.debug("Withdrawal skipped: module disabled")
            return False

        url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        logger.info("Navigating to %s to withdraw", url)

        if self.dry_run:
            logger.info("[DRY RUN] Would withdraw application for job %s", job_id)
            self._update_status(job_id, "withdrawn_dry_run")
            return True

        try:
            driver.get(url)
            time.sleep(2)

            # Step 1: find and click the "..." menu button
            more_button = self._find_more_button(driver)
            if not more_button:
                logger.warning("Could not find more-actions button for job %s", job_id)
                self._update_status(job_id, "failed_no_menu")
                return False

            more_button.click()
            time.sleep(1)

            # Step 2: find and click "Withdraw application"
            withdraw_item = self._find_withdraw_option(driver)
            if not withdraw_item:
                logger.warning("Withdraw option not found for job %s", job_id)
                self._update_status(job_id, "failed_no_option")
                return False

            withdraw_item.click()
            time.sleep(1)

            # Step 3: confirm the withdrawal dialog if present
            self._confirm_withdrawal_dialog(driver)
            time.sleep(1)

            self._update_status(job_id, "withdrawn")
            logger.info("Successfully withdrew application for job %s", job_id)
            return True

        except Exception:
            logger.exception("Error withdrawing application for job %s", job_id)
            self._update_status(job_id, "failed_error")
            return False

    def process_withdrawals(self, driver):
        """Process all pending withdrawals up to batch_size.

        Args:
            driver: Selenium WebDriver instance.

        Returns:
            Dict with counts: {"processed", "succeeded", "failed", "remaining"}.
        """
        if not self.enabled:
            logger.debug("Withdrawal processing skipped: module disabled")
            return {"processed": 0, "succeeded": 0, "failed": 0, "remaining": 0}

        pending = self._get_pending_withdrawals()
        batch = pending[: self.batch_size]
        succeeded = 0
        failed = 0

        for row in batch:
            job_id = row["job_id"]
            company = row.get("company", "?")
            title = row.get("title", "?")
            logger.info("Withdrawing: %s at %s (job_id=%s)", title, company, job_id)

            ok = self.withdraw_application(driver, job_id)
            if ok:
                succeeded += 1
            else:
                # Increment attempt counter; mark as permanently failed after 3 tries
                attempt = row.get("attempts", 0) + 1
                new_status = "failed" if attempt >= 3 else "pending"
                try:
                    now = datetime.now(timezone.utc).isoformat()
                    self.state.conn.execute(
                        """UPDATE withdrawal_queue
                           SET status=?, attempts=?, processed_at=?
                           WHERE job_id=? AND status IN ('pending','failed_no_menu',
                               'failed_no_option','failed_error')""",
                        (new_status, attempt, now, job_id),
                    )
                    self.state.conn.commit()
                except Exception:
                    logger.exception("Failed to update attempt count for job %s", job_id)
                failed += 1

            # Delay between withdrawals to look human
            if self.delay_between_withdrawals > 0 and row is not batch[-1]:
                time.sleep(self.delay_between_withdrawals)

        remaining = self._count_pending()
        result = {
            "processed": len(batch),
            "succeeded": succeeded,
            "failed": failed,
            "remaining": remaining,
        }
        logger.info("Withdrawal batch complete: %s", result)
        return result

    def auto_withdraw_on_offer(self, company):
        """When an offer is received, withdraw all other pending applications.

        Args:
            company: The company that extended the offer.

        Returns:
            Number of applications queued for withdrawal.
        """
        if not self.enabled:
            logger.debug("auto_withdraw_on_offer skipped: module disabled")
            return 0

        try:
            rows = self.state.conn.execute(
                """SELECT job_id, company, title FROM applications
                   WHERE status IN ('applied', 'pending', 'submitted')
                   AND LOWER(company) != LOWER(?)""",
                (company,),
            ).fetchall()
        except Exception:
            logger.exception("Failed to query applications for auto-withdrawal")
            return 0

        if not rows:
            logger.info("No other pending applications to withdraw after offer from %s", company)
            return 0

        queued_count = 0
        for row in rows:
            ok = self.queue_withdrawal(
                row["job_id"],
                row["company"],
                row["title"],
                reason="accepted_offer",
            )
            if ok:
                queued_count += 1

        logger.info(
            "Offer from %s: queued %d withdrawal(s) for other companies",
            company, queued_count,
        )
        return queued_count

    def get_pending_count(self):
        """Return the number of pending withdrawals."""
        return self._count_pending()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_pending_withdrawals(self):
        """Fetch all pending withdrawal rows."""
        try:
            return self.state.conn.execute(
                """SELECT * FROM withdrawal_queue
                   WHERE status = 'pending'
                   ORDER BY queued_at ASC"""
            )
        except Exception:
            logger.exception("Failed to fetch pending withdrawals")
            return []

    def _count_pending(self):
        """Count pending withdrawals."""
        try:
            row = self.state.conn.execute(
                "SELECT COUNT(*) as cnt FROM withdrawal_queue WHERE status = 'pending'"
            ).fetchone()
            return row["cnt"] if row else 0
        except Exception:
            return 0

    def _update_status(self, job_id, status):
        """Update the status of a withdrawal queue entry."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            self.state.conn.execute(
                """UPDATE withdrawal_queue
                   SET status = ?, processed_at = ?
                   WHERE job_id = ? AND status = 'pending'""",
                (status, now, str(job_id)),
            )
            self.state.conn.commit()
        except Exception:
            logger.exception("Failed to update withdrawal status for job %s", job_id)

    def _find_more_button(self, driver):
        """Locate the '...' overflow menu on a LinkedIn job page."""
        selectors = [
            "button[aria-label='More actions']",
            "button.jobs-save-button + button",
            "button.artdeco-dropdown__trigger",
            "button[data-control-name='overflow_menu']",
        ]
        for sel in selectors:
            try:
                from selenium.webdriver.common.by import By

                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                if elems:
                    return elems[0]
            except Exception:
                continue
        return None

    def _find_withdraw_option(self, driver):
        """Find the 'Withdraw application' menu item."""
        try:
            from selenium.webdriver.common.by import By

            items = driver.find_elements(
                By.CSS_SELECTOR,
                "div[role='menuitem'], li.artdeco-dropdown__item, [role='menuitem']",
            )
            for item in items:
                text = item.text.strip().lower()
                if "withdraw" in text:
                    return item
        except Exception:
            logger.debug("Error searching for withdraw option")
        return None

    def _confirm_withdrawal_dialog(self, driver):
        """Click confirm on the withdrawal confirmation dialog if it appears."""
        try:
            from selenium.webdriver.common.by import By

            buttons = driver.find_elements(
                By.CSS_SELECTOR,
                "button[data-control-name='withdraw_confirm'], "
                "button.artdeco-modal__confirm-dialog-btn",
            )
            for btn in buttons:
                if btn.is_displayed():
                    btn.click()
                    logger.debug("Confirmed withdrawal dialog")
                    return True
            # Fallback: primary button in a modal
            modals = driver.find_elements(
                By.CSS_SELECTOR,
                "div.artdeco-modal button.artdeco-button--primary",
            )
            for btn in modals:
                text = btn.text.strip().lower()
                if btn.is_displayed() and ("withdraw" in text or "yes" in text or "confirm" in text):
                    btn.click()
                    return True
        except Exception:
            logger.debug("No withdrawal confirmation dialog found or error clicking it")
        return False
