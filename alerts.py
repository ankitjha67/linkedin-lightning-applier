"""
Notification Alerts — Telegram, Discord, Slack.

Sends instant notifications on every application, error alerts, and daily summaries.
"""

import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("lla.alerts")


class AlertManager:
    """Send notifications via Telegram, Discord, and Slack."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        alert_cfg = cfg.get("alerts", {})
        self.enabled = alert_cfg.get("enabled", False)
        self.on_apply = alert_cfg.get("on_apply", True)
        self.on_error = alert_cfg.get("on_error", True)
        self.daily_summary = alert_cfg.get("daily_summary", True)
        self.daily_summary_time = alert_cfg.get("daily_summary_time", "22:00")

        # Telegram
        tg = alert_cfg.get("telegram", {})
        self.tg_enabled = tg.get("enabled", False)
        self.tg_token = tg.get("bot_token", "")
        self.tg_chat_id = tg.get("chat_id", "")

        # Discord
        dc = alert_cfg.get("discord", {})
        self.dc_enabled = dc.get("enabled", False)
        self.dc_webhook = dc.get("webhook_url", "")

        # Slack
        sl = alert_cfg.get("slack", {})
        self.sl_enabled = sl.get("enabled", False)
        self.sl_webhook = sl.get("webhook_url", "")

        self._last_summary_date = ""

    def send_applied(self, job_title: str, company: str, salary: str = "",
                     visa: str = "", recruiter: str = "", match_score: int = 0,
                     job_url: str = ""):
        """Send notification when a job application is submitted."""
        if not self.enabled or not self.on_apply:
            return

        parts = [f"Applied: {job_title} @ {company}"]
        if match_score:
            parts.append(f"Match: {match_score}%")
        if salary:
            parts.append(f"Salary: {salary}")
        if visa and visa != "unknown":
            parts.append(f"Visa: {visa}")
        if recruiter:
            parts.append(f"Recruiter: {recruiter}")
        if job_url:
            parts.append(f"Link: {job_url}")

        text = "\n".join(parts)
        self._send_all(text)

    def send_error(self, error_msg: str):
        """Send alert when an error occurs."""
        if not self.enabled or not self.on_error:
            return
        text = f"Error: {error_msg[:500]}"
        self._send_all(text)

    def send_daily_summary(self, stats: dict):
        """Send daily summary at configured time."""
        if not self.enabled or not self.daily_summary:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_summary_date == today:
            return

        now_time = datetime.now().strftime("%H:%M")
        if now_time < self.daily_summary_time:
            return

        self._last_summary_date = today

        text = (
            f"Daily Summary ({today})\n"
            f"{'=' * 30}\n"
            f"Applied: {stats.get('applied', 0)}\n"
            f"Skipped: {stats.get('skipped', 0)}\n"
            f"Failed: {stats.get('failed', 0)}\n"
            f"Cycles: {stats.get('cycles', 0)}\n"
        )

        if stats.get("avg_match_score"):
            text += f"Avg Match Score: {stats['avg_match_score']}%\n"
        if stats.get("top_companies"):
            text += f"Top Companies: {', '.join(stats['top_companies'][:5])}\n"

        self._send_all(text)

    def check_daily_summary(self, state):
        """Check if it's time to send daily summary."""
        if not self.enabled or not self.daily_summary:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_summary_date == today:
            return

        now_time = datetime.now().strftime("%H:%M")
        if now_time >= self.daily_summary_time:
            stats_row = state.conn.execute(
                "SELECT * FROM daily_stats WHERE date=?", (today,)
            ).fetchone()

            if stats_row:
                self.send_daily_summary(dict(stats_row))

    def _send_all(self, text: str):
        """Send to all enabled channels."""
        if self.tg_enabled:
            self._send_telegram(text)
        if self.dc_enabled:
            self._send_discord(text)
        if self.sl_enabled:
            self._send_slack(text)

    def _send_telegram(self, text: str):
        """Send message via Telegram Bot API."""
        if not self.tg_token or not self.tg_chat_id:
            return
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": self.tg_chat_id,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=10)
            if resp.status_code != 200:
                log.warning(f"Telegram send failed: {resp.status_code}")
        except ImportError:
            log.warning("requests package needed for Telegram alerts")
        except Exception as e:
            log.warning(f"Telegram error: {e}")

    def _send_discord(self, text: str):
        """Send message via Discord webhook."""
        if not self.dc_webhook:
            return
        try:
            import requests
            resp = requests.post(self.dc_webhook, json={
                "content": text,
            }, timeout=10)
            if resp.status_code not in (200, 204):
                log.warning(f"Discord send failed: {resp.status_code}")
        except ImportError:
            log.warning("requests package needed for Discord alerts")
        except Exception as e:
            log.warning(f"Discord error: {e}")

    def _send_slack(self, text: str):
        """Send message via Slack webhook."""
        if not self.sl_webhook:
            return
        try:
            import requests
            resp = requests.post(self.sl_webhook, json={
                "text": text,
            }, timeout=10)
            if resp.status_code != 200:
                log.warning(f"Slack send failed: {resp.status_code}")
        except ImportError:
            log.warning("requests package needed for Slack alerts")
        except Exception as e:
            log.warning(f"Slack error: {e}")
