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
Apply: Easy Apply (LinkedIn) OR External ATS (Greenhouse/Lever/Workday)
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
  ├── external_apply.py (depends on: ai.py, selenium)
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

## Database Schema

SQLite database at `data/state.db` with 32 tables:

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

### New Feature Tables (v2.1)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `dedup_fingerprints` | Fuzzy fingerprint cache for cross-platform dedup | fingerprint_hash (PK), title, company, platform, created_at |
| `jd_snapshots` | JD version history for change tracking | job_id, snapshot_text, captured_at, diff_from_previous |
| `recruiter_interactions` | CRM interaction log | recruiter_id, interaction_type, notes, timestamp, score_delta |
| `recruiter_scores` | Computed relationship scores | recruiter_id (PK), total_score, last_interaction, follow_up_due |
| `apply_queue` | Time-optimized application queue | job_id (PK), queued_at, scheduled_for, priority_score, status |
| `negotiation_briefs` | Generated salary negotiation documents | job_id (PK), market_median, counter_suggestion, brief_path |
| `ats_statuses` | Scraped ATS portal statuses | job_id, portal, status, last_checked, status_changed_at |
| `job_watchlist` | Bookmarked jobs with activity tracking | job_id (PK), added_at, last_checked, is_active, next_reminder |

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
