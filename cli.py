#!/usr/bin/env python3
"""LinkedIn Lightning Applier -- Command Line Interface.

Provides subcommands for every major feature: run the bot, evaluate jobs,
score matches, compare offers, interview prep, story bank, forensics,
market intelligence, career simulation, ghost prediction, SLA tracking,
skill gap analysis, salary benchmarking, portfolio evaluation, training
evaluation, pipeline management, dashboard, config validation, data
export, session stats, and first-time setup.

Usage:
    python cli.py run                  # Start the main bot
    python cli.py stats                # Show session statistics
    python cli.py validate-config      # Validate config.yaml
    python cli.py skill-gaps           # Show skill gap report
    python cli.py salary --role "..."  # Salary benchmark
    python cli.py setup                # Interactive setup wizard
"""

import argparse
import logging
import os
import sys
import textwrap
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so local imports work
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    import yaml
except ImportError:
    print("PyYAML is required.  Run:  pip install pyyaml")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
log = logging.getLogger("lla.cli")


# ═══════════════════════════════════════════════════════════════════════════
# Helper utilities
# ═══════════════════════════════════════════════════════════════════════════

def _load_config(path: str = "config.yaml") -> dict:
    """Load and return the YAML configuration file."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        print(f"Error: config file '{path}' not found.")
        print("Run  python cli.py setup  to create one interactively.")
        sys.exit(1)
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg


def _init_state(cfg: dict):
    """Create and return a State instance."""
    from state import State
    db_path = cfg.get("state", {}).get("db_path", "data/state.db")
    return State(db_path=db_path)


def _init_ai(cfg: dict):
    """Create and return an AIAnswerer (may be disabled)."""
    try:
        from ai import AIAnswerer
        return AIAnswerer(cfg)
    except Exception as exc:
        log.warning("AI module unavailable: %s", exc)
        return None


def _color(text: str, color: str) -> str:
    """Wrap *text* in ANSI color codes.  Falls back to plain text."""
    codes = {
        "green": "\033[92m",
        "red": "\033[91m",
        "yellow": "\033[93m",
        "cyan": "\033[96m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "reset": "\033[0m",
    }
    if not sys.stdout.isatty():
        return text
    start = codes.get(color, "")
    end = codes.get("reset", "")
    return f"{start}{text}{end}"


def _print_table(headers: list, rows: list, col_widths: list = None):
    """Print a simple ASCII table to stdout."""
    if not rows:
        print("  (no data)")
        return
    if col_widths is None:
        col_widths = []
        for i, h in enumerate(headers):
            max_w = len(str(h))
            for row in rows:
                val = str(row[i]) if i < len(row) else ""
                max_w = max(max_w, len(val))
            col_widths.append(min(max_w + 2, 50))

    def _fmt_row(cells):
        parts = []
        for i, cell in enumerate(cells):
            w = col_widths[i] if i < len(col_widths) else 20
            parts.append(str(cell).ljust(w)[:w])
        return " | ".join(parts)

    header_line = _fmt_row(headers)
    sep = "-+-".join("-" * w for w in col_widths)
    print(f"  {header_line}")
    print(f"  {sep}")
    for row in rows:
        print(f"  {_fmt_row(row)}")


def _print_banner(title: str):
    """Print a section banner."""
    width = 60
    print()
    print(_color("=" * width, "cyan"))
    print(_color(f"  {title}", "bold"))
    print(_color("=" * width, "cyan"))
    print()


# ═══════════════════════════════════════════════════════════════════════════
# Subcommand handlers
# ═══════════════════════════════════════════════════════════════════════════

def cmd_run(args):
    """Start the main LinkedIn Lightning Applier bot."""
    _print_banner("LinkedIn Lightning Applier -- Run")
    cfg = _load_config(args.config)
    # Delegate to main.py's entry point
    sys.argv = ["main.py"]
    if args.config != "config.yaml":
        sys.argv.extend(["-c", args.config])
    import main  # noqa: F811
    # main.py runs on import or via its __main__ guard;
    # if it exposes a callable, use it.
    if hasattr(main, "main"):
        main.main()


def cmd_evaluate(args):
    """Run a structured A-F evaluation for a specific job."""
    _print_banner("Job Evaluation")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from job_evaluator import JobEvaluator
    evaluator = JobEvaluator(ai, cfg, state)
    result = evaluator.evaluate(
        job_id=args.job_id,
        title=args.title or "",
        company=args.company or "",
        description=args.description or "",
    )
    if not result:
        print("  Evaluation returned no results (is AI enabled?).")
        return
    if "full_report" in result:
        print(result["full_report"])
    else:
        for key, val in result.items():
            print(f"\n  [{key}]")
            print(textwrap.indent(str(val), "    "))


def cmd_score(args):
    """Score a job against the candidate CV."""
    _print_banner("Match Scoring")
    cfg = _load_config(args.config)
    ai = _init_ai(cfg)
    from match_scorer import MatchScorer
    scorer = MatchScorer(ai, cfg)
    result = scorer.score_job(
        title=args.title,
        company=args.company or "",
        description=args.description or "",
        location=args.location or "",
    )
    print(f"  Score:          {_color(str(result.get('score', 0)), 'bold')} / 100")
    print(f"  Explanation:    {result.get('explanation', 'n/a')}")
    if result.get("skill_matches"):
        print(f"  Matched skills: {', '.join(result['skill_matches'])}")
    if result.get("missing_skills"):
        print(f"  Missing skills: {', '.join(result['missing_skills'])}")


def cmd_compare_offers(args):
    """Compare multiple job offers side-by-side."""
    _print_banner("Offer War Room -- Compare Offers")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from offer_war_room import OfferWarRoom
    war_room = OfferWarRoom(ai, cfg, state)
    job_ids = [jid.strip() for jid in args.job_ids.split(",")]
    result = war_room.compare_offers(job_ids)
    if not result:
        print("  No comparison data returned.  Ensure offers exist in state.")
        return
    print("  Offer comparison matrix:")
    for jid, scores in result.items():
        if isinstance(scores, dict):
            score_str = "  ".join(f"{k}: {v}" for k, v in scores.items())
            print(f"    {jid}: {score_str}")


def cmd_interview(args):
    """Generate interview prep materials for a job."""
    _print_banner("Interview Prep")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from interview_prep import InterviewPrepGenerator
    prep = InterviewPrepGenerator(ai, cfg)
    result = prep.generate(
        job_id=args.job_id,
        title=args.title or "",
        company=args.company or "",
        description=args.description or "",
        state=state,
    )
    if not result:
        print("  No prep generated (is AI enabled + interview_prep.enabled?).")
        return
    for section, content in result.items():
        print(f"\n  --- {section.replace('_', ' ').title()} ---")
        print(textwrap.indent(str(content), "    "))


def cmd_stories(args):
    """Display or manage the STAR+R story bank."""
    _print_banner("Story Bank")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from story_bank import StoryBank
    bank = StoryBank(ai, cfg, state)
    if args.export:
        report = bank.export_story_bank()
        print(report if report else "  Story bank is empty.")
        return
    stories = bank.get_stories(theme=args.theme, limit=args.limit or 20)
    if not stories:
        print("  No stories found.")
        return
    for i, s in enumerate(stories, 1):
        theme = s.get("theme", "general") if isinstance(s, dict) else "?"
        title = s.get("title", "(untitled)") if isinstance(s, dict) else str(s)
        print(f"  {i}. [{theme}] {title}")


def cmd_forensics(args):
    """Run application forensics analysis."""
    _print_banner("Application Forensics")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from application_forensics import ApplicationForensics
    forensics = ApplicationForensics(ai, cfg, state)
    result = forensics.run_full_analysis()
    if not result:
        print("  No forensics data available yet.  Apply to some jobs first.")
        return
    if isinstance(result, dict):
        for key, val in result.items():
            print(f"\n  --- {key.replace('_', ' ').title()} ---")
            if isinstance(val, dict):
                for k2, v2 in val.items():
                    print(f"    {k2}: {v2}")
            else:
                print(textwrap.indent(str(val), "    "))


def cmd_market(args):
    """Show job market intelligence."""
    _print_banner("Market Pulse")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from market_pulse import MarketPulse
    pulse = MarketPulse(ai, cfg, state)
    if args.brief:
        brief = pulse.generate_weekly_brief()
        print(brief if brief else "  Not enough data for a market brief yet.")
    else:
        snap = pulse.capture_snapshot(
            role_pattern=args.role or "",
            location=args.location or "",
        )
        if not snap:
            print("  No market snapshot data available.")
            return
        for k, v in snap.items():
            print(f"  {k}: {v}")


def cmd_career_sim(args):
    """Run a career path simulation."""
    _print_banner("Career Path Simulator")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from career_simulator import CareerSimulator
    sim = CareerSimulator(ai, cfg, state)
    if args.compare and args.sim_id:
        report = sim.compare_paths(int(args.sim_id))
        print(report if report else "  Simulation not found.")
    else:
        paths = [p.strip() for p in args.paths.split(",")] if args.paths else []
        if not paths:
            print("  Provide paths with --paths 'Path A, Path B'")
            return
        path_dicts = [{"name": p} for p in paths]
        result = sim.simulate(path_dicts, current_role=args.current_role or "")
        if result:
            print(f"  Simulation saved.  ID: {result.get('simulation_id', 'n/a')}")
            for p in result.get("paths", []):
                name = p.get("name", "?")
                print(f"    - {name}")


def cmd_ghost_check(args):
    """Predict ghost probability for a job application."""
    _print_banner("Ghost Predictor")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from ghost_predictor import GhostPredictor
    predictor = GhostPredictor(ai, cfg, state)
    result = predictor.predict(
        job_id=args.job_id,
        title=args.title or "",
        company=args.company or "",
        description=args.description or "",
        match_score=int(args.match_score) if args.match_score else 0,
    )
    if not result:
        print("  Prediction unavailable.")
        return
    prob = result.get("ghost_probability", 0)
    risk = result.get("risk_label", "unknown")
    color = "green" if prob < 0.3 else ("yellow" if prob < 0.6 else "red")
    print(f"  Ghost probability: {_color(f'{prob:.0%}', color)}  ({risk})")
    factors = result.get("factors", {})
    if factors:
        print("  Factor breakdown:")
        for fname, fval in factors.items():
            print(f"    {fname}: {fval}")


def cmd_sla(args):
    """Show employer response-time SLA tracking."""
    _print_banner("Employer SLA Tracker")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    from employer_sla_tracker import EmployerSLATracker
    tracker = EmployerSLATracker(cfg, state)
    overdue = tracker.get_overdue_applications()
    if not overdue:
        print("  No overdue applications detected.  All companies within SLA.")
        return
    print(f"  {len(overdue)} overdue application(s):\n")
    headers = ["Company", "Job", "Stage", "Days Overdue"]
    rows = []
    for item in overdue:
        if isinstance(item, dict):
            rows.append([
                item.get("company", "?"),
                item.get("title", item.get("job_id", "?")),
                item.get("stage", "?"),
                item.get("days_overdue", "?"),
            ])
        else:
            rows.append([str(item), "", "", ""])
    _print_table(headers, rows)


def cmd_skill_gaps(args):
    """Show skill gap analysis report."""
    _print_banner("Skill Gap Analysis")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from skill_gap_analysis import SkillGapAnalyzer
    analyzer = SkillGapAnalyzer(ai, cfg, state)
    report = analyzer.generate_report()
    print(report if report else "  No skill gap data available yet.")


def cmd_salary(args):
    """Show salary intelligence benchmarks."""
    _print_banner("Salary Intelligence")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from salary_intel import SalaryIntel
    intel = SalaryIntel(state, ai, cfg)
    report = intel.get_benchmark_report(
        title_pattern=args.role or "",
        location_pattern=args.location or "",
    )
    print(report)


def cmd_portfolio(args):
    """Evaluate a portfolio project idea."""
    _print_banner("Portfolio Evaluator")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from portfolio_evaluator import PortfolioEvaluator
    evaluator = PortfolioEvaluator(ai, cfg, state)
    if not args.idea:
        print("  Provide a project idea with --idea 'Build a ...'")
        return
    result = evaluator.evaluate(args.idea) if hasattr(evaluator, "evaluate") else {}
    if not result:
        print("  Evaluation returned no data (is AI enabled?).")
        return
    for k, v in result.items():
        print(f"  {k}: {v}")


def cmd_training(args):
    """Evaluate a training course or certification."""
    _print_banner("Training Evaluator")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    ai = _init_ai(cfg)
    from training_evaluator import TrainingEvaluator
    evaluator = TrainingEvaluator(ai, cfg, state)
    if not args.course:
        print("  Provide a course name with --course 'Course Name'")
        return
    result = evaluator.evaluate(args.course) if hasattr(evaluator, "evaluate") else {}
    if not result:
        print("  Evaluation returned no data (is AI enabled?).")
        return
    for k, v in result.items():
        print(f"  {k}: {v}")


def cmd_pipeline(args):
    """Show application pipeline summary."""
    _print_banner("Application Pipeline")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    from pipeline_manager import PipelineManager
    pm = PipelineManager(cfg, state)
    summary = pm.get_pipeline_summary()
    if not summary:
        print("  Pipeline is empty.")
        return
    headers = ["Stage", "Count"]
    rows = []
    total = 0
    for stage, count in summary.items():
        if count > 0:
            rows.append([stage, count])
            total += count
    rows.append(["TOTAL", total])
    _print_table(headers, rows)


def cmd_dashboard(args):
    """Launch the monitoring dashboard in the foreground."""
    _print_banner("Dashboard")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    from dashboard import Dashboard
    dash = Dashboard(state, cfg)
    port = args.port or dash.port
    host = args.host or dash.host
    print(f"  Starting dashboard on http://{host}:{port}")
    print("  Press Ctrl+C to stop.\n")
    # Override config to start in foreground
    dash.enabled = True
    dash.port = port
    dash.host = host
    try:
        dash.start()
        # Keep main thread alive while dashboard runs in background thread
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")


def cmd_validate_config(args):
    """Validate the configuration file."""
    _print_banner("Config Validation")
    cfg = _load_config(args.config)
    from validate_config import ConfigValidator
    validator = ConfigValidator(cfg)
    is_valid = validator.validate()
    if validator.warnings:
        print(f"  {_color('Warnings:', 'yellow')}")
        for w in validator.warnings:
            print(f"    - {w}")
    if validator.errors:
        print(f"\n  {_color('Errors:', 'red')}")
        for e in validator.errors:
            print(f"    - {e}")
    if is_valid:
        print(f"\n  {_color('Config is VALID.', 'green')}")
    else:
        print(f"\n  {_color('Config has ERRORS.  Fix them before running.', 'red')}")
        sys.exit(1)


def cmd_export(args):
    """Export application data to CSV files."""
    _print_banner("Data Export")
    cfg = _load_config(args.config)
    state = _init_state(cfg)
    export_dir = args.output or "data"
    state.export_csv(export_dir=export_dir, cfg=cfg)
    print(f"  Data exported to {os.path.abspath(export_dir)}/")


def cmd_stats(args):
    """Show application statistics."""
    _print_banner("Application Statistics")
    cfg = _load_config(args.config)
    state = _init_state(cfg)

    # Session stats
    elapsed = datetime.now() - state.session_start
    hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    print("  Session:")
    print(f"    Applied:   {state.session_applied}")
    print(f"    Skipped:   {state.session_skipped}")
    print(f"    Failed:    {state.session_failed}")
    print(f"    Duration:  {hours}h {minutes}m {seconds}s")

    # All-time counts from database
    try:
        applied_total = state.conn.execute(
            "SELECT COUNT(*) FROM applied_jobs"
        ).fetchone()[0]
        skipped_total = state.conn.execute(
            "SELECT COUNT(*) FROM skipped_jobs"
        ).fetchone()[0]
        failed_total = state.conn.execute(
            "SELECT COUNT(*) FROM failed_jobs"
        ).fetchone()[0]
    except Exception:
        applied_total = skipped_total = failed_total = 0

    print("\n  All-Time:")
    print(f"    Applied:   {_color(str(applied_total), 'green')}")
    print(f"    Skipped:   {_color(str(skipped_total), 'yellow')}")
    print(f"    Failed:    {_color(str(failed_total), 'red')}")

    # Today's counts
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        applied_today = state.conn.execute(
            "SELECT COUNT(*) FROM applied_jobs WHERE applied_at LIKE ?",
            (f"{today}%",),
        ).fetchone()[0]
        skipped_today = state.conn.execute(
            "SELECT COUNT(*) FROM skipped_jobs WHERE skipped_at LIKE ?",
            (f"{today}%",),
        ).fetchone()[0]
    except Exception:
        applied_today = skipped_today = 0

    print(f"\n  Today ({today}):")
    print(f"    Applied:   {applied_today}")
    print(f"    Skipped:   {skipped_today}")


def cmd_setup(args):
    """Run the interactive setup wizard."""
    _print_banner("Setup Wizard")
    from setup_wizard import SetupWizard
    wizard = SetupWizard()
    wizard.run()


# ═══════════════════════════════════════════════════════════════════════════
# Argument parser construction
# ═══════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="lla",
        description="LinkedIn Lightning Applier -- CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              lla run                          Start the bot
              lla stats                        Show statistics
              lla validate-config              Check config.yaml
              lla skill-gaps                   Skill gap report
              lla salary --role "Engineer"     Salary benchmarks
              lla setup                        Interactive setup
        """),
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml",
        help="Path to config YAML file (default: config.yaml)",
    )

    subs = parser.add_subparsers(dest="command", help="Available commands")

    # --- run ---
    subs.add_parser("run", help="Start the main bot")

    # --- evaluate ---
    p = subs.add_parser("evaluate", help="Evaluate a specific job (A-F blocks)")
    p.add_argument("--job-id", required=True, help="Job ID to evaluate")
    p.add_argument("--title", help="Job title")
    p.add_argument("--company", help="Company name")
    p.add_argument("--description", help="Job description text")

    # --- score ---
    p = subs.add_parser("score", help="Score a job against your CV")
    p.add_argument("--title", required=True, help="Job title")
    p.add_argument("--company", help="Company name")
    p.add_argument("--description", help="Job description text")
    p.add_argument("--location", help="Job location")

    # --- compare-offers ---
    p = subs.add_parser("compare-offers", help="Compare multiple offers")
    p.add_argument("--job-ids", required=True,
                   help="Comma-separated job IDs to compare")

    # --- interview ---
    p = subs.add_parser("interview", help="Generate interview prep for a job")
    p.add_argument("--job-id", required=True, help="Job ID")
    p.add_argument("--title", help="Job title")
    p.add_argument("--company", help="Company name")
    p.add_argument("--description", help="Job description text")

    # --- stories ---
    p = subs.add_parser("stories", help="View/export STAR+R story bank")
    p.add_argument("--theme", help="Filter by theme")
    p.add_argument("--limit", type=int, help="Max stories to show")
    p.add_argument("--export", action="store_true", help="Export full bank")

    # --- forensics ---
    subs.add_parser("forensics", help="Run application forensics analysis")

    # --- market ---
    p = subs.add_parser("market", help="Job market intelligence")
    p.add_argument("--role", help="Role pattern to analyze")
    p.add_argument("--location", help="Location to analyze")
    p.add_argument("--brief", action="store_true", help="Generate weekly brief")

    # --- career-sim ---
    p = subs.add_parser("career-sim", help="Career path simulation")
    p.add_argument("--paths", help="Comma-separated path names")
    p.add_argument("--current-role", help="Your current role title")
    p.add_argument("--compare", action="store_true", help="Compare saved sim")
    p.add_argument("--sim-id", help="Simulation ID to compare")

    # --- ghost-check ---
    p = subs.add_parser("ghost-check", help="Predict ghost probability")
    p.add_argument("--job-id", required=True, help="Job ID")
    p.add_argument("--title", help="Job title")
    p.add_argument("--company", help="Company name")
    p.add_argument("--description", help="Job description text")
    p.add_argument("--match-score", help="Match score (0-100)")

    # --- sla ---
    subs.add_parser("sla", help="Employer response-time SLA tracking")

    # --- skill-gaps ---
    subs.add_parser("skill-gaps", help="Skill gap analysis report")

    # --- salary ---
    p = subs.add_parser("salary", help="Salary intelligence benchmarks")
    p.add_argument("--role", help="Role pattern (e.g. 'Risk Manager')")
    p.add_argument("--location", help="Location pattern (e.g. 'London')")

    # --- portfolio ---
    p = subs.add_parser("portfolio", help="Evaluate a portfolio project idea")
    p.add_argument("--idea", help="Project idea description")

    # --- training ---
    p = subs.add_parser("training", help="Evaluate a training course/cert")
    p.add_argument("--course", help="Course or certification name")

    # --- pipeline ---
    subs.add_parser("pipeline", help="Application pipeline summary")

    # --- dashboard ---
    p = subs.add_parser("dashboard", help="Launch monitoring dashboard")
    p.add_argument("--port", type=int, help="Port number")
    p.add_argument("--host", help="Host to bind to")

    # --- validate-config ---
    subs.add_parser("validate-config", help="Validate configuration file")

    # --- export ---
    p = subs.add_parser("export", help="Export data to CSV")
    p.add_argument("--output", help="Output directory (default: data/)")

    # --- stats ---
    subs.add_parser("stats", help="Show application statistics")

    # --- setup ---
    subs.add_parser("setup", help="Interactive setup wizard")

    return parser


# ═══════════════════════════════════════════════════════════════════════════
# Command dispatcher
# ═══════════════════════════════════════════════════════════════════════════

COMMAND_MAP = {
    "run": cmd_run,
    "evaluate": cmd_evaluate,
    "score": cmd_score,
    "compare-offers": cmd_compare_offers,
    "interview": cmd_interview,
    "stories": cmd_stories,
    "forensics": cmd_forensics,
    "market": cmd_market,
    "career-sim": cmd_career_sim,
    "ghost-check": cmd_ghost_check,
    "sla": cmd_sla,
    "skill-gaps": cmd_skill_gaps,
    "salary": cmd_salary,
    "portfolio": cmd_portfolio,
    "training": cmd_training,
    "pipeline": cmd_pipeline,
    "dashboard": cmd_dashboard,
    "validate-config": cmd_validate_config,
    "export": cmd_export,
    "stats": cmd_stats,
    "setup": cmd_setup,
}


def main():
    """Parse arguments and dispatch to the appropriate subcommand."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    handler = COMMAND_MAP.get(args.command)
    if handler is None:
        print(f"Unknown command: {args.command}")
        parser.print_help()
        sys.exit(1)

    try:
        handler(args)
    except KeyboardInterrupt:
        print("\n  Interrupted.")
        sys.exit(130)
    except Exception as exc:
        log.error("Command '%s' failed: %s", args.command, exc, exc_info=True)
        print(f"\n  {_color('Error:', 'red')} {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
