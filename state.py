"""
State persistence with SQLite.
Tracks applied/skipped/failed jobs, recruiters, visa sponsorship, daily stats,
match scores, message queue, salary data, interview prep, Google Jobs, response tracking.
Exports to CSV automatically.
"""

import csv
import json
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path


class State:
    def __init__(self, db_path: str = "data/state.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
        self._migrate_tables()
        self.session_applied = 0
        self.session_skipped = 0
        self.session_failed = 0
        self.session_start = datetime.now()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS applied_jobs (
                job_id          TEXT PRIMARY KEY,
                title           TEXT,
                company         TEXT,
                location        TEXT,
                work_style      TEXT DEFAULT '',
                job_url         TEXT DEFAULT '',
                description     TEXT DEFAULT '',
                salary_info     TEXT DEFAULT '',
                experience_req  TEXT DEFAULT '',
                recruiter_name  TEXT DEFAULT '',
                recruiter_title TEXT DEFAULT '',
                hiring_manager  TEXT DEFAULT '',
                visa_sponsorship TEXT DEFAULT 'unknown',
                posted_time     TEXT DEFAULT '',
                applied_at      TEXT DEFAULT (datetime('now','localtime')),
                search_term     TEXT DEFAULT '',
                search_location TEXT DEFAULT '',
                match_score     INTEGER DEFAULT 0,
                resume_version  TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS skipped_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT,
                title           TEXT,
                company         TEXT,
                location        TEXT,
                reason          TEXT,
                visa_sponsorship TEXT DEFAULT 'unknown',
                recruiter_name  TEXT DEFAULT '',
                skipped_at      TEXT DEFAULT (datetime('now','localtime')),
                search_term     TEXT DEFAULT '',
                search_location TEXT DEFAULT '',
                match_score     INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS failed_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT,
                title           TEXT,
                company         TEXT,
                reason          TEXT,
                failed_at       TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS recruiters (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT,
                title           TEXT,
                company         TEXT,
                job_id          TEXT,
                job_title       TEXT,
                profile_url     TEXT DEFAULT '',
                source          TEXT DEFAULT 'hiring_team',
                seen_at         TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(name, company, job_id)
            );

            CREATE TABLE IF NOT EXISTS visa_sponsors (
                company         TEXT PRIMARY KEY,
                evidence        TEXT,
                job_id          TEXT,
                first_seen      TEXT DEFAULT (datetime('now','localtime')),
                times_seen      INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                date            TEXT PRIMARY KEY,
                applied         INTEGER DEFAULT 0,
                skipped         INTEGER DEFAULT 0,
                failed          INTEGER DEFAULT 0,
                cycles          INTEGER DEFAULT 0
            );

            -- Match Scores
            CREATE TABLE IF NOT EXISTS match_scores (
                job_id          TEXT PRIMARY KEY,
                title           TEXT,
                company         TEXT,
                score           INTEGER DEFAULT 0,
                skill_matches   TEXT DEFAULT '',
                missing_skills  TEXT DEFAULT '',
                explanation     TEXT DEFAULT '',
                scored_at       TEXT DEFAULT (datetime('now','localtime'))
            );

            -- Recruiter Message Queue
            CREATE TABLE IF NOT EXISTS message_queue (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT,
                recruiter_name  TEXT,
                profile_url     TEXT,
                message_text    TEXT,
                scheduled_at    TEXT,
                sent_at         TEXT,
                status          TEXT DEFAULT 'pending',
                company         TEXT,
                job_title       TEXT,
                created_at      TEXT DEFAULT (datetime('now','localtime'))
            );

            -- Salary Intelligence
            CREATE TABLE IF NOT EXISTS salary_data (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT,
                title           TEXT,
                company         TEXT,
                location        TEXT,
                salary_raw      TEXT,
                salary_min      REAL DEFAULT 0,
                salary_max      REAL DEFAULT 0,
                currency        TEXT DEFAULT '',
                period          TEXT DEFAULT 'yearly',
                source          TEXT DEFAULT 'linkedin',
                collected_at    TEXT DEFAULT (datetime('now','localtime'))
            );

            -- Interview Prep
            CREATE TABLE IF NOT EXISTS interview_prep (
                job_id          TEXT PRIMARY KEY,
                company         TEXT,
                title           TEXT,
                company_research TEXT DEFAULT '',
                likely_questions TEXT DEFAULT '',
                talking_points  TEXT DEFAULT '',
                generated_at    TEXT DEFAULT (datetime('now','localtime'))
            );

            -- Google Jobs Discovery
            CREATE TABLE IF NOT EXISTS google_jobs (
                google_job_id   TEXT PRIMARY KEY,
                title           TEXT,
                company         TEXT,
                location        TEXT,
                description     TEXT DEFAULT '',
                salary_raw      TEXT DEFAULT '',
                source_url      TEXT DEFAULT '',
                source_platform TEXT DEFAULT '',
                linkedin_job_id TEXT DEFAULT '',
                discovered_at   TEXT DEFAULT (datetime('now','localtime')),
                status          TEXT DEFAULT 'new'
            );

            -- Response Tracking
            CREATE TABLE IF NOT EXISTS response_tracking (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT,
                title           TEXT,
                company         TEXT,
                applied_at      TEXT,
                response_type   TEXT DEFAULT '',
                response_at     TEXT,
                match_score     INTEGER DEFAULT 0,
                resume_version  TEXT DEFAULT '',
                recruiter_messaged INTEGER DEFAULT 0,
                days_to_response REAL DEFAULT 0,
                notes           TEXT DEFAULT ''
            );

            -- Hiring Velocity (Smart Scheduling)
            CREATE TABLE IF NOT EXISTS hiring_velocity (
                company         TEXT,
                title_pattern   TEXT,
                first_seen      TEXT,
                last_seen       TEXT,
                days_active     INTEGER DEFAULT 0,
                filled          INTEGER DEFAULT 0,
                PRIMARY KEY (company, title_pattern)
            );
        """)
        self.conn.commit()

    def _migrate_tables(self):
        """Add new columns to existing tables if they don't exist (backward compat)."""
        migrations = [
            ("applied_jobs", "match_score", "INTEGER DEFAULT 0"),
            ("applied_jobs", "resume_version", "TEXT DEFAULT ''"),
            ("skipped_jobs", "match_score", "INTEGER DEFAULT 0"),
        ]
        for table, col, col_type in migrations:
            try:
                self.conn.execute(f"SELECT {col} FROM {table} LIMIT 1")
            except sqlite3.OperationalError:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                self.conn.commit()

    # ── Applied Jobs ──────────────────────────────────────────
    def is_applied(self, job_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM applied_jobs WHERE job_id=?", (job_id,)
        ).fetchone() is not None

    def is_skipped(self, job_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM skipped_jobs WHERE job_id=?", (job_id,)
        ).fetchone() is not None

    def mark_applied(self, job_id: str, title: str = "", company: str = "",
                     location: str = "", work_style: str = "", job_url: str = "",
                     description: str = "", salary_info: str = "",
                     experience_req: str = "", recruiter_name: str = "",
                     recruiter_title: str = "", hiring_manager: str = "",
                     visa_sponsorship: str = "unknown", posted_time: str = "",
                     search_term: str = "", search_location: str = "",
                     match_score: int = 0, resume_version: str = ""):
        self.conn.execute("""
            INSERT OR REPLACE INTO applied_jobs
            (job_id, title, company, location, work_style, job_url, description,
             salary_info, experience_req, recruiter_name, recruiter_title,
             hiring_manager, visa_sponsorship, posted_time, search_term, search_location,
             match_score, resume_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (job_id, title, company, location, work_style, job_url,
              description[:2000] if description else "",
              salary_info, experience_req, recruiter_name, recruiter_title,
              hiring_manager, visa_sponsorship, posted_time, search_term, search_location,
              match_score, resume_version))
        self.conn.commit()
        self.session_applied += 1
        self._inc_daily("applied")

    def mark_skipped(self, job_id: str = "", title: str = "", company: str = "",
                     location: str = "", reason: str = "",
                     visa_sponsorship: str = "unknown", recruiter_name: str = "",
                     search_term: str = "", search_location: str = "",
                     match_score: int = 0):
        self.conn.execute("""
            INSERT INTO skipped_jobs
            (job_id, title, company, location, reason, visa_sponsorship,
             recruiter_name, search_term, search_location, match_score)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (job_id, title, company, location, reason, visa_sponsorship,
              recruiter_name, search_term, search_location, match_score))
        self.conn.commit()
        self.session_skipped += 1
        self._inc_daily("skipped")

    def mark_failed(self, job_id: str, title: str = "", company: str = "", reason: str = ""):
        self.conn.execute(
            "INSERT INTO failed_jobs (job_id, title, company, reason) VALUES (?,?,?,?)",
            (job_id, title, company, reason))
        self.conn.commit()
        self.session_failed += 1
        self._inc_daily("failed")

    # ── Recruiters ────────────────────────────────────────────
    def save_recruiter(self, name: str, title: str = "", company: str = "",
                       job_id: str = "", job_title: str = "",
                       profile_url: str = "", source: str = "hiring_team"):
        if not name or not name.strip():
            return
        self.conn.execute("""
            INSERT OR IGNORE INTO recruiters
            (name, title, company, job_id, job_title, profile_url, source)
            VALUES (?,?,?,?,?,?,?)
        """, (name.strip(), title.strip(), company.strip(), job_id, job_title, profile_url, source))
        self.conn.commit()

    # ── Visa Sponsors ─────────────────────────────────────────
    def save_visa_sponsor(self, company: str, evidence: str = "", job_id: str = ""):
        if not company:
            return
        existing = self.conn.execute(
            "SELECT times_seen FROM visa_sponsors WHERE company=?", (company,)
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE visa_sponsors SET times_seen=times_seen+1, evidence=? WHERE company=?",
                (evidence, company))
        else:
            self.conn.execute(
                "INSERT INTO visa_sponsors (company, evidence, job_id) VALUES (?,?,?)",
                (company, evidence, job_id))
        self.conn.commit()

    # ── Match Scores ──────────────────────────────────────────
    def save_match_score(self, job_id: str, title: str = "", company: str = "",
                         score: int = 0, skill_matches: str = "",
                         missing_skills: str = "", explanation: str = ""):
        self.conn.execute("""
            INSERT OR REPLACE INTO match_scores
            (job_id, title, company, score, skill_matches, missing_skills, explanation)
            VALUES (?,?,?,?,?,?,?)
        """, (job_id, title, company, score, skill_matches, missing_skills, explanation))
        self.conn.commit()

    def get_match_score(self, job_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM match_scores WHERE job_id=?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── Message Queue ─────────────────────────────────────────
    def queue_message(self, job_id: str, recruiter_name: str, profile_url: str,
                      message_text: str, scheduled_at: str,
                      company: str = "", job_title: str = ""):
        self.conn.execute("""
            INSERT INTO message_queue
            (job_id, recruiter_name, profile_url, message_text, scheduled_at, company, job_title)
            VALUES (?,?,?,?,?,?,?)
        """, (job_id, recruiter_name, profile_url, message_text, scheduled_at, company, job_title))
        self.conn.commit()

    def get_pending_messages(self) -> list[dict]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = self.conn.execute("""
            SELECT * FROM message_queue
            WHERE status='pending' AND scheduled_at <= ?
            ORDER BY scheduled_at ASC
        """, (now,)).fetchall()
        return [dict(r) for r in rows]

    def update_message_status(self, msg_id: int, status: str, sent_at: str = ""):
        self.conn.execute("""
            UPDATE message_queue SET status=?, sent_at=? WHERE id=?
        """, (status, sent_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg_id))
        self.conn.commit()

    def daily_message_count(self) -> int:
        today = self._today()
        row = self.conn.execute("""
            SELECT COUNT(*) as c FROM message_queue
            WHERE status='sent' AND date(sent_at)=?
        """, (today,)).fetchone()
        return row["c"] if row else 0

    # ── Salary Data ───────────────────────────────────────────
    def save_salary_data(self, job_id: str, title: str = "", company: str = "",
                         location: str = "", salary_raw: str = "",
                         salary_min: float = 0, salary_max: float = 0,
                         currency: str = "", period: str = "yearly",
                         source: str = "linkedin"):
        self.conn.execute("""
            INSERT OR REPLACE INTO salary_data
            (job_id, title, company, location, salary_raw, salary_min, salary_max,
             currency, period, source)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (job_id, title, company, location, salary_raw, salary_min, salary_max,
              currency, period, source))
        self.conn.commit()

    def get_salary_benchmark(self, title_pattern: str = "", location_pattern: str = "") -> dict:
        query = "SELECT salary_min, salary_max, currency, title, location FROM salary_data WHERE salary_max > 0"
        params = []
        if title_pattern:
            query += " AND LOWER(title) LIKE ?"
            params.append(f"%{title_pattern.lower()}%")
        if location_pattern:
            query += " AND LOWER(location) LIKE ?"
            params.append(f"%{location_pattern.lower()}%")

        rows = self.conn.execute(query, params).fetchall()
        if not rows:
            return {"count": 0, "median_min": 0, "median_max": 0, "currency": ""}

        mins = sorted([r["salary_min"] for r in rows if r["salary_min"] > 0])
        maxs = sorted([r["salary_max"] for r in rows if r["salary_max"] > 0])

        def median(lst):
            if not lst:
                return 0
            n = len(lst)
            return lst[n // 2] if n % 2 else (lst[n // 2 - 1] + lst[n // 2]) / 2

        return {
            "count": len(rows),
            "median_min": median(mins),
            "median_max": median(maxs),
            "min_salary": mins[0] if mins else 0,
            "max_salary": maxs[-1] if maxs else 0,
            "currency": rows[0]["currency"] if rows else "",
        }

    # ── Interview Prep ────────────────────────────────────────
    def save_interview_prep(self, job_id: str, company: str = "", title: str = "",
                            company_research: str = "", likely_questions: str = "",
                            talking_points: str = ""):
        self.conn.execute("""
            INSERT OR REPLACE INTO interview_prep
            (job_id, company, title, company_research, likely_questions, talking_points)
            VALUES (?,?,?,?,?,?)
        """, (job_id, company, title, company_research, likely_questions, talking_points))
        self.conn.commit()

    def get_interview_prep(self, job_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM interview_prep WHERE job_id=?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── Google Jobs ───────────────────────────────────────────
    def save_google_job(self, google_job_id: str, title: str = "", company: str = "",
                        location: str = "", description: str = "", salary_raw: str = "",
                        source_url: str = "", source_platform: str = "",
                        linkedin_job_id: str = ""):
        self.conn.execute("""
            INSERT OR IGNORE INTO google_jobs
            (google_job_id, title, company, location, description, salary_raw,
             source_url, source_platform, linkedin_job_id)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (google_job_id, title, company, location, description[:2000] if description else "",
              salary_raw, source_url, source_platform, linkedin_job_id))
        self.conn.commit()

    def get_google_jobs_by_status(self, status: str = "new") -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM google_jobs WHERE status=? ORDER BY discovered_at ASC", (status,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_google_job_status(self, google_job_id: str, status: str):
        self.conn.execute(
            "UPDATE google_jobs SET status=? WHERE google_job_id=?", (status, google_job_id))
        self.conn.commit()

    # ── Response Tracking ─────────────────────────────────────
    def save_response(self, job_id: str, title: str = "", company: str = "",
                      applied_at: str = "", response_type: str = "",
                      match_score: int = 0, resume_version: str = "",
                      recruiter_messaged: bool = False, notes: str = ""):
        days = 0
        if applied_at:
            try:
                applied_dt = datetime.strptime(applied_at, "%Y-%m-%d %H:%M:%S")
                days = (datetime.now() - applied_dt).total_seconds() / 86400
            except (ValueError, TypeError):
                pass
        self.conn.execute("""
            INSERT INTO response_tracking
            (job_id, title, company, applied_at, response_type, response_at,
             match_score, resume_version, recruiter_messaged, days_to_response, notes)
            VALUES (?,?,?,?,?,datetime('now','localtime'),?,?,?,?,?)
        """, (job_id, title, company, applied_at, response_type,
              match_score, resume_version, 1 if recruiter_messaged else 0, days, notes))
        self.conn.commit()

    def get_response_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) as c FROM response_tracking").fetchone()["c"]
        by_type = self.conn.execute("""
            SELECT response_type, COUNT(*) as c FROM response_tracking
            GROUP BY response_type
        """).fetchall()
        avg_days = self.conn.execute(
            "SELECT AVG(days_to_response) as avg_d FROM response_tracking WHERE days_to_response > 0"
        ).fetchone()
        return {
            "total_responses": total,
            "by_type": {r["response_type"]: r["c"] for r in by_type},
            "avg_days_to_response": round(avg_days["avg_d"], 1) if avg_days["avg_d"] else 0,
        }

    # ── Hiring Velocity ───────────────────────────────────────
    def update_hiring_velocity(self, company: str, title_pattern: str):
        today = self._today()
        existing = self.conn.execute(
            "SELECT * FROM hiring_velocity WHERE company=? AND title_pattern=?",
            (company, title_pattern)
        ).fetchone()
        if existing:
            first = existing["first_seen"]
            days = (date.today() - date.fromisoformat(first)).days
            self.conn.execute("""
                UPDATE hiring_velocity SET last_seen=?, days_active=?
                WHERE company=? AND title_pattern=?
            """, (today, days, company, title_pattern))
        else:
            self.conn.execute("""
                INSERT INTO hiring_velocity (company, title_pattern, first_seen, last_seen)
                VALUES (?,?,?,?)
            """, (company, title_pattern, today, today))
        self.conn.commit()

    def get_fast_hiring_companies(self, max_days: int = 7) -> list[dict]:
        rows = self.conn.execute("""
            SELECT company, title_pattern, days_active, first_seen, last_seen
            FROM hiring_velocity
            WHERE filled=1 AND days_active <= ? AND days_active > 0
            ORDER BY days_active ASC
        """, (max_days,)).fetchall()
        return [dict(r) for r in rows]

    # ── Daily Stats ───────────────────────────────────────────
    def _today(self) -> str:
        return date.today().isoformat()

    def _ensure_today(self):
        self.conn.execute("INSERT OR IGNORE INTO daily_stats (date) VALUES (?)", (self._today(),))

    def _inc_daily(self, field: str):
        self._ensure_today()
        self.conn.execute(f"UPDATE daily_stats SET {field}={field}+1 WHERE date=?", (self._today(),))
        self.conn.commit()

    def inc_cycles(self):
        self._ensure_today()
        self.conn.execute("UPDATE daily_stats SET cycles=cycles+1 WHERE date=?", (self._today(),))
        self.conn.commit()

    def daily_applied_count(self) -> int:
        self._ensure_today()
        row = self.conn.execute("SELECT applied FROM daily_stats WHERE date=?", (self._today(),)).fetchone()
        return row["applied"] if row else 0

    def total_applied(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as c FROM applied_jobs").fetchone()
        return row["c"]

    def get_daily_stats(self, days: int = 30) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM daily_stats ORDER BY date DESC LIMIT ?
        """, (days,)).fetchall()
        return [dict(r) for r in rows]

    def get_all_applied(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM applied_jobs ORDER BY applied_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_all_recruiters(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM recruiters ORDER BY seen_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_all_visa_sponsors(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM visa_sponsors ORDER BY times_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_funnel_stats(self) -> dict:
        applied = self.conn.execute("SELECT COUNT(*) as c FROM applied_jobs").fetchone()["c"]
        skipped = self.conn.execute("SELECT COUNT(*) as c FROM skipped_jobs").fetchone()["c"]
        failed = self.conn.execute("SELECT COUNT(*) as c FROM failed_jobs").fetchone()["c"]
        responses = self.conn.execute("SELECT COUNT(*) as c FROM response_tracking").fetchone()["c"]
        return {"applied": applied, "skipped": skipped, "failed": failed, "responses": responses}

    # ── CSV EXPORT ────────────────────────────────────────────
    def export_csv(self, export_dir: str = "data", cfg: dict = None):
        """Export all tables to CSV files."""
        exp = cfg.get("export", {}) if cfg else {}
        out = Path(export_dir)
        out.mkdir(parents=True, exist_ok=True)

        self._export_table("applied_jobs", out / exp.get("applied_csv", "applied_jobs.csv"),
            ["job_id", "title", "company", "location", "work_style", "job_url",
             "salary_info", "experience_req", "recruiter_name", "recruiter_title",
             "hiring_manager", "visa_sponsorship", "posted_time", "applied_at",
             "search_term", "search_location", "match_score", "resume_version"],
            order="applied_at DESC")

        self._export_table("skipped_jobs", out / exp.get("skipped_csv", "skipped_jobs.csv"),
            ["job_id", "title", "company", "location", "reason",
             "visa_sponsorship", "recruiter_name", "skipped_at",
             "search_term", "search_location", "match_score"],
            order="skipped_at DESC")

        self._export_table("recruiters", out / exp.get("recruiters_csv", "recruiters.csv"),
            ["name", "title", "company", "job_title", "profile_url", "source", "seen_at"],
            order="seen_at DESC")

        self._export_table("visa_sponsors", out / exp.get("visa_sponsors_csv", "visa_sponsors.csv"),
            ["company", "evidence", "times_seen", "first_seen"],
            order="times_seen DESC")

        self._export_table("match_scores", out / "match_scores.csv",
            ["job_id", "title", "company", "score", "skill_matches",
             "missing_skills", "explanation", "scored_at"],
            order="scored_at DESC")

        self._export_table("salary_data", out / exp.get("salary_csv", "salary_data.csv"),
            ["job_id", "title", "company", "location", "salary_raw",
             "salary_min", "salary_max", "currency", "period", "source", "collected_at"],
            order="collected_at DESC")

        self._export_table("interview_prep", out / "interview_prep.csv",
            ["job_id", "company", "title", "company_research",
             "likely_questions", "talking_points", "generated_at"],
            order="generated_at DESC")

    def _export_table(self, table: str, filepath: Path, columns: list, order: str = ""):
        order_clause = f" ORDER BY {order}" if order else ""
        try:
            rows = self.conn.execute(f"SELECT {','.join(columns)} FROM {table}{order_clause}").fetchall()
        except sqlite3.OperationalError:
            return
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([row[c] for c in columns])

    # ── Summary ───────────────────────────────────────────────
    def session_summary(self) -> str:
        elapsed = datetime.now() - self.session_start
        mins = int(elapsed.total_seconds() / 60)
        sponsors = self.conn.execute("SELECT COUNT(*) as c FROM visa_sponsors").fetchone()["c"]
        recruiters = self.conn.execute("SELECT COUNT(*) as c FROM recruiters").fetchone()["c"]
        scores = self.conn.execute("SELECT AVG(score) as avg_s FROM match_scores WHERE score > 0").fetchone()
        avg_score = round(scores["avg_s"], 1) if scores["avg_s"] else 0
        return (
            f"Session: {self.session_applied}A {self.session_skipped}S {self.session_failed}F in {mins}min | "
            f"Today: {self.daily_applied_count()} applied | "
            f"All-time: {self.total_applied()} applied | "
            f"Recruiters tracked: {recruiters} | "
            f"Visa sponsors found: {sponsors} | "
            f"Avg match score: {avg_score}"
        )

    def close(self):
        self.conn.close()
