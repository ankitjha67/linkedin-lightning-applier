"""
State persistence with SQLite.
Tracks applied/skipped/failed jobs, recruiters, visa sponsorship, daily stats.
Exports to CSV automatically.
"""

import csv
import sqlite3
from datetime import datetime, date
from pathlib import Path


class State:
    def __init__(self, db_path: str = "data/state.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
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
                search_location TEXT DEFAULT ''
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
                search_location TEXT DEFAULT ''
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
        """)
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
                     search_term: str = "", search_location: str = ""):
        self.conn.execute("""
            INSERT OR REPLACE INTO applied_jobs
            (job_id, title, company, location, work_style, job_url, description,
             salary_info, experience_req, recruiter_name, recruiter_title,
             hiring_manager, visa_sponsorship, posted_time, search_term, search_location)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (job_id, title, company, location, work_style, job_url,
              description[:2000] if description else "",  # Truncate long descriptions
              salary_info, experience_req, recruiter_name, recruiter_title,
              hiring_manager, visa_sponsorship, posted_time, search_term, search_location))
        self.conn.commit()
        self.session_applied += 1
        self._inc_daily("applied")

    def mark_skipped(self, job_id: str = "", title: str = "", company: str = "",
                     location: str = "", reason: str = "",
                     visa_sponsorship: str = "unknown", recruiter_name: str = "",
                     search_term: str = "", search_location: str = ""):
        self.conn.execute("""
            INSERT INTO skipped_jobs
            (job_id, title, company, location, reason, visa_sponsorship,
             recruiter_name, search_term, search_location)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (job_id, title, company, location, reason, visa_sponsorship,
              recruiter_name, search_term, search_location))
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
             "search_term", "search_location"],
            order="applied_at DESC")

        self._export_table("skipped_jobs", out / exp.get("skipped_csv", "skipped_jobs.csv"),
            ["job_id", "title", "company", "location", "reason",
             "visa_sponsorship", "recruiter_name", "skipped_at",
             "search_term", "search_location"],
            order="skipped_at DESC")

        self._export_table("recruiters", out / exp.get("recruiters_csv", "recruiters.csv"),
            ["name", "title", "company", "job_title", "profile_url", "source", "seen_at"],
            order="seen_at DESC")

        self._export_table("visa_sponsors", out / exp.get("visa_sponsors_csv", "visa_sponsors.csv"),
            ["company", "evidence", "times_seen", "first_seen"],
            order="times_seen DESC")

    def _export_table(self, table: str, filepath: Path, columns: list, order: str = ""):
        order_clause = f" ORDER BY {order}" if order else ""
        rows = self.conn.execute(f"SELECT {','.join(columns)} FROM {table}{order_clause}").fetchall()
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
        return (
            f"Session: {self.session_applied}A {self.session_skipped}S {self.session_failed}F in {mins}min | "
            f"Today: {self.daily_applied_count()} applied | "
            f"All-time: {self.total_applied()} applied | "
            f"Recruiters tracked: {recruiters} | "
            f"Visa sponsors found: {sponsors}"
        )

    def close(self):
        self.conn.close()
