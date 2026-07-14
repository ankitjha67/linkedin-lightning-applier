# Changelog

## v2.9.0 — Screener Gate, LaTeX Docs, Answer RAG & Application Craftsmanship

### Added (Answer RAG, any-job-board, more LLMs)
- **Semantic Answer Memory (RAG)** (`answer_rag.py`, wired into
  `AIAnswerer.answer()`) — remembers every form answer; a semantically-similar
  question is answered straight from memory with **zero LLM tokens**
  (options-aware: only if the stored answer fits the offered choices), and
  near-matches are injected into the prompt so answers stay consistent.
  Pure-Python TF-IDF cosine — no numpy/embeddings, works offline with any
  provider. New `answer_rag` SQLite table (tables 48 → 49) and `rag:` config
  block (`reuse_threshold` 0.85, `context_threshold` 0.50). Includes a
  regression fix: "us" is not a stopword, so a UK visa answer is never reused
  for a US visa question.
- **Any-job-board generic apply** — URLs matching no known ATS now fall back to
  the generic handler (`external_apply.allow_generic_fallback`, default true)
  which sweeps the form and can **register/sign in** using shared
  `ats_accounts.generic` credentials. Wired into the run loop and the batch
  applier.
- **3 new LLM providers**: `xai` (Grok), `mistral`, and `custom` — ANY
  OpenAI-compatible endpoint (vLLM, llama.cpp server, LocalAI) via
  `ai.base_url` with no API key required for local servers. Env keys:
  `XAI_API_KEY`/`GROK_API_KEY`, `MISTRAL_API_KEY`, `CUSTOM_API_KEY`/`LLM_API_KEY`.
  13 providers total.
- +21 tests (`tests/test_answer_rag.py`). Suite 315 → 336.

### Fixed (stale-data sweep)
- **`companies.json`: 9 of 30 entries were dead** (404 on their ATS API —
  companies moved platforms). Fixed OpenAI, Notion, and Plaid (all migrated
  Greenhouse → Ashby) and replaced Mistral AI, Wise, Klarna, HashiCorp, Canva,
  and Retool (moved to proprietary portals the free-API scanner can't read)
  with verified-live Greenhouse companies: Monzo, GoCardless, Adyen, Affirm,
  Marqeta, Mercury. All 30 entries re-verified 200 against live APIs.
- **Model defaults refreshed** in `ai.py`: `anthropic` → `claude-sonnet-5`
  (was `claude-sonnet-4-6`), `groq` → `llama-3.3-70b-versatile` (the 3.1-70b
  model was decommissioned by Groq).
- Docs stats reconciled with reality: 33,402 lines / 101 Python files /
  53 features (counted from the README feature list) / 315 tests / 25 CLI
  commands. README "4-model fallback chain" → the chain is config-driven.
- CLI header docstring and README module listing now include all new modules
  and commands (`apply`, `docs`, `screen`, `test-llm`).

### Added
- **Screener Simulator** (`screener_sim.py` + `lla screen`) — see your resume
  through the employer's AI screen before submitting. Rubric adapted from
  HackerRank's open-source hiring-agent (interviewstreet/hiring-agent, MIT),
  inverted to the candidate side: deterministic hygiene lint (missing links,
  unquantified bullets, generic project names, missing contact text), role-aware
  rubric evaluation (engineering vs professional category sets) with hard caps,
  evidence, a clamped bonus/deduction ledger (bonus ≤ 20, final ∈ [−20, 120]),
  key strengths and areas for improvement. Fairness rules preserved verbatim
  (never scores name/school/grades/location). Degrades to lint-only without AI.
- **GitHub signal enrichment** (`github_enrich.py`) — classify your repos the
  way screeners do (true open-source vs self-project vs fork), warn when the
  profile caps the open-source category (~10/35), and rank the top projects to
  feature per posting (stars, docs, live demo, recency, JD language/topic
  relevance). Public GitHub API, `GITHUB_TOKEN` optional, fails soft.
- **Screener pre-submit gate** — `ScreenerSimulator.gate()` shared by all three
  submit paths: `lla docs` (prints verdict, exit 2 on block), `lla apply` batch
  (skips blocked rows), and the autonomous loop (opt-in via
  `screener.gate_in_run`; blocked jobs are marked skipped with the reason).
  Config: `screener.gate: off|warn|block`. Fail-open by design: jobs with no
  substantial description, AI unavailable, or an unparseable evaluation are
  never blocked.
- +30 tests (`tests/test_screener_sim.py`). Suite 285 → 315.
- **Professional application-document pipeline** (inspired by
  MadsLorentzen/ai-job-search, adapted to this repo's Python architecture):
  - **`latex_docs.py`** — typeset **moderncv** CV + cover letter. Pure-function
    renderers (`render_cv_tex` / `render_cover_tex`) plus `LaTeXDocsBuilder` that
    reads identity from config, writes `.tex`, and compiles to PDF with
    lualatex/xelatex/pdflatex when installed (graceful `.tex`-only fallback).
    Uses the standard `moderncv` package — nothing vendored.
  - **`ats_pdf_check.py`** — verify a CV the way an ATS parser sees it:
    `extract_pdf_text` (pdftotext → pdfminer), `check_parseability` (contact
    details present, reading order, glyph garbage), `keyword_coverage`, and a
    combined `ats_report`. Honesty rule: genuine gaps are surfaced, never stuffed.
  - **`relevance_cutter.py`** — trim a CV to a line budget by
    relevance×uniqueness×cover-dependency, not by age; preserves order.
  - **`doc_reviewer.py`** — drafter-reviewer loop: `review` → `revise` →
    `check_honesty` (flags claims the profile doesn't support). Degrades to
    no-ops without AI.
  - **`lla docs`** command — one shot: tailor CV + cover letter, compile, ATS
    keyword-coverage report, reviewer critique, and honesty check.
  - **`.claude/` integration** — a `/apply` slash command and a
    `job-application-assistant` skill for a human-in-the-loop apply flow inside
    Claude Code (this repo previously had no `.claude/` skills or commands).
  - **`application_docs.py`** — shared `ApplicationDocsGenerator` orchestrator
    (tailor → build → ATS-check → review) used by both `lla docs` and the loop.
  - **Autonomous-loop wiring** — when `latex_docs.auto_generate: true`, every
    above-threshold job in `lla run` gets a typeset LaTeX CV whose compiled PDF
    becomes the upload resume (threaded through process_page/run_cycle; opt-in,
    off by default). Graceful when no LaTeX engine is present.
  - **`/setup` onboarding** — a `setup.md` slash command + `profile_setup.py`
    (`gather_profile_text`) that turns a `documents/` folder (CV, LinkedIn
    export, diplomas, references) into profile text for `ai.cv_text`/`personal.*`.
    Added the `documents/` scaffold (gitignored contents, tracked structure).
  - +21 tests (`tests/test_application_docs.py`). Optional dep: `pdfminer.six`
    (extra `ats`). Fixed a tokenizer bug that captured trailing sentence
    punctuation ("SQL." → "sql." ) in keyword extraction.
- **`lla test-llm` command** — send one real prompt to the configured (or
  `--provider`/`--model`/`--base-url`/`--api-key` overridden) LLM and print the
  reply, latency, and resolved provider/model. Uses a low-level probe that
  surfaces the real error (bad model id, 404, auth, unreachable) instead of the
  silent `""` that `AIAnswerer.generate()` returns on failure, and flags models
  that return no text (rerank/embedding models can't generate answers). +5 tests.

### Fixed
- **`validate_config` no longer flags `openrouter`/`claude_cli` as "unknown AI
  provider"** — both are fully supported by `ai.py` but were missing from the
  validator's allowlist.
- **Refreshed the default `OPENROUTER_FREE_CHAIN`** — 3 of its 4 models had been
  delisted from OpenRouter. Now three verified-live free models
  (Llama-3.3-70B, Nemotron-3-Super-120B, Nemotron-Nano-9B). The provider-init
  test no longer pins a magic chain length.

## v2.8.0 — Multi-ATS Auto-Apply (12 platforms, incl. Workday)

### Added
- **`ats_handlers/` package** — a plugin registry that drives external
  applications across **12 ATS platforms**: Workday, Greenhouse, Lever, Ashby,
  SmartRecruiters, Workable, Jobvite, BambooHR, iCIMS, Taleo, SuccessFactors,
  and ADP (was 4: Greenhouse/Lever/Workday/Ashby).
  - `base.py` — `ATSHandler` with the hard-won primitives: React-friendly
    `fill_text` (dispatches input/change events), `select_custom_dropdown`
    (ARIA listboxes, not native `<select>`), `click_button` (text OR
    `data-automation-id` OR aria-label), `run_multistep` wizard loop with
    terminal-state detection, `sweep_page`, `keyword_match`, `upload_file`.
  - `generic.py` — `SinglePageHandler` / `MultiStepHandler` shapes plus
    `AccountMixin` for login-gated portals.
  - `handlers.py` — 11 thin per-platform subclasses.
  - `workday.py` — robust **Workday** handler: account creation/sign-in reused
    across every company tenant, multi-page wizard (My Information → Experience →
    Questions → Voluntary Disclosures → Self-Identify → Review → Submit),
    targeting the stable `data-automation-id` attributes that are identical on
    every Workday tenant. Voluntary self-ID defaults to "decline to answer".
  - `__init__.py` — registry: `detect_ats(url)`, `get_handler(name)`,
    `handler_for_url(url)`.
- **`apply_urls.py` — standalone batch apply runner** (+ `lla apply` CLI command).
  Closes the discovery→submission gap: hand it a list of apply URLs (inline, or a
  `.txt`/`.csv`/`.json` file) and it drives a real browser through each ATS form
  and submits — **without LinkedIn** (external ATS apply only needs the browser +
  config). Reuses the same `ExternalApplier`/`ats_handlers` engine, records
  results to SQLite with URL-based dedup, and offers a `--dry-run` that detects
  the ATS per URL with no browser and submits nothing. Flags: `--file`,
  `--resume`, `--max`, `--headless`, `--force`, `--dry-run`.
- **`external_apply.ats_accounts`** config — credentials for login-gated ATSes
  (Workday, iCIMS, Taleo, SuccessFactors, ADP). One email+password is reused
  per platform; account created on first visit, signed into thereafter. Supports
  optional per-tenant Workday overrides. Missing creds → platform skipped, not
  failed. Also added `max_wizard_pages` and `slow_mo_seconds` knobs.
- **34 new tests** (`tests/test_ats_handlers.py`, `tests/test_apply_urls.py`) —
  detection across all 12 platforms, registry wiring, handler shapes/account
  flags, Workday per-tenant credential resolution, keyword-match field logic,
  plus the batch runner's input parsing (txt/csv/json), URL dedup, stable job
  IDs, and browserless planning. Suite: 217 → 251.

### Changed
- **`external_apply.py`** is now a thin orchestrator (tab management, per-cycle
  caps, ATS detection) that delegates form filling to `ats_handlers/`. Its
  public API (`.enabled`, `.can_apply()`, `.detect_ats()`, `.apply_external()`,
  `.max_per_cycle`, `.applied_this_cycle`) is unchanged — `main.py` needs no
  edits. `supported_ats` now defaults to all 12 platforms.
- `pyproject.toml` 2.7.1 → 2.8.0; wheel now bundles `ats_handlers/**`.
- README, CONFIGURATION.md, ARCHITECTURE.md updated with the 12-platform list,
  the per-platform handling table, and the handler-framework design.

### Notes
- Adding a new ATS is two edits: a subclass in `ats_handlers/handlers.py` and a
  URL pattern in `ats_handlers/__init__.py`. No changes to `external_apply.py`
  or `main.py` required.
- Login-gated enterprise portals (iCIMS/Taleo/SuccessFactors/ADP) use a
  best-effort generic register-or-sign-in flow; Workday's flow is fully custom.

---

## v2.7.1 — Stale-data refresh

### Changed
- Refreshed default model IDs to current generations: `anthropic` →
  `claude-sonnet-4-6` (was a pinned 2025-05 build), `gemini` →
  `gemini-2.5-flash` (was `gemini-2.0-flash`).
- `pyproject.toml` version bumped 2.5.0 → 2.7.0 to match the changelog.
- `config.example.yaml` AI block now shows a real, current setup (Gemini 2.5
  with env-var key) instead of the fictional `qwen3.5-9b` placeholder.
- Corrected stale stats across docs: test count (165 → 217), table count
  (13/32 → 48), feature count (36 → 55), LOC (29,590 → 29,504).
- Fixed wrong table names/columns in ARCHITECTURE.md schema reference
  (`dedup_fingerprints` → `job_fingerprints`, `apply_queue` →
  `apply_schedule`, plus accurate columns for the lifecycle/intelligence
  tables) and expanded it to cover all 48 tables.

---

## v2.7.0 — Daily Scheduling & Env-Var API Keys

### Added
- **`--once` run mode** (`main.py --once`) — runs a single scan cycle then exits,
  so it can be scheduled from cron without overlapping processes.
- **Daily automation scripts** — `run_daily.sh` (loads `.env`, picks the project
  venv, runs one cycle, logs to `logs/daily_*.log`, prunes logs >30 days) and
  `setup_cron.sh` (idempotent installer: `./setup_cron.sh [HOUR MINUTE]`,
  `--remove` to uninstall).
- **Environment-variable API keys** — `AIAnswerer` now falls back to per-provider
  env vars when `api_key` is blank in config: `GEMINI_API_KEY`/`GOOGLE_API_KEY`,
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`,
  `GROQ_API_KEY`, `TOGETHER_API_KEY`. Keeps secrets out of committed files.
  Applies to both the primary and fallback providers.
- **`.env.example`** — template for `.env` (gitignored), read by `run_daily.sh`.

### Notes
- To use Gemini 2.5 Pro: set `provider: "gemini"`, `model: "gemini-2.5-pro"`,
  leave `api_key` blank, and export `GEMINI_API_KEY`.

---

## v2.6.0 — MCP Server, Free LLMs & Careers Scanner (from autopilot-jobhunt)

Brings the best ideas from [autopilot-jobhunt](https://github.com/tarunlnmiit/autopilot-jobhunt):
MCP control, zero-cost LLM backends, and a company-first careers-page scanner.

### Added
- **MCP Server** (`mcp_server.py` + `tools_layer.py`) — Control the bot with natural
  language from Claude Code / Claude Desktop. 13 MCP tools (score job, evaluate, salary
  benchmark, skill gaps, tailor resume, forensics, market report, list applied/recruiters,
  visa sponsors, export, pipeline, stats). Protocol-agnostic `tools_layer.py` is the single
  source of truth so future OpenAI/Gemini function-calling adapters share one implementation.
  Install: `pip install -e '.[mcp]'` then `claude mcp add lla -- python -m mcp_server`.
- **Claude CLI LLM backend** (`provider: claude_cli`) — Uses the local `claude` binary as
  the LLM (no API key, zero cost if you have Claude Code). Subprocess call with
  `--strict-mcp-config` to minimize context overhead.
- **OpenRouter provider** (`provider: openrouter`) — Free 4-model fallback chain
  (Llama 3.3 70B, Nemotron 70B, Gemma 2 9B, Qwen 2.5 72B). Auto-falls-back on rate limit.
  Zero cost, no credit card.
- **Careers-Page Scanner** (`careers_scanner.py` + `companies.json`) — Company-first job
  discovery via free ATS JSON APIs (Greenhouse, Lever, Ashby) with HTML-scraping fallback.
  Curated 30-company database (EU/US/APAC/Remote). Scores each role and surfaces top matches.
  Complements the LinkedIn/Google scrapers. Wired into the scan cycle (opt-in).
- **PyPI packaging** (`pyproject.toml`) — `pip install` distribution with `lla` CLI entry
  point and optional-dependency groups (`[mcp]`, `[web]`, `[documents]`, `[anthropic]`, `[all]`).
- **18 new tests** (`tests/test_integrations_new.py`) — covering both new providers, the
  careers scanner, the tools layer (incl. a "never raises" contract test), and companies.json.

### Fixed
- `main.py` plugin-loading block referenced `features` before it was defined (NameError when
  plugins loaded) — fixed to use a `plugin_count` counter applied in the features section.

### Stats
- 81 Python files, 29,590 lines of code, 217 tests, 55 features, 10 LLM providers.

---

## v2.5.0 — Extension Framework & Integration Tests

### Added
- **Plugin API** (`plugin_api.py`, 265 lines) — Extension framework with PluginRegistry and PluginLoader. 7 extension points: ATS handlers, job platforms, resume templates, archetypes, scorers, notifiers, lifecycle hooks. Auto-discovers plugins in `plugins/` directory. Creates example plugin on first run.
- **Integration Tests** (`tests/test_integration.py`, 34 tests) — End-to-end pipeline tests: all 47+ tables created, full application records with match scores, CSV export verification, dedup cross-platform, config validation, plugin API, metrics, checkpoint, rate limiter.
- **plugins/** directory with example plugin template

### Stats
- 75 Python files, 26,814 lines of code, 199 tests (165 unit + 34 integration)

---

## v2.4.0 — Novel Intelligence (unique features)

### Added

**8 Novel Features (4,710 lines) — capabilities no other job tool has**

- **Interview Simulator** (`interview_simulator.py`, 461 lines) — Multi-turn conversational mock interviews. Generates role-specific questions (2 behavioral, 2 technical, 1 situational, 1 culture-fit). Scores responses 1-10 on relevance, specificity, structure, and impact. Asks follow-up probes. Tracks improvement across sessions.

- **Offer War Room** (`offer_war_room.py`, 560 lines) — Multi-offer comparison with 6-dimension weighted scoring (total comp 30%, growth 20%, culture 15%, location 15%, visa 10%, work-life 10%). 5-year earnings projections with estimated raises by company type. Per-offer negotiation playbooks with leverage analysis, counter-offer scripts, and walk-away numbers.

- **Application Forensics** (`application_forensics.py`, 542 lines) — Post-mortem pattern analysis across all applications. Analyzes by company type, resume style, timing, match score, recruiter messaging, JD keywords, location, and salary range. AI synthesizes findings into actionable recommendations: "You get 3x more callbacks when your resume leads with metrics."

- **Ghost Predictor** (`ghost_predictor.py`, 450 lines) — Predicts ghost probability (0.0-1.0) before applying. Factors: company historical ghost rate, posting age, JD quality/specificity score, salary transparency, recruiter identification, market saturation. Weighted logistic combination calibrated against actuals.

- **Market Pulse** (`market_pulse.py`, 646 lines) — Real-time job market intelligence. Captures weekly snapshots: posting volume trends, salary trajectories, new company detection, demand heatmaps (role × location). Generates weekly market briefs. Detects layoff signals (reduced postings). Identifies emerging role titles.

- **Employer SLA Tracker** (`employer_sla_tracker.py`, 569 lines) — Tracks response time per company per pipeline stage (avg/min/max days). Predicts expected response dates. Flags overdue applications. Ranks fastest and slowest companies. Auto-learns from response_tracking data.

- **Quality Gate** (`quality_gate.py`, 688 lines) — Pre-submission application quality scoring. Resume-JD keyword overlap %, cover letter specificity/relevance/authenticity, form completeness, match score integration. Detects issues ("Resume doesn't mention Python"). Configurable threshold — warn or block weak applications.

- **Career Path Simulator** (`career_simulator.py`, 619 lines) — Models 5-year career trajectories from competing offers. Projects: title progression, salary trajectory with company-type raises (startup 5-15%, big tech 8-12%, finance 8-20%), equity vesting, promotion probability, skill growth, visa timeline. Side-by-side path comparison with AI recommendation.

**Schema:** 8 new SQLite tables (interview_sessions, offer_comparisons, offers, forensics_reports, ghost_predictions, market_snapshots, employer_sla, quality_scores, career_simulations)

### Stats
- 73 Python files, 25,993 lines of code, 165 tests, 47+ DB tables, 52 features

---

## v2.3.0 — Career Intelligence (from career-ops)

### Added

**Career Intelligence Modules (8 new, 3,457 lines)**
- **Job Evaluator** (`job_evaluator.py`, 416 lines) — Structured A-F evaluation: role summary, CV match with gap mitigation strategy, level strategy ("sell senior without lying"), comp research, top 5 CV/LinkedIn changes, STAR+R interview plan.
- **Story Bank** (`story_bank.py`, 504 lines) — Persistent STAR+Reflection story accumulator. Extracts stories from evaluations, deduplicates, finds best stories for specific interview questions, generates "tell me about yourself" narratives.
- **Archetype Classifier** (`archetype_classifier.py`, 445 lines) — Classifies jobs into 11 archetypes (backend, frontend, fullstack, data engineer, data scientist, devops, PM, eng manager, AI/ML, security, risk/finance). Keyword + AI classification with confidence scores.
- **Portfolio Evaluator** (`portfolio_evaluator.py`, 379 lines) — Scores project ideas on 6 weighted dimensions (signal 25%, uniqueness 20%, demo-ability 20%, metrics 15%, time-to-MVP 10%, STAR potential 10%). BUILD/SKIP/PIVOT verdicts with 2-week plans.
- **Training Evaluator** (`training_evaluator.py`, 384 lines) — Scores courses/certifications on 6 dimensions (alignment, recruiter signal, time/effort, opportunity cost, risks, portfolio output). TAKE/SKIP/TIMEBOX verdicts with weekly deliverables.
- **Deep Researcher** (`deep_research.py`, 416 lines) — 6-axis company research: AI strategy, recent moves, engineering culture, probable challenges, competitors, candidate angle. Each axis gets a separate AI call for depth.
- **CV Template Engine** (`cv_template_engine.py`, 531 lines) — ATS-optimized HTML→PDF CV generation. Embedded professional template. Keyword injection from JD. Playwright/weasyprint/fpdf2 fallback chain for PDF rendering.
- **Pipeline Manager** (`pipeline_manager.py`, 382 lines) — Formal state machine: discovered → evaluated → queued → applied → responded → interviewing → offer → accepted/rejected/withdrawn/ghosted. Enforced transitions, auto-ghosting, priority queue.

**Schema:** 7 new SQLite tables (job_evaluations, story_bank, job_archetypes, portfolio_projects, training_evaluations, deep_research, pipeline_states)

**Config:** 8 new sections with full documentation

### Stats
- 65 Python files, 21,283 lines of code, 165 tests, 39 database tables, 44 features

---

## v2.2.0 — Production Hardening

### Added

**Test Suite (165 tests)**
- `tests/test_state.py` — 32 tables, CRUD operations, migration, CSV export, salary benchmarks
- `tests/test_match_scorer.py` — JSON parsing, score bounds, threshold logic, mock AI
- `tests/test_salary_intel.py` — 10+ currency formats, AUD/CAD/SGD multi-char symbol fix
- `tests/test_dedup_engine.py` — Fingerprinting, cross-platform dedup, company/title normalization
- `tests/test_apply_timing.py` — Freshness scoring, queue reordering, posted time parsing
- `tests/test_jd_change_tracker.py` — Snapshot capture, change detection, salary change tracking
- `tests/test_validate_config.py` — Missing sections, conflicting settings, numeric bounds
- `tests/run_tests.py` — Test runner

**Crash Recovery**
- **Checkpoint Manager** (`checkpoint_manager.py`) — Saves cycle state every N jobs (search term, location, job index, seen IDs). Resumes after crash. Auto-discards stale checkpoints (>2h). Atomic writes via tmp+rename.

**Dynamic Rate Limiting**
- **Rate Limiter** (`rate_limiter.py`) — Detects ban signals (CAPTCHAs, "unusual activity", 429s, throttle redirects). 5-level escalation (normal → cautious → slow → very_slow → paused). Exponential cooldowns (5-60min). Page load anomaly detection. Error rate monitoring. Gradual deescalation.

**Configuration Validation**
- **Config Validator** (`validate_config.py`) — Validates 11 config areas on startup: credentials, search terms, AI provider, scheduling limits, numeric ranges, file paths, feature dependencies, conflicting settings. Errors vs warnings separation.

**Observability**
- **Prometheus Metrics** (`metrics.py`) — Exports at `/metrics` in Prometheus text format. Counters (applications, skips, errors), gauges (daily applied, match scores), histograms with p50/p95/p99 (cycle duration, AI latency).

### Fixed
- AUD/CAD/SGD/HKD currency detection — multi-char symbols (A$, C$, S$, HK$) now checked before single-char ($)
- JD salary changes now detected even when overall text similarity is above threshold
- Test expectation for 25h freshness score corrected (0.3, not 0.5)
- JD tracker test column name alignment (snapshot_hash)

### Stats
- 57 Python files, 17,663 lines of code, 165 tests, 32 database tables

---

## v2.1.0 — Intelligence & Automation Expansion

### Added

**Application Lifecycle Management**
- **Application Withdrawal** (`application_withdrawal.py`) — Auto-withdraws pending applications when an offer is received. Configurable exclusion list and dry-run mode.
- **JD Change Tracker** (`jd_change_tracker.py`) — Snapshots job descriptions at apply time and periodically diffs them. Detects salary changes, requirement edits, and description rewrites. Alerts on significant modifications.
- **ATS Status Scraper** (`status_scraper.py`) — Logs into Greenhouse, Workday, and Lever candidate portals and scrapes real-time application status updates.

**Smart Scheduling & Deduplication**
- **Apply Scheduler** (`apply_scheduler.py`) — Time-of-day optimized apply queue. Jobs discovered outside the 6-10am peak window are queued and batch-applied for maximum recruiter visibility (3x more views).
- **Dedup Engine** (`dedup_engine.py`) — Cross-platform duplicate job detection using fuzzy fingerprinting on title + company + location. Prevents redundant applications across LinkedIn, Google Jobs, Indeed, and Glassdoor.

**Recruiter & Referral Tools**
- **Recruiter CRM** (`recruiter_crm.py`) — Full relationship scoring CRM with interaction history, follow-up reminders, and engagement tracking per recruiter.
- **Referral Automator** (`referral_automator.py`) — Scans 1st-degree LinkedIn connections at target companies and auto-drafts personalized referral request messages. Daily caps and per-person cooldowns.

**Salary & Negotiation**
- **Salary Negotiation** (`salary_negotiation.py`) — Auto-generates negotiation briefs with market rate comparisons, anonymized competing offer data, and suggested counter ranges.

**Tracking & Internationalization**
- **Job Watchlist** (`job_watchlist.py`) — Smart bookmarking with configurable reminders. Auto-checks if watched jobs are still active and alerts on expiry.
- **Multi-Language Support** (`multi_language.py`) — Detects JD language and translates tailored resume/cover letter into 10 supported languages via AI or DeepL.

**Dashboard Overhaul**
- **All-in-One Dashboard** (`dashboard.py`) — Complete rewrite as a command center with 9 tabs: Overview, Applications, Recruiters, Salary, Skills, Interview Prep, Watchlist, Analytics, and System. New API endpoints: `/api/skills`, `/api/watchlist`, `/api/salary/top`.

**Database Extensions**
- 8 new SQLite tables: `dedup_fingerprints`, `jd_snapshots`, `recruiter_interactions`, `recruiter_scores`, `apply_queue`, `negotiation_briefs`, `ats_statuses`, `job_watchlist`
- Total tables: 21 (up from 13)

**Stats**
- 44 Python files, 15,282 lines of code, 36 features total

---

## v2.0.0 — Major Feature Release

### Added

**AI-Powered Application Intelligence**
- **Match Scoring Engine** (`match_scorer.py`) — AI scores every job 0-100% against your CV before applying. Configurable minimum threshold (default 70%). Scores exported in CSVs.
- **Resume Tailoring** (`resume_tailor.py`) — AI generates custom PDF/DOCX resumes per job, emphasizing skills that match each specific JD. Uploaded automatically during Easy Apply.
- **Interview Prep Generator** (`interview_prep.py`) — After each application, generates company research, 8-10 likely interview questions, and talking points mapped to the JD.

**Recruiter Engagement**
- **Auto Recruiter Messaging** (`recruiter_messenger.py`) — Queues personalized AI-generated LinkedIn messages to hiring managers with configurable delay (default 2 hours). Daily caps and message scheduling.

**Multi-Platform Job Discovery**
- **Google Jobs Scraper** (`google_jobs_scraper.py`) — Discovers jobs across all platforms via Google Jobs search. Three scraping modes: Selenium, SerpAPI, or requests+BS4. LinkedIn-linked results processed directly; ATS results handed to external applier.
- **External ATS Apply** (`external_apply.py`) — Fills application forms on Greenhouse, Lever, Workday, and Ashby using keyword matching + AI. Multi-page form support.
- **Platform Plugins** (`platform_plugins/`) — Abstract `JobPlatform` interface with implementations for LinkedIn, Indeed, and Glassdoor.

**Monitoring & Alerts**
- **Real-time Dashboard** (`dashboard.py`) — Flask web dashboard with live stats, application funnel, recruiter directory, visa sponsors. Dark theme, responsive design, auto-refresh.
- **Telegram/Discord/Slack Alerts** (`alerts.py`) — Instant notifications per application, error alerts, configurable daily summaries.

**Data Intelligence**
- **Salary Intelligence** (`salary_intel.py`) — Parses salary data from job postings across 10+ currency formats (USD, GBP, EUR, INR LPA, JPY, AUD, etc.). Builds benchmarks by role and location.
- **Success Tracking** (`success_tracker.py`) — Logistic regression trained on historical application data. 9-feature model predicts response probability. Correlation analysis by match score, messaging, resume, visa, day of week.
- **Smart Scheduling** (`smart_scheduler.py`) — Learns optimal scan times from posting patterns. Wilson score ranking for search terms. Company priority scoring. Market activity detection.

**Stealth & Infrastructure**
- **Activity Simulation** (`activity_sim.py`) — Between apply cycles, scrolls LinkedIn feed, likes posts, views profiles. Configurable action types and count.
- **Proxy Rotation** (`proxy_manager.py`) — Health-scored proxy rotation with exponential backoff, sticky sessions, auto-banning, latency tracking. Persists health data to disk.
- **Docker Deployment** (`docker/`) — Dockerfile with headless Chrome, docker-compose with volume mounts, health check endpoint.
- **SaaS Web App** (`webapp/`) — Flask app with password auth, CSRF protection, paginated job browser, salary benchmarks, interview prep viewer, REST API.

**Database Extensions**
- 7 new SQLite tables: `match_scores`, `message_queue`, `salary_data`, `interview_prep`, `google_jobs`, `response_tracking`, `hiring_velocity`
- New columns on `applied_jobs`: `match_score`, `resume_version`
- New column on `skipped_jobs`: `match_score`
- Automatic schema migration for existing databases

**Documentation**
- Complete README rewrite with full feature documentation
- `docs/CONFIGURATION.md` — Comprehensive config reference for all 25+ sections
- `docs/ARCHITECTURE.md` — System design, data flow diagrams, module dependencies, database schema
- `docs/DEPLOYMENT.md` — Local, Docker, cloud VPS, proxy, and alert setup guides
- `docs/API.md` — Dashboard and web app API reference with examples
- `CHANGELOG.md` — This file

### Changed
- `state.py` — Extended from 6 to 13 tables with migration support. New methods for match scores, message queue, salary data, interview prep, Google Jobs, response tracking, hiring velocity.
- `main.py` — Integrated all 13 new modules into the orchestrator loop with graceful degradation. All features check `enabled` flags and do nothing if disabled or if dependencies are missing.
- `linkedin.py` — Added `get_external_apply_url()` for detecting external ATS links and `send_linkedin_message()` for recruiter messaging.
- `config.example.yaml` — Added 15 new configuration sections, all backward-compatible with existing configs.
- `requirements.txt` — Added fpdf2, python-docx, flask, requests, beautifulsoup4.

### Fixed
- Fixed f-string backslash syntax error in `linkedin.py` line 662 (Python 3.11 compatibility).
- Fixed INR LPA salary range parsing to capture both min and max values (e.g., "20-30 LPA" now correctly parses as 20L-30L).

---

## v1.0.0 — Initial Release

- Autonomous LinkedIn Easy Apply bot
- Multi-location search with adaptive time filters
- AI form filling with 8 LLM providers
- Recruiter tracking from "Meet the hiring team"
- Visa sponsorship detection
- SQLite persistence with CSV export
- undetected-chromedriver stealth
- Configurable scheduling, rate limiting, and filtering
