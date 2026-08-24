#!/usr/bin/env python3
"""LinkedIn Lightning Applier -- Command Line Interface.

Provides subcommands for every major feature: run the bot, batch external
apply, tailored LaTeX documents, employer-side screener simulation, LLM
connectivity testing, evaluate jobs, score matches, compare offers,
interview prep, story bank, forensics, market intelligence, career
simulation, ghost prediction, SLA tracking, skill gap analysis, salary
benchmarking, portfolio evaluation, training evaluation, pipeline
management, dashboard, config validation, data export, session stats,
and first-time setup.

Usage:
    python cli.py run                  # Start the main bot
    python cli.py apply --file urls.txt  # Batch-apply to ATS URLs (no LinkedIn)
    python cli.py docs --jd-file jd.txt  # Tailored LaTeX CV + cover letter
    python cli.py screen --jd-file jd.txt  # Employer-side screener simulation
    python cli.py test-llm             # Verify the configured LLM works
    python cli.py stats                # Show session statistics
    python cli.py validate-config      # Validate config.yaml
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
    try:
        from cv_profile import enrich_config_profile
        cfg = enrich_config_profile(cfg)
    except Exception as exc:
        log.debug("CV profile enrichment skipped: %s", exc)
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
    _load_config(args.config)
    # Delegate to main.py's entry point
    sys.argv = ["main.py"]
    if args.config != "config.yaml":
        sys.argv.extend(["-c", args.config])
    import main  # noqa: F811
    # main.py runs on import or via its __main__ guard;
    # if it exposes a callable, use it.
    if hasattr(main, "main"):
        main.main()


def _probe_llm(ai, prompt: str) -> str:
    """One low-level LLM call that SURFACES errors.

    AIAnswerer.generate() deliberately swallows failures and returns "" so the
    bot degrades gracefully. For a connectivity test we want the real exception
    (bad model id, 404, auth error) instead of a silent empty string. For the
    OpenAI-compatible providers we hit the client directly; the two special
    backends (Claude CLI subprocess, Anthropic native SDK) fall back to generate().
    """
    if ai.provider == "claude_cli" or (
            ai.provider == "anthropic" and getattr(ai, "_use_anthropic", False)):
        return ai.generate(prompt)
    if ai.client is None:
        raise RuntimeError(
            "LLM client could not be created. For OpenAI-compatible providers "
            "(openrouter/gemini/ollama/lmstudio/openai/groq/...) install the SDK: "
            "pip install openai")
    resp = ai.client.chat.completions.create(
        model=ai.model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=64,
    )
    return (resp.choices[0].message.content or "").strip()


def cmd_docs(args):
    """Generate a tailored LaTeX CV + cover letter for a job, with ATS + review checks.

    Ties together latex_docs (typeset PDFs), ats_pdf_check (keyword coverage +
    parseability), and doc_reviewer (drafter-reviewer critique + honesty check).
    Compiles to PDF if a LaTeX engine is installed; otherwise writes .tex.
    """
    _print_banner("Tailored Application Documents")
    cfg = _load_config(args.config)
    ai = _init_ai(cfg)

    jd = args.jd or ""
    if args.jd_file:
        jd = Path(args.jd_file).read_text(encoding="utf-8")
    if not jd:
        print("  Provide a job description with --jd '...' or --jd-file path.txt")
        return
    title, company = args.title or "the role", args.company or "the company"

    from application_docs import ApplicationDocsGenerator
    gen = ApplicationDocsGenerator(ai, cfg)
    gen.min_coverage = args.min_coverage
    res = gen.generate(title, company, jd)
    report = res["ats"]

    print(f"  Engine     : {res['engine'] or 'none (wrote .tex only — install TeX Live to compile)'}")
    print(f"  CV         : {res['cv_tex']}" + (f"  →  {res['cv_pdf']}" if res.get('cv_pdf') else ""))
    print(f"  Cover      : {res['cover_tex']}" + (f"  →  {res['cover_pdf']}" if res.get('cover_pdf') else ""))
    print()
    color = "green" if report["passed"] else "yellow"
    print(f"  ATS check  : {_color(('PASS' if report['passed'] else 'REVIEW'), color)}  "
          f"(keyword coverage {report['coverage_pct']}%, min {report['min_coverage']}%)")
    if report["missing_keywords"]:
        print(f"    Missing keywords: {', '.join(report['missing_keywords'][:12])}")
    for issue in report["issues"]:
        print(f"    ⚠ {issue}")
    if res["critique"]:
        print("\n  Reviewer critique (cover letter):")
        for line in res["critique"].splitlines()[:8]:
            if line.strip():
                print(f"    {line.strip()}")
    if res["honesty_flags"]:
        print(f"\n  {_color('Honesty check flagged claims to verify:', 'red')}")
        for f in res["honesty_flags"]:
            print(f"    • {f}")
    else:
        print(f"\n  {_color('Honesty check: all claims supported by profile.', 'green')}")

    # Employer-side screener gate on the tailored application
    from screener_sim import ScreenerSimulator
    sim = ScreenerSimulator(ai, cfg)
    g = sim.gate(cfg.get("ai", {}).get("cv_text", ""), jd)
    if g["action"] == "skip":
        print(f"\n  Screener    : not scored ({g['reason']})")
    elif g["final"] is not None:
        color = "green" if g["action"] == "pass" else "red"
        verdict = {"pass": "LIKELY PASS", "warn": "AT RISK", "block": "BLOCKED"}[g["action"]]
        print(f"\n  Screener    : {_color(verdict, color)}  ({g['reason']}; max 120)")
        ev = (g["result"] or {}).get("evaluation") or {}
        for a in (ev.get("areas_for_improvement") or [])[:3]:
            print(f"    ✗ {a}")
        if g["action"] == "block":
            print(f"\n  {_color('Screener gate is set to block — fix the issues above, or', 'red')}")
            print(f"  {_color('lower screener.pass_score / set screener.gate: warn to override.', 'red')}")
            sys.exit(2)


def cmd_screen(args):
    """Simulate the employer-side AI screen on your resume for a specific JD.

    Runs the deterministic hygiene lint always; adds the rubric evaluation
    (category scores + evidence + bonus/deduction ledger) when AI is available.
    Optionally enriches with your GitHub signal.
    """
    _print_banner("Screener Simulator (employer-side view)")
    cfg = _load_config(args.config)

    jd = args.jd or ""
    if args.jd_file:
        jd = Path(args.jd_file).read_text(encoding="utf-8")
    resume_text = ""
    if args.resume_file:
        from profile_setup import read_file_text
        resume_text = read_file_text(args.resume_file)
    if not resume_text:
        resume_text = cfg.get("ai", {}).get("cv_text", "")
    if not resume_text:
        print("  No resume text: pass --resume-file or set ai.cv_text in config.")
        return

    from screener_sim import ScreenerSimulator, pick_rubric
    ai = _init_ai(cfg)
    sim = ScreenerSimulator(ai, cfg)
    rubric = args.rubric or pick_rubric(jd)
    res = sim.simulate(resume_text, jd, rubric)

    print(f"  Rubric      : {res['rubric']}")
    lint = res["lint"]
    print(f"  Hygiene     : {len(lint['issues'])} issue(s)  "
          f"({lint['stats']['bullets']} bullets, {lint['stats']['urls']} links, "
          f"{lint['stats']['words']} words)")
    for issue in lint["issues"]:
        print(f"    ⚠ {issue}")

    if res["ai_used"] and res["total"]:
        t = res["total"]
        print(f"\n  Screener score: {_color(str(t['final']), 'bold')} / {t['max_possible']}"
              f"  →  {_color('LIKELY PASS' if res['passed'] else 'AT RISK', 'green' if res['passed'] else 'red')}"
              f"  (threshold {sim.pass_score})")
        for cat, c in t["categories"].items():
            print(f"    {cat:22} {c['score']:>3}/{c['max']:<3} {c['evidence'][:70]}")
        print(f"    {'bonus':22} +{t['bonus']}   {'deductions':12} -{t['deductions']}")
        ev = res["evaluation"] or {}
        for s in (ev.get("key_strengths") or [])[:5]:
            print(f"    ✓ {s}")
        for a in (ev.get("areas_for_improvement") or [])[:3]:
            print(f"    ✗ {a}")
    else:
        print("\n  (AI unavailable — hygiene lint only. Configure a provider for the "
              "full rubric evaluation.)")

    if args.github:
        from github_enrich import extract_username, fetch_repos, github_signal_summary, rank_projects
        username = extract_username(args.github)
        repos = fetch_repos(username)
        sig = github_signal_summary(repos)
        print(f"\n  GitHub signal ({username}): {sig['repos']} repos, "
              f"{sig['open_source']} open-source, {sig['self_projects']} self, "
              f"{sig['total_stars']} stars")
        for w in sig["warnings"]:
            print(f"    ⚠ {w}")
        best = rank_projects(repos, jd, top=5)
        if best:
            print("  Projects to feature for this posting:")
            for p in best:
                print(f"    {p['score']:>6.1f}  {p['name']}  [{p['type']}, "
                      f"★{p['stars']}] {p['description'][:50]}")


def cmd_doctor(args):
    """Detect Python/Chrome/packages/tools; install or update as needed.

    `lla doctor` reports; `--fix` installs missing packages; `--upgrade` also
    updates the browser stack (undetected-chromedriver + selenium) so a freshly
    auto-updated Chrome keeps working.
    """
    _print_banner("Environment Doctor")
    from env_doctor import MIN_PYTHON, run_doctor
    rep = run_doctor(fix=args.fix, upgrade=args.upgrade)

    py = rep["python"]
    print(f"  Python    : {py['version']}  "
          f"{_color('OK', 'green') if py['ok'] else _color(f'needs >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}', 'red')}")
    ch = rep["chrome"]["version"]
    print(f"  Chrome    : {('v' + str(ch) + '  ' if ch else '')}"
          f"{_color('detected — driver will be pinned automatically', 'green') if ch else _color('NOT FOUND — install Google Chrome', 'red')}")

    print("\n  Packages:")
    for p in rep["packages"]:
        mark = _color("✓", "green") if p["installed"] else (
            _color("✗ MISSING", "red") if p["kind"] == "required"
            else _color("– not installed (optional)", "yellow"))
        ver = f" {p['installed']}" if p["installed"] else ""
        print(f"    {p['name']:26} {mark}{ver}")
    if rep["installed_now"]:
        print(f"\n  {_color('Installed now:', 'green')} {', '.join(rep['installed_now'])}")
    if rep["upgraded_now"]:
        print(f"  {_color('Upgraded now:', 'green')} {', '.join(rep['upgraded_now'])}")

    t = rep["tools"]
    print("\n  Optional tools:")
    print(f"    LaTeX engine   : {t['latex_engine'] or 'none (lla docs writes .tex only)'}")
    print(f"    pdftotext      : {'yes' if t['pdftotext'] else 'no (ATS check uses pdfminer/plain text)'}")
    print(f"    Ollama         : {'running :11434' if t['ollama_running'] else 'not running'}")
    print(f"    LM Studio      : {'running :1234' if t['lmstudio_running'] else 'not running'}")

    if rep["ok"]:
        print(f"\n  {_color('READY — environment is good to launch.', 'green')}")
    else:
        missing = ", ".join(rep["missing_required"]) or "python version"
        print(f"\n  {_color('NOT READY:', 'red')} {missing}")
        if not args.fix:
            print("  Run:  python cli.py doctor --fix")
        sys.exit(1)


def cmd_test_llm(args):
    """Send one real prompt to the configured (or overridden) LLM and print the reply.

    Verifies end-to-end connectivity for ANY provider — cloud (OpenAI, Anthropic,
    Gemini, Groq, DeepSeek, OpenRouter, Claude CLI) or local (Ollama, LM Studio) —
    before you rely on it for form answers. Override the provider/model on the fly
    with --provider/--model so you can try a model without editing config.

    A model that returns empty text (e.g. a reranker or embedding model) is
    flagged as unusable for generation.
    """
    _print_banner("LLM Connectivity Test")
    cfg = _load_config(args.config)
    ai_cfg = cfg.setdefault("ai", {})
    ai_cfg["enabled"] = True
    if args.provider:
        ai_cfg["provider"] = args.provider
        # A bare provider override shouldn't inherit an unrelated model from config.
        if not args.model:
            ai_cfg["model"] = ""
    if args.model:
        ai_cfg["model"] = args.model
    if args.base_url:
        ai_cfg["base_url"] = args.base_url
    if args.api_key:
        ai_cfg["api_key"] = args.api_key

    ai = _init_ai(cfg)
    if ai is None or not getattr(ai, "enabled", False):
        print(f"  {_color('AI is disabled or failed to initialize.', 'red')}")
        sys.exit(1)

    print(f"  Provider : {ai.provider}")
    print(f"  Model    : {ai.model or '(provider default)'}")
    print(f"  Base URL : {ai.base_url or '(native SDK)'}")
    print(f"  API key  : {'set' if ai.api_key else '(none — ok for local / claude_cli)'}")

    prompt = args.prompt or "Reply with exactly the word: OK"
    print(f"\n  Prompt   : {prompt}")
    import time as _time
    t0 = _time.time()
    try:
        reply = _probe_llm(ai, prompt)
    except Exception as exc:
        print(f"\n  {_color('FAILED', 'red')}: {exc}")
        print("  Check the model id, api key, and base_url. For OpenRouter, confirm the")
        print("  model exists at https://openrouter.ai/models and is a chat (not rerank) model.")
        sys.exit(1)
    dt = _time.time() - t0

    if reply and reply.strip():
        print(f"  Reply    : {_color(reply.strip()[:500], 'green')}")
        print(f"  Latency  : {dt:.1f}s")
        print(f"\n  {_color('LLM is working — usable for form answers.', 'green')}")
    else:
        print(f"  Reply    : {_color('(empty)', 'yellow')}")
        print(f"\n  {_color('Reachable but returned no text.', 'yellow')} If this is a rerank or "
              "embedding\n  model, it cannot generate answers — pick a chat/completion model instead.")
        sys.exit(1)


def cmd_apply(args):
    """Submit applications to external ATS forms from a list of apply URLs.

    The "last mile": hand it apply links (from Indeed, Google Jobs, a careers
    page, or a spreadsheet) and it drives a real browser through each ATS form
    and submits — no LinkedIn required. Supports all 12 ATS platforms.
    """
    _print_banner("External Batch Apply")
    from apply_urls import BatchApplier, load_jobs
    from ats_handlers import detect_ats

    jobs = load_jobs(args.urls, args.file)
    if not jobs:
        print("  No valid URLs provided. Pass URLs or --file <path>.")
        return

    if args.dry_run:
        supported = 0
        headers = ["ATS", "Status", "URL"]
        rows = []
        for j in jobs:
            ats = detect_ats(j["url"])
            status = "ready" if ats else "unsupported"
            if ats:
                supported += 1
            rows.append([ats or "—", status, j["url"][:55]])
        _print_table(headers, rows)
        print(f"\n  {supported}/{len(jobs)} ready to apply. "
              f"(dry run — nothing submitted)")
        return

    cfg = _load_config(args.config)
    runner = BatchApplier(cfg, resume_path=args.resume or "",
                          headless=args.headless or None,
                          max_apply=args.max_apply, force=args.force)
    ok, fail, skip = runner.run(jobs)
    print(f"\n  Done: {_color(str(ok)+' submitted', 'green')}   "
          f"{_color(str(fail)+' failed', 'red')}   {skip} skipped")
    if fail:
        sys.exit(1)


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
              lla apply URL [URL ...]          Submit external ATS applications
              lla apply --file urls.txt        Batch-apply from a file
              lla apply --file urls.txt --dry-run   Preview ATS detection
              lla docs --jd-file jd.txt --title "Risk Manager" --company "Monzo"
              lla screen --jd-file jd.txt      Employer-side AI screen simulation
              lla test-llm                     Verify your configured LLM works
              lla test-llm --provider openrouter --model nvidia/nemotron-3-super-120b-a12b:free
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

    # --- docs ---
    p = subs.add_parser("docs", help="Generate tailored LaTeX CV + cover letter (ATS + review checks)")
    p.add_argument("--jd", help="Job description text")
    p.add_argument("--jd-file", dest="jd_file", help="Path to a file with the job description")
    p.add_argument("--title", help="Job title")
    p.add_argument("--company", help="Company name")
    p.add_argument("--min-coverage", dest="min_coverage", type=int, default=60,
                   help="Min ATS keyword-coverage %% to pass (default 60)")

    # --- screen ---
    p = subs.add_parser("screen", help="Simulate the employer-side AI resume screen for a JD")
    p.add_argument("--jd", help="Job description text")
    p.add_argument("--jd-file", dest="jd_file", help="Path to a file with the job description")
    p.add_argument("--resume-file", dest="resume_file",
                   help="Resume file (.txt/.md/.tex/.pdf); defaults to ai.cv_text from config")
    p.add_argument("--rubric", choices=["engineering", "professional"],
                   help="Rubric profile (default: auto-detect from the JD)")
    p.add_argument("--github", help="Your GitHub URL/username for signal analysis")

    # --- doctor ---
    p = subs.add_parser("doctor", help="Detect/install/update dependencies, Chrome driver pinning, tools")
    p.add_argument("--fix", action="store_true", help="Install missing packages automatically")
    p.add_argument("--upgrade", action="store_true",
                   help="Also upgrade the browser stack (undetected-chromedriver, selenium)")

    # --- test-llm ---
    p = subs.add_parser("test-llm", help="Send one prompt to the configured LLM to verify it works")
    p.add_argument("--provider", help="Override provider (openrouter, gemini, ollama, lmstudio, ...)")
    p.add_argument("--model", help="Override model id (e.g. nvidia/nemotron-3-super-120b-a12b:free)")
    p.add_argument("--base-url", dest="base_url", help="Override base URL (for local/self-hosted)")
    p.add_argument("--api-key", dest="api_key", help="Override API key (else config / env var)")
    p.add_argument("--prompt", help="Custom test prompt")

    # --- apply ---
    p = subs.add_parser("apply", help="Submit external ATS applications from apply URLs")
    p.add_argument("urls", nargs="*", help="Apply URL(s) to submit")
    p.add_argument("-f", "--file", help="File of URLs (.txt one-per-line, .csv, or .json)")
    p.add_argument("-r", "--resume", help="Resume file to upload (overrides config)")
    p.add_argument("--max", type=int, dest="max_apply", help="Max applications this run")
    p.add_argument("--headless", action="store_true", help="Run the browser headless")
    p.add_argument("--force", action="store_true", help="Re-apply even if already applied")
    p.add_argument("--dry-run", action="store_true",
                   help="Detect the ATS for each URL and print a plan — no browser, no submit")

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
    "apply": cmd_apply,
    "docs": cmd_docs,
    "screen": cmd_screen,
    "doctor": cmd_doctor,
    "test-llm": cmd_test_llm,
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
