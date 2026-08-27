"""Protocol-agnostic tool layer for linkedin-lightning-applier.

This module is the single source of truth for "what the assistant can do".
It has ZERO MCP / OpenAI / Gemini / protocol dependencies. It exposes a set
of plain functions, each returning a human-readable string, that wrap the
data and AI modules of the application.

Any protocol adapter (MCP server, OpenAI function-calling, Gemini, etc.)
should import these functions and expose them in its own way.

Every tool is defensive:
  * modules may be disabled,
  * the AI layer may be off,
  * the database may be empty or missing.
In all of those cases the tool returns a helpful message instead of raising.
"""

import os

CONFIG_PATH = "config.yaml"
DB_PATH = "data/state.db"


def _load():
    """Load config and initialise State + AIAnswerer.

    Returns a tuple ``(cfg, state, ai)``. The caller is responsible for
    calling ``state.close()`` in a ``finally`` block.
    """
    import yaml
    from state import State
    from ai import AIAnswerer

    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as fh:
                cfg = yaml.safe_load(fh) or {}
        except Exception:
            cfg = {}

    state = State(DB_PATH)
    ai = AIAnswerer(cfg, db_conn=getattr(state, "conn", None))
    return cfg, state, ai


def _safe_close(state):
    """Close a State object without ever raising."""
    if state is None:
        return
    try:
        state.close()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Stats / reporting tools
# --------------------------------------------------------------------------- #

def tool_stats() -> str:
    """Session, daily and all-time application statistics."""
    state = None
    try:
        _cfg, state, _ai = _load()
        lines = ["Job Application Statistics", "=" * 30]

        try:
            summary = state.session_summary()
            if isinstance(summary, dict):
                for key, val in summary.items():
                    lines.append(f"  {key}: {val}")
            elif summary:
                lines.append(str(summary))
        except Exception as exc:
            lines.append(f"  (session summary unavailable: {exc})")

        try:
            lines.append(f"Applied today: {state.daily_applied_count()}")
        except Exception as exc:
            lines.append(f"Applied today: unavailable ({exc})")

        try:
            lines.append(f"Total applied (all-time): {state.total_applied()}")
        except Exception as exc:
            lines.append(f"Total applied: unavailable ({exc})")

        try:
            recruiters = state.get_all_recruiters(1000)
            lines.append(f"Recruiters tracked: {len(recruiters or [])}")
        except Exception:
            pass

        try:
            sponsors = state.get_all_visa_sponsors()
            lines.append(f"Visa sponsors found: {len(sponsors or [])}")
        except Exception:
            pass

        try:
            funnel = state.get_funnel_stats()
            if funnel:
                lines.append("Funnel:")
                if isinstance(funnel, dict):
                    for key, val in funnel.items():
                        lines.append(f"  {key}: {val}")
                else:
                    lines.append(f"  {funnel}")
        except Exception:
            pass

        return "\n".join(lines)
    except Exception as exc:
        return f"Could not load statistics: {exc}"
    finally:
        _safe_close(state)


def tool_list_applied(limit: int = 20) -> str:
    """List recently applied-to jobs."""
    state = None
    try:
        _cfg, state, _ai = _load()
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 20
        rows = state.get_all_applied(limit) or []
        if not rows:
            return "No applications recorded yet."
        lines = [f"Applied jobs (showing up to {limit}):", "=" * 30]
        for idx, row in enumerate(rows, 1):
            lines.append(f"{idx}. {_fmt_row(row)}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not list applied jobs: {exc}"
    finally:
        _safe_close(state)


def tool_list_recruiters(limit: int = 20) -> str:
    """List tracked recruiters / hiring contacts."""
    state = None
    try:
        _cfg, state, _ai = _load()
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 20
        rows = state.get_all_recruiters(limit) or []
        if not rows:
            return "No recruiters tracked yet."
        lines = [f"Recruiters (showing up to {limit}):", "=" * 30]
        for idx, row in enumerate(rows, 1):
            lines.append(f"{idx}. {_fmt_row(row)}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not list recruiters: {exc}"
    finally:
        _safe_close(state)


def tool_visa_sponsors() -> str:
    """List companies identified as visa sponsors."""
    state = None
    try:
        _cfg, state, _ai = _load()
        rows = state.get_all_visa_sponsors() or []
        if not rows:
            return "No visa sponsors identified yet."
        lines = [f"Visa sponsors ({len(rows)}):", "=" * 30]
        for idx, row in enumerate(rows, 1):
            lines.append(f"{idx}. {_fmt_row(row)}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not list visa sponsors: {exc}"
    finally:
        _safe_close(state)


def tool_export_csv(min_score: int = 0) -> str:
    """Export applications to CSV and report the output path."""
    state = None
    try:
        cfg, state, _ai = _load()
        export_dir = cfg.get("export", {}).get("export_dir", "data")
        result = state.export_csv(export_dir, cfg)
        if result:
            return f"Exported applications to: {result}"
        return f"Export completed (output directory: {export_dir})."
    except Exception as exc:
        return f"Could not export CSV: {exc}"
    finally:
        _safe_close(state)


def tool_pipeline_summary() -> str:
    """Summary of the application pipeline / funnel stages."""
    state = None
    try:
        cfg, state, _ai = _load()
        try:
            from pipeline_manager import PipelineManager
        except Exception as exc:
            return f"Pipeline manager unavailable: {exc}"
        try:
            pm = PipelineManager(cfg, state)
        except Exception as exc:
            return f"Could not initialise pipeline manager: {exc}"
        summary = pm.get_pipeline_summary()
        if not summary:
            return "Pipeline is empty."
        if isinstance(summary, str):
            return summary
        if isinstance(summary, dict):
            lines = ["Pipeline summary", "=" * 30]
            for key, val in summary.items():
                lines.append(f"  {key}: {val}")
            return "\n".join(lines)
        return str(summary)
    except Exception as exc:
        return f"Could not build pipeline summary: {exc}"
    finally:
        _safe_close(state)


# --------------------------------------------------------------------------- #
# AI-backed analysis tools
# --------------------------------------------------------------------------- #

def tool_score_job(title: str, company: str, description: str,
                   location: str = "") -> str:
    """Score how well a job matches the candidate's CV (0-100)."""
    state = None
    try:
        cfg, state, ai = _load()
        if not getattr(ai, "enabled", False):
            return ("AI is disabled in config.yaml, so job scoring is not "
                    "available. Enable the 'ai' section to use this tool.")
        from match_scorer import MatchScorer
        scorer = MatchScorer(ai, cfg)
        result = scorer.score_job(title, company, description, location)
        if not result:
            return "Scorer returned no result."

        lines = [f"Match score for: {title} @ {company}", "=" * 30]
        lines.append(f"Score: {result.get('score', 'n/a')}/100")

        matches = result.get("skill_matches") or []
        if matches:
            lines.append("Matching skills: " + ", ".join(map(str, matches)))
        missing = result.get("missing_skills") or []
        if missing:
            lines.append("Missing / gap skills: " + ", ".join(map(str, missing)))

        explanation = result.get("explanation")
        if explanation:
            lines.append("")
            lines.append("Explanation:")
            lines.append(str(explanation))
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not score job: {exc}"
    finally:
        _safe_close(state)


def tool_evaluate_job(job_id, title: str, company: str,
                      description: str) -> str:
    """Run the full A-F job evaluation report."""
    state = None
    try:
        cfg, state, ai = _load()
        if not getattr(ai, "enabled", False):
            return ("AI is disabled in config.yaml, so job evaluation is not "
                    "available. Enable the 'ai' section to use this tool.")
        from job_evaluator import JobEvaluator
        evaluator = JobEvaluator(ai, cfg, state)
        result = evaluator.evaluate(job_id, title, company, description)
        if not result:
            return "Evaluator returned no result."
        if isinstance(result, str):
            return result

        lines = [f"Job evaluation: {title} @ {company}", "=" * 30]
        if isinstance(result, dict):
            for block, content in result.items():
                lines.append("")
                lines.append(f"[{block}]")
                if isinstance(content, (dict, list)):
                    import json
                    lines.append(json.dumps(content, indent=2, default=str))
                else:
                    lines.append(str(content))
        else:
            lines.append(str(result))
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not evaluate job: {exc}"
    finally:
        _safe_close(state)


def tool_salary_benchmark(role: str, location: str = "") -> str:
    """Salary benchmark for a role / location."""
    state = None
    try:
        cfg, state, ai = _load()
        from salary_intel import SalaryIntel
        intel = SalaryIntel(state, ai, cfg)
        result = intel.get_benchmark(role, location)
        if not result:
            return f"No salary data available for '{role}'."
        if isinstance(result, str):
            return result
        loc = location or "any location"
        lines = [f"Salary benchmark: {role} ({loc})", "=" * 30]
        if isinstance(result, dict):
            for key, val in result.items():
                lines.append(f"  {key}: {val}")
        else:
            lines.append(str(result))
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not build salary benchmark: {exc}"
    finally:
        _safe_close(state)


def tool_skill_gaps() -> str:
    """Generate the skill-gap analysis report."""
    state = None
    try:
        cfg, state, ai = _load()
        from skill_gap_analysis import SkillGapAnalyzer
        analyzer = SkillGapAnalyzer(ai, cfg, state)
        report = analyzer.generate_report()
        if not report:
            return "No skill-gap data available yet."
        return str(report)
    except Exception as exc:
        return f"Could not generate skill-gap report: {exc}"
    finally:
        _safe_close(state)


def tool_tailor_resume(title: str, company: str, description: str) -> str:
    """Tailor the resume to a specific job; return the output file path."""
    state = None
    try:
        cfg, state, ai = _load()
        if not getattr(ai, "enabled", False):
            return ("AI is disabled in config.yaml, so resume tailoring is not "
                    "available. Enable the 'ai' section to use this tool.")
        from resume_tailor import ResumeTailor
        tailor = ResumeTailor(ai, cfg)
        path = tailor.tailor_resume(title, company, description)
        if not path:
            return ("Resume tailoring did not produce a file (the tailor may "
                    "be disabled or the source resume is missing).")
        return f"Tailored resume written to: {path}"
    except Exception as exc:
        return f"Could not tailor resume: {exc}"
    finally:
        _safe_close(state)


def tool_forensics() -> str:
    """Run application forensics and format the insights."""
    state = None
    try:
        cfg, state, ai = _load()
        from application_forensics import ApplicationForensics
        forensics = ApplicationForensics(ai, cfg, state)
        result = forensics.run_full_analysis()
        if not result:
            try:
                latest = forensics.get_latest_report()
                if latest:
                    return str(latest)
            except Exception:
                pass
            return "No forensics insights available yet."
        if isinstance(result, str):
            return result
        lines = ["Application Forensics", "=" * 30]
        if isinstance(result, dict):
            for key, val in result.items():
                lines.append("")
                lines.append(f"[{key}]")
                if isinstance(val, (list, dict)):
                    import json
                    lines.append(json.dumps(val, indent=2, default=str))
                else:
                    lines.append(str(val))
        else:
            lines.append(str(result))
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not run forensics: {exc}"
    finally:
        _safe_close(state)


def tool_market_report() -> str:
    """Generate the weekly market-pulse brief."""
    state = None
    try:
        cfg, state, ai = _load()
        from market_pulse import MarketPulse
        pulse = MarketPulse(ai, cfg, state)
        brief = pulse.generate_weekly_brief()
        if not brief:
            return "No market data available yet."
        return str(brief)
    except Exception as exc:
        return f"Could not generate market report: {exc}"
    finally:
        _safe_close(state)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _fmt_row(row):
    """Best-effort one-line formatting of a DB row of unknown shape."""
    try:
        # sqlite3.Row supports keys()
        if hasattr(row, "keys"):
            return ", ".join(f"{k}={row[k]}" for k in row.keys())
    except Exception:
        pass
    if isinstance(row, dict):
        return ", ".join(f"{k}={v}" for k, v in row.items())
    if isinstance(row, (list, tuple)):
        return " | ".join(str(item) for item in row)
    return str(row)


def tool_propose_outcomes_from_email(emails_json: str) -> str:
    """Read recruiter emails and propose outcomes. Writes NOTHING.

    `emails_json` is a JSON list of {id, from, subject, body, date}. This is
    the read half of the approval gate: an MCP client that already has Gmail
    access passes messages in, gets proposals back, shows them to the person,
    and only then calls tool_apply_email_outcomes with the ids they approved.
    """
    import json
    state = None
    try:
        emails = json.loads(emails_json) if isinstance(emails_json, str) else emails_json
        if not isinstance(emails, list):
            return "Expected a JSON list of messages: [{id, from, subject, body, date}]"
        _cfg, state, _ai = _load()
        from gmail_sync import propose_outcomes, summarise
        proposals = propose_outcomes(state, emails)
        return json.dumps({"summary": summarise(proposals),
                           "proposals": [p.as_dict() for p in proposals]}, indent=2)
    except Exception as exc:
        return f"Could not read those messages: {exc}"
    finally:
        if state:
            try:
                state.close()
            except Exception:
                pass


def tool_apply_email_outcomes(emails_json: str, approved_email_ids: str) -> str:
    """Record ONLY the proposals whose email ids are explicitly approved.

    `approved_email_ids` is a JSON list (or comma-separated string) of ids from
    tool_propose_outcomes_from_email. Nothing else is written, whatever its
    confidence — the person approving is the gate. Each recorded outcome cites
    the email it came from.
    """
    import json
    state = None
    try:
        emails = json.loads(emails_json) if isinstance(emails_json, str) else emails_json
        try:
            approved = json.loads(approved_email_ids)
        except Exception:
            approved = [s.strip() for s in str(approved_email_ids).split(",") if s.strip()]
        if not approved:
            return "No email ids approved — nothing was recorded."
        _cfg, state, _ai = _load()
        from gmail_sync import apply_proposals, propose_outcomes
        proposals = propose_outcomes(state, emails)
        written = apply_proposals(state, proposals, approve=set(approved))
        if not written:
            skipped = [p.as_dict() for p in proposals
                       if p.email_id in set(approved) and p.error]
            return json.dumps({"recorded": 0, "skipped": skipped}, indent=2)
        return json.dumps({"recorded": len(written),
                           "outcomes": [p.as_dict() for p in written]}, indent=2)
    except Exception as exc:
        return f"Could not record those outcomes: {exc}"
    finally:
        if state:
            try:
                state.close()
            except Exception:
                pass


def tool_record_outcome(query: str, outcome: str, notes: str = "",
                        when: str = "") -> str:
    """Record what happened to an application (interview, offer, rejection…).

    `query` is a company, job title, job id or URL. Refuses rather than guesses
    when it matches several applications.
    """
    state = None
    try:
        _cfg, state, _ai = _load()
        from outcomes import (
            ALL_TYPES,
            find_applications,
            normalise_type,
            parse_when,
            record_outcome,
        )
        key = normalise_type(outcome)
        if not key:
            return f"'{outcome}' is not an outcome. Use one of: {', '.join(ALL_TYPES)}"
        matches = find_applications(state, query)
        if not matches:
            return f"No application matches '{query}'."
        if len(matches) > 1:
            listed = "\n".join(f"  {m['company']} — {m['title']} (id {m['job_id']})"
                               for m in matches[:10])
            return (f"'{query}' matches {len(matches)} applications; say which:\n"
                    f"{listed}")
        when_dt = parse_when(when) if when else None
        if when and when_dt is None:
            return f"Could not read the date '{when}'."
        _ok, msg = record_outcome(state, matches[0]["job_id"], key,
                                  notes=notes, when=when_dt)
        return msg
    except Exception as exc:
        return f"Could not record that outcome: {exc}"
    finally:
        if state:
            try:
                state.close()
            except Exception:
                pass


def tool_open_applications(quiet_days: int = 0) -> str:
    """Applications with no final outcome yet, longest silence first."""
    state = None
    try:
        _cfg, state, _ai = _load()
        from outcomes import format_pending, pending_applications
        return format_pending(pending_applications(state, quiet_days))
    except Exception as exc:
        return f"Could not list open applications: {exc}"
    finally:
        if state:
            try:
                state.close()
            except Exception:
                pass


def tool_outcome_summary() -> str:
    """The application funnel: applied, engaged, interviews, offers, ghosted."""
    state = None
    try:
        _cfg, state, _ai = _load()
        from outcomes import format_summary, outcome_summary
        return format_summary(outcome_summary(state))
    except Exception as exc:
        return f"Could not build the funnel: {exc}"
    finally:
        if state:
            try:
                state.close()
            except Exception:
                pass


# Registry of all tools, useful for adapters that want to enumerate them.
ALL_TOOLS = {
    "tool_propose_outcomes_from_email": tool_propose_outcomes_from_email,
    "tool_apply_email_outcomes": tool_apply_email_outcomes,
    "tool_record_outcome": tool_record_outcome,
    "tool_open_applications": tool_open_applications,
    "tool_outcome_summary": tool_outcome_summary,
    "tool_stats": tool_stats,
    "tool_score_job": tool_score_job,
    "tool_evaluate_job": tool_evaluate_job,
    "tool_salary_benchmark": tool_salary_benchmark,
    "tool_skill_gaps": tool_skill_gaps,
    "tool_tailor_resume": tool_tailor_resume,
    "tool_forensics": tool_forensics,
    "tool_market_report": tool_market_report,
    "tool_list_applied": tool_list_applied,
    "tool_list_recruiters": tool_list_recruiters,
    "tool_visa_sponsors": tool_visa_sponsors,
    "tool_export_csv": tool_export_csv,
    "tool_pipeline_summary": tool_pipeline_summary,
}
