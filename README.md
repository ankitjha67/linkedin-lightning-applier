# LinkedIn Lightning Applier

Autonomous job application engine. Searches LinkedIn every 10 minutes, applies the moment jobs appear, tailors your resume per job using AI, scores job-candidate fit, messages recruiters, scrapes Google Jobs for cross-platform discovery, fills external ATS forms on 12 platforms (Workday, Greenhouse, Lever, iCIMS, Taleo, and more), tracks everything in SQLite, and serves a real-time monitoring dashboard — all running 24/7.

Built because the difference between "applied 2 minutes after posting" and "applied 24 hours later" is the difference between getting an interview and getting buried under 500 applicants.

## What It Does

The bot runs in a continuous loop. Every cycle:

1. **Discovers jobs** — Searches LinkedIn across all your configured terms and locations. Optionally scrapes Google Jobs for cross-platform coverage (Indeed, Glassdoor, company sites).
2. **Scores every job** — AI compares the job description against your CV and scores the match 0-100%. Jobs below your threshold (e.g. 70%) are skipped automatically.
3. **Tailors your resume** — For jobs above the threshold, AI generates a custom PDF resume emphasizing the skills that match this specific job description.
4. **Applies** — Clicks Easy Apply (LinkedIn) or fills external ATS forms across 12 platforms (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Jobvite, BambooHR, iCIMS, Taleo, SuccessFactors, ADP). Creates accounts and drives multi-page wizards where required. Keyword matching handles 90% of form fields for free; AI fills the rest.
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
- **External ATS Apply** — Fills application forms on **12 ATS platforms**: Workday, Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Jobvite, BambooHR, iCIMS, Taleo, SuccessFactors, and ADP. Handles Workday's account-creation + multi-page wizard, custom React dropdowns, and login-gated enterprise portals. Keyword matching fills ~90% of fields for free; AI handles the rest.
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

### Application Craftsmanship (11 — new in v2.9)
- **Browser Extension (24/7 in-browser autopilot)** (`browser_extension/`) — Chrome/Edge MV3 companion that runs in your **real logged-in browser** (no chromedriver): alarm-driven board scanning, LLM relevance scoring with **NVIDIA NIM / frontier / local Ollama-LM Studio** provider placeholders, per-country work-auth answers, learned-answer reuse, resume auto-attach, fill-only or auto-submit.
- **Environment Doctor** (`env_doctor.py` / `lla doctor --fix`) — Auto-detects your Python and **installed Chrome version** (Windows registry / macOS / Linux), installs missing packages one-by-one, and reports optional tools. Browser launch self-heals a Chrome↔driver mismatch (re-pins, then auto-upgrades the driver) so it never dies on a version error.
- **Country-Aware Work Authorization** (`work_auth.py`) — "Authorized to work?" / "Need sponsorship?" answered from the JOB's country vs your citizenship + visas held. Countries you're not authorized in automatically answer No / sponsorship-required. Deterministic, zero tokens, and never cached across borders.
- **Batch External Apply** (`apply_urls.py`) — Submit ATS applications from a plain URL list (txt/CSV/JSON) in a real browser, **no LinkedIn required**. URL-dedup against SQLite; `--dry-run` previews ATS detection.
- **LaTeX Application Documents** (`latex_docs.py`, `application_docs.py`) — Typeset moderncv CV + matching cover letter per job; compiles via lualatex/xelatex/pdflatex, degrades to `.tex`. Optional auto-generation in the run loop.
- **ATS Text-Layer Verification** (`ats_pdf_check.py`) — Checks the compiled PDF the way an ATS parser reads it: literal contact details, reading order, glyph garbage, and JD keyword coverage. Relevance-weighted CV trimming (`relevance_cutter.py`) cuts by value, not age.
- **Drafter-Reviewer + Honesty Check** (`doc_reviewer.py`) — A second AI pass critiques each draft, revises it, and flags any claim your profile doesn't support. Real gaps stay visible; nothing is fabricated.
- **Screener Simulator + Pre-Submit Gate** (`screener_sim.py`) — Scores your resume the way employer-side AI screeners do (rubric from HackerRank's open-source hiring-agent) and can warn/block weak submissions across `lla docs`, batch apply, and the run loop.
- **Semantic Answer Memory (RAG)** (`answer_rag.py`) — Remembers every form answer; semantically-similar questions are answered straight from memory with **zero LLM tokens**, and near-matches are injected into the prompt for consistency. Pure-Python TF-IDF — works offline with any provider.
- **Any-Job-Board Generic Apply** — URLs matching no known ATS fall back to a best-effort generic handler that sweeps the form and can **register/sign in** with shared credentials (`ats_accounts.generic`). Disable with `external_apply.allow_generic_fallback: false`.
- **GitHub Signal Enrichment** (`github_enrich.py`) — Classifies your repos like a screener (open-source vs self-project vs fork) and ranks which projects to feature per posting.

### Core Foundations
- **AI Form Filling** — 13 LLM providers: OpenAI, Anthropic Claude, Google Gemini, DeepSeek, Groq, Together, OpenRouter (free model chain), xAI Grok, Mistral, Claude CLI (no API key), Ollama (local), LM Studio (local), and `custom` — any OpenAI-compatible endpoint (vLLM, llama.cpp server, LocalAI). Answers cached in SQLite.
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
  supported_ats: ["workday", "greenhouse", "lever", "ashby", "smartrecruiters",
                  "workable", "jobvite", "bamboohr", "icims", "taleo",
                  "successfactors", "adp"]
  ats_accounts:                 # creds for login-gated ATSes (workday, icims, ...)
    workday: { email: "", password: "" }

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
state.py                SQLite — 49 tables, migrations, CSV export

match_scorer.py         AI match scoring engine (0-100%)
resume_tailor.py        AI resume generation — PDF/DOCX/TXT output
recruiter_messenger.py  Message queue with scheduled delivery
google_jobs_scraper.py  Google Jobs scraping — Selenium, SerpAPI, or requests
external_apply.py       ATS orchestrator — tab mgmt, detection, per-cycle caps
ats_handlers/           12 ATS handlers (Workday, Greenhouse, iCIMS, Taleo, ...)
apply_urls.py           Standalone batch apply — submit ATS forms from a URL list
application_docs.py     Orchestrator: tailor → LaTeX build → ATS check → review
latex_docs.py           moderncv CV + cover letter renderer/compiler
ats_pdf_check.py        ATS text-layer verification + keyword coverage
relevance_cutter.py     Relevance-weighted CV trimming (value, not age)
doc_reviewer.py         Drafter-reviewer loop + honesty check
screener_sim.py         Employer-side screener simulation + pre-submit gate
github_enrich.py        GitHub repo classification + per-posting project ranking
profile_setup.py        /setup onboarding — documents/ folder → profile text
answer_rag.py           Semantic answer memory — zero-token reuse of similar Q&As
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
plugin_api.py           Extension framework — custom ATS, platforms, templates, hooks
plugins/                Community extensions directory (auto-loaded on startup)
mcp_server.py           MCP server — 13 tools for Claude Code / Claude Desktop
tools_layer.py          Protocol-agnostic tool layer (MCP/adapter foundation)
careers_scanner.py      Curated company careers-page scanner (Greenhouse/Lever/Ashby)
companies.json          Curated target-company database (30+ companies)
pyproject.toml          PyPI packaging — `pip install` + `lla` CLI entry point
tests/                  386 unit + integration tests
```

35,166 lines across 108 Python files and 58 features. Includes 386 unit tests.

## AI Providers

| Provider | Cost | Setup |
|---|---|---|
| **Claude CLI** | **Free** (uses your Claude Code auth) | `provider: claude_cli` — requires the `claude` binary in PATH |
| **OpenRouter** | **Free** (free-model fallback chain) | `provider: openrouter` + free key from openrouter.ai |
| Ollama | Free, local | `ollama pull llama3.1` |
| LM Studio | Free, local | Load model, click Start Server |
| Groq | Free tier | Get API key from groq.com |
| Gemini | Free tier | Get API key from Google AI Studio |
| DeepSeek | ~$0.0003/question | deepseek.com |
| OpenAI | ~$0.001/question | platform.openai.com |
| Anthropic | ~$0.003/question | console.anthropic.com |
| Together | ~$0.0005/question | together.ai |

Set `provider` and `fallback_provider` in config. The bot tries: keyword matching (free) -> primary AI -> fallback AI.

**Claude CLI** (`provider: claude_cli`) uses your local `claude` binary as the LLM backend — zero API cost if you already have Claude Code. **OpenRouter** (`provider: openrouter`) auto-falls-back through a chain of free models on rate limit — zero cost, no credit card.

**Test any model before relying on it:**

```bash
lla test-llm                      # test whatever is in config.yaml
lla test-llm --provider ollama --model llama3.1                      # local, no key
lla test-llm --provider openrouter --model nvidia/nemotron-3-super-120b-a12b:free
```

It sends one real prompt and prints the reply, latency, and the resolved
provider/model/base-url — surfacing the actual error on failure (bad model id,
auth, unreachable server). Note: only **chat/completion** models work for form
answers. **Rerank/embedding models cannot generate text** — e.g. an ID with
`rerank` in it (like `…-nemotron-rerank-vl-…`) will fail the test. Pick a chat
model instead.

## MCP Server (Claude Code / Claude Desktop)

Control the bot with natural language from Claude Code or Claude Desktop:

```bash
pip install -e '.[mcp]'        # install MCP support
claude mcp add lla -- python -m mcp_server
```

Then in any Claude session: *"Score this job for me"*, *"Show my application stats"*, *"Run application forensics"*, *"Generate a market report"*, *"Tailor my resume for this role"*. 13 tools exposed via the Model Context Protocol — all backed by the same engine as the CLI and bot.

## Apply Without LinkedIn (Batch ATS Apply)

Discovery tools (Indeed, Google Jobs, a careers page, a spreadsheet a recruiter
sent you) can find jobs and hand you apply links — but they can't click submit.
`apply_urls.py` is the **last mile**: give it apply URLs and it drives a real
browser through each ATS form and submits. **No LinkedIn login required** —
external ATS applications only need your browser + config. Works on all 12
supported platforms (Workday, Greenhouse, Lever, iCIMS, Taleo, …).

```bash
# One or more apply URLs
python apply_urls.py https://boards.greenhouse.io/acme/jobs/123 \
                     https://nvidia.wd5.myworkdayjobs.com/careers/job/x

# From a file — one URL per line, or a CSV/JSON with metadata
python apply_urls.py --file urls.txt --resume ~/cv.pdf
python apply_urls.py --file jobs.csv          # header: url,title,company,description

# Preview first — detect the ATS for each URL, submit nothing
python apply_urls.py --file urls.txt --dry-run

# Caps & options
python apply_urls.py --file urls.txt --max 10 --headless
#   --force     re-apply even if already applied (dedup is by URL)

# Same thing via the CLI:
lla apply --file urls.txt --dry-run
```

It fills identity fields from your config for free, uses AI for open-ended
questions, uploads your resume, creates accounts on login-gated portals
(Workday/iCIMS/Taleo/… — see `external_apply.ats_accounts`), and records every
result to SQLite (`applied_jobs` / `failed_jobs`), so re-runs skip anything
already submitted. Exit status is `0` only if every attempted URL submitted.

The AI that answers open questions is **whatever LLM you configure** — not tied
to any one provider. Use a cloud model (Gemini, OpenAI, Anthropic, Groq,
DeepSeek, OpenRouter's free chain, Claude CLI) or a **fully local model**
(Ollama / LM Studio) that needs **no API key and makes no cloud calls** — a nice
fit for a job tool handling your personal data. See [AI Providers](#ai-providers).
Keyword matching still fills ~90% of fields even with AI disabled.

**Workflow to close the loop:** find jobs however you like → collect the apply
links into `urls.txt` (or a CSV) → `python apply_urls.py --file urls.txt`.

## Tailored Application Documents (LaTeX CV + cover letter)

Beyond the bot's fpdf2 resumes, `lla docs` produces **typeset LaTeX documents** —
a `moderncv` CV and a matching cover letter — tailored to a specific posting,
with three quality gates built in:

```bash
lla docs --jd-file jd.txt --title "Credit Risk Manager" --company "Monzo"
```

- **ATS text-layer check** — scores the CV's keyword coverage against the posting
  and verifies it parses the way an ATS actually reads it (contact details as
  literal text, sane reading order, no glyph garbage).
- **Drafter → reviewer loop** — a second AI pass critiques the draft (missed
  keywords, weak framing, generic language), then revises.
- **Honesty check** — flags any claim the draft makes that your profile
  (`ai.cv_text` + `documents/`) doesn't support. Real gaps stay visible; nothing
  is fabricated or keyword-stuffed.

Compiles to PDF if a LaTeX engine (TeX Live / MiKTeX with `moderncv`) is
installed; otherwise it writes the `.tex` for you to compile. The ATS check uses
`pdftotext` or `pip install pdfminer.six` when available, else the plain text.

Set `latex_docs.auto_generate: true` to have the **autonomous loop** (`lla run`)
build a tailored LaTeX CV for every above-threshold job and upload the compiled
PDF as the resume (off by default — it needs a LaTeX engine and is slower per job).

**Inside Claude Code**, two slash commands drive the human-in-the-loop flow:
- **`/setup`** — turns your `documents/` folder (CV, LinkedIn export, diplomas,
  references), a pasted CV, or a short interview into your profile
  (`ai.cv_text` + `personal.*` + search targets).
- **`/apply <url-or-jd>`** — evaluate fit → tailor docs → ATS/review/honesty →
  confirm → optionally submit.

The `job-application-assistant` skill documents the modules. This complements the
autonomous bot and the batch `lla apply` submitter.

_Document-quality approach inspired by [MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search) (MIT), reworked as testable Python modules._

## Screener Simulator (see your resume through the employer's AI)

Companies increasingly screen resumes with AI before a human ever reads them.
`lla screen` runs that screen on **your** resume for a specific posting — rubric
adapted from [HackerRank's open-source hiring-agent](https://github.com/interviewstreet/hiring-agent) (MIT), inverted to the candidate side:

```bash
lla screen --jd-file jd.txt                          # uses ai.cv_text from config
lla screen --jd-file jd.txt --resume-file cv.pdf     # or a specific file
lla screen --jd-file jd.txt --github your-username   # + GitHub signal analysis
```

Three layers:
- **Hygiene lint (no AI needed)** — the exact deductions screeners apply:
  projects/roles without links (−30-50%), unquantified bullets, generic project
  names, missing literal contact details.
- **Rubric evaluation (with AI)** — role-aware category scores with hard caps
  (engineering: open-source/self-projects/production/skills; professional:
  experience/domain/impact/tools), evidence per category, a bonus/deduction
  ledger capped exactly like the employer side, key strengths, and areas for
  improvement. Fairness rules preserved: never scores name, school, grades, or location.
- **GitHub signal** (`--github`) — classifies your repos the way the screener
  does (true open-source contributions vs. personal repos vs. forks), warns when
  your profile caps the open-source category, and ranks which projects to
  feature for *this* posting.

**Pre-submit gate.** The same simulation gates every submit path. Set
`screener.gate` to `warn` (default — log and submit anyway) or `block`:
`lla docs` prints the verdict (and exits non-zero on block), the batch
`lla apply` skips blocked rows, and with `screener.gate_in_run: true` the
autonomous loop skips below-threshold jobs (marked in the DB with the reason).
Fail-open by design — a job with no substantial description, an unavailable AI,
or an unparseable evaluation is never blocked.

## Careers-Page Scanner

A targeted, company-first discovery mode that complements LinkedIn/Google search. Scans a curated `companies.json` (30+ companies, extensible) via **free** ATS JSON APIs (Greenhouse, Lever, Ashby) with HTML-scraping fallback — no paid API needed. Enable with `careers_scanner.enabled: true`. Scores every role with your match scorer and surfaces the top matches.

## Daily Automation

Run one scan cycle per day on a schedule (instead of the continuous loop):

```bash
# 1. Put your API key in .env (gitignored)
cp .env.example .env
echo 'GEMINI_API_KEY=your-gemini-2.5-pro-key' > .env

# 2. Install a daily cron job (default 09:00; pass HOUR MINUTE to change)
./setup_cron.sh 9 0          # runs every day at 09:00 local time
crontab -l                   # verify
./setup_cron.sh --remove     # uninstall

# Run a single cycle manually any time:
./run_daily.sh               # or: python main.py --once -c config.yaml
```

`main.py --once` runs exactly one scan cycle then exits — safe for cron with no overlapping processes. `run_daily.sh` loads `.env`, picks the project venv, logs each run to `logs/daily_*.log`, and prunes logs older than 30 days.

### API keys via environment variables

Any provider's API key can be supplied through the environment instead of `config.yaml` (preferred for cron/Docker — keeps secrets out of files):

| Provider | Env var(s) |
|---|---|
| Gemini | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| DeepSeek / Groq / Together | `DEEPSEEK_API_KEY` / `GROQ_API_KEY` / `TOGETHER_API_KEY` |

To use **Gemini 2.5 Pro**, set in `config.yaml`:
```yaml
ai:
  enabled: true
  provider: "gemini"
  model: "gemini-2.5-pro"
  api_key: ""          # leave blank — read from GEMINI_API_KEY env var
```

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

## Plugins

Extend the bot without modifying core code. Drop Python files in `plugins/`:

```python
# plugins/my_ats_handler.py
def register(registry):
    registry.register_plugin("my-ats", "1.0.0", "You", "Custom ATS support")
    registry.register_ats("bamboohr", BambooHRHandler)
    registry.register_hook("post_apply", lambda **kw: print(f"Applied to {kw.get('company')}!"))
    registry.register_notifier("webhook", lambda msg: requests.post(URL, json={"text": msg}))
```

Extension points: ATS handlers, job platforms, resume templates, role archetypes, custom scorers, notification channels, lifecycle hooks (pre/post apply, pre/post cycle, on_error, on_response).

## Testing

```bash
# Run all 386 tests
python -m unittest discover -s tests -v

# Run specific test module
python -m unittest tests.test_state -v
python -m unittest tests.test_salary_intel -v
```

Tests cover: State class (49 tables, CRUD, migration, CSV export), match scoring (JSON parsing, bounds, thresholds), salary parsing (10+ currencies), dedup engine (fingerprinting, cross-platform matching), apply timing (freshness scoring, queue reordering), JD change tracking (snapshot capture, change detection), and config validation (missing sections, conflicts, numeric bounds).

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
