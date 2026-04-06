# LinkedIn Lightning Applier

Autonomous job application engine. Searches LinkedIn every 10 minutes, applies the moment jobs appear, tailors your resume per job using AI, scores job-candidate fit, messages recruiters, scrapes Google Jobs for cross-platform discovery, fills external ATS forms (Greenhouse, Lever, Workday), tracks everything in SQLite, and serves a real-time monitoring dashboard — all running 24/7.

Built because the difference between "applied 2 minutes after posting" and "applied 24 hours later" is the difference between getting an interview and getting buried under 500 applicants.

## What It Does

The bot runs in a continuous loop. Every cycle:

1. **Discovers jobs** — Searches LinkedIn across all your configured terms and locations. Optionally scrapes Google Jobs for cross-platform coverage (Indeed, Glassdoor, company sites).
2. **Scores every job** — AI compares the job description against your CV and scores the match 0-100%. Jobs below your threshold (e.g. 70%) are skipped automatically.
3. **Tailors your resume** — For jobs above the threshold, AI generates a custom PDF resume emphasizing the skills that match this specific job description.
4. **Applies** — Clicks Easy Apply (LinkedIn) or fills external ATS forms (Greenhouse, Lever, Workday, Ashby). Keyword matching handles 90% of form fields for free; AI fills the rest.
5. **Messages recruiters** — After applying, queues a personalized LinkedIn message to the hiring manager with a configurable delay (e.g. 2 hours).
6. **Generates interview prep** — Company research, likely interview questions, and talking points mapped to the JD — saved per job.
7. **Tracks everything** — Applied/skipped/failed jobs, recruiter directory, visa sponsors, salary benchmarks, match scores, response tracking with ML prediction.
8. **Sends alerts** — Telegram, Discord, or Slack notifications on every application, errors, and daily summaries.
9. **Simulates human activity** — Between cycles, scrolls the LinkedIn feed, likes posts, views profiles to keep the account looking natural.
10. **Exports data** — Auto-exports 7 CSV files and serves a live web dashboard.

## Features

### Tier 1 — Core Engine
- **AI Match Scoring** — Scores jobs 0-100% before applying. Trained logistic regression predicts response probability.
- **AI Resume Tailoring** — Generates custom PDF/DOCX resumes per job using your master CV + the JD. Uploads automatically.
- **Auto Recruiter Messaging** — AI-generated personalized messages sent via LinkedIn messaging with configurable delay.
- **External ATS Apply** — Fills Greenhouse, Lever, Workday, and Ashby application forms using AI.
- **Google Jobs Scraping** — Discovers jobs across all platforms via Google Jobs. LinkedIn-linked results processed directly; ATS results handed to the external applier.

### Tier 2 — Intelligence & Monitoring
- **All-in-One Dashboard** — Complete command center with 9 tabs (Overview, Applications, Recruiters, Salary, Skills, Interview Prep, Watchlist, Analytics, System). Flask web app at `http://localhost:5000`.
- **Telegram/Discord/Slack Alerts** — Instant notifications per application. Daily summary at configurable time. Error alerts.
- **LinkedIn Activity Simulation** — Scroll feed, like posts, view profiles between apply cycles. Configurable action count.
- **Salary Intelligence** — Parses salary data from every job (supports USD, GBP, EUR, INR LPA, and more). Builds benchmarks by role and location.
- **Interview Prep Generator** — Company research, 8-10 likely questions, talking points mapped to requirements. Saved per job in the database.
- **Success Tracking** — Logistic regression trained on your data. Correlates response rates with match score, recruiter messaging, resume tailoring, visa status, day of week.
- **Smart Scheduling** — Learns optimal scan times from posting patterns. Prioritizes fast-hiring companies. Wilson score ranking for search terms.
- **Application Withdrawal** — Auto-withdraws pending applications when an offer is received. Keeps your pipeline clean.
- **Dedup Engine** — Cross-platform duplicate job detection via fuzzy fingerprinting. Prevents applying to the same job twice across LinkedIn, Indeed, and Google Jobs.
- **JD Change Tracker** — Monitors job descriptions after applying for edits (salary changes, requirement changes). Alerts on significant modifications.
- **Recruiter CRM** — Relationship scoring CRM with full interaction history, follow-up reminders, and engagement tracking per recruiter.
- **Apply Scheduler** — Time-of-day optimized apply queue. Studies show 6-10am applications get 3x more views; the scheduler batches accordingly.
- **Salary Negotiation** — Auto-generates negotiation briefs with market rate data, competing offer context, and suggested counter ranges.
- **ATS Status Scraper** — Scrapes Greenhouse, Workday, and Lever applicant portals for real-time application status updates.
- **Job Watchlist** — Smart bookmarking with reminders. Auto-checks if bookmarked jobs are still active and alerts on changes.
- **Referral Automator** — Auto-drafts referral request messages for 1st-degree LinkedIn connections at target companies.
- **Multi-Language Support** — Detects JD language and translates resume/cover letter into 10 supported languages.

### Tier 3 — Scale & Platform
- **Multi-Platform Plugins** — Abstract `JobPlatform` interface with LinkedIn, Indeed, and Glassdoor implementations. Extensible to any platform.
- **Proxy Rotation** — Health-scored proxy rotation with exponential backoff, sticky sessions, and auto-banning. Persists proxy health to disk.
- **Docker Deployment** — Dockerfile with headless Chrome, docker-compose with volume mounts, health check endpoint.
- **SaaS Web App** — Flask app with authentication, CSRF protection, job search, salary benchmarks, interview prep viewer.

### Novel Intelligence (8 — unique to this tool)
- **Interview Simulator** (`interview_simulator.py`) — Conversational AI mock interviews. Multi-turn Q&A with scoring, follow-up probes, improvement tracking across sessions.
- **Offer War Room** (`offer_war_room.py`) — Multi-offer comparison matrix. 6-dimension scoring weighted by priorities. 5-year comp projections. Per-offer negotiation playbooks.
- **Application Forensics** (`application_forensics.py`) — Pattern analysis across hundreds of applications. Finds hidden correlations: which resume styles, timing, keywords, company types get callbacks.
- **Ghost Predictor** (`ghost_predictor.py`) — Predicts ghost probability (0-1) before applying. Factors: company history, posting age, JD quality, salary transparency, recruiter presence.
- **Market Pulse** (`market_pulse.py`) — Real-time job market intelligence. Posting trends, salary trajectories, new company detection, demand heatmaps, weekly market briefs.
- **Employer SLA Tracker** (`employer_sla_tracker.py`) — Tracks response time per company per stage. Predicts when to expect responses. Flags overdue applications.
- **Quality Gate** (`quality_gate.py`) — Scores application quality before submitting. Resume-JD match %, cover letter specificity, form completeness. Blocks weak applications.
- **Career Path Simulator** (`career_simulator.py`) — Models 5-year career trajectories from competing offers. Comp projections, promotion timelines, skill growth, risk assessment.

### Career Intelligence (8 — from career-ops)
- **A-F Job Evaluation** (`job_evaluator.py`) — 6-block structured evaluation: role summary, CV match with gap mitigation, level strategy, comp research, personalization plan, STAR+R interview prep.
- **Interview Story Bank** (`story_bank.py`) — Accumulates STAR+Reflection stories across all evaluations. 5-10 master stories that answer any behavioral question.
- **Role Archetype Classifier** (`archetype_classifier.py`) — Classifies jobs into archetypes (backend, frontend, data, devops, PM, etc.). Changes which skills to emphasize.
- **Portfolio Project Evaluator** (`portfolio_evaluator.py`) — Scores project ideas on 6 dimensions (signal, uniqueness, demo-ability, metrics, time-to-MVP, STAR potential). BUILD/SKIP/PIVOT verdicts.
- **Training/Cert Evaluator** (`training_evaluator.py`) — Scores courses on alignment, recruiter signal, time/effort, opportunity cost, risks, portfolio output. TAKE/SKIP/TIMEBOX verdicts.
- **Deep Company Research** (`deep_research.py`) — 6-axis research: AI strategy, recent moves, eng culture, challenges, competitors, candidate angle.
- **ATS CV Template Engine** (`cv_template_engine.py`) — ATS-optimized HTML→PDF CV generation with keyword injection from JD.
- **Pipeline State Machine** (`pipeline_manager.py`) — Formal lifecycle states (discovered → evaluated → applied → interviewing → offer) with enforced transitions.

### Core Foundations
- **AI Form Filling** — 8 LLM providers: OpenAI, Anthropic Claude, Google Gemini, DeepSeek, Groq, Together, Ollama (local), LM Studio (local). Answers cached in SQLite.
- **Recruiter Tracking** — Names, titles, and LinkedIn URLs from "Meet the hiring team" sections.
- **Visa Detection** — Positive/negative keyword matching for sponsorship signals.
- **Ban Prevention** — undetected-chromedriver, daily/cycle caps, randomized delays, active hours, human-like scrolling.
- **Hot-Reload Config** — Edit `config.yaml` while running; changes apply next cycle.

> **Disclaimer:** This software automates interactions with LinkedIn and other platforms, which may violate their Terms of Service. **Use at your own risk.** See [DISCLAIMER.md](DISCLAIMER.md) and [TERMS_OF_USE.md](TERMS_OF_USE.md) before using.

## Quick Start

> **Full guide:** See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for the complete step-by-step walkthrough covering Chrome setup, AI provider selection, all config options, Docker deployment, alert setup, and troubleshooting.

```bash
git clone https://github.com/ankitjha67/linkedin-lightning-applier.git
cd linkedin-lightning-applier
pip install -r requirements.txt

# Copy and fill in your details
cp config.example.yaml config.yaml
nano config.yaml   # Fill email, password, search terms, personal info

# Run
python main.py
```

### Requirements

- Python 3.10+
- Google Chrome (stable channel)
- `pip install -r requirements.txt`

For AI features (match scoring, resume tailoring, form filling):
- [Ollama](https://ollama.ai) with any model (`ollama pull llama3.1`) — free, local
- OR [LM Studio](https://lmstudio.ai) — free, local
- OR any OpenAI-compatible API (OpenAI, Anthropic, Gemini, DeepSeek, Groq, Together)

For the dashboard: Flask is included in requirements.txt. Dashboard runs at `http://localhost:5000` by default.

### Docker Deployment

```bash
# Build and run with Docker Compose
cp config.yaml docker/  # Place your config
cd docker
docker-compose up -d

# Dashboard available at http://localhost:5000
# Health check at http://localhost:8080/health
```

## Configuration

All settings live in `config.yaml` (gitignored). Copy `config.example.yaml` and fill in your details. The bot hot-reloads config every cycle.

### Essential Settings

```yaml
linkedin:
  email: "you@example.com"
  password: "your-password"

search:
  search_terms: ["Software Engineer", "Backend Developer"]
  search_locations: ["London, United Kingdom", "New York, NY"]
  date_posted: "Past hour"    # Widens automatically if no results

ai:
  enabled: true
  provider: "ollama"           # or lmstudio, openai, anthropic, gemini, deepseek, groq, together
  model: "llama3.1"
  cv_text: |
    YOUR CV TEXT HERE...
```

### Feature Toggles

Every feature has an `enabled: true/false` flag. All are independent and degrade gracefully:

```yaml
match_scoring:
  enabled: true
  minimum_score: 70            # Skip jobs below this match %

resume_tailoring:
  enabled: true
  output_dir: "data/tailored_resumes"
  format: "pdf"                # pdf, docx, or txt

recruiter_messaging:
  enabled: true
  delay_minutes: 120           # Wait 2 hours after applying
  max_messages_per_day: 10

external_apply:
  enabled: true
  supported_ats: ["greenhouse", "lever", "workday", "ashby"]

google_jobs:
  enabled: true
  country_code: "uk"
  date_posted: "today"

dashboard:
  enabled: true
  port: 5000

alerts:
  enabled: false
  telegram: { enabled: false, bot_token: "", chat_id: "" }
  discord: { enabled: false, webhook_url: "" }
  slack: { enabled: false, webhook_url: "" }

activity_simulation:
  enabled: true
  actions_per_cycle: 5
```

See `config.example.yaml` for the complete reference with all options documented.

## Output

The `data/` folder (auto-created) contains:

| File | Contents |
|---|---|
| `applied_jobs.csv` | Every application — title, company, salary, recruiter, visa, match score, resume version |
| `skipped_jobs.csv` | Every skipped job with reason and match score |
| `recruiters.csv` | Hiring team — name, title, company, LinkedIn URL |
| `visa_sponsors.csv` | Companies confirmed to sponsor visas |
| `match_scores.csv` | AI match scores with skill matches and gaps |
| `salary_data.csv` | Parsed salary data with min/max/currency |
| `interview_prep.csv` | Company research, likely questions, talking points |
| `state.db` | SQLite database (all of the above, queryable) |
| `tailored_resumes/` | AI-generated custom resumes per job (PDF/DOCX) |
| `logs/` | Daily log files |

## Architecture

```text
main.py                 Orchestrator — scheduling, filtering, feature integration
linkedin.py             Browser — login, search, Easy Apply, recruiter messaging
ai.py                   Multi-provider LLM — answers, cover letters, skill extraction
state.py                SQLite — 13 tables, migrations, CSV export

match_scorer.py         AI match scoring engine (0-100%)
resume_tailor.py        AI resume generation — PDF/DOCX/TXT output
recruiter_messenger.py  Message queue with scheduled delivery
google_jobs_scraper.py  Google Jobs scraping — Selenium, SerpAPI, or requests
external_apply.py       ATS form filling — Greenhouse, Lever, Workday, Ashby
activity_sim.py         Human behavior simulation — feed, likes, profile views
alerts.py               Telegram / Discord / Slack notifications
dashboard.py            Flask real-time dashboard with dark theme
salary_intel.py         Salary parsing and benchmarking (10+ currency formats)
interview_prep.py       Company research + questions + talking points
success_tracker.py      ML prediction — logistic regression on 9 features
smart_scheduler.py      Learned scan times, Wilson score term ranking
proxy_manager.py        Health-scored proxy rotation with failover
platform_plugins/       Multi-platform abstraction (LinkedIn, Indeed, Glassdoor)
application_withdrawal.py  Auto-withdraw pending apps on offer received
dedup_engine.py         Cross-platform duplicate detection via fuzzy fingerprinting
jd_change_tracker.py    Tracks JD edits after applying (salary, requirements)
recruiter_crm.py        Recruiter relationship scoring CRM with interaction history
apply_scheduler.py      Time-of-day optimized apply queue (6-10am = 3x views)
salary_negotiation.py   Negotiation briefs with market rate data
status_scraper.py       Scrapes ATS portals for application status updates
job_watchlist.py        Smart bookmarking with reminders and activity checks
referral_automator.py   Auto-drafts referral request messages for connections
multi_language.py       JD language detection + resume/cover letter translation
checkpoint_manager.py   Crash recovery — saves/restores cycle state mid-progress
rate_limiter.py         Dynamic throttling — detects bans, CAPTCHAs, auto-backs off
validate_config.py      Startup config validation (11 checks, errors vs warnings)
metrics.py              Prometheus-compatible /metrics endpoint for Grafana
webapp/                 SaaS web app with auth, CSRF, search, API
docker/                 Dockerfile, docker-compose, health check
job_evaluator.py        Structured A-F evaluation per job (6 blocks)
story_bank.py           Persistent STAR+R interview story accumulator
archetype_classifier.py Role archetype classification (11 default types)
portfolio_evaluator.py  Portfolio project scoring (6 dimensions, BUILD/SKIP/PIVOT)
training_evaluator.py   Course/cert ROI scoring (TAKE/SKIP/TIMEBOX)
deep_research.py        6-axis deep company research
cv_template_engine.py   ATS-optimized HTML→PDF CV generation
pipeline_manager.py     Application lifecycle state machine
interview_simulator.py  Conversational AI mock interview practice
offer_war_room.py       Multi-offer comparison + negotiation playbooks
application_forensics.py Pattern analysis across all applications
ghost_predictor.py      Ghost probability scoring before applying
market_pulse.py         Real-time job market intelligence + weekly briefs
employer_sla_tracker.py Response time tracking per company per stage
quality_gate.py         Application quality scoring before submit
career_simulator.py     5-year career path projection + comparison
tests/                  165 unit tests (state, scoring, salary, dedup, timing, JD tracking, config)
```

25,993 lines across 73 Python files and 52 features. Includes 165 unit tests.

## AI Providers

| Provider | Cost | Setup |
|---|---|---|
| Ollama | Free, local | `ollama pull llama3.1` |
| LM Studio | Free, local | Load model, click Start Server |
| Groq | Free tier | Get API key from groq.com |
| Gemini | Free tier | Get API key from Google AI Studio |
| DeepSeek | ~$0.0003/question | deepseek.com |
| OpenAI | ~$0.001/question | platform.openai.com |
| Anthropic | ~$0.003/question | console.anthropic.com |
| Together | ~$0.0005/question | together.ai |

Set `provider` and `fallback_provider` in config. The bot tries: keyword matching (free) -> primary AI -> fallback AI.

## Dashboard

The real-time dashboard runs at `http://localhost:5000` when `dashboard.enabled: true`.

Completely rewritten as an all-in-one command center with 9 tabs: Overview, Applications, Recruiters, Salary, Skills, Interview Prep, Watchlist, Analytics, and System. Each tab provides dedicated views with filtering, sorting, and drill-down. Auto-refreshes every 30 seconds. Responsive design works on mobile.

## Web App

A full SaaS-style web app is available at `webapp/app.py`:

```bash
python webapp/app.py
# Runs at http://localhost:8080
# IMPORTANT: Change the default password before exposing to any network.
# Set credentials via environment variables:
#   export LLA_USERNAME="your-username"
#   export LLA_PASSWORD_HASH=$(python3 -c "import hashlib; print(hashlib.sha256(b'your-password').hexdigest())")
```

Features: login with session auth, CSRF protection, paginated job browser with search, recruiter directory, salary benchmarks, interview prep viewer, REST API endpoints, health check.

## Testing

```bash
# Run all 165 tests
python -m unittest discover -s tests -v

# Run specific test module
python -m unittest tests.test_state -v
python -m unittest tests.test_salary_intel -v
```

Tests cover: State class (32 tables, CRUD, migration, CSV export), match scoring (JSON parsing, bounds, thresholds), salary parsing (10+ currencies), dedup engine (fingerprinting, cross-platform matching), apply timing (freshness scoring, queue reordering), JD change tracking (snapshot capture, change detection), and config validation (missing sections, conflicts, numeric bounds).

## Production Hardening

The bot includes 4 hardening modules for reliable 24/7 operation:

- **Crash Recovery** (`checkpoint_manager.py`) — Saves cycle state every 5 jobs. On restart, resumes from checkpoint instead of re-processing. Stale checkpoints (>2h) auto-discarded.
- **Rate Limiting** (`rate_limiter.py`) — Detects LinkedIn ban signals (CAPTCHAs, "unusual activity", 429s). 5-level throttle escalation with cooldowns from 5-60 minutes. Page load anomaly detection. Gradual deescalation when safe.
- **Config Validation** (`validate_config.py`) — Validates 11 config areas on startup: credentials, search terms, AI provider, scheduling, numeric ranges, file paths, feature deps, conflicting settings. Reports errors vs warnings.
- **Prometheus Metrics** (`metrics.py`) — Exports counters/gauges/histograms at `/metrics` for Grafana dashboards. Tracks: applications, skips, errors, cycle duration, AI latency, match scores.

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/amazing`)
3. Make your changes
4. Ensure all files pass syntax check: `python -c "import ast, glob, sys; [ast.parse(open(f).read()) for f in glob.glob('**/*.py', recursive=True)]; print('OK')" || exit 1`
5. Submit a PR

## Inspired By

[GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn) — the original Python Selenium bot with 1.9K+ stars. This project takes the core idea and rebuilds it with AI resume tailoring, match scoring, multi-platform support, recruiter messaging, Google Jobs scraping, real-time dashboard, and ML-powered success prediction.

## Legal

- **[DISCLAIMER.md](DISCLAIMER.md)** — Risk disclosure, LinkedIn ToS implications, legal considerations, data security warnings, AI content caveats, recommended precautions
- **[TERMS_OF_USE.md](TERMS_OF_USE.md)** — User responsibilities, prohibited uses, AI-generated content terms, data privacy, limitation of liability, indemnification
- **[LICENSE](LICENSE)** — MIT License

**This software may violate LinkedIn's Terms of Service. Using automation on LinkedIn can result in account restrictions or bans. The authors accept no liability. Use at your own risk.**

## License

MIT
