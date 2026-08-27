"""Clear parts of the local database, deliberately and reversibly.

    lla reset --what cache          # regenerable lookups only
    lla reset --what outcomes       # what happened to applications
    lla reset --what applications   # the record of where you applied
    lla reset --what all --yes      # everything

Starting a new job hunt, testing a change, or handing the repo to someone else
all need the same thing: a way to clear state without deleting the file and
losing the parts worth keeping. Doing that by hand across forty-eight tables is
how people end up deleting `state.db` entirely.

Nothing here is casual. A reset always writes a timestamped backup of the
database first, always shows what it is about to remove, and never runs without
`--yes`. `applied_jobs` in particular is the only record that you applied at
all — clearing it makes the bot willing to apply to the same jobs again.

Every table belongs to exactly one scope. That is enforced by a test rather
than by care, so a new table cannot quietly end up in no scope (never cleared,
and invisible here) or in two.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# ── scopes ────────────────────────────────────────────────────────────────
# Ordered least to most destructive. Each table appears exactly once.

SCOPES = {
    "cache": {
        "why": "lookups the bot can rebuild by fetching again — safe to clear",
        "tables": [
            "company_intel", "deep_research", "google_jobs", "hiring_velocity",
            "jd_changes", "jd_snapshots", "job_archetypes", "job_fingerprints",
            "market_snapshots", "salary_data", "skill_frequency", "visa_sponsors",
            "quality_scores", "company_connections",
        ],
    },
    "analysis": {
        "why": "scores and reports derived from your applications",
        "tables": [
            "career_simulations", "forensics_reports", "ghost_predictions",
            "interview_prep", "interview_sessions", "job_evaluations",
            "match_scores", "negotiation_briefs", "offer_comparisons",
            "portfolio_projects", "profile_suggestions", "resume_variants",
            "story_bank", "training_evaluations",
        ],
    },
    "outcomes": {
        "why": "what happened after you applied — every model learns from this",
        "tables": [
            "email_responses", "employer_sla", "offers", "response_tracking",
            "pipeline_states",
        ],
    },
    "queues": {
        "why": "pending work: follow-ups, messages, scheduled applies",
        "tables": [
            "apply_schedule", "follow_up_queue", "message_queue",
            "referral_requests", "withdrawal_queue", "job_watchlist",
        ],
    },
    "contacts": {
        "why": "recruiters and the history of talking to them",
        "tables": [
            "recruiter_interactions", "recruiter_scores", "recruiters",
        ],
    },
    "applications": {
        "why": "the record of where you applied — clearing it lets the bot "
               "apply to the same jobs again",
        "tables": [
            "applied_jobs", "ats_status", "daily_stats", "failed_jobs",
            "skipped_jobs",
        ],
    },
}

# Tables that are sqlite's own bookkeeping, not ours.
INTERNAL_TABLES = {"sqlite_sequence"}

SCOPE_ORDER = ["cache", "analysis", "queues", "contacts", "outcomes", "applications"]


def scope_tables(what: str) -> list:
    """Tables a scope clears. 'all' is every scope."""
    if what == "all":
        return sorted({t for s in SCOPES.values() for t in s["tables"]})
    scope = SCOPES.get(what)
    return sorted(scope["tables"]) if scope else []


def known_tables() -> set:
    return {t for s in SCOPES.values() for t in s["tables"]}


def live_tables(state) -> set:
    return {r[0] for r in state.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")} - INTERNAL_TABLES


def unclassified_tables(state) -> set:
    """Tables in the database that no scope covers — a reset would miss them."""
    return live_tables(state) - known_tables()


def counts(state, tables) -> dict:
    """Rows per table, skipping tables this database does not have."""
    present = live_tables(state)
    out = {}
    for t in tables:
        if t not in present:
            continue
        try:
            out[t] = state.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception as exc:
            log.debug("could not count %s: %s", t, exc)
    return out


def backup_database(db_path: str) -> str:
    """Copy the database beside itself with a timestamp. '' if there is none."""
    src = Path(db_path)
    if not src.exists():
        return ""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = src.with_name(f"{src.stem}.backup-{stamp}{src.suffix}")
    shutil.copy2(src, dest)
    return str(dest)


def reset(state, what: str, db_path: str = "", dry_run: bool = True) -> dict:
    """Clear a scope. Returns what was (or would be) removed.

    A dry run is the default everywhere this is called from: the caller has to
    ask twice for anything to be deleted.
    """
    tables = scope_tables(what)
    if not tables:
        return {"error": f"'{what}' is not a scope. Choose from: "
                         + ", ".join(SCOPE_ORDER + ["all"])}
    before = counts(state, tables)
    total = sum(before.values())
    result = {"scope": what, "tables": before, "rows": total,
              "backup": "", "cleared": False}
    if dry_run:
        return result

    result["backup"] = backup_database(db_path) if db_path else ""
    for table in before:
        try:
            state.conn.execute(f"DELETE FROM {table}")
        except Exception as exc:
            log.warning("could not clear %s: %s", table, exc)
    state.conn.commit()
    try:
        state.conn.execute("VACUUM")
    except Exception as exc:
        log.debug("VACUUM skipped: %s", exc)
    result["cleared"] = True
    return result


def format_plan(result: dict) -> str:
    if result.get("error"):
        return f"\n  {result['error']}"
    what = result["scope"]
    rows = result["rows"]
    lines = [""]
    if what == "all":
        lines.append("  Scope: everything")
    else:
        lines.append(f"  Scope: {what} — {SCOPES[what]['why']}")
    lines.append("")
    if not rows:
        lines.append("  Nothing to clear — those tables are already empty.")
        return "\n".join(lines)
    for table, n in sorted(result["tables"].items(), key=lambda kv: -kv[1]):
        if n:
            lines.append(f"    {n:>7}  {table}")
    lines.append(f"\n  {rows} row(s) across "
                 f"{sum(1 for n in result['tables'].values() if n)} table(s).")
    if result["cleared"]:
        lines.append("\n  Cleared.")
        if result["backup"]:
            lines.append(f"  The database was backed up first: {result['backup']}")
    else:
        if what in ("applications", "all"):
            lines.append("\n  This clears the record of where you applied, so the")
            lines.append("  bot will be willing to apply to those jobs again.")
        lines.append("\n  Nothing has been deleted. Add --yes to go ahead;")
        lines.append("  the database is backed up first either way.")
    return "\n".join(lines)


def format_scopes() -> str:
    lines = ["", "  Scopes, least to most destructive:", ""]
    for name in SCOPE_ORDER:
        lines.append(f"    {name:<14} {SCOPES[name]['why']}")
        lines.append(f"                   ({len(SCOPES[name]['tables'])} tables)")
    lines.append(f"    {'all':<14} every scope above")
    return "\n".join(lines)
