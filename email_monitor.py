"""
Email Response Monitor.

Monitors your email inbox (IMAP or Gmail API) for responses from companies.
Auto-classifies: interview invite, rejection, assessment link, positive, ghosted.
Feeds into success_tracker for real outcome data instead of manual entry.
"""

import email
import imaplib
import logging
import re
from datetime import datetime, timedelta
from email.header import decode_header
from typing import Optional

log = logging.getLogger("lla.email_monitor")

# Classification patterns
RESPONSE_PATTERNS = {
    "interview": [
        r"interview", r"phone screen", r"schedule a call", r"meet with",
        r"next step", r"speak with you", r"chat with you",
        r"calendar invite", r"availab(le|ility)", r"time slot",
        r"video call", r"zoom link", r"teams meeting",
    ],
    "assessment": [
        r"assessment", r"coding challenge", r"technical test",
        r"take[- ]home", r"hackerrank", r"codility", r"leetcode",
        r"case study", r"work sample", r"complete the following",
    ],
    "rejection": [
        r"unfortunately", r"not (be )?moving forward", r"other candidates",
        r"decided not to", r"will not be", r"not selected",
        r"position has been filled", r"gone with another",
        r"not a fit", r"regret to inform", r"thank you for your interest",
    ],
    "positive": [
        r"congratulations", r"pleased to", r"offer", r"welcome aboard",
        r"excited to", r"delighted to", r"look forward to having you",
    ],
}

# Sender patterns for ATS/recruitment emails
RECRUITER_SENDER_PATTERNS = [
    r"@greenhouse\.io", r"@lever\.co", r"@workday\.com",
    r"@ashbyhq\.com", r"@smartrecruiters\.com", r"@icims\.com",
    r"talent", r"recruit", r"hiring", r"careers", r"hr@",
    r"people@", r"jobs@", r"noreply.*career",
]


class EmailMonitor:
    """Monitor email for application responses via IMAP."""

    def __init__(self, cfg: dict, state):
        self.cfg = cfg
        self.state = state
        em_cfg = cfg.get("email_monitor", {})
        self.enabled = em_cfg.get("enabled", False)
        self.imap_server = em_cfg.get("imap_server", "")
        self.imap_port = em_cfg.get("imap_port", 993)
        self.email_address = em_cfg.get("email", "") or cfg.get("personal", {}).get("email", "")
        self.email_password = em_cfg.get("password", "")
        self.folder = em_cfg.get("folder", "INBOX")
        self.check_last_days = em_cfg.get("check_last_days", 7)
        self.use_gmail_api = em_cfg.get("use_gmail_api", False)

        # Auto-detect IMAP server from email domain
        if not self.imap_server and self.email_address:
            domain = self.email_address.split("@")[-1].lower()
            imap_map = {
                "gmail.com": "imap.gmail.com",
                "outlook.com": "outlook.office365.com",
                "hotmail.com": "outlook.office365.com",
                "yahoo.com": "imap.mail.yahoo.com",
                "icloud.com": "imap.mail.me.com",
            }
            self.imap_server = imap_map.get(domain, f"imap.{domain}")

    def check_inbox(self) -> list[dict]:
        """
        Check inbox for application-related emails.

        Returns list of classified responses:
        [{company, sender, subject, response_type, received_at, body_snippet}]
        """
        if not self.enabled:
            return []

        if not self.email_address or not self.email_password:
            log.warning("Email monitoring enabled but no credentials configured")
            return []

        try:
            return self._check_via_imap()
        except Exception as e:
            log.warning(f"Email check failed: {e}")
            return []

    def _check_via_imap(self) -> list[dict]:
        """Check email via IMAP connection."""
        responses = []

        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.email_address, self.email_password)
            mail.select(self.folder)

            # Search for recent emails
            since_date = (datetime.now() - timedelta(days=self.check_last_days)).strftime("%d-%b-%Y")
            _, msg_ids = mail.search(None, f'(SINCE "{since_date}")')

            if not msg_ids[0]:
                mail.logout()
                return []

            ids = msg_ids[0].split()
            log.info(f"  Checking {len(ids)} recent emails...")

            for msg_id in ids[-100:]:  # Limit to last 100
                try:
                    _, msg_data = mail.fetch(msg_id, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    sender = self._decode_header(msg.get("From", ""))
                    subject = self._decode_header(msg.get("Subject", ""))
                    date_str = msg.get("Date", "")
                    body = self._get_body(msg)

                    # Filter: only process recruitment-related emails
                    if not self._is_recruitment_email(sender, subject, body):
                        continue

                    # Classify the response
                    response_type = self._classify(subject, body)
                    if not response_type:
                        continue

                    # Extract company name
                    company = self._extract_company(sender, subject, body)

                    # Match to a job application
                    job_id = self._match_to_application(company, subject, body)

                    response = {
                        "company": company,
                        "sender": sender[:200],
                        "subject": subject[:200],
                        "response_type": response_type,
                        "received_at": date_str[:50],
                        "body_snippet": body[:300],
                        "job_id": job_id,
                    }

                    # Check if already processed (avoid duplicates)
                    existing = self.state.conn.execute("""
                        SELECT 1 FROM email_responses
                        WHERE sender=? AND subject=? AND received_at=?
                    """, (response["sender"], response["subject"],
                          response["received_at"])).fetchone()

                    if not existing:
                        self.state.save_email_response(**response)
                        responses.append(response)

                        # Also feed into success tracker
                        if job_id and response_type in ("interview", "positive", "rejection"):
                            self.state.save_response(
                                job_id=job_id,
                                company=company,
                                response_type="callback" if response_type == "positive" else response_type,
                            )

                except Exception as e:
                    log.debug(f"  Email parse error: {e}")
                    continue

            mail.logout()

        except imaplib.IMAP4.error as e:
            log.warning(f"IMAP error: {e}")
        except Exception as e:
            log.warning(f"Email connection error: {e}")

        if responses:
            log.info(f"  Found {len(responses)} new application responses")
            for r in responses:
                log.info(f"    {r['response_type'].upper()}: {r['company']} — {r['subject'][:60]}")

        return responses

    def _decode_header(self, header: str) -> str:
        """Decode email header (handles encoded formats)."""
        if not header:
            return ""
        try:
            parts = decode_header(header)
            decoded = []
            for data, charset in parts:
                if isinstance(data, bytes):
                    decoded.append(data.decode(charset or "utf-8", errors="replace"))
                else:
                    decoded.append(str(data))
            return " ".join(decoded)
        except Exception:
            return str(header)

    def _get_body(self, msg) -> str:
        """Extract plain text body from email message."""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    try:
                        payload = part.get_payload(decode=True)
                        body = payload.decode(part.get_content_charset() or "utf-8",
                                            errors="replace")
                        break
                    except Exception:
                        continue
        else:
            try:
                payload = msg.get_payload(decode=True)
                body = payload.decode(msg.get_content_charset() or "utf-8",
                                     errors="replace")
            except Exception:
                pass
        return body[:5000]  # Limit body size

    def _is_recruitment_email(self, sender: str, subject: str, body: str) -> bool:
        """Check if email is likely recruitment-related."""
        combined = f"{sender} {subject}".lower()

        # Check sender patterns
        for pattern in RECRUITER_SENDER_PATTERNS:
            if re.search(pattern, combined, re.IGNORECASE):
                return True

        # Check subject for job-related keywords
        job_keywords = ["application", "position", "role", "opportunity",
                       "candidate", "interview", "your resume", "your application"]
        for kw in job_keywords:
            if kw in combined:
                return True

        # Check if sender matches any company we applied to
        companies = self.state.conn.execute(
            "SELECT DISTINCT company FROM applied_jobs"
        ).fetchall()
        for row in companies:
            if row["company"].lower() in combined:
                return True

        return False

    def _classify(self, subject: str, body: str) -> str:
        """Classify email response type."""
        text = f"{subject} {body[:2000]}".lower()

        # Check each pattern category (order matters — positive > interview > rejection)
        scores = {}
        for resp_type, patterns in RESPONSE_PATTERNS.items():
            score = sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))
            if score > 0:
                scores[resp_type] = score

        if not scores:
            return ""

        # Return highest-scoring type
        return max(scores.keys(), key=lambda k: scores[k])

    def _extract_company(self, sender: str, subject: str, body: str) -> str:
        """Extract company name from email."""
        # Try matching sender domain to known companies
        sender_lower = sender.lower()

        companies = self.state.conn.execute(
            "SELECT DISTINCT company FROM applied_jobs"
        ).fetchall()
        for row in companies:
            company = row["company"]
            if company.lower() in sender_lower or company.lower() in subject.lower():
                return company

        # Extract from sender name (e.g. "Jane from Acme Corp <jane@acme.com>")
        m = re.search(r'(?:from|at)\s+([A-Z][A-Za-z\s&]+?)(?:\s*<|\s*$)', sender)
        if m:
            return m.group(1).strip()

        # Extract from domain
        m = re.search(r'@([a-z0-9-]+)\.[a-z]+', sender_lower)
        if m:
            domain = m.group(1)
            # Skip generic email providers
            if domain not in ("gmail", "yahoo", "outlook", "hotmail", "icloud"):
                return domain.replace("-", " ").title()

        return ""

    def _match_to_application(self, company: str, subject: str, body: str) -> str:
        """Try to match email to a specific job application."""
        if not company:
            return ""

        # Search applied jobs by company
        rows = self.state.conn.execute("""
            SELECT job_id, title FROM applied_jobs
            WHERE company LIKE ? ORDER BY applied_at DESC LIMIT 5
        """, (f"%{company}%",)).fetchall()

        if len(rows) == 1:
            return rows[0]["job_id"]

        # Try matching job title in subject/body
        text = f"{subject} {body[:1000]}".lower()
        for row in rows:
            title_words = row["title"].lower().split()
            matches = sum(1 for w in title_words if w in text and len(w) > 3)
            if matches >= 2:
                return row["job_id"]

        # Return most recent application at the company
        return rows[0]["job_id"] if rows else ""
