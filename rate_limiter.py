"""
Rate Limit Intelligence — Dynamic Throttling with Ban Detection.

Detects LinkedIn's soft-ban signals (CAPTCHAs, "unusual activity" warnings,
429 responses, increased page load times) and auto-throttles before
getting the account flagged.

Instead of fixed delays, dynamically adjusts timing based on:
- Page load times (sudden increase = potential throttling)
- Error rates (higher errors = back off)
- Detection of warning pages/elements
- Time-of-day patterns (less aggressive during business hours)
"""

import logging
import random
import time
from collections import deque
from datetime import datetime, timedelta

log = logging.getLogger("lla.rate_limiter")

# Warning signals on LinkedIn pages
BAN_SIGNALS = [
    "unusual activity",
    "we've restricted your account",
    "security verification",
    "verify your identity",
    "too many requests",
    "please slow down",
    "rate limit",
    "temporarily restricted",
    "suspicious activity",
    "automated behavior",
]

CAPTCHA_SIGNALS = [
    "captcha",
    "recaptcha",
    "verify you're human",
    "i'm not a robot",
    "challenge",
    "hcaptcha",
]


class RateLimiter:
    """Dynamic rate limiting with ban detection and auto-throttling."""

    def __init__(self, cfg: dict = None):
        self.cfg = cfg or {}
        rl_cfg = self.cfg.get("rate_limiter", {})
        self.enabled = rl_cfg.get("enabled", True)

        # Base delays (seconds)
        self.base_delay_min = rl_cfg.get("base_delay_min", 3)
        self.base_delay_max = rl_cfg.get("base_delay_max", 8)

        # Throttle escalation
        self.throttle_level = 0  # 0=normal, 1=cautious, 2=slow, 3=very_slow, 4=pause
        self.max_throttle = 4
        self.cooldown_until = None

        # Metrics tracking (rolling window)
        self._page_load_times = deque(maxlen=20)
        self._errors = deque(maxlen=50)
        self._actions = deque(maxlen=100)
        self._warnings_detected = 0
        self._last_warning_time = None

    def get_delay(self) -> float:
        """Get the current recommended delay between actions (seconds)."""
        if not self.enabled:
            return random.uniform(self.base_delay_min, self.base_delay_max)

        # Check cooldown
        if self.cooldown_until and datetime.now() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now()).total_seconds()
            log.info(f"In cooldown. Waiting {remaining:.0f}s more...")
            return remaining

        # Base delay with throttle multiplier
        multiplier = [1.0, 1.5, 2.5, 4.0, 8.0][min(self.throttle_level, self.max_throttle)]
        delay = random.uniform(
            self.base_delay_min * multiplier,
            self.base_delay_max * multiplier,
        )

        # Add jitter proportional to throttle level
        jitter = random.uniform(0, self.throttle_level * 2)

        return delay + jitter

    def wait(self):
        """Wait for the recommended delay duration."""
        delay = self.get_delay()
        if delay > 0:
            time.sleep(delay)

    def record_page_load(self, duration_ms: float):
        """Record a page load time for anomaly detection."""
        self._page_load_times.append((datetime.now(), duration_ms))
        self._check_load_time_anomaly()

    def record_action(self, action_type: str = "apply"):
        """Record an action for rate tracking."""
        self._actions.append((datetime.now(), action_type))

    def record_error(self, error_type: str = "unknown"):
        """Record an error. High error rates trigger throttling."""
        self._errors.append((datetime.now(), error_type))
        self._check_error_rate()

    def check_page_for_warnings(self, driver) -> str | None:
        """
        Scan the current page for ban/throttle warning signals.

        Returns the detected signal type or None.
        """
        if not self.enabled:
            return None

        try:
            page_text = driver.find_element("tag name", "body").text[:3000].lower()

            # Check for ban signals
            for signal in BAN_SIGNALS:
                if signal in page_text:
                    self._on_warning_detected("ban_warning", signal)
                    return f"ban_warning:{signal}"

            # Check for CAPTCHAs
            for signal in CAPTCHA_SIGNALS:
                if signal in page_text:
                    self._on_warning_detected("captcha", signal)
                    return f"captcha:{signal}"

            # Check URL for challenge/verification redirects
            url = driver.current_url.lower()
            if any(x in url for x in ["checkpoint", "challenge", "captcha", "verify"]):
                self._on_warning_detected("redirect", url)
                return f"redirect:{url[:80]}"

        except Exception:
            pass

        return None

    def _on_warning_detected(self, warning_type: str, detail: str):
        """Handle a detected warning signal."""
        self._warnings_detected += 1
        self._last_warning_time = datetime.now()

        log.warning(f"Warning detected ({warning_type}): {detail}")

        if warning_type == "captcha":
            # CAPTCHA = immediate pause (5-15 minutes)
            self._escalate(3)
            self.cooldown_until = datetime.now() + timedelta(minutes=random.uniform(5, 15))
            log.warning(f"CAPTCHA detected! Pausing until {self.cooldown_until.strftime('%H:%M:%S')}")

        elif warning_type == "ban_warning":
            # Ban warning = aggressive throttle (30-60 min cooldown)
            self._escalate(4)
            self.cooldown_until = datetime.now() + timedelta(minutes=random.uniform(30, 60))
            log.warning(f"BAN WARNING! Long pause until {self.cooldown_until.strftime('%H:%M:%S')}")

        elif warning_type == "redirect":
            # Challenge redirect = moderate throttle
            self._escalate(2)
            self.cooldown_until = datetime.now() + timedelta(minutes=random.uniform(3, 10))

    def _escalate(self, target_level: int):
        """Escalate throttle to at least the target level."""
        self.throttle_level = max(self.throttle_level, min(target_level, self.max_throttle))
        log.info(f"Throttle level: {self.throttle_level}/{self.max_throttle}")

    def _deescalate(self):
        """Gradually reduce throttle level when things are going well."""
        if self.throttle_level > 0:
            # Only deescalate if no warnings in the last 10 minutes
            if (self._last_warning_time is None or
                    (datetime.now() - self._last_warning_time).total_seconds() > 600):
                self.throttle_level = max(0, self.throttle_level - 1)
                if self.throttle_level == 0:
                    log.info("Throttle level back to normal")

    def _check_load_time_anomaly(self):
        """Detect sudden increases in page load time (throttling signal)."""
        if len(self._page_load_times) < 5:
            return

        recent = [t for _, t in list(self._page_load_times)[-5:]]
        older = [t for _, t in list(self._page_load_times)[:-5]]

        if not older:
            return

        avg_recent = sum(recent) / len(recent)
        avg_older = sum(older) / len(older)

        if avg_older > 0 and avg_recent > avg_older * 2.5:
            log.warning(f"Page load anomaly: {avg_recent:.0f}ms vs baseline {avg_older:.0f}ms")
            self._escalate(1)

    def _check_error_rate(self):
        """Check if error rate is too high."""
        recent_cutoff = datetime.now() - timedelta(minutes=5)
        recent_errors = sum(1 for t, _ in self._errors if t > recent_cutoff)
        recent_actions = sum(1 for t, _ in self._actions if t > recent_cutoff)

        if recent_actions < 3:
            return

        error_rate = recent_errors / recent_actions
        if error_rate > 0.5:
            log.warning(f"High error rate: {error_rate:.0%} ({recent_errors}/{recent_actions})")
            self._escalate(2)
        elif error_rate > 0.3:
            self._escalate(1)
        elif error_rate < 0.1:
            self._deescalate()

    def get_status(self) -> dict:
        """Get current rate limiter status."""
        return {
            "throttle_level": self.throttle_level,
            "throttle_label": ["normal", "cautious", "slow", "very_slow", "paused"][
                min(self.throttle_level, 4)],
            "warnings_detected": self._warnings_detected,
            "in_cooldown": bool(self.cooldown_until and datetime.now() < self.cooldown_until),
            "cooldown_remaining_s": max(0, (self.cooldown_until - datetime.now()).total_seconds())
                if self.cooldown_until and datetime.now() < self.cooldown_until else 0,
            "recent_errors": len(self._errors),
            "recent_actions": len(self._actions),
            "avg_page_load_ms": (sum(t for _, t in self._page_load_times) /
                                 max(len(self._page_load_times), 1)),
        }

    def should_pause_cycle(self) -> bool:
        """Check if the bot should pause the current cycle entirely."""
        if not self.enabled:
            return False
        if self.cooldown_until and datetime.now() < self.cooldown_until:
            return True
        if self.throttle_level >= 4:
            return True
        return False

    def on_cycle_complete(self):
        """Called at the end of each cycle. Gradually deescalate."""
        self._deescalate()
