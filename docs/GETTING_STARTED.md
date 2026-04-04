# How to Run LinkedIn Lightning Applier — Complete Step-by-Step Guide

This guide covers everything from zero to a fully running bot with all 36 features.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [Chrome Setup](#3-chrome-setup)
4. [AI / LLM Setup](#4-ai--llm-setup)
5. [Configuration](#5-configuration)
6. [First Run](#6-first-run)
7. [Dashboard Access](#7-dashboard-access)
8. [Setting Up Alerts](#8-setting-up-alerts)
9. [Resume & Cover Letter Setup](#9-resume--cover-letter-setup)
10. [Running Tests](#10-running-tests)
11. [Docker Deployment](#11-docker-deployment)
12. [Monitoring with Prometheus/Grafana](#12-monitoring-with-prometheusgrafana)
13. [Web App Setup](#13-web-app-setup)
14. [Advanced: Proxy Setup](#14-advanced-proxy-setup)
15. [Troubleshooting](#15-troubleshooting)
16. [Feature Reference](#16-feature-reference-what-each-config-flag-does)

---

## 1. Prerequisites

### Required

| Dependency | Version | Why |
|-----------|---------|-----|
| Python | 3.10+ (3.12 recommended) | Core runtime |
| Google Chrome | Stable channel (latest) | Browser automation |
| pip | Latest | Package installer |

### Check your versions

```bash
python3 --version    # Must be 3.10+
google-chrome --version  # Or: chrome --version (Windows)
pip3 --version
```

### Install Chrome (if not installed)

**Ubuntu/Debian:**
```bash
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt update && sudo apt install -y google-chrome-stable
```

**macOS:**
```bash
brew install --cask google-chrome
```

**Windows:**
Download from https://www.google.com/chrome/

---

## 2. Installation

```bash
# Clone the repository
git clone https://github.com/ankitjha67/linkedin-lightning-applier.git
cd linkedin-lightning-applier

# Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate    # Linux/macOS
# venv\Scripts\activate     # Windows

# Install all dependencies
pip install -r requirements.txt
```

### Verify installation

```bash
python3 -c "
import yaml, selenium, openai, fpdf, flask, requests, bs4
print('All dependencies installed successfully!')
"
```

If any import fails, install individually:
```bash
pip install undetected-chromedriver selenium PyYAML openai anthropic fpdf2 python-docx flask requests beautifulsoup4
```

---

## 3. Chrome Setup

The bot uses `undetected-chromedriver` which auto-downloads the matching ChromeDriver. But you need to tell it your Chrome version.

### Find your Chrome version

```bash
google-chrome --version   # Linux
# or check: chrome://version in Chrome browser
```

Example output: `Google Chrome 125.0.6422.76`

### Update config to match

In `config.yaml`:
```yaml
browser:
  chrome_version: 125          # ← Match your major version number
  headless: false              # true = no visible window (for servers)
  user_data_dir: ""            # Set this to persist login cookies (see below)
```

### Persist login (recommended)

Set `user_data_dir` to avoid re-logging in every time:

```yaml
browser:
  user_data_dir: "/home/youruser/chrome-lla-profile"  # Linux
  # user_data_dir: "C:/Users/YourName/chrome-lla-profile"  # Windows
```

First run will require manual login. After that, cookies are saved.

---

## 4. AI / LLM Setup

The bot needs an LLM for: form filling, match scoring, resume tailoring, cover letters, interview prep, and more. Choose ONE primary provider:

### Option A: Ollama (Free, Local, Recommended)

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull llama3.1          # 8GB, good balance
# or: ollama pull qwen2.5:7b  # Lighter alternative

# Start Ollama (runs in background)
ollama serve
```

Config:
```yaml
ai:
  enabled: true
  provider: "ollama"
  model: "llama3.1"
  # base_url defaults to http://localhost:11434/v1
```

### Option B: LM Studio (Free, Local, GUI)

1. Download from https://lmstudio.ai
2. Open LM Studio, download a model (e.g., Qwen 2.5 7B)
3. Click "Start Server" (runs at localhost:1234)

Config:
```yaml
ai:
  enabled: true
  provider: "lmstudio"
  model: "qwen2.5-7b"
  base_url: "http://localhost:1234/v1"
```

### Option C: Cloud API (Paid)

```yaml
ai:
  enabled: true
  provider: "openai"           # or: anthropic, gemini, deepseek, groq, together
  api_key: "sk-your-key-here"
  model: "gpt-4o-mini"         # cheapest OpenAI option
```

### Option D: No AI (Basic mode)

```yaml
ai:
  enabled: false
```

The bot will still work — it uses keyword matching from `question_answers` for form filling. But match scoring, resume tailoring, and other AI features will be disabled.

---

## 5. Configuration

### Create your config file

```bash
cp config.example.yaml config.yaml
```

### Essential settings to fill in

Open `config.yaml` in any editor and fill these sections:

#### 1. LinkedIn Credentials

```yaml
linkedin:
  email: "your-linkedin-email@example.com"
  password: "your-linkedin-password"
```

#### 2. Personal Information

```yaml
personal:
  first_name: "Your"
  last_name: "Name"
  full_name: "Your Name"
  email: "your@email.com"
  phone: "+1 555-0100"
  city: "Your City"
  state: "Your State"
  zip_code: "10001"
  country: "Your Country"
```

#### 3. Search Terms & Locations

```yaml
search:
  search_terms:
    - "Software Engineer"
    - "Backend Developer"
    - "Python Developer"
    # Add your target job titles

  search_locations:
    - "New York, NY, United States"
    - "San Francisco, CA, United States"
    - "London, England, United Kingdom"
    # Add your target locations

  date_posted: "Past hour"       # Auto-widens if no results
  easy_apply_only: true          # Set false if external_apply enabled
```

#### 4. Your CV (Critical for AI features)

```yaml
ai:
  cv_text: |
    YOUR FULL CV/RESUME TEXT HERE.
    Include: name, experience (with dates and achievements),
    education, skills, certifications.
    The more detail, the better AI scoring and tailoring works.
```

#### 5. Question Answers (Free instant matching)

```yaml
question_answers:
  "first name": "Your"
  "last name": "Name"
  "email": "your@email.com"
  "phone": "+1 555-0100"
  "years of experience": "5"
  "salary": "Negotiable"
  "visa": "Yes"
  "relocate": "Yes"
  "linkedin": "https://linkedin.com/in/yourprofile"
  # Add any common question you've seen on applications
```

#### 6. Resume File

```yaml
resume:
  default_resume_path: "/path/to/your/resume.pdf"
```

#### 7. Feature Toggles (all optional)

Every feature defaults to sensible values. Key ones to consider:

```yaml
match_scoring:
  enabled: true
  minimum_score: 70             # Lower = more applications, higher = more targeted

resume_tailoring:
  enabled: true                 # Requires AI enabled

recruiter_messaging:
  enabled: true
  delay_minutes: 120            # Wait 2 hours after applying

dashboard:
  enabled: true                 # Web dashboard at localhost:5000

activity_simulation:
  enabled: true                 # Simulates human browsing between cycles

alerts:
  enabled: false                # Set up Telegram/Discord/Slack (see section 8)
```

---

## 6. First Run

### Start the bot

```bash
python main.py
```

Or with a custom config:
```bash
python main.py -c my_config.yaml
```

### What happens on first run

1. **Config validation** — checks for missing/conflicting settings
2. **Chrome launches** — a browser window opens (unless headless)
3. **Login** — Bot tries auto-login. If it fails:
   - You'll see: `PLEASE LOGIN MANUALLY in the browser window!`
   - Log in to LinkedIn in the Chrome window
   - Complete any 2FA/CAPTCHA challenges
   - Bot detects login and continues (3-minute timeout)
4. **Dashboard starts** — http://localhost:5000 (if enabled)
5. **First cycle begins** — searches for jobs, scores, applies

### Expected console output

```
07:30:00 | INFO | lla | ═══════════════════════════════════════
07:30:00 | INFO | lla | ⚡  LinkedIn Lightning Applier v2
07:30:00 | INFO | lla | ═══════════════════════════════════════
07:30:00 | INFO | lla | Features: Match Scoring (min: 70%), Resume Tailoring, ...
07:30:00 | INFO | lla | Dashboard started at http://0.0.0.0:5000
07:30:01 | INFO | lla | Launching browser...
07:30:05 | INFO | lla | Logging in...
07:30:10 | INFO | lla | 🚀  Running. Ctrl+C to stop.
07:30:10 | INFO | lla | ── Cycle start (every 10min) ──
07:30:10 | INFO | lla | 🔍  "Software Engineer" → "New York" (Past hour)
07:30:15 | INFO | lla | Found 12 cards via 'li[data-occludable-job-id]'
07:30:16 | INFO | lla | ▶  [0] Senior Software Engineer @ Google (ID: 3945612345)
07:30:17 | INFO | lla |    🎯 Match score: 87% (Strong skill overlap)
07:30:18 | INFO | lla |    📄 Tailored resume: Google_Senior_Software_20260404.pdf
07:30:20 | INFO | lla |    ✅  APPLIED!
```

### Stopping the bot

Press `Ctrl+C`. The bot will:
- Finish the current action
- Export final CSV files
- Close the browser
- Print session summary

---

## 7. Dashboard Access

When `dashboard.enabled: true`, open:

```
http://localhost:5000
```

### 9 tabs available:

| Tab | What it shows |
|-----|--------------|
| Overview | Stats cards, application funnel, daily trend chart |
| Applications | Full table of applied jobs with match scores, salary, visa |
| Recruiters | Recruiter directory + visa sponsor list |
| Salary | Salary data with min/max/currency |
| Skills | Top demanded skills + skill gap chart |
| Interview Prep | Company research, questions, talking points per job |
| Watchlist | Bookmarked jobs with status |
| Analytics | Response rates by score, daily application charts |
| System | System health, total counts |

Auto-refreshes every 30 seconds. Works on mobile.

---

## 8. Setting Up Alerts

### Telegram

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → follow prompts
2. Copy the bot token (looks like `123456:ABC-DEF...`)
3. Start a chat with your bot, then get your chat ID:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates"
   ```
4. Config:
   ```yaml
   alerts:
     enabled: true
     telegram:
       enabled: true
       bot_token: "123456:ABC-DEF..."
       chat_id: "987654321"
   ```

### Discord

1. Server Settings → Integrations → Create Webhook
2. Copy webhook URL
3. Config:
   ```yaml
   alerts:
     enabled: true
     discord:
       enabled: true
       webhook_url: "https://discord.com/api/webhooks/..."
   ```

### Slack

1. https://api.slack.com/apps → Create App → Incoming Webhooks
2. Add to channel, copy URL
3. Config:
   ```yaml
   alerts:
     enabled: true
     slack:
       enabled: true
       webhook_url: "https://hooks.slack.com/services/..."
   ```

---

## 9. Resume & Cover Letter Setup

### Resume Tailoring (AI generates custom resumes)

```yaml
resume_tailoring:
  enabled: true
  output_dir: "data/tailored_resumes"
  format: "pdf"                  # pdf, docx, or txt
```

Requires: `ai.enabled: true` and `ai.cv_text` filled in.

Tailored resumes are saved in `data/tailored_resumes/` as:
`CompanyName_JobTitle_20260404_123456.pdf`

### Cover Letters

```yaml
cover_letter:
  enabled: true
  output_dir: "data/cover_letters"
  tone: "professional"           # professional, conversational, enthusiastic
  length: "medium"               # short, medium, full
```

### Resume A/B Testing

```yaml
resume_ab_testing:
  enabled: true
  variants_per_job: 2
  styles:
    - "skills_first"
    - "achievement_focused"
    - "narrative"
```

The bot generates multiple resume variants and uses Thompson sampling to learn which style gets the most callbacks.

---

## 10. Running Tests

```bash
# Run all 165 tests
python -m unittest discover -s tests -v

# Run specific test module
python -m unittest tests.test_state -v
python -m unittest tests.test_salary_intel -v
python -m unittest tests.test_dedup_engine -v

# Quick check that all Python files are valid
python -c "import ast, glob; [ast.parse(open(f).read()) for f in glob.glob('**/*.py', recursive=True)]; print('All files OK')"
```

---

## 11. Docker Deployment

For 24/7 server deployment:

```bash
# Prepare config
cp config.example.yaml config.yaml
nano config.yaml  # Fill in your details

# IMPORTANT: Set headless mode for Docker
# In config.yaml:
#   browser:
#     headless: true

# Build
docker build -f docker/Dockerfile -t lla .

# Run
docker run -d \
  --name lla \
  --shm-size=2g \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v lla-data:/app/data \
  -v lla-logs:/app/logs \
  -p 5000:5000 \
  lla

# View logs
docker logs -f lla

# Stop
docker stop lla
```

### Docker Compose (easier)

```bash
cd docker
cp ../config.yaml .
docker-compose up -d
docker-compose logs -f
```

---

## 12. Monitoring with Prometheus/Grafana

### Enable metrics

```yaml
metrics:
  enabled: true
  port: 9090
```

### Scrape with Prometheus

Add to `prometheus.yml`:
```yaml
scrape_configs:
  - job_name: 'lla'
    static_configs:
      - targets: ['localhost:9090']
    scrape_interval: 30s
```

### Available metrics

- `lla_applications_total` — Total applications submitted
- `lla_daily_applied` — Today's count
- `lla_avg_match_score` — Average match score
- `lla_cycle_duration_seconds` — How long each cycle takes (p50, p95, p99)
- `lla_errors_total` — Error count
- `lla_ai_latency_ms` — AI response time

---

## 13. Web App Setup

A full web app with authentication is available separately from the dashboard:

```bash
# Set credentials (required — no defaults)
export LLA_PASSWORD_HASH=$(python3 -c "import hashlib; print(hashlib.sha256(b'your-strong-password').hexdigest())")
export LLA_USERNAME="admin"

# Run
python webapp/app.py
# → http://localhost:8080
```

Features: login auth, CSRF protection, paginated job browser with search, recruiter directory, salary benchmarks, interview prep viewer, REST API.

---

## 14. Advanced: Proxy Setup

For residential proxy rotation:

```yaml
proxy:
  enabled: true
  proxy_list:
    - "http://ip1:port1"
    - "http://ip2:port2"
  # Or load from file:
  # proxy_file: "proxies.txt"
  rotate_per_session: true
  sticky_session_minutes: 30
```

> Note: Chrome's `--proxy-server` does NOT support authenticated proxies (user:pass@host). Use unauthenticated proxies or set up a local proxy forwarder.

---

## 15. Troubleshooting

### Chrome won't start

```bash
# Verify Chrome is installed
google-chrome --version

# For headless servers without display:
sudo apt install -y xvfb
Xvfb :99 -screen 0 1280x900x24 &
export DISPLAY=:99
python main.py
```

### Login fails / keeps asking for manual login

- Verify credentials in `config.yaml`
- LinkedIn may require 2FA — complete it in the browser window
- Set `browser.user_data_dir` to persist cookies across restarts
- Try logging in manually once, then restart the bot

### "No job cards found"

- LinkedIn may have changed their DOM. Check logs for selector errors.
- Try increasing `scheduling.scan_interval_minutes` to 15+
- Verify search terms actually return results on LinkedIn manually

### AI not answering questions

```bash
# Check if Ollama is running
curl http://localhost:11434/v1/models

# Check if LM Studio is running
curl http://localhost:1234/v1/models

# Check logs for AI errors
grep "AI error\|AI failed\|provider" logs/lla_*.log
```

### Bot gets rate-limited / CAPTCHA

- The rate limiter should detect this automatically
- Reduce `scheduling.max_applies_per_day` to 20-30
- Increase delays in scheduling section
- Enable `activity_simulation` to look more human
- Check `rate_limiter` status in logs

### Database errors

```bash
# Check DB integrity
python3 -c "
from state import State
s = State('data/state.db')
tables = [r['name'] for r in s.conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
print(f'{len(tables)} tables: {sorted(tables)}')
s.close()
"
```

### Validate your config

```bash
python3 -c "
import yaml
from validate_config import ConfigValidator
cfg = yaml.safe_load(open('config.yaml'))
v = ConfigValidator(cfg)
v.validate()
print(v.get_report())
"
```

---

## 16. Feature Reference: What Each Config Flag Does

Every feature has `enabled: true/false`. Here's the complete list:

| Feature | Config Key | Default | What Happens When Enabled |
|---------|-----------|---------|--------------------------|
| AI Form Filling | `ai.enabled` | true | Answers unknown form questions using LLM |
| Match Scoring | `match_scoring.enabled` | true | Scores each job 0-100%, skips below threshold |
| Resume Tailoring | `resume_tailoring.enabled` | true | Generates custom PDF resume per job |
| Recruiter Messaging | `recruiter_messaging.enabled` | true | Sends LinkedIn message to recruiter after applying |
| External ATS Apply | `external_apply.enabled` | true | Fills Greenhouse/Lever/Workday forms |
| Google Jobs | `google_jobs.enabled` | true | Scrapes Google for cross-platform job discovery |
| Dashboard | `dashboard.enabled` | true | 9-tab web dashboard at localhost:5000 |
| Alerts | `alerts.enabled` | false | Telegram/Discord/Slack notifications |
| Activity Simulation | `activity_simulation.enabled` | true | Human-like browsing between cycles |
| Salary Intelligence | `salary_intelligence.enabled` | true | Parses and benchmarks salary data |
| Interview Prep | `interview_prep.enabled` | true | Generates prep materials after applying |
| Success Tracking | `success_tracking.enabled` | true | ML model predicting response probability |
| Smart Scheduling | `smart_scheduling.enabled` | true | Learns optimal scan times |
| Follow-Up Engine | `follow_up.enabled` | true | Multi-touch follow-up messages (5d, 14d) |
| Network Leverage | `network_leverage.enabled` | true | Checks connections at target companies |
| Resume A/B Testing | `resume_ab_testing.enabled` | true | Tests multiple resume styles |
| Cover Letters | `cover_letter.enabled` | true | PDF cover letter per job |
| Skill Gap Analysis | `skill_gap_analysis.enabled` | true | Tracks demanded skills vs your CV |
| Company Intel | `company_intel.enabled` | true | Enriches company data (rating, size, industry) |
| Apply Timing | `apply_timing.enabled` | true | Prioritizes freshest job postings |
| Email Monitor | `email_monitor.enabled` | false | Monitors inbox for responses (needs IMAP creds) |
| Profile Optimizer | `profile_optimizer.enabled` | true | Suggests LinkedIn profile improvements |
| Fingerprint Rotation | `fingerprint.enabled` | false | Browser fingerprint randomization |
| Application Withdrawal | `application_withdrawal.enabled` | true | Auto-withdraws apps when offer received |
| Dedup Engine | `dedup.enabled` | true | Prevents applying to same job across platforms |
| JD Change Tracker | `jd_tracking.enabled` | true | Detects job description edits |
| Recruiter CRM | `recruiter_crm.enabled` | true | Tracks recruiter relationship scores |
| Apply Scheduler | `apply_scheduler.enabled` | false | Queues jobs for optimal time-of-day |
| Salary Negotiation | `salary_negotiation.enabled` | true | Generates negotiation briefs |
| ATS Status Scraper | `ats_status_scraper.enabled` | true | Checks ATS portal statuses |
| Job Watchlist | `job_watchlist.enabled` | true | Bookmark jobs for later |
| Referral Automator | `referral_automator.enabled` | true | Drafts referral request messages |
| Multi-Language | `multi_language.enabled` | true | Translates resume/CL for international jobs |
| Checkpoint | `checkpoint.enabled` | true | Crash recovery |
| Rate Limiter | `rate_limiter.enabled` | true | Dynamic throttling with ban detection |
| Metrics | `metrics.enabled` | false | Prometheus endpoint at :9090 |

---

## Quick Start Summary

```bash
# 1. Clone and install
git clone https://github.com/ankitjha67/linkedin-lightning-applier.git
cd linkedin-lightning-applier
pip install -r requirements.txt

# 2. Set up AI (pick one)
ollama pull llama3.1    # Free, local
# or use any cloud API

# 3. Configure
cp config.example.yaml config.yaml
nano config.yaml
# Fill: linkedin email/password, personal info, search terms, cv_text

# 4. Run
python main.py

# 5. Open dashboard
# http://localhost:5000

# 6. Stop
# Ctrl+C
```

That's it. The bot handles everything else automatically.
