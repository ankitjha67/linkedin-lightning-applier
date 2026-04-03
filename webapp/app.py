"""
SaaS Web Application.

Full web app with authentication, job management, recruiter directory,
salary benchmarks, interview prep, and system health monitoring.
Includes CSRF protection, input validation, error handling, and logging.
"""

import functools
import hashlib
import logging
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lla.webapp")


def create_webapp(db_path: str = "data/state.db", secret_key: str = None):
    """Create and configure the Flask web application."""
    try:
        from flask import (Flask, render_template, jsonify, request,
                          redirect, url_for, session, abort, g)
    except ImportError:
        log.error("Flask not installed. Run: pip install flask")
        return None

    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(__file__), "templates"),
                static_folder=os.path.join(os.path.dirname(__file__), "static"))
    app.secret_key = secret_key or os.environ.get("LLA_SECRET_KEY") or secrets.token_hex(32)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # ── Auth Config ───────────────────────────────────────────
    # Set via env vars or config
    AUTH_ENABLED = os.environ.get("LLA_AUTH_ENABLED", "true").lower() == "true"
    AUTH_USERNAME = os.environ.get("LLA_USERNAME", "admin")
    AUTH_PASSWORD_HASH = os.environ.get("LLA_PASSWORD_HASH", "")
    # If no hash set, use default password "changeme" (SHA-256)
    if not AUTH_PASSWORD_HASH:
        AUTH_PASSWORD_HASH = hashlib.sha256("changeme".encode()).hexdigest()

    def _hash_password(password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()

    def login_required(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if not AUTH_ENABLED:
                return f(*args, **kwargs)
            if not session.get("authenticated"):
                if request.is_json or request.path.startswith("/api/"):
                    abort(401)
                return redirect(url_for("login_page"))
            return f(*args, **kwargs)
        return decorated

    # ── Database ──────────────────────────────────────────────
    def get_db():
        if "db" not in g:
            g.db = sqlite3.connect(db_path, check_same_thread=False)
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close_db(exception):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def safe_int(value, default=0, max_val=10000):
        try:
            v = int(value)
            return min(max(v, 0), max_val)
        except (ValueError, TypeError):
            return default

    def safe_str(value, max_len=200):
        if not value:
            return ""
        return str(value)[:max_len]

    # ── CSRF Protection ───────────────────────────────────────
    @app.before_request
    def csrf_protect():
        if request.method == "POST" and not request.path.startswith("/api/"):
            token = session.get("csrf_token")
            form_token = request.form.get("csrf_token")
            if not token or token != form_token:
                abort(403)

    def generate_csrf():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(32)
        return session["csrf_token"]

    app.jinja_env.globals["csrf_token"] = generate_csrf

    # ── Error Handlers ────────────────────────────────────────
    @app.errorhandler(401)
    def unauthorized(e):
        if request.is_json:
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for("login_page"))

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"error": "Forbidden — CSRF token invalid"}) if request.is_json \
            else "Forbidden", 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}) if request.is_json else "Not found", 404

    @app.errorhandler(500)
    def server_error(e):
        log.error(f"Server error: {e}")
        return jsonify({"error": "Internal server error"}) if request.is_json \
            else "Internal server error", 500

    # ── Auth Routes ───────────────────────────────────────────
    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        if not AUTH_ENABLED:
            return redirect(url_for("index"))
        error = None
        if request.method == "POST":
            username = safe_str(request.form.get("username"))
            password = request.form.get("password", "")
            if username == AUTH_USERNAME and _hash_password(password) == AUTH_PASSWORD_HASH:
                session["authenticated"] = True
                session.permanent = True
                log.info(f"Login successful from {request.remote_addr}")
                return redirect(url_for("index"))
            else:
                error = "Invalid credentials"
                log.warning(f"Failed login attempt from {request.remote_addr}")
        return f"""<!DOCTYPE html>
<html><head><title>Login — LLA</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: #f5f5f5;
       display: flex; justify-content: center; align-items: center; height: 100vh; }}
.login {{ background: white; padding: 40px; border-radius: 12px; width: 350px;
          box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
.login h2 {{ text-align: center; color: #0a66c2; margin-bottom: 20px; }}
input {{ width: 100%; padding: 12px; margin: 8px 0; border: 1px solid #ddd;
         border-radius: 6px; box-sizing: border-box; }}
button {{ width: 100%; padding: 12px; background: #0a66c2; color: white;
          border: none; border-radius: 6px; cursor: pointer; font-size: 1em; }}
button:hover {{ background: #004182; }}
.error {{ color: #c0392b; text-align: center; margin-top: 10px; }}
</style></head><body>
<div class="login">
    <h2>LLA Dashboard</h2>
    <form method="post">
        <input type="hidden" name="csrf_token" value="{generate_csrf()}">
        <input type="text" name="username" placeholder="Username" required>
        <input type="password" name="password" placeholder="Password" required>
        <button type="submit">Login</button>
        {'<p class="error">' + error + '</p>' if error else ''}
    </form>
</div></body></html>"""

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login_page"))

    # ── Page Routes ───────────────────────────────────────────
    @app.route("/")
    @login_required
    def index():
        db = get_db()
        try:
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
            except sqlite3.OperationalError:
                stats["avg_score"] = 0

            recent = db.execute(
                "SELECT * FROM applied_jobs ORDER BY applied_at DESC LIMIT 10"
            ).fetchall()
            return render_template("index.html", stats=stats, recent=[dict(r) for r in recent])
        except Exception as e:
            log.error(f"Dashboard error: {e}")
            return f"Dashboard error: check logs", 500

    @app.route("/jobs")
    @login_required
    def jobs():
        db = get_db()
        page = safe_int(request.args.get("page"), 1, 1000)
        per_page = 50
        offset = (page - 1) * per_page

        search = safe_str(request.args.get("q"))
        params = []
        where = ""
        if search:
            where = "WHERE title LIKE ? OR company LIKE ?"
            params = [f"%{search}%", f"%{search}%"]

        try:
            total = db.execute(
                f"SELECT COUNT(*) as c FROM applied_jobs {where}", params
            ).fetchone()["c"]
            jobs_list = db.execute(
                f"SELECT * FROM applied_jobs {where} ORDER BY applied_at DESC LIMIT ? OFFSET ?",
                params + [per_page, offset]
            ).fetchall()
            return render_template("jobs.html", jobs=[dict(j) for j in jobs_list],
                                 page=page, total=total, per_page=per_page, search=search)
        except Exception as e:
            log.error(f"Jobs page error: {e}")
            return "Error loading jobs", 500

    @app.route("/recruiters")
    @login_required
    def recruiters():
        db = get_db()
        try:
            recs = db.execute(
                "SELECT * FROM recruiters ORDER BY seen_at DESC LIMIT 200"
            ).fetchall()
            return render_template("recruiters.html", recruiters=[dict(r) for r in recs])
        except Exception as e:
            log.error(f"Recruiters page error: {e}")
            return "Error loading recruiters", 500

    @app.route("/salary")
    @login_required
    def salary():
        db = get_db()
        try:
            data = db.execute(
                "SELECT * FROM salary_data ORDER BY collected_at DESC LIMIT 200"
            ).fetchall()
            return render_template("salary.html", salary_data=[dict(d) for d in data])
        except sqlite3.OperationalError:
            return render_template("salary.html", salary_data=[])

    @app.route("/interview-prep")
    @login_required
    def interview_prep():
        db = get_db()
        try:
            preps = db.execute(
                "SELECT * FROM interview_prep ORDER BY generated_at DESC LIMIT 50"
            ).fetchall()
            return render_template("interview_prep.html", preps=[dict(p) for p in preps])
        except sqlite3.OperationalError:
            return render_template("interview_prep.html", preps=[])

    # ── API Routes ────────────────────────────────────────────
    @app.route("/api/v1/stats")
    @login_required
    def api_stats():
        db = get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            daily = db.execute("SELECT * FROM daily_stats WHERE date=?", (today,)).fetchone()
            total = db.execute("SELECT COUNT(*) as c FROM applied_jobs").fetchone()["c"]
            return jsonify({
                "today": dict(daily) if daily else {"applied": 0, "skipped": 0, "failed": 0, "cycles": 0},
                "total_applied": total,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/jobs")
    @login_required
    def api_jobs():
        db = get_db()
        limit = safe_int(request.args.get("limit"), 100, 500)
        offset = safe_int(request.args.get("offset"), 0, 100000)
        try:
            jobs_list = db.execute(
                "SELECT * FROM applied_jobs ORDER BY applied_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
            return jsonify([dict(j) for j in jobs_list])
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/salary/benchmark")
    @login_required
    def api_salary_benchmark():
        db = get_db()
        title = safe_str(request.args.get("title"))
        location = safe_str(request.args.get("location"))
        try:
            query = "SELECT salary_min, salary_max, currency FROM salary_data WHERE salary_max > 0"
            params = []
            if title:
                query += " AND LOWER(title) LIKE ?"
                params.append(f"%{title.lower()}%")
            if location:
                query += " AND LOWER(location) LIKE ?"
                params.append(f"%{location.lower()}%")
            rows = db.execute(query, params).fetchall()
            if not rows:
                return jsonify({"count": 0})
            mins = sorted([r["salary_min"] for r in rows if r["salary_min"] > 0])
            maxs = sorted([r["salary_max"] for r in rows if r["salary_max"] > 0])
            n = len(mins)
            return jsonify({
                "count": len(rows),
                "currency": rows[0]["currency"],
                "median_min": mins[n // 2] if mins else 0,
                "median_max": maxs[n // 2] if maxs else 0,
                "range_min": mins[0] if mins else 0,
                "range_max": maxs[-1] if maxs else 0,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/health")
    def health():
        """Health check endpoint (no auth required)."""
        try:
            db = get_db()
            db.execute("SELECT 1").fetchone()
            return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})
        except Exception:
            return jsonify({"status": "unhealthy"}), 503

    return app


def run_webapp(host: str = "0.0.0.0", port: int = 8080, db_path: str = "data/state.db"):
    """Run the web application standalone."""
    app = create_webapp(db_path)
    if app:
        print(f"Starting LLA Web App at http://{host}:{port}")
        print(f"Default credentials: admin / changeme")
        app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_webapp()
