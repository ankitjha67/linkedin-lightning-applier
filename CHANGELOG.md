# Changelog

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
