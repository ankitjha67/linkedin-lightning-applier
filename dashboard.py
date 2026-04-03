"""
Real-time Monitoring Dashboard.

Flask-based web dashboard showing live stats, applied jobs table,
recruiter directory, visa sponsor list, application funnel, and daily charts.
Runs in a background thread alongside the main bot.
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lla.dashboard")

# HTML template embedded directly to avoid external template files
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LinkedIn Lightning Applier — Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: #0a0a0a; color: #e0e0e0; padding: 20px; }
        h1 { text-align: center; margin-bottom: 20px; color: #00b4d8; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                      gap: 15px; margin-bottom: 30px; }
        .stat-card { background: #1a1a2e; border-radius: 12px; padding: 20px; text-align: center;
                     border: 1px solid #16213e; }
        .stat-card .value { font-size: 2.5em; font-weight: bold; color: #00b4d8; }
        .stat-card .label { font-size: 0.9em; color: #888; margin-top: 5px; }
        .stat-card.green .value { color: #00f593; }
        .stat-card.yellow .value { color: #ffd60a; }
        .stat-card.red .value { color: #ef476f; }
        .section { margin-bottom: 30px; }
        .section h2 { margin-bottom: 15px; color: #00b4d8; border-bottom: 1px solid #16213e;
                      padding-bottom: 8px; }
        table { width: 100%; border-collapse: collapse; background: #1a1a2e; border-radius: 8px;
                overflow: hidden; }
        th { background: #16213e; padding: 12px 15px; text-align: left; font-weight: 600;
             color: #00b4d8; }
        td { padding: 10px 15px; border-bottom: 1px solid #16213e; }
        tr:hover { background: #16213e; }
        .score { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.85em;
                 font-weight: bold; }
        .score-high { background: #00f593; color: #000; }
        .score-mid { background: #ffd60a; color: #000; }
        .score-low { background: #ef476f; color: #fff; }
        .funnel { display: flex; justify-content: center; align-items: center; gap: 10px;
                  margin: 20px 0; }
        .funnel-step { text-align: center; padding: 15px 25px; border-radius: 8px;
                       background: #16213e; min-width: 120px; }
        .funnel-step .count { font-size: 1.8em; font-weight: bold; }
        .funnel-arrow { font-size: 2em; color: #444; }
        .refresh-note { text-align: center; color: #555; font-size: 0.85em; margin-top: 20px; }
        .visa-badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
                      font-size: 0.8em; }
        .visa-yes { background: #00f593; color: #000; }
        .visa-no { background: #ef476f; color: #fff; }
        .visa-unknown { background: #444; color: #aaa; }
        @media (max-width: 768px) {
            body { padding: 10px; }
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            th, td { padding: 8px 10px; font-size: 0.85em; }
        }
    </style>
</head>
<body>
    <h1>⚡ LinkedIn Lightning Applier</h1>
    <div id="content">Loading...</div>
    <p class="refresh-note">Auto-refreshes every <span id="interval">30</span>s</p>
    <script>
        function scoreClass(s) { return s >= 70 ? 'score-high' : s >= 50 ? 'score-mid' : 'score-low'; }
        function visaClass(v) { return v === 'yes' ? 'visa-yes' : v === 'no' ? 'visa-no' : 'visa-unknown'; }
        function truncate(s, n) { return s && s.length > n ? s.substring(0, n) + '...' : s || ''; }

        async function refresh() {
            try {
                const [stats, jobs, recruiters, visa, funnel] = await Promise.all([
                    fetch('/api/stats').then(r => r.json()),
                    fetch('/api/jobs?limit=50').then(r => r.json()),
                    fetch('/api/recruiters?limit=50').then(r => r.json()),
                    fetch('/api/visa').then(r => r.json()),
                    fetch('/api/funnel').then(r => r.json()),
                ]);

                let html = `
                <div class="stats-grid">
                    <div class="stat-card green"><div class="value">${stats.today_applied}</div>
                        <div class="label">Applied Today</div></div>
                    <div class="stat-card"><div class="value">${stats.total_applied}</div>
                        <div class="label">Total Applied</div></div>
                    <div class="stat-card yellow"><div class="value">${stats.today_skipped}</div>
                        <div class="label">Skipped Today</div></div>
                    <div class="stat-card red"><div class="value">${stats.today_failed}</div>
                        <div class="label">Failed Today</div></div>
                    <div class="stat-card"><div class="value">${stats.recruiters}</div>
                        <div class="label">Recruiters Tracked</div></div>
                    <div class="stat-card"><div class="value">${stats.visa_sponsors}</div>
                        <div class="label">Visa Sponsors</div></div>
                    <div class="stat-card"><div class="value">${stats.avg_match_score}%</div>
                        <div class="label">Avg Match Score</div></div>
                    <div class="stat-card"><div class="value">${stats.cycles_today}</div>
                        <div class="label">Cycles Today</div></div>
                </div>

                <div class="section">
                    <h2>Application Funnel</h2>
                    <div class="funnel">
                        <div class="funnel-step" style="background:#16213e">
                            <div class="count">${funnel.applied + funnel.skipped + funnel.failed}</div>
                            <div>Processed</div>
                        </div>
                        <div class="funnel-arrow">→</div>
                        <div class="funnel-step" style="background:#003566">
                            <div class="count">${funnel.applied}</div>
                            <div>Applied</div>
                        </div>
                        <div class="funnel-arrow">→</div>
                        <div class="funnel-step" style="background:#004d00">
                            <div class="count">${funnel.responses}</div>
                            <div>Responses</div>
                        </div>
                    </div>
                </div>

                <div class="section">
                    <h2>Recent Applications</h2>
                    <table>
                        <tr><th>Title</th><th>Company</th><th>Location</th><th>Match</th>
                            <th>Salary</th><th>Visa</th><th>Applied</th></tr>
                        ${jobs.map(j => `<tr>
                            <td>${truncate(j.title, 40)}</td>
                            <td>${truncate(j.company, 25)}</td>
                            <td>${truncate(j.location, 20)}</td>
                            <td><span class="score ${scoreClass(j.match_score)}">${j.match_score || '-'}%</span></td>
                            <td>${truncate(j.salary_info, 20)}</td>
                            <td><span class="visa-badge ${visaClass(j.visa_sponsorship)}">${j.visa_sponsorship}</span></td>
                            <td>${j.applied_at ? j.applied_at.substring(5, 16) : ''}</td>
                        </tr>`).join('')}
                    </table>
                </div>

                <div class="section">
                    <h2>Recruiter Directory</h2>
                    <table>
                        <tr><th>Name</th><th>Title</th><th>Company</th><th>Job</th><th>Profile</th></tr>
                        ${recruiters.map(r => `<tr>
                            <td>${r.name}</td>
                            <td>${truncate(r.title, 30)}</td>
                            <td>${r.company}</td>
                            <td>${truncate(r.job_title, 30)}</td>
                            <td>${r.profile_url ? '<a href="' + r.profile_url + '" target="_blank" style="color:#00b4d8">View</a>' : ''}</td>
                        </tr>`).join('')}
                    </table>
                </div>

                <div class="section">
                    <h2>Visa Sponsors</h2>
                    <table>
                        <tr><th>Company</th><th>Evidence</th><th>Times Seen</th><th>First Seen</th></tr>
                        ${visa.map(v => `<tr>
                            <td>${v.company}</td>
                            <td>${truncate(v.evidence, 30)}</td>
                            <td>${v.times_seen}</td>
                            <td>${v.first_seen ? v.first_seen.substring(0, 10) : ''}</td>
                        </tr>`).join('')}
                    </table>
                </div>`;

                document.getElementById('content').innerHTML = html;
            } catch (e) {
                console.error('Dashboard refresh error:', e);
            }
        }

        refresh();
        setInterval(refresh, REFRESH_INTERVAL * 1000);
    </script>
</body>
</html>"""


class Dashboard:
    """Flask-based monitoring dashboard running in a background thread."""

    def __init__(self, state, cfg: dict):
        self.state = state
        self.cfg = cfg
        dash_cfg = cfg.get("dashboard", {})
        self.enabled = dash_cfg.get("enabled", False)
        self.port = dash_cfg.get("port", 5000)
        self.host = dash_cfg.get("host", "0.0.0.0")
        self.refresh_interval = dash_cfg.get("refresh_interval", 30)
        self._thread = None
        self._app = None

    def start(self):
        """Start dashboard in a background thread."""
        if not self.enabled:
            return

        try:
            from flask import Flask, jsonify, request
        except ImportError:
            log.warning("Flask not installed. Run: pip install flask. Dashboard disabled.")
            return

        app = Flask(__name__)
        app.config['JSON_SORT_KEYS'] = False
        self._app = app

        state = self.state
        refresh = self.refresh_interval

        @app.route("/")
        def index():
            html = DASHBOARD_HTML.replace("REFRESH_INTERVAL", str(refresh))
            return html

        @app.route("/api/stats")
        def api_stats():
            today = state._today()
            daily = state.conn.execute(
                "SELECT * FROM daily_stats WHERE date=?", (today,)
            ).fetchone()

            total = state.total_applied()
            recruiters = state.conn.execute("SELECT COUNT(*) as c FROM recruiters").fetchone()["c"]
            sponsors = state.conn.execute("SELECT COUNT(*) as c FROM visa_sponsors").fetchone()["c"]
            avg_score = state.conn.execute(
                "SELECT AVG(score) as avg_s FROM match_scores WHERE score > 0"
            ).fetchone()

            return jsonify({
                "today_applied": daily["applied"] if daily else 0,
                "today_skipped": daily["skipped"] if daily else 0,
                "today_failed": daily["failed"] if daily else 0,
                "cycles_today": daily["cycles"] if daily else 0,
                "total_applied": total,
                "recruiters": recruiters,
                "visa_sponsors": sponsors,
                "avg_match_score": round(avg_score["avg_s"], 1) if avg_score and avg_score["avg_s"] else 0,
            })

        @app.route("/api/jobs")
        def api_jobs():
            limit = request.args.get("limit", 100, type=int)
            return jsonify(state.get_all_applied(limit))

        @app.route("/api/recruiters")
        def api_recruiters():
            limit = request.args.get("limit", 200, type=int)
            return jsonify(state.get_all_recruiters(limit))

        @app.route("/api/visa")
        def api_visa():
            return jsonify(state.get_all_visa_sponsors())

        @app.route("/api/funnel")
        def api_funnel():
            return jsonify(state.get_funnel_stats())

        @app.route("/api/daily")
        def api_daily():
            days = request.args.get("days", 30, type=int)
            return jsonify(state.get_daily_stats(days))

        @app.route("/api/salary")
        def api_salary():
            title = request.args.get("title", "")
            location = request.args.get("location", "")
            return jsonify(state.get_salary_benchmark(title, location))

        @app.route("/health")
        def health():
            return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

        # Run in background thread
        self._thread = threading.Thread(
            target=lambda: app.run(host=self.host, port=self.port,
                                   debug=False, use_reloader=False),
            daemon=True,
        )
        self._thread.start()
        log.info(f"📊 Dashboard started at http://{self.host}:{self.port}")

    def stop(self):
        """Stop dashboard (thread is daemon, stops with main process)."""
        pass
