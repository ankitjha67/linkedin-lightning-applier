"""
SaaS Web Application.

Full web app with user dashboard, job management, and configuration.
Can be run standalone or integrated with the main bot.
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lla.webapp")


def create_webapp(db_path: str = "data/state.db"):
    """Create and configure the Flask web application."""
    try:
        from flask import Flask, render_template, jsonify, request, redirect, url_for, session
    except ImportError:
        log.error("Flask not installed. Run: pip install flask")
        return None

    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(__file__), "templates"),
                static_folder=os.path.join(os.path.dirname(__file__), "static"))
    app.secret_key = os.urandom(24)

    def get_db():
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @app.route("/")
    def index():
        db = get_db()
        stats = {
            "total_applied": db.execute("SELECT COUNT(*) as c FROM applied_jobs").fetchone()["c"],
            "total_skipped": db.execute("SELECT COUNT(*) as c FROM skipped_jobs").fetchone()["c"],
            "recruiters": db.execute("SELECT COUNT(*) as c FROM recruiters").fetchone()["c"],
            "visa_sponsors": db.execute("SELECT COUNT(*) as c FROM visa_sponsors").fetchone()["c"],
        }
        try:
            avg_score = db.execute(
                "SELECT AVG(score) as s FROM match_scores WHERE score > 0"
            ).fetchone()
            stats["avg_score"] = round(avg_score["s"], 1) if avg_score["s"] else 0
        except Exception:
            stats["avg_score"] = 0

        recent = db.execute(
            "SELECT * FROM applied_jobs ORDER BY applied_at DESC LIMIT 10"
        ).fetchall()
        db.close()
        return render_template("index.html", stats=stats, recent=[dict(r) for r in recent])

    @app.route("/jobs")
    def jobs():
        db = get_db()
        page = request.args.get("page", 1, type=int)
        per_page = 50
        offset = (page - 1) * per_page
        jobs = db.execute(
            "SELECT * FROM applied_jobs ORDER BY applied_at DESC LIMIT ? OFFSET ?",
            (per_page, offset)
        ).fetchall()
        total = db.execute("SELECT COUNT(*) as c FROM applied_jobs").fetchone()["c"]
        db.close()
        return render_template("jobs.html", jobs=[dict(j) for j in jobs],
                             page=page, total=total, per_page=per_page)

    @app.route("/recruiters")
    def recruiters():
        db = get_db()
        recs = db.execute(
            "SELECT * FROM recruiters ORDER BY seen_at DESC LIMIT 200"
        ).fetchall()
        db.close()
        return render_template("recruiters.html", recruiters=[dict(r) for r in recs])

    @app.route("/salary")
    def salary():
        db = get_db()
        data = db.execute(
            "SELECT * FROM salary_data ORDER BY collected_at DESC LIMIT 200"
        ).fetchall()
        db.close()
        return render_template("salary.html", salary_data=[dict(d) for d in data])

    @app.route("/interview-prep")
    def interview_prep():
        db = get_db()
        preps = db.execute(
            "SELECT * FROM interview_prep ORDER BY generated_at DESC LIMIT 50"
        ).fetchall()
        db.close()
        return render_template("interview_prep.html", preps=[dict(p) for p in preps])

    @app.route("/api/v1/stats")
    def api_stats():
        db = get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        daily = db.execute("SELECT * FROM daily_stats WHERE date=?", (today,)).fetchone()
        total = db.execute("SELECT COUNT(*) as c FROM applied_jobs").fetchone()["c"]
        db.close()
        return jsonify({
            "today": dict(daily) if daily else {},
            "total_applied": total,
            "timestamp": datetime.now().isoformat(),
        })

    @app.route("/api/v1/jobs")
    def api_jobs():
        db = get_db()
        limit = request.args.get("limit", 100, type=int)
        jobs = db.execute(
            "SELECT * FROM applied_jobs ORDER BY applied_at DESC LIMIT ?", (limit,)
        ).fetchall()
        db.close()
        return jsonify([dict(j) for j in jobs])

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

    return app


def run_webapp(host: str = "0.0.0.0", port: int = 8080, db_path: str = "data/state.db"):
    """Run the web application standalone."""
    app = create_webapp(db_path)
    if app:
        app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_webapp()
