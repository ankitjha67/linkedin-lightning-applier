# API Reference

## Dashboard API (port 5000)

The all-in-one dashboard (`dashboard.py`) serves a 9-tab command center and exposes JSON API endpoints. No authentication required (intended for internal/LAN access).

### Dashboard Tabs

| Tab | Description |
|-----|-------------|
| **Overview** | Stat cards, application funnel, daily trend chart, match score distribution |
| **Applications** | Full application table with search, sort, filter by status/score/date |
| **Recruiters** | Recruiter CRM view with relationship scores, interaction history, follow-up queue |
| **Salary** | Salary benchmarks by role/location, top offers, negotiation brief viewer |
| **Skills** | Skill gap analysis — most-requested skills vs. your match rate, learning priorities |
| **Interview Prep** | Per-job prep materials: company research, likely questions, talking points |
| **Watchlist** | Bookmarked jobs with active/expired status, next reminder, quick-apply action |
| **Analytics** | Response rate analysis, success prediction, time-to-response trends, A/B metrics |
| **System** | Config viewer, proxy health, ATS scraper status, scheduler queue, log tail |

### GET /health

Health check endpoint.

```json
{"status": "ok", "timestamp": "2026-04-03T12:00:00"}
```

### GET /api/stats

Current statistics.

```json
{
  "today_applied": 12,
  "today_skipped": 45,
  "today_failed": 2,
  "cycles_today": 8,
  "total_applied": 340,
  "recruiters": 89,
  "visa_sponsors": 23,
  "avg_match_score": 76.3
}
```

### GET /api/jobs?limit=50

Recent applications. `limit` defaults to 100, max 500.

```json
[
  {
    "job_id": "3945612345",
    "title": "Senior Risk Manager",
    "company": "Goldman Sachs",
    "location": "London, UK",
    "salary_info": "£120K-£150K",
    "match_score": 87,
    "resume_version": "Goldman_Sachs_Senior_Risk_Manager_20260403.pdf",
    "visa_sponsorship": "yes",
    "recruiter_name": "Jane Smith",
    "applied_at": "2026-04-03 10:23:45"
  }
]
```

### GET /api/recruiters?limit=50

Recruiter directory. `limit` defaults to 200.

```json
[
  {
    "name": "Jane Smith",
    "title": "VP Talent Acquisition",
    "company": "Goldman Sachs",
    "job_title": "Senior Risk Manager",
    "profile_url": "https://www.linkedin.com/in/janesmith",
    "source": "hiring_team",
    "seen_at": "2026-04-03 10:23:00"
  }
]
```

### GET /api/visa

Visa sponsor directory.

```json
[
  {
    "company": "Goldman Sachs",
    "evidence": "Skilled Worker visa",
    "times_seen": 5,
    "first_seen": "2026-03-15 08:30:00"
  }
]
```

### GET /api/funnel

Application funnel statistics.

```json
{
  "applied": 340,
  "skipped": 1250,
  "failed": 45,
  "responses": 28
}
```

### GET /api/skills

Skill gap analysis across all applied jobs.

```json
{
  "top_required": [
    {"skill": "Python", "count": 145, "match_rate": 0.95},
    {"skill": "AWS", "count": 98, "match_rate": 0.72},
    {"skill": "Kubernetes", "count": 67, "match_rate": 0.45}
  ],
  "top_gaps": [
    {"skill": "Terraform", "count": 52, "match_rate": 0.12},
    {"skill": "Go", "count": 41, "match_rate": 0.08}
  ]
}
```

### GET /api/watchlist?status=active

Job watchlist entries. Filter by `status`: `active`, `expired`, `all` (default: `active`).

```json
[
  {
    "job_id": "3945619876",
    "title": "Staff Engineer",
    "company": "Stripe",
    "added_at": "2026-03-28 14:30:00",
    "is_active": true,
    "last_checked": "2026-04-03 06:00:00",
    "next_reminder": "2026-04-04 14:30:00"
  }
]
```

### GET /api/salary/top?limit=10

Top salary ranges across all collected data, ranked by max salary.

```json
[
  {
    "title": "Staff Engineer",
    "company": "Netflix",
    "location": "Remote, US",
    "salary_min": 350000,
    "salary_max": 500000,
    "currency": "USD"
  }
]
```

### GET /api/daily?days=30

Daily statistics for the last N days.

```json
[
  {"date": "2026-04-03", "applied": 12, "skipped": 45, "failed": 2, "cycles": 8},
  {"date": "2026-04-02", "applied": 15, "skipped": 52, "failed": 1, "cycles": 10}
]
```

### GET /api/salary?title=&location=

Salary benchmark data.

```json
{
  "count": 45,
  "currency": "GBP",
  "median_min": 85000,
  "median_max": 120000,
  "range_min": 60000,
  "range_max": 180000
}
```

---

---

## Prometheus Metrics (port 9090)

When `metrics.enabled: true`, a Prometheus-compatible endpoint is served.

### GET /metrics

Returns all metrics in Prometheus text format.

```
# TYPE lla_applications_total counter
lla_applications_total 342

# TYPE lla_daily_applied gauge
lla_daily_applied 12

# TYPE lla_avg_match_score gauge
lla_avg_match_score 76.3

# TYPE lla_cycle_duration_seconds summary
lla_cycle_duration_seconds_count 45
lla_cycle_duration_seconds_sum 2340.5
lla_cycle_duration_seconds_quantile{quantile="0.5"} 48.2
lla_cycle_duration_seconds_quantile{quantile="0.95"} 92.1
```

### GET /health

```
ok
```

---

## Web App API (port 8080)

The SaaS web app (`webapp/app.py`) requires authentication. All `/api/v1/` endpoints require a valid session.

### POST /login

Authenticate with username/password.

- Default credentials: `admin` / `changeme`
- Set custom credentials via environment variables:
  - `LLA_USERNAME`
  - `LLA_PASSWORD_HASH` (SHA-256 hex digest)

### GET /api/v1/stats

Same as dashboard `/api/stats` but with auth.

```json
{
  "today": {"applied": 12, "skipped": 45, "failed": 2, "cycles": 8},
  "total_applied": 340,
  "timestamp": "2026-04-03T12:00:00"
}
```

### GET /api/v1/jobs?limit=100&offset=0

Paginated job list.

### GET /api/v1/salary/benchmark?title=risk+manager&location=london

Salary benchmark filtered by title and/or location pattern.

```json
{
  "count": 23,
  "currency": "GBP",
  "median_min": 85000,
  "median_max": 115000,
  "range_min": 65000,
  "range_max": 160000
}
```

### GET /health

Health check (no auth required).

```json
{"status": "ok", "timestamp": "2026-04-03T12:00:00"}
```

---

## Web App Pages

| Route | Description |
|-------|-------------|
| `/` | Dashboard with stats and recent applications |
| `/jobs` | Paginated job browser with search |
| `/recruiters` | Recruiter directory |
| `/salary` | Salary data explorer |
| `/interview-prep` | Interview prep viewer |
| `/login` | Authentication page |
| `/logout` | Clear session |

---

## SQLite Direct Access

For custom queries, connect directly to the SQLite database:

```python
import sqlite3

conn = sqlite3.connect("data/state.db")
conn.row_factory = sqlite3.Row

# Top companies by application count
rows = conn.execute("""
    SELECT company, COUNT(*) as applications, AVG(match_score) as avg_score
    FROM applied_jobs
    GROUP BY company
    ORDER BY applications DESC
    LIMIT 20
""").fetchall()

for row in rows:
    print(f"{row['company']}: {row['applications']} apps, avg score {row['avg_score']:.0f}%")

# Salary benchmarks by location
rows = conn.execute("""
    SELECT location, currency,
           AVG(salary_min) as avg_min, AVG(salary_max) as avg_max,
           COUNT(*) as data_points
    FROM salary_data
    WHERE salary_max > 0
    GROUP BY location, currency
    HAVING data_points >= 3
    ORDER BY avg_max DESC
""").fetchall()

# Response rate analysis
rows = conn.execute("""
    SELECT
        CASE
            WHEN match_score >= 90 THEN '90-100'
            WHEN match_score >= 70 THEN '70-89'
            WHEN match_score >= 50 THEN '50-69'
            ELSE '0-49'
        END as score_bucket,
        COUNT(*) as total,
        SUM(CASE WHEN response_type IN ('callback','interview','offer') THEN 1 ELSE 0 END) as positive
    FROM response_tracking
    GROUP BY score_bucket
""").fetchall()
```
