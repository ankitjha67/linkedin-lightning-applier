# LinkedIn Lightning Applier

Autonomous LinkedIn Easy Apply bot. Scans for new jobs every 10 minutes, applies the moment they appear, tracks recruiters, detects visa sponsorship, answers unknown form questions using local AI — all running 24/7 from your machine.

Built because the difference between "applied 2 minutes after posting" and "applied 24 hours later" is the difference between getting an interview and getting buried in a pile of 500 applicants.

## What It Actually Does

The bot runs in a loop. Every cycle:

1. Searches LinkedIn for jobs matching your terms across all your configured locations
2. Starts with "Past hour" filter — if nothing found, widens to 2h → 6h → 12h → 24h → week
3. For each job: clicks the card, reads the full description, extracts hiring team names, checks for visa sponsorship keywords
4. Filters by blacklisted companies, bad words in descriptions, experience requirements, visa-only mode
5. Clicks Easy Apply, fills every form field (keyword match first, AI fallback for unknowns), submits
6. Saves everything to SQLite + auto-exports 4 CSV files every cycle
7. Sleeps with random jitter, then repeats

## Features

**Core engine:** Multi-location cycling, adaptive time filter widening, cross-search deduplication, "no results" detection (ignores LinkedIn's suggested/recommended jobs when your filter returns nothing).

**AI form filling:** 8 LLM providers supported. Keyword match handles 90% of questions for free. AI handles the rest using your CV as context. Answers are cached in SQLite — same question never hits the API twice. Supports primary + fallback provider chain (e.g. LM Studio primary, Ollama fallback).

**Tracking:** Every applied/skipped/failed job logged with full details. Recruiter names and titles extracted from "Meet the hiring team" section. Visa sponsorship positive/negative detection. Daily stats.

**Ban prevention:** undetected-chromedriver, daily/cycle caps, randomized delays, active hours, cycle jitter, human-like scrolling.

## Quick Start

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

## Requirements

- Python 3.10+ (tested on 3.12 and 3.14)
- Google Chrome (stable channel)
- `pip install -r requirements.txt`

For AI form filling (optional but recommended):
- [Ollama](https://ollama.ai) with any model (`ollama pull llama3.1`)
- OR [LM Studio](https://lmstudio.ai) with any model loaded
- OR any OpenAI-compatible API (OpenAI, Anthropic, Gemini, DeepSeek, Groq, Together)

## Configuration

All settings live in `config.yaml`. The bot hot-reloads this file every cycle — edit while running, changes apply next scan.

**`config.example.yaml`** is the template. Copy it to `config.yaml` and fill in your details. `config.yaml` is gitignored (contains credentials).

Key sections:

```yaml
linkedin:
  email: "you@example.com"
  password: "your-password"

search:
  search_terms: ["Credit Risk Manager", "Basel III", "Risk Analyst"]
  search_locations: ["London, England, United Kingdom", "Singapore", "Toronto, Ontario, Canada"]
  date_posted: "Past hour"          # Widens automatically if no results

ai:
  enabled: true
  provider: "ollama"                # or lmstudio, openai, anthropic, gemini, etc.
  fallback_provider: "ollama"
  fallback_model: "llama3.1"

browser:
  user_data_dir: "C:/Users/YOU/chrome-lla-profile"  # Saves cookies — login once, skip forever
```

## Output

The `data/` folder (auto-created) contains:

| File | What's in it |
|---|---|
| `applied_jobs.csv` | Every job applied to — title, company, salary, recruiter, visa status, timestamp |
| `skipped_jobs.csv` | Every skipped job with reason |
| `recruiters.csv` | Hiring team members — name, title, company, LinkedIn URL |
| `visa_sponsors.csv` | Companies confirmed to sponsor visas |
| `state.db` | SQLite database (all of the above, queryable) |

## Architecture

```
config.yaml     ← All settings (hot-reloadable)
main.py         ← Orchestrator: scheduling, filtering, rate limiting
linkedin.py     ← Browser: login, search, card scrolling, Easy Apply flow
ai.py           ← Multi-provider LLM: answer questions, cover letters
state.py        ← SQLite: jobs, recruiters, visa sponsors, answer cache, CSV export
```

2,400 lines across 4 modules.

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

Set `provider` and `fallback_provider` in config. The bot tries keyword matching first (free, instant), then primary AI, then fallback AI.

## Inspired By

[GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn) — the original Python Selenium bot with 1.9K+ stars. This project takes the core idea and rebuilds it with a single YAML config, multi-provider AI, recruiter tracking, visa detection, adaptive time filters, and virtual-scroll-aware card handling.

## License

MIT
