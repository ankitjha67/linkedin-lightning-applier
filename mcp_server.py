"""MCP server for linkedin-lightning-applier.

Exposes the protocol-agnostic tools in ``tools_layer`` over the Model Context
Protocol (MCP), so Claude Code / Claude Desktop (or any MCP client) can drive
the job-application tool with natural language.

Run directly (stdio transport):

    python3 mcp_server.py

Or register it with Claude Desktop / Claude Code as an MCP server pointing at
this file.
"""

import os  # noqa: F401  (kept available for adapters / future use)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise ImportError("MCP SDK not installed. Run: pip install mcp")

from tools_layer import (
    tool_stats,
    tool_score_job,
    tool_evaluate_job,
    tool_salary_benchmark,
    tool_skill_gaps,
    tool_tailor_resume,
    tool_forensics,
    tool_market_report,
    tool_list_applied,
    tool_list_recruiters,
    tool_visa_sponsors,
    tool_export_csv,
    tool_pipeline_summary,
    tool_propose_outcomes_from_email,
    tool_apply_email_outcomes,
    tool_record_outcome,
    tool_open_applications,
    tool_outcome_summary,
)

mcp = FastMCP("linkedin-lightning-applier")


@mcp.tool()
def get_stats() -> str:
    """Get job application statistics: today's applications, all-time totals,
    recruiters tracked, visa sponsors found, funnel breakdown, and a session
    summary. Use this when the user asks how the job hunt is going or wants
    numbers/progress."""
    return tool_stats()


@mcp.tool()
def score_job(title: str, company: str, description: str,
              location: str = "") -> str:
    """Score how well a job matches the candidate's CV (0-100). Provide the
    job title, company, and full job description (location optional). Returns
    the numeric score, matching skills, skill gaps, and an explanation. Use
    this to quickly judge whether a specific posting is worth applying to."""
    return tool_score_job(title, company, description, location)


@mcp.tool()
def evaluate_job(job_id: str, title: str, company: str,
                 description: str) -> str:
    """Run the full structured A-F job evaluation for a posting. Provide a
    job_id (any identifier), title, company, and description. Returns a
    detailed multi-block report covering fit, compensation, risks, and more.
    Use this for a deep dive on a single job, beyond the quick match score."""
    return tool_evaluate_job(job_id, title, company, description)


@mcp.tool()
def salary_benchmark(role: str, location: str = "") -> str:
    """Get a salary benchmark for a given role and optional location. Returns
    typical pay ranges and related intelligence drawn from collected job data.
    Use this when the user asks what a role pays or wants to gauge an offer."""
    return tool_salary_benchmark(role, location)


@mcp.tool()
def skill_gaps() -> str:
    """Generate a skill-gap analysis report comparing the candidate's skills
    against the requirements seen across collected job postings. Use this when
    the user asks what skills they are missing or should learn next."""
    return tool_skill_gaps()


@mcp.tool()
def tailor_resume(title: str, company: str, description: str) -> str:
    """Tailor the candidate's resume to a specific job posting. Provide the
    job title, company, and description. Returns the file path of the newly
    generated, tailored resume. Use this before applying to a high-value role."""
    return tool_tailor_resume(title, company, description)


@mcp.tool()
def forensics() -> str:
    """Run application forensics: analyze past applications to surface patterns,
    what is working, and what is not (e.g. response rates by company type or
    score band). Use this when the user wants to understand and improve their
    application strategy."""
    return tool_forensics()


@mcp.tool()
def market_report() -> str:
    """Generate the weekly market-pulse brief summarizing hiring trends, demand
    signals, and notable activity across collected job data. Use this when the
    user wants a high-level read on the job market."""
    return tool_market_report()


@mcp.tool()
def list_applied(limit: int = 20) -> str:
    """List the jobs the candidate has applied to (most recent first). Optional
    `limit` controls how many to show (default 20). Use this when the user asks
    which jobs they have applied to."""
    return tool_list_applied(limit)


@mcp.tool()
def list_recruiters(limit: int = 20) -> str:
    """List tracked recruiters and hiring contacts. Optional `limit` controls
    how many to show (default 20). Use this when the user asks about recruiter
    contacts they have gathered."""
    return tool_list_recruiters(limit)


@mcp.tool()
def visa_sponsors() -> str:
    """List companies identified as visa sponsors among the collected jobs. Use
    this when the user needs employers that sponsor work visas."""
    return tool_visa_sponsors()


@mcp.tool()
def export_csv(min_score: int = 0) -> str:
    """Export the application data to a CSV file and return the output path.
    Optional `min_score` can filter to higher-quality matches. Use this when the
    user wants to download or back up their application records."""
    return tool_export_csv(min_score)


@mcp.tool()
def pipeline_summary() -> str:
    """Summarize the application pipeline by stage (e.g. saved, applied,
    interviewing). Use this when the user asks for an overview of where their
    applications stand."""
    return tool_pipeline_summary()


if __name__ == "__main__":
    mcp.run()


@mcp.tool()
def propose_outcomes_from_email(emails_json: str) -> str:
    """Read recruiter emails and propose application outcomes WITHOUT recording
    anything. Pass a JSON list of messages: [{id, from, subject, body, date}].
    Returns each proposed outcome with the application it matched, a confidence
    level, and why it matched. Use this when the user asks you to check their
    inbox for job-application news. Always show the proposals to the user and
    get their approval before calling apply_email_outcomes."""
    return tool_propose_outcomes_from_email(emails_json)


@mcp.tool()
def apply_email_outcomes(emails_json: str, approved_email_ids: str) -> str:
    """Record ONLY the email-derived outcomes the user has explicitly approved.
    Pass the same messages JSON plus a JSON list of the approved email ids.
    Nothing else is recorded regardless of confidence. Each stored outcome
    cites the email it came from. Never call this without the user's approval
    of the specific proposals."""
    return tool_apply_email_outcomes(emails_json, approved_email_ids)


@mcp.tool()
def record_outcome(query: str, outcome: str, notes: str = "",
                   when: str = "") -> str:
    """Record what happened to a job application. `query` is a company, job
    title, job id or URL; `outcome` is one of callback, assessment, interview,
    offer, rejection, withdrawn, ghosted. `when` accepts '2026-08-14',
    '3 days ago' or 'yesterday' — use it whenever the user says the event
    happened earlier, so response times stay accurate. Use this when the user
    mentions an interview, offer or rejection."""
    return tool_record_outcome(query, outcome, notes, when)


@mcp.tool()
def open_applications(quiet_days: int = 0) -> str:
    """List applications with no final outcome yet, longest silence first. Set
    quiet_days to only show those silent that many days. Use this when the user
    asks what they are still waiting on or who to chase."""
    return tool_open_applications(quiet_days)


@mcp.tool()
def outcome_summary() -> str:
    """The application funnel: applied, engaged, interviews, offers, rejections,
    ghosted and still-open, with the average days to a first response. Use this
    when the user asks how their applications are converting."""
    return tool_outcome_summary()
