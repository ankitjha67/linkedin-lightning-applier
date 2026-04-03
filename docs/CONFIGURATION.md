# Configuration Reference

All settings live in `config.yaml`. The bot hot-reloads this file every cycle — edit while running and changes apply on the next scan.

Copy `config.example.yaml` to `config.yaml` and fill in your details. `config.yaml` is gitignored (contains credentials).

## Table of Contents

- [LinkedIn Credentials](#linkedin-credentials)
- [Personal Info](#personal-info)
- [Job Search](#job-search)
- [Application Defaults](#application-defaults)
- [Question Answers](#question-answers)
- [Filters](#filters)
- [Resume](#resume)
- [Scheduling](#scheduling)
- [Browser](#browser)
- [Data Export](#data-export)
- [AI / LLM](#ai--llm)
- [Match Scoring](#match-scoring)
- [Resume Tailoring](#resume-tailoring)
- [Recruiter Messaging](#recruiter-messaging)
- [External Apply](#external-apply)
- [Google Jobs](#google-jobs)
- [Dashboard](#dashboard)
- [Alerts](#alerts)
- [Activity Simulation](#activity-simulation)
- [Salary Intelligence](#salary-intelligence)
- [Interview Prep](#interview-prep)
- [Success Tracking](#success-tracking)
- [Smart Scheduling](#smart-scheduling)
- [Multi-Platform](#multi-platform)
- [Proxy](#proxy)
- [Logging](#logging)

---

## LinkedIn Credentials

```yaml
linkedin:
  email: "you@example.com"
  password: "your-password"
```

If left empty, the bot opens Chrome and waits for you to log in manually (3-minute timeout).

## Personal Info

Used for form filling (keyword matching). These are the first thing checked before AI is called.

```yaml
personal:
  first_name: "John"
  last_name: "Doe"
  full_name: "John Doe"
  email: "john@example.com"
  phone: "+1 555-0100"
  city: "New York"
  state: "NY"
  zip_code: "10001"
  country: "United States"
  linkedin_headline: "Senior Software Engineer | Python, Go, AWS"
```

## Job Search

```yaml
search:
  search_terms:
    - "Software Engineer"
    - "Backend Developer"
    - "Python Developer"

  search_locations:
    - "New York, NY, United States"
    - "San Francisco, CA, United States"
    - "London, England, United Kingdom"

  date_posted: "Past hour"       # Widens automatically: hour -> 2h -> 6h -> 12h -> 24h -> week
  sort_by: "Most recent"
  easy_apply_only: true          # Set to false if external_apply is enabled
  experience_level: ["Mid-Senior level", "Associate"]  # Internship, Entry level, Associate, Mid-Senior level, Director, Executive
  job_type: ["Full-time"]        # Full-time, Part-time, Contract, Temporary, Internship
  work_location: []              # On-site, Remote, Hybrid (empty = all)
  randomize_order: true          # Shuffles search terms each cycle
```

**Adaptive time filter:** The bot starts with your configured `date_posted` filter. If zero results are found, it automatically widens: Past hour -> Past 2 hours -> Past 6 hours -> ... -> Past week. This ensures you never miss jobs while still prioritizing the freshest listings.

## Application Defaults

Fallback values for common form questions when keyword matching handles them:

```yaml
application:
  years_of_experience: 7
  notice_period_days: "30"
  desired_salary: "Negotiable"
  current_ctc: "20000"
  require_visa: "Yes"
  authorized_to_work: "Yes"
  willing_to_relocate: "Yes"
```

## Question Answers

Keyword-based matching. Free, instant, always correct. The key is matched against form field labels — if the key is found anywhere in the label, the value is used.

```yaml
question_answers:
  "first name": "John"
  "email": "john@example.com"
  "years of experience": "7"
  "python": "5"                  # "How many years of Python?"
  "salary": "Negotiable"
  "visa": "Yes, will require sponsorship"
  "cover letter": "Brief cover letter text here..."
```

These are checked **before** AI is called. Add entries for any question the bot encounters to avoid AI API calls.

## Filters

```yaml
filters:
  bad_words:                     # Skip jobs containing these in the description
    - "security clearance required"
    - "must be a US citizen"
    - "CPA required"

  blacklisted_companies:
    - "Revature"

  bad_title_words:               # Skip jobs with these in the title
    - "Intern"
    - "Junior"
    - "Driver"

  experience_buffer: 3           # Apply if job asks for your_exp + buffer years
  visa_sponsorship_only: false   # Only apply to jobs with visa sponsorship

  visa_positive_keywords:        # Signals that visa is offered
    - "visa sponsorship"
    - "will sponsor"
    - "H1B"
    - "Skilled Worker visa"

  visa_negative_keywords:        # Signals that visa is NOT offered
    - "no sponsorship"
    - "must be authorized"
    - "citizens only"
```

## Resume

```yaml
resume:
  default_resume_path: "/path/to/your/resume.pdf"
```

This is the fallback resume uploaded when resume tailoring is disabled or fails.

## Scheduling

```yaml
scheduling:
  scan_interval_minutes: 10      # Time between cycles
  max_applies_per_day: 40        # Hard daily cap
  max_applies_per_cycle: 15      # Max per cycle
  active_hours_start: 0          # 24h format (0 = midnight)
  active_hours_end: 24           # Bot sleeps outside these hours
  min_delay_between_jobs: 3      # Seconds between job processing
  max_delay_between_jobs: 8
  min_delay_between_searches: 5  # Seconds between search term switches
  max_delay_between_searches: 15
  delay_after_apply: 4           # Seconds after submitting
```

## Browser

```yaml
browser:
  headless: false                # true = no visible window (for servers)
  stealth_mode: true
  chrome_version: 146            # Pin to your installed Chrome version
  window_width: 1280
  window_height: 900
  user_data_dir: ""              # Chrome profile dir (saves cookies between runs)
  follow_company: false          # Uncheck "Follow company" on Easy Apply
```

**Tip:** Set `user_data_dir` to persist cookies across restarts. Log in once manually, then the bot reuses the session.

## Data Export

```yaml
export:
  auto_export_csv: true
  export_dir: "data"
  applied_csv: "applied_jobs.csv"
  skipped_csv: "skipped_jobs.csv"
  recruiters_csv: "recruiters.csv"
  visa_sponsors_csv: "visa_sponsors.csv"
```

## AI / LLM

```yaml
ai:
  enabled: true

  # Primary provider
  provider: "ollama"             # openai, anthropic, gemini, deepseek, groq, together, ollama, lmstudio
  model: "llama3.1"              # Auto-selected if empty
  base_url: ""                   # Auto-selected per provider
  api_key: ""                    # Not needed for ollama/lmstudio

  # Fallback provider (tried when primary fails)
  fallback_enabled: true
  fallback_provider: "ollama"
  fallback_model: "llama3.1"

  temperature: 0.2               # Lower = more consistent answers
  max_tokens: 200
  timeout_seconds: 30

  # Your CV (used as context for all AI operations)
  cv_text: |
    YOUR FULL CV TEXT HERE...

  # Or point to a file:
  cv_text_file: "my_cv.txt"
```

The AI priority chain: **keyword matching** (free, instant) -> **primary AI** -> **fallback AI**. Answers are cached in SQLite — the same question never hits the API twice.

## Match Scoring

```yaml
match_scoring:
  enabled: true
  minimum_score: 70              # 0-100 threshold; jobs below this are skipped
  score_in_csv: true             # Include match scores in CSV exports
```

The AI analyzes the job description against your CV and returns a structured score with matching skills, missing skills, and an explanation. Jobs below the minimum score are skipped with reason logged.

## Resume Tailoring

```yaml
resume_tailoring:
  enabled: true
  master_resume_path: ""         # Path to master resume (PDF/DOCX/TXT)
  master_resume_text: ""         # Or paste text directly
  output_dir: "data/tailored_resumes"
  format: "pdf"                  # pdf, docx, or txt
  template_style: "professional" # professional, modern, minimal
```

For each job above the match threshold, AI rewrites your CV to emphasize matching skills and uses keywords from the JD. The tailored resume is saved as a PDF and uploaded instead of your default resume.

## Recruiter Messaging

```yaml
recruiter_messaging:
  enabled: true
  delay_minutes: 120             # Wait N minutes after applying before messaging
  max_messages_per_day: 10
  message_template: ""           # Custom template (supports {recruiter_name}, {job_title}, {company})
  skip_if_no_profile_url: true   # Skip if recruiter's profile URL not found
```

Messages are queued with a scheduled send time. The queue is processed at the end of each cycle. AI generates a personalized 3-5 sentence message referencing the specific role and your qualifications.

## External Apply

```yaml
external_apply:
  enabled: true
  supported_ats:
    - "greenhouse"
    - "lever"
    - "workday"
    - "ashby"
  max_external_per_cycle: 5
  timeout_seconds: 120
```

When a job doesn't have Easy Apply, the bot checks for an external application link. If the URL matches a supported ATS, it opens the form in a new tab, fills all fields using keyword matching + AI, uploads your resume, and submits.

## Google Jobs

```yaml
google_jobs:
  enabled: true
  use_serpapi: false             # SerpAPI for structured data (requires API key)
  serpapi_key: ""
  scrape_interval_minutes: 30
  max_results_per_query: 50
  search_queries: []             # Auto-generates from search terms if empty
  country_code: "uk"             # Google country for localized results
  date_posted: "today"           # today, 3days, week, month
  deduplicate_with_linkedin: true
```

Three scraping strategies: Selenium (uses existing browser, most reliable), SerpAPI (structured API, costs money), and requests+BeautifulSoup (fast but less reliable). Google-discovered jobs that link to LinkedIn are processed directly; ATS-linked jobs are handed to the external applier.

## Dashboard

```yaml
dashboard:
  enabled: true
  port: 5000
  host: "0.0.0.0"               # "0.0.0.0" = accessible from other devices
  refresh_interval: 30           # Auto-refresh seconds
```

Runs in a background thread. Dark theme, responsive design, works on mobile. Shows: stat cards, application funnel, recent applications with match scores, recruiter directory, visa sponsor list.

## Alerts

```yaml
alerts:
  enabled: false
  telegram:
    enabled: false
    bot_token: ""                # Get from @BotFather on Telegram
    chat_id: ""                  # Your chat/group ID
  discord:
    enabled: false
    webhook_url: ""              # Discord webhook URL
  slack:
    enabled: false
    webhook_url: ""              # Slack incoming webhook URL
  on_apply: true                 # Notify on each application
  on_error: true                 # Notify on errors
  daily_summary: true            # Send daily stats summary
  daily_summary_time: "22:00"    # When to send (24h format)
```

## Activity Simulation

```yaml
activity_simulation:
  enabled: true
  actions_per_cycle: 5           # Number of random actions (or "3-7" for range)
  view_profiles: true
  like_posts: true
  scroll_feed: true
```

Between apply cycles, the bot performs random human-like activities on LinkedIn to avoid detection patterns. Actions are randomized with realistic delays.

## Salary Intelligence

```yaml
salary_intelligence:
  enabled: true
  export_csv: true
```

Parses salary strings from job postings into structured data (min, max, currency, period). Supports: USD ($), GBP, EUR, INR (LPA/Lakhs), JPY, AUD, CAD, SGD, AED, HKD. Builds benchmarks queryable by role and location.

## Interview Prep

```yaml
interview_prep:
  enabled: true
  auto_generate: true            # Generate automatically after applying
```

After each successful application, AI generates: company research notes, 8-10 likely interview questions based on the JD, and talking points mapping your experience to their requirements. Saved per job and viewable in the web app.

## Success Tracking

```yaml
success_tracking:
  enabled: true
```

Trains a logistic regression model on your application outcomes. Features: match score, recruiter messaging, resume tailoring, visa status, day of week. Predicts response probability for new applications. Auto-marks applications as "ghosted" after 14 days with no response.

## Smart Scheduling

```yaml
smart_scheduling:
  enabled: true
  prioritize_fast_hiring: true
```

Learns when jobs are most frequently posted and adjusts scan intervals accordingly. Tracks hiring velocity per company. Ranks search terms by historical success using Wilson score. Detects market activity level (hot/normal/slow).

## Multi-Platform

```yaml
multi_platform:
  enabled: false
  platforms:
    - "linkedin"
    # - "indeed"
    # - "glassdoor"
```

Plugin architecture for multiple job platforms. LinkedIn is the primary (fully implemented). Indeed and Glassdoor plugins are available with job search, extraction, and form filling support.

## Proxy

```yaml
proxy:
  enabled: false
  proxy_list: []                 # ["http://ip:port", "socks5://ip:port"]
  proxy_file: ""                 # Path to file with one proxy per line
  rotate_per_session: true
  sticky_session_minutes: 30     # Reuse same proxy within this window
  validate_on_start: true        # Test proxies on startup
  profiles_dir: "data/profiles"
```

Health-scored rotation: proxies are scored 0-1 based on success rate, latency, and recency. Failed proxies get exponential backoff cooldowns. After 10 consecutive failures, a proxy is auto-banned. Health data persists to disk.

## Logging

```yaml
logging:
  level: "INFO"                  # DEBUG, INFO, WARNING, ERROR
  log_to_file: true
  log_dir: "logs"
```

Logs are written to `logs/lla_YYYYMMDD.log` and stdout. Set to DEBUG for verbose output including AI responses and selector details.
