# Architecture

## System Overview

```
                                    config.yaml (hot-reloaded)
                                          |
                                      main.py
                                    Orchestrator
                                   /     |     \
                                  /      |      \
                          linkedin.py  ai.py  state.py
                         (Browser)    (LLM)  (SQLite)
                              |         |        |
              +---------------+---------+--------+------------------+
              |               |         |        |                  |
        match_scorer   resume_tailor  alerts  dashboard    google_jobs_scraper
        external_apply recruiter_msg  salary  interview    activity_sim
        smart_sched    success_track  proxy   webapp       platform_plugins
        dedup_engine   jd_change_tr   crm     watchlist    apply_scheduler
        app_withdrawal salary_negot   status  referral     multi_language
```

## Data Flow

### Per-Job Processing Pipeline

```
Job Card Discovered
    |
    v
Extract Info (title, company, location)
    |
    v
Basic Filters (blacklist, bad titles, already applied)
    |  SKIP if filtered
    v
Dedup Check (fuzzy fingerprint against cross-platform cache)
    |  SKIP if duplicate
    v
Extract Full Details (description, salary, hiring team, visa)
    |
    v
Description Filters (bad words, experience requirements)
    |  SKIP if filtered
    v
Match Scoring (AI scores 0-100%)  -----> match_scores table
    |  SKIP if below threshold
    v
Resume Tailoring (AI generates custom PDF)  -----> data/tailored_resumes/
    |
    v
Screener Gate (optional: simulate employer-side AI screen; block/warn below threshold)
    |  SKIP if blocked (screener.gate_in_run + gate: block)
    v
LaTeX Docs (optional: typeset moderncv CV; compiled PDF becomes the upload resume)
    |
    v
Apply: Easy Apply (LinkedIn) OR External ATS (12 platforms via ats_handlers/)
    |
    |--- SUCCESS ---+
    |               |
    v               v
Mark Applied    Queue Recruiter Message -----> message_queue table
    |               |
    v               v
Store Salary    Generate Interview Prep -----> interview_prep table
    |               |
    v               v
Send Alert      Track Hiring Velocity   -----> hiring_velocity table
    |
    v
JD Change Tracker (snapshot JD for future diff)  -----> jd_snapshots table
    |
    v
Add to Watchlist (if configured)  -----> job_watchlist table
    |
    v
Export CSV
```

### Cycle Flow

```
run_forever() loop:
    |
    +---> Activity Simulation (scroll feed, like posts)
    |
    +---> run_cycle():
    |       |
    |       +---> Google Jobs Scraping (discover cross-platform jobs)
    |       |       |
    |       |       +---> Process LinkedIn-linked Google jobs (direct navigate)
    |       |
    |       +---> LinkedIn Search Loop (terms x locations):
    |       |       |
    |       |       +---> Adaptive Time Filter (hour -> 2h -> 6h -> ... -> week)
    |       |       |
    |       |       +---> process_page() for each search result page
    |       |
    |       +---> Process Google-discovered ATS jobs (external apply)
    |       |
    |       +---> Export CSVs
    |
    +---> Process Recruiter Message Queue
    |
    +---> Check Daily Summary Alerts
    |
    +---> Sleep (interval +/- jitter, adjusted by smart scheduler)
```

## Module Dependency Graph

```
main.py
  ├── state.py          (SQLite persistence, no external deps)
  ├── ai.py             (LLM providers, requires: openai, anthropic)
  ├── linkedin.py       (Browser automation, requires: selenium, undetected-chromedriver)
  ├── match_scorer.py   (depends on: ai.py)
  ├── resume_tailor.py  (depends on: ai.py, optional: fpdf2, python-docx)
  ├── google_jobs_scraper.py (depends on: state.py, optional: selenium, beautifulsoup4, serpapi)
  ├── activity_sim.py   (depends on: selenium)
  ├── external_apply.py (depends on: ai.py, selenium, ats_handlers/)
  │     └── ats_handlers/  (12 ATS handlers: base + generic shapes + registry)
  ├── apply_urls.py     (standalone batch apply; depends on: external_apply, linkedin.create_browser, state)
  ├── application_docs.py (orchestrator; depends on: latex_docs, ats_pdf_check, doc_reviewer)
  │     ├── latex_docs.py      (moderncv render + compile; optional: lualatex/xelatex/pdflatex)
  │     ├── ats_pdf_check.py   (text-layer + keyword coverage; optional: pdftotext/pdfminer)
  │     ├── relevance_cutter.py (pure logic)
  │     └── doc_reviewer.py    (depends on: ai.py)
  ├── screener_sim.py   (employer-side screen simulation + gate; depends on: ai.py)
  ├── github_enrich.py  (GitHub repo classification/ranking; optional: requests)
  ├── profile_setup.py  (documents/ folder → profile text; optional: pdftotext/pdfminer)
  ├── recruiter_messenger.py (depends on: ai.py, state.py, selenium)
  ├── alerts.py         (optional: requests)
  ├── dashboard.py      (depends on: state.py, optional: flask)
  ├── salary_intel.py   (depends on: state.py)
  ├── interview_prep.py (depends on: ai.py)
  ├── success_tracker.py (depends on: state.py)
  ├── smart_scheduler.py (depends on: state.py)
  ├── proxy_manager.py  (standalone, optional: requests)
  ├── application_withdrawal.py (depends on: state.py)
  ├── dedup_engine.py   (depends on: state.py)
  ├── jd_change_tracker.py (depends on: state.py, ai.py)
  ├── recruiter_crm.py  (depends on: state.py)
  ├── apply_scheduler.py (depends on: state.py)
  ├── salary_negotiation.py (depends on: state.py, ai.py)
  ├── status_scraper.py (depends on: selenium, state.py)
  ├── job_watchlist.py  (depends on: state.py, selenium)
  ├── referral_automator.py (depends on: ai.py, state.py, selenium)
  ├── multi_language.py (depends on: ai.py, optional: deepl)
  ├── checkpoint_manager.py (standalone, saves to data/checkpoint.json)
  ├── rate_limiter.py  (standalone, monitors driver for ban signals)
  ├── validate_config.py (standalone, runs on startup)
  └── metrics.py       (depends on: state.py, optional: flask)
```

Every module except the 4 core files (main, linkedin, ai, state) is imported with `try/except` and degrades gracefully if missing or disabled.

## ATS Handler Framework

`external_apply.py` is a thin orchestrator (tab management, per-cycle caps, ATS
detection). The actual form filling lives in the `ats_handlers/` package, a
plugin registry that maps an application URL to a platform-specific handler.

```
ats_handlers/
  base.py        ATSHandler — shared primitives (fill_text, select_custom_dropdown,
                 click_button, run_multistep, sweep_page, keyword_match, upload_file…)
  generic.py     SinglePageHandler / MultiStepHandler / AccountMixin / GenericHandler
  handlers.py    11 thin per-platform subclasses (Greenhouse, Lever, iCIMS, Taleo…)
  workday.py     WorkdayHandler — account flow + multi-page wizard, data-automation-id
  __init__.py    registry: detect_ats(url), get_handler(name), handler_for_url(url)
```

Design principles:

- **Stable selectors over fragile ones.** Workday exposes the same
  `data-automation-id` attributes across every company tenant, so one handler
  drives all of them. The base targets those before falling back to labels.
- **Two shapes cover most ATSes.** `SinglePageHandler` (fill once → submit) and
  `MultiStepHandler` (optional login → Next/Submit wizard loop). A new platform
  is usually a ~5-line subclass plus a URL pattern.
- **React-friendly.** `fill_text` dispatches `input`/`change` events; dropdowns
  are opened as ARIA listboxes, not treated as native `<select>`.
- **Login-gated platforms** (Workday, iCIMS, Taleo, SuccessFactors, ADP) reuse a
  single email+password from `external_apply.ats_accounts`, creating the account
  on first visit and signing in thereafter. Missing credentials → skip, not fail.

```
apply_url ──> detect_ats() ──> get_handler() ──> handler.apply(driver, ctx, resume)
                                                        │
                        ┌───────────────────────────────┼───────────────────────────┐
                   SinglePage                       Workday                      MultiStep
                (sweep → submit)          (auth → per-page wizard)      (login → Next/Submit loop)
```

## Database Schema

SQLite database at `data/state.db` with 48 tables:

### Core Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `applied_jobs` | All successful applications | job_id (PK), title, company, location, salary_info, match_score, resume_version, applied_at |
| `skipped_jobs` | Jobs skipped with reason | job_id, reason, match_score, skipped_at |
| `failed_jobs` | Applications that errored | job_id, reason, failed_at |
| `daily_stats` | Daily counters | date (PK), applied, skipped, failed, cycles |

### Tracking Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `recruiters` | Hiring team members | name, title, company, profile_url, UNIQUE(name, company, job_id) |
| `visa_sponsors` | Confirmed visa sponsors | company (PK), evidence, times_seen |
| `match_scores` | AI match scoring results | job_id (PK), score, skill_matches, missing_skills, explanation |
| `salary_data` | Parsed salary information | job_id, salary_min, salary_max, currency, period |

### Feature Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `message_queue` | Scheduled recruiter messages | job_id, recruiter_name, message_text, scheduled_at, status |
| `interview_prep` | Generated prep materials | job_id (PK), company_research, likely_questions, talking_points |
| `google_jobs` | Google Jobs discoveries | google_job_id (PK), source_url, source_platform, linkedin_job_id, status |
| `response_tracking` | Application outcomes | job_id, response_type, match_score, recruiter_messaged, days_to_response |
| `hiring_velocity` | Company hiring speed | company + title_pattern (PK), days_active, filled |

### Lifecycle & Intelligence Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `job_fingerprints` | Fuzzy fingerprint cache for cross-platform dedup | fingerprint (PK), job_id, platform, title, company, location, times_seen |
| `jd_snapshots` | JD version history for change tracking | id, job_id, snapshot_hash, description, salary_info, captured_at |
| `jd_changes` | Detected JD edits | id, job_id, change_type, old_value, new_value, detected_at |
| `recruiter_interactions` | CRM interaction log | id, recruiter_name, company, interaction_type, job_id, notes, occurred_at |
| `recruiter_scores` | Computed relationship scores | recruiter_name + company (PK), relationship_score, interactions, responses, last_interaction |
| `apply_schedule` | Time-optimized application queue | id, job_id, title, company, optimal_time, timezone, status, created_at |
| `negotiation_briefs` | Generated salary negotiation briefs | job_id (PK), company, title, market_rate, company_range, leverage_points, counter_offer |
| `ats_status` | Scraped ATS portal statuses | id, job_id, company, portal_url, current_status, previous_status, last_checked |
| `job_watchlist` | Bookmarked jobs with activity tracking | id, job_id, title, company, match_score, reason, remind_at, status, still_active |
| `withdrawal_queue` | Scheduled application withdrawals | id, job_id, company, title, reason, status, scheduled_at, withdrawn_at |
| `offers` / `offer_comparisons` | Offer war-room data | job_id (PK), base_salary, bonus, equity, visa_support, deadline |
| `interview_sessions` | Mock interview transcripts | id, job_id, archetype, questions_asked, responses, scores, overall_score |
| `ghost_predictions` | Pre-apply ghost-risk scores | job_id (PK), ghost_probability, risk_factors, predicted_at |
| `market_snapshots` | Periodic market-pulse data | id, role_pattern, location, posting_count, trend, snapshot_at |
| `employer_sla` | Per-company response SLAs | company + stage (PK), avg_days, min_days, max_days, sample_size |
| `quality_scores` | Pre-submit application quality | job_id (PK), resume_match_pct, cover_letter_score, overall_quality |
| `career_simulations` | Career-path projections | id, simulation_name, current_role, paths, recommendation |
| `job_evaluations` / `story_bank` / `job_archetypes` | A-F eval, STAR stories, role classification | job_id (PK) / id / job_id (PK) |
| `portfolio_projects` / `training_evaluations` | Project & course ROI scoring | id, total_score, verdict, plan |
| `deep_research` | 6-axis company research | job_id (PK), ai_strategy, recent_moves, eng_culture, candidate_angle |
| `pipeline_states` | Application lifecycle state machine | job_id (PK), current_state, previous_state, state_history, priority |
| `referral_requests` | Drafted referral messages | id, job_id, company, connection_name, message_text, status |
| `company_connections` | 1st/2nd-degree network at companies | id, company, connection_name, degree, job_id |
| `company_intel` | Enriched company data | company (PK), glassdoor_rating, company_size, industry |
| `email_responses` | IMAP-detected responses | id, job_id, company, response_type, received_at |
| `skill_frequency` | Skill demand across all JDs | skill (PK), times_seen, times_matched |
| `profile_suggestions` | LinkedIn profile optimization tips | id, section, suggestion, keyword, frequency |

### Schema Migrations

New columns are added automatically via `_migrate_tables()` on startup. Existing databases are upgraded without data loss:

```python
# Example: adds match_score to applied_jobs if it doesn't exist
ALTER TABLE applied_jobs ADD COLUMN match_score INTEGER DEFAULT 0
```

## AI Architecture

### Provider Abstraction

All LLM providers use the OpenAI-compatible API format:

```
AIAnswerer
  ├── OpenAI client (OpenAI, Groq, Together, DeepSeek, Ollama, LM Studio)
  └── Anthropic native client (Claude)
```

### AI Call Priority

```
Form question received
    |
    v
1. Check answer cache (SQLite)  -----> HIT: return cached answer
    |  MISS
    v
2. Keyword matching (config.yaml question_answers)  -----> MATCH: return value
    |  NO MATCH
    v
3. Call primary LLM  -----> SUCCESS: cache + return
    |  FAIL
    v
4. Call fallback LLM  -----> SUCCESS: cache + return
    |  FAIL
    v
5. Return empty string (field left blank)
```

### AI Usage by Module

| Module | AI Operations | Typical Tokens |
|--------|--------------|----------------|
| Form filling | Answer questions, generate cover letters | 50-300 per question |
| Match scoring | Score job-CV fit with structured JSON | 200-500 per job |
| Resume tailoring | Rewrite CV sections for specific JD | 1000-2000 per job |
| Recruiter messaging | Generate personalized message | 100-300 per message |
| Interview prep | Company research + questions + talking points | 500-800 per job |
| Skill extraction | Extract key skills from JD | 100-200 per job |

## Security Considerations

- **Credentials:** `config.yaml` is gitignored. Never commit API keys or passwords.
- **Web app:** Protected by password auth + CSRF tokens. Default password must be changed.
- **Proxy auth:** Supports user:pass@host:port format. Credentials stored in config only.
- **Browser profile:** `user_data_dir` contains session cookies. Protect this directory.
- **SQLite:** Contains job descriptions and personal data. Not encrypted at rest.
- **Rate limiting:** Built-in daily caps, cycle caps, and randomized delays to avoid account flags.
