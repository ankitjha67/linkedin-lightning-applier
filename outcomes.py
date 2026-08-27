"""Record what actually happened to an application — and close the loop.

    lla outcome                          what is still waiting on an answer
    lla outcome "Monzo" --type interview  record a real outcome
    lla outcome --summary                 the funnel, end to end
    lla outcome --ghost-sweep             mark long-silent applications ghosted

Eight modules in this project read `response_tracking` — the ghost predictor,
the SLA tracker, application forensics, the smart scheduler, apply timing, the
follow-up engine, the success tracker and A/B resume testing. All of them are
learning from outcomes. Until now the only things writing that table were the
email monitor and an automatic ghost sweep, so unless a reply arrived by email
and was correctly classified, nothing you learned ever reached them.

That is the loop this closes. A recruiter who phones you, an interview booked
over LinkedIn, a rejection delivered in person — none of it is an email, and
all of it is exactly the signal the models need.

Every outcome is stored against the application it belongs to, with the date
it happened, so `days_to_response` stays truthful rather than being measured
from whenever you got around to typing it in.
"""

import logging
import re
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

DATE_FMT = "%Y-%m-%d %H:%M:%S"


class OutcomeType:
    def __init__(self, key, label, positive, terminal, help_text):
        self.key = key
        self.label = label
        self.positive = positive      # counts as "they engaged"
        self.terminal = terminal      # the application is over
        self.help = help_text


# The canonical vocabulary. `positive` is what every "did this application
# work?" query means, and it is the single place that answer is defined —
# these strings are spread across ten hand-written SQL queries elsewhere.
OUTCOME_TYPES = [
    OutcomeType("callback", "Callback", True, False,
                "a recruiter got in touch — call, message, or email"),
    OutcomeType("assessment", "Assessment", True, False,
                "a take-home, coding challenge or work sample was sent"),
    OutcomeType("interview", "Interview", True, False,
                "an interview was scheduled or held"),
    OutcomeType("offer", "Offer", True, True,
                "they offered you the job"),
    OutcomeType("rejection", "Rejection", False, True,
                "they told you no"),
    OutcomeType("withdrawn", "Withdrawn", False, True,
                "you pulled out — took another role, or lost interest"),
    OutcomeType("ghosted", "Ghosted", False, True,
                "no answer at all, long enough that there will not be one"),
]

BY_KEY = {t.key: t for t in OUTCOME_TYPES}
POSITIVE_TYPES = tuple(t.key for t in OUTCOME_TYPES if t.positive)
TERMINAL_TYPES = tuple(t.key for t in OUTCOME_TYPES if t.terminal)
ALL_TYPES = tuple(t.key for t in OUTCOME_TYPES)

# Words people actually type, mapped to the canonical key.
ALIASES = {
    "call": "callback", "callback": "callback", "recruiter": "callback",
    "screen": "callback", "phone": "callback", "response": "callback",
    "positive": "callback",
    "test": "assessment", "challenge": "assessment", "takehome": "assessment",
    "take-home": "assessment", "hackerrank": "assessment", "codility": "assessment",
    "assessment": "assessment", "task": "assessment",
    "interview": "interview", "onsite": "interview", "final": "interview",
    "offer": "offer", "hired": "offer", "accepted": "offer",
    "reject": "rejection", "rejected": "rejection", "rejection": "rejection",
    "no": "rejection", "declined": "rejection",
    "withdraw": "withdrawn", "withdrawn": "withdrawn", "quit": "withdrawn",
    "ghost": "ghosted", "ghosted": "ghosted", "silence": "ghosted",
}


def normalise_type(value: str) -> str:
    """'Rejected' → 'rejection'. Returns '' if it is not a known outcome."""
    key = (value or "").strip().lower().replace(" ", "")
    return ALIASES.get(key, key if key in BY_KEY else "")


def parse_when(value: str):
    """Accept a date, a datetime, or 'today'/'yesterday'/'3d'. None if unusable.

    An empty value is None, not "now": callers fall back to another date with
    `parse_when(a) or parse_when(b)`, and answering "now" for a missing value
    would silently win that fallback and report every application as fresh.
    """
    text = (value or "").strip().lower()
    if not text:
        return None
    if text == "today":
        return datetime.now()
    if text == "yesterday":
        return datetime.now() - timedelta(days=1)
    m = re.fullmatch(r"(\d+)\s*d(ays?)?(\s*ago)?", text)
    if m:
        return datetime.now() - timedelta(days=int(m.group(1)))
    for fmt in (DATE_FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Finding the application an outcome belongs to
# ---------------------------------------------------------------------------

def find_applications(state, query: str, limit: int = 25) -> list:
    """Applications matching a job id, a URL, or text in the company/title.

    Ordered most-recently-applied first, because that is almost always the one
    being talked about.
    """
    q = (query or "").strip()
    if not q:
        return []
    rows = state.conn.execute(
        "SELECT job_id, title, company, job_url, applied_at FROM applied_jobs "
        "WHERE job_id = ? OR job_url = ?", (q, q)).fetchall()
    if rows:
        return [dict(r) for r in rows]

    like = f"%{q}%"
    rows = state.conn.execute(
        "SELECT job_id, title, company, job_url, applied_at FROM applied_jobs "
        "WHERE company LIKE ? OR title LIKE ? OR job_url LIKE ? "
        "ORDER BY applied_at DESC LIMIT ?",
        (like, like, like, limit)).fetchall()
    return [dict(r) for r in rows]


def existing_outcomes(state, job_id: str) -> list:
    rows = state.conn.execute(
        "SELECT response_type, response_at, notes FROM response_tracking "
        "WHERE job_id = ? ORDER BY response_at", (job_id,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def record_outcome(state, job_id: str, outcome: str, notes: str = "",
                   when=None, allow_duplicate: bool = False):
    """Store one outcome. Returns (ok, message).

    An application legitimately produces several outcomes over time — callback,
    then assessment, then interview, then offer — so this appends rather than
    replaces. Recording the *same* outcome twice is almost always a mistake and
    is refused unless explicitly allowed.
    """
    key = normalise_type(outcome)
    if not key:
        return False, (f"'{outcome}' is not an outcome. Use one of: "
                       + ", ".join(ALL_TYPES))

    row = state.conn.execute(
        "SELECT job_id, title, company, applied_at, match_score "
        "FROM applied_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return False, f"no application found with job_id {job_id!r}"
    row = dict(row)

    if not allow_duplicate:
        already = [o for o in existing_outcomes(state, job_id)
                   if o["response_type"] == key]
        if already:
            return False, (f"'{key}' is already recorded for {row['company']} "
                           f"({already[0]['response_at']}). Use --force to add "
                           "it again.")

    happened = when or datetime.now()
    applied_at = row.get("applied_at") or ""
    days = 0.0
    applied_dt = parse_when(applied_at)
    if applied_dt:
        days = max(0.0, (happened - applied_dt).total_seconds() / 86400)

    # save_response() timestamps with datetime('now'), which is wrong for an
    # outcome that happened last week, so the row is written directly and the
    # real date is preserved.
    state.conn.execute("""
        INSERT INTO response_tracking
        (job_id, title, company, applied_at, response_type, response_at,
         match_score, resume_version, recruiter_messaged, days_to_response, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (job_id, row.get("title", ""), row.get("company", ""), applied_at,
          key, happened.strftime(DATE_FMT), row.get("match_score", 0) or 0,
          "", 0, round(days, 2), notes or ""))
    state.conn.commit()

    t = BY_KEY[key]
    when_txt = f" after {days:.0f} day{'s' if round(days) != 1 else ''}" if days else ""
    return True, (f"{t.label} recorded for {row.get('title', '?')} @ "
                  f"{row.get('company', '?')}{when_txt}")


# ---------------------------------------------------------------------------
# What is still open
# ---------------------------------------------------------------------------

def pending_applications(state, quiet_days: int = 0, limit: int = 200) -> list:
    """Applications with no terminal outcome yet, oldest silence first.

    `quiet_days` filters to those with no outcome of any kind for that long —
    the ones actually worth chasing.
    """
    placeholders = ",".join("?" * len(TERMINAL_TYPES))
    rows = state.conn.execute(f"""
        SELECT a.job_id, a.title, a.company, a.applied_at, a.job_url,
               (SELECT COUNT(*) FROM response_tracking r WHERE r.job_id = a.job_id)
                   AS responses,
               (SELECT MAX(r.response_at) FROM response_tracking r
                    WHERE r.job_id = a.job_id) AS last_response
        FROM applied_jobs a
        WHERE a.job_id NOT IN (
            SELECT job_id FROM response_tracking
            WHERE response_type IN ({placeholders})
        )
        ORDER BY a.applied_at ASC
        LIMIT ?
    """, (*TERMINAL_TYPES, limit)).fetchall()

    now = datetime.now()
    out = []
    for r in rows:
        r = dict(r)
        last = parse_when(r.get("last_response") or "") or parse_when(r.get("applied_at") or "")
        r["days_quiet"] = round((now - last).total_seconds() / 86400, 1) if last else None
        if quiet_days and (r["days_quiet"] is None or r["days_quiet"] < quiet_days):
            continue
        out.append(r)
    out.sort(key=lambda r: r["days_quiet"] or 0, reverse=True)
    return out


def sweep_ghosted(state, after_days: int = 45, dry_run: bool = True) -> list:
    """Mark applications silent for `after_days` as ghosted. Returns the rows.

    Ghosting is data too: the ghost predictor and the SLA tracker both learn
    from how long a company stays silent, and an application left permanently
    "pending" teaches them nothing.
    """
    stale = [r for r in pending_applications(state, quiet_days=after_days)
             if not r["responses"]]
    if dry_run:
        return stale
    done = []
    for r in stale:
        ok, _ = record_outcome(state, r["job_id"], "ghosted",
                               notes=f"no response in {after_days}+ days "
                                     "(automatic sweep)")
        if ok:
            done.append(r)
    return done


# ---------------------------------------------------------------------------
# The funnel
# ---------------------------------------------------------------------------

def outcome_summary(state) -> dict:
    """Applications → engaged → interviewed → offered, with response times."""
    applied = state.conn.execute(
        "SELECT COUNT(*) AS c FROM applied_jobs").fetchone()["c"]
    counts = {t.key: 0 for t in OUTCOME_TYPES}
    for r in state.conn.execute(
            "SELECT response_type, COUNT(DISTINCT job_id) AS c "
            "FROM response_tracking GROUP BY response_type").fetchall():
        if r["response_type"] in counts:
            counts[r["response_type"]] = r["c"]

    placeholders = ",".join("?" * len(POSITIVE_TYPES))
    engaged = state.conn.execute(
        f"SELECT COUNT(DISTINCT job_id) AS c FROM response_tracking "
        f"WHERE response_type IN ({placeholders})", POSITIVE_TYPES).fetchone()["c"]
    avg = state.conn.execute(
        f"SELECT AVG(days_to_response) AS d FROM response_tracking "
        f"WHERE days_to_response > 0 AND response_type IN ({placeholders})",
        POSITIVE_TYPES).fetchone()["d"]

    pending = len(pending_applications(state))
    return {
        "applied": applied,
        "engaged": engaged,
        "engagement_rate": round(engaged / applied * 100, 1) if applied else 0.0,
        "interviews": counts["interview"],
        "offers": counts["offer"],
        "rejections": counts["rejection"],
        "ghosted": counts["ghosted"],
        "pending": pending,
        "avg_days_to_first_response": round(avg, 1) if avg else 0.0,
        "by_type": counts,
    }


def format_summary(s: dict) -> str:
    def pct(n):
        return f"{n / s['applied'] * 100:4.1f}%" if s["applied"] else "   — "

    lines = [
        "",
        f"  Applied         {s['applied']:>5}",
        f"  Engaged         {s['engaged']:>5}   {pct(s['engaged'])}  "
        "(callback, assessment, interview or offer)",
        f"  Interviews      {s['interviews']:>5}   {pct(s['interviews'])}",
        f"  Offers          {s['offers']:>5}   {pct(s['offers'])}",
        f"  Rejections      {s['rejections']:>5}   {pct(s['rejections'])}",
        f"  Ghosted         {s['ghosted']:>5}   {pct(s['ghosted'])}",
        f"  Still open      {s['pending']:>5}   {pct(s['pending'])}",
    ]
    if s["avg_days_to_first_response"]:
        lines.append(f"\n  Average {s['avg_days_to_first_response']} days to a first response.")
    if not s["applied"]:
        lines.append("\n  No applications recorded yet.")
    elif not s["engaged"] and not s["rejections"]:
        lines.append("\n  No outcomes recorded yet. Every module that predicts")
        lines.append("  ghosting, response time or success is learning from this")
        lines.append("  table — recording outcomes is what makes them work.")
    return "\n".join(lines)


def format_pending(rows: list, limit: int = 30) -> str:
    if not rows:
        return "\n  Nothing outstanding — every application has a final outcome."
    out = ["", f"  {len(rows)} application(s) still open, longest silence first:", ""]
    for r in rows[:limit]:
        quiet = f"{r['days_quiet']:.0f}d" if r["days_quiet"] is not None else "  ?"
        seen = "" if not r["responses"] else f"  ({r['responses']} update(s))"
        out.append(f"    {quiet:>5} quiet   {(r['company'] or '?')[:28]:<28} "
                   f"{(r['title'] or '?')[:36]}{seen}")
        out.append(f"                  {r['job_id']}")
    if len(rows) > limit:
        out.append(f"\n    … and {len(rows) - limit} more.")
    out.append("\n  Record one with:  lla outcome \"<company>\" --type interview")
    return "\n".join(out)
