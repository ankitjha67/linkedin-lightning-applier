"""Turn recruiter email into recorded outcomes — with approval, and citations.

    lla sync-email                 # what your inbox suggests happened
    lla sync-email --apply         # record the confident ones
    lla sync-email --apply --all   # record the uncertain ones too

`lla outcome` closes the learning loop by hand. Most of what it wants to know
already arrived by email months ago. This reads those messages and proposes
outcomes — it never writes one on its own.

Two rules make that safe to trust:

  **Nothing is recorded without approval.** Reading produces *proposals*.
  Writing is a separate, explicit step, and by default only proposals the
  matcher is confident about are eligible.

  **Every recorded outcome cites the email it came from** — sender, subject,
  and date, stored in the outcome's notes. When a model later tells you a
  company ghosts people, you can walk back to the message that said so. An
  inference with no source is a guess, and guesses do not belong in the table
  eight other modules learn from.

Emails reach this module as plain dicts, so the source does not matter:

    {"id": ..., "from": ..., "subject": ..., "body": ..., "date": ...}

`fetch_via_imap()` uses the existing email monitor. An MCP client that already
has Gmail access (Claude Desktop, Claude Code) can hand messages straight to
`propose_outcomes()` instead — no second set of credentials, and this project
never holds a Gmail token of its own.
"""

import logging
import re
from datetime import datetime

log = logging.getLogger(__name__)

# How an email's classification maps onto the canonical outcome vocabulary.
# email_monitor's classifier speaks in "positive"/"assessment"; outcomes.py
# speaks in callback/assessment/interview/offer/rejection.
CLASS_TO_OUTCOME = {
    "interview": "interview",
    "assessment": "assessment",
    "rejection": "rejection",
    "positive": "callback",
}

# An offer is a much stronger claim than "positive", so it needs its own words.
OFFER_PATTERNS = [
    r"\boffer of employment\b", r"\bformal offer\b", r"\bjob offer\b",
    r"\bwe would like to offer\b", r"\bpleased to offer\b",
    r"\boffer letter\b", r"\bcontract of employment\b",
]

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


class Proposal:
    """One outcome an email suggests — and everything needed to check it."""

    def __init__(self, email_id, outcome, job_id, company, title,
                 sender, subject, received, confidence, why):
        self.email_id = email_id
        self.outcome = outcome
        self.job_id = job_id
        self.company = company
        self.title = title
        self.sender = sender
        self.subject = subject
        self.received = received
        self.confidence = confidence
        self.why = why
        self.applied = False
        self.error = ""

    @property
    def citation(self) -> str:
        """The provenance line stored with the outcome."""
        when = self.received or "date unknown"
        return (f"from email: {self.sender or 'unknown sender'} — "
                f"\"{(self.subject or '(no subject)')[:120]}\" ({when})"
                + (f" [id {self.email_id}]" if self.email_id else ""))

    def as_dict(self):
        return {"email_id": self.email_id, "outcome": self.outcome,
                "job_id": self.job_id, "company": self.company,
                "title": self.title, "sender": self.sender,
                "subject": self.subject, "received": self.received,
                "confidence": self.confidence, "why": self.why,
                "citation": self.citation, "applied": self.applied,
                "error": self.error}

    def __repr__(self):
        return (f"<Proposal {self.outcome} for {self.company or '?'} "
                f"({self.confidence})>")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_email(subject: str, body: str) -> str:
    """The canonical outcome an email describes, or '' if it describes none."""
    text = f"{subject or ''} {(body or '')[:2000]}"
    low = text.lower()

    # "Offer" beats everything: it is specific, and getting it wrong is costly.
    if any(re.search(p, low) for p in OFFER_PATTERNS):
        return "offer"

    try:
        from email_monitor import RESPONSE_PATTERNS
    except Exception:                                          # pragma: no cover
        return ""

    scores = {}
    for kind, patterns in RESPONSE_PATTERNS.items():
        hits = sum(1 for p in patterns if re.search(p, low, re.IGNORECASE))
        if hits:
            scores[kind] = hits
    if not scores:
        return ""

    # A rejection phrased politely still contains interview-ish words ("we
    # enjoyed speaking with you, but"). Rejection language is unambiguous, so
    # when it is present at all it decides.
    if "rejection" in scores:
        return "rejection"
    best = max(scores, key=lambda k: scores[k])
    return CLASS_TO_OUTCOME.get(best, "")


# ---------------------------------------------------------------------------
# Matching an email to the application it is about
# ---------------------------------------------------------------------------

_DOMAIN_NOISE = {
    "gmail", "googlemail", "outlook", "hotmail", "yahoo", "icloud", "mail",
    "greenhouse", "lever", "workday", "myworkday", "ashbyhq", "smartrecruiters",
    "icims", "taleo", "successfactors", "bamboohr", "jobvite", "teamtailor",
    "notifications", "noreply", "no-reply", "email", "mailer", "info", "hire",
    "recruiting", "recruitment", "talent", "careers", "jobs", "applytojob",
}


def sender_tokens(sender: str) -> list:
    """Company-ish words from an address: 'careers@monzo.com' → ['monzo']."""
    m = re.search(r"@([\w.-]+)", sender or "")
    if not m:
        return []
    parts = [p.lower() for p in m.group(1).split(".")]
    # Drop the TLD and known ATS/mail-provider noise.
    return [p for p in parts[:-1] if p and p not in _DOMAIN_NOISE and len(p) > 2]


def match_application(state, sender: str, subject: str, body: str):
    """(job_id, company, title, confidence, why) for the application this is about.

    Confidence is the whole point: a proposal the matcher is unsure of must not
    be applied by default, because a misattributed outcome is worse than a
    missing one — it teaches every downstream model something false.
    """
    text = f"{subject or ''} {(body or '')[:3000]}"
    rows = [dict(r) for r in state.conn.execute(
        "SELECT job_id, title, company, applied_at FROM applied_jobs "
        "ORDER BY applied_at DESC").fetchall()]
    if not rows:
        return "", "", "", "low", "no applications on record"

    # 1. The job id itself appears in the message (ATS emails often quote it).
    for r in rows:
        jid = (r.get("job_id") or "").strip()
        if len(jid) >= 6 and jid in text:
            return (r["job_id"], r["company"], r["title"], "high",
                    f"job id {jid} appears in the email")

    # 2. The company name appears, and the sender's domain agrees.
    tokens = sender_tokens(sender)
    named = [r for r in rows
             if r.get("company") and re.search(
                 r"(?<![a-z0-9])" + re.escape(r["company"].lower()) + r"(?![a-z0-9])",
                 text.lower())]
    for r in named:
        company_word = re.sub(r"[^a-z0-9]", "", (r["company"] or "").lower())
        if any(t in company_word or company_word in t for t in tokens):
            return (r["job_id"], r["company"], r["title"], "high",
                    f"company '{r['company']}' named in the email and in the sender")

    # 3. The sender's domain alone identifies the company.
    for r in rows:
        company_word = re.sub(r"[^a-z0-9]", "", (r.get("company") or "").lower())
        if company_word and any(t == company_word for t in tokens):
            matches = [x for x in rows
                       if re.sub(r"[^a-z0-9]", "", (x.get("company") or "").lower())
                       == company_word]
            if len(matches) == 1:
                return (r["job_id"], r["company"], r["title"], "high",
                        f"sender domain matches '{r['company']}'")
            # Several roles at that company: the message usually names the one
            # it means ("for the Risk Manager position"), so ask before giving up.
            titled = [x for x in matches
                      if x.get("title") and x["title"].lower() in text.lower()]
            if len(titled) == 1:
                x = titled[0]
                return (x["job_id"], x["company"], x["title"], "high",
                        f"sender domain matches '{x['company']}' and the email "
                        f"names '{x['title']}'")
            return (matches[0]["job_id"], matches[0]["company"], matches[0]["title"],
                    "low",
                    f"sender domain matches '{r['company']}', but you applied to "
                    f"{len(matches)} roles there and the email does not say "
                    "which — verify before recording")

    # 4. The company is named but nothing corroborates it.
    if len(named) == 1:
        r = named[0]
        return (r["job_id"], r["company"], r["title"], "medium",
                f"company '{r['company']}' named in the email")
    if len(named) > 1:
        r = named[0]
        return (r["job_id"], r["company"], r["title"], "low",
                f"{len(named)} of your applications are named — verify which")

    # 5. The job title is distinctive enough on its own.
    titled = [r for r in rows
              if r.get("title") and len(r["title"]) > 8
              and r["title"].lower() in text.lower()]
    if len(titled) == 1:
        r = titled[0]
        return (r["job_id"], r["company"], r["title"], "low",
                f"only the job title '{r['title']}' matched")

    return "", "", "", "low", "could not tell which application this is about"


# ---------------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------------

def propose_outcomes(state, emails) -> list:
    """Read messages, propose outcomes. Writes nothing."""
    from outcomes import existing_outcomes

    proposals = []
    for e in emails or []:
        subject = e.get("subject", "") or ""
        body = e.get("body", "") or e.get("snippet", "") or ""
        sender = e.get("from", "") or e.get("sender", "") or ""
        outcome = classify_email(subject, body)
        if not outcome:
            continue
        job_id, company, title, confidence, why = match_application(
            state, sender, subject, body)
        if not job_id:
            proposals.append(Proposal(
                e.get("id", ""), outcome, "", "", "", sender, subject,
                e.get("date", "") or e.get("received", ""), "low", why))
            continue
        # Already recorded? Then there is nothing to propose.
        if any(o["response_type"] == outcome for o in existing_outcomes(state, job_id)):
            continue
        proposals.append(Proposal(
            e.get("id", ""), outcome, job_id, company, title, sender, subject,
            e.get("date", "") or e.get("received", ""), confidence, why))
    return proposals


def parse_email_date(value: str):
    """Best-effort date from an email header. None if it cannot be read."""
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        return dt.replace(tzinfo=None) if dt else None
    except Exception:
        pass
    from outcomes import parse_when
    return parse_when(value)


# ---------------------------------------------------------------------------
# Applying — the approval gate
# ---------------------------------------------------------------------------

def apply_proposals(state, proposals, approve=None, min_confidence="high"):
    """Record approved proposals. Returns the ones that were written.

    `approve` is the explicit gate: a set of email ids, or a predicate. Without
    it only proposals at or above `min_confidence` are eligible — and a
    proposal with no matched application is never eligible at all.
    """
    from outcomes import record_outcome

    floor = CONFIDENCE_ORDER.get(min_confidence, 2)
    written = []
    for p in proposals:
        if not p.job_id:
            p.error = "not matched to an application"
            continue
        if approve is None:
            eligible = CONFIDENCE_ORDER.get(p.confidence, 0) >= floor
        elif callable(approve):
            eligible = bool(approve(p))
        else:
            eligible = p.email_id in set(approve)
        if not eligible:
            continue

        when = parse_email_date(p.received)
        ok, msg = record_outcome(state, p.job_id, p.outcome,
                                 notes=p.citation, when=when)
        p.applied = ok
        if ok:
            written.append(p)
        else:
            p.error = msg
    return written


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def fetch_via_imap(cfg, state, days: int = 90) -> list:
    """Messages from the configured IMAP mailbox, as plain dicts.

    An MCP client with Gmail access can skip this entirely and pass messages
    straight to propose_outcomes(), which is why nothing here is required.
    """
    try:
        from email_monitor import EmailMonitor
    except Exception as exc:
        log.debug("email monitor unavailable: %s", exc)
        return []
    monitor = EmailMonitor(cfg, state)
    if not getattr(monitor, "enabled", False):
        return []
    try:
        found = monitor.check_responses(days=days)
    except TypeError:
        found = monitor.check_responses()
    except Exception as exc:
        log.warning("could not read the mailbox: %s", exc)
        return []
    return [{"id": r.get("message_id", "") or r.get("id", ""),
             "from": r.get("sender", ""),
             "subject": r.get("subject", ""),
             "body": r.get("body_snippet", "") or r.get("body", ""),
             "date": r.get("received_at", "")}
            for r in (found or [])]


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

def format_proposals(proposals, applied=None) -> str:
    if not proposals:
        return ("\n  Nothing new. Either no recruiter mail matched an "
                "application,\n  or every outcome it describes is already "
                "recorded.")
    applied_ids = {p.email_id for p in (applied or [])}
    order = sorted(proposals,
                   key=lambda p: -CONFIDENCE_ORDER.get(p.confidence, 0))
    out = ["", f"  {len(proposals)} outcome(s) suggested by your inbox:", ""]
    for p in order:
        mark = "recorded" if p.email_id in applied_ids else p.confidence
        out.append(f"    [{mark:>8}]  {p.outcome.upper():<11} "
                   f"{(p.company or 'UNMATCHED')[:26]:<26} {(p.title or '')[:30]}")
        out.append(f"                {p.subject[:70]}")
        out.append(f"                from {p.sender[:60]}  {p.received}")
        out.append(f"                why: {p.why}")
        if p.error:
            out.append(f"                not recorded: {p.error}")
        out.append("")
    n_high = sum(1 for p in proposals if p.confidence == "high" and p.job_id)
    if applied is None:
        out.append(f"  Nothing has been recorded. {n_high} are confident enough")
        out.append("  to apply with:  lla sync-email --apply")
        out.append("  Add --all to include the uncertain ones as well.")
    else:
        out.append(f"  Recorded {len(applied_ids)} outcome(s), each citing the")
        out.append("  email it came from.")
    return "\n".join(out)


def summarise(proposals, applied=None) -> dict:
    return {
        "proposed": len(proposals),
        "matched": sum(1 for p in proposals if p.job_id),
        "high_confidence": sum(1 for p in proposals
                               if p.job_id and p.confidence == "high"),
        "applied": len(applied or []),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
