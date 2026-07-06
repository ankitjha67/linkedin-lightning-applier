"""
External Apply — ATS form filling.

For jobs whose "apply" link leaves LinkedIn, open the company's ATS and complete
the application with AI. This covers the ~60% of LinkedIn jobs that aren't Easy
Apply, plus every job discovered off-platform (Google Jobs, careers pages).

This module is a thin orchestrator: it manages the browser tab, per-cycle caps,
and ATS detection, then delegates the actual form filling to a platform handler
in ``ats_handlers/``. Supported platforms: Workday, Greenhouse, Lever, Ashby,
SmartRecruiters, Workable, Jobvite, BambooHR, iCIMS, Taleo, SuccessFactors, ADP.

Public API (used by main.py — keep stable):
    .enabled, .max_per_cycle, .applied_this_cycle
    .can_apply()  .detect_ats(url)  .apply_external(driver, url, ctx, resume)
"""

import logging
import time
from typing import Optional

from ats_handlers import ALL_ATS, get_handler
from ats_handlers import detect_ats as _registry_detect

log = logging.getLogger("lla.external_apply")


class ExternalApplier:
    """Detect an external ATS and drive its application form via a handler."""

    def __init__(self, ai, cfg: dict):
        self.ai = ai
        self.cfg = cfg
        ea_cfg = cfg.get("external_apply", {})
        self.enabled = ea_cfg.get("enabled", False)
        # Default to every platform we can drive; a config list narrows it.
        self.supported_ats = set(ea_cfg.get("supported_ats", ALL_ATS))
        self.max_per_cycle = ea_cfg.get("max_external_per_cycle", 5)
        self.timeout = ea_cfg.get("timeout_seconds", 120)
        self.applied_this_cycle = 0

    def can_apply(self) -> bool:
        return self.enabled and self.applied_this_cycle < self.max_per_cycle

    def detect_ats(self, url: str) -> Optional[str]:
        """Detect the ATS platform from a URL, honoring the supported_ats filter."""
        ats = _registry_detect(url)
        if ats and ats in self.supported_ats:
            return ats
        return None

    def apply_external(self, driver, apply_url: str, job_context: dict,
                       resume_path: str = "") -> bool:
        """
        Open the ATS URL in a new tab, fill and submit the form, then clean up.

        Args:
            driver: Selenium WebDriver
            apply_url: URL to the external application form
            job_context: {title, company, description, location}
            resume_path: Path to a resume file for upload

        Returns:
            True if the application was submitted (best-effort confirmed).
        """
        if not self.can_apply():
            return False

        ats = self.detect_ats(apply_url)
        if not ats:
            log.info(f"   Unsupported ATS: {apply_url[:80]}")
            return False

        handler = get_handler(ats, self.ai, self.cfg)
        if handler is None:
            log.info(f"   No handler for ATS '{ats}'")
            return False

        log.info(f"   🌐 External apply ({ats}): {apply_url[:80]}")
        original_window = driver.current_window_handle

        try:
            driver.execute_script(f"window.open('{apply_url}', '_blank');")
            time.sleep(2)

            new_window = [w for w in driver.window_handles if w != original_window]
            if not new_window:
                log.warning("   Failed to open new tab")
                return False

            driver.switch_to.window(new_window[0])
            time.sleep(3)

            success = False
            try:
                success = handler.apply(driver, job_context, resume_path)
            except Exception as e:
                log.warning(f"   {ats} handler error: {e}")

            if success:
                self.applied_this_cycle += 1
            return success

        except Exception as e:
            log.warning(f"   External apply error: {e}")
            return False
        finally:
            try:
                if len(driver.window_handles) > 1:
                    driver.close()
                driver.switch_to.window(original_window)
                time.sleep(1)
            except Exception:
                pass
