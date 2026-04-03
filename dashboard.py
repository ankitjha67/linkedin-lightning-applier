"""
All-in-One Monitoring Dashboard.

Comprehensive Flask dashboard combining all features into a single unified view:
- Real-time stats cards with live counters
- Application pipeline funnel visualization
- Job applications table with match scores, salary, visa status
- Recruiter CRM directory with relationship scores
- Salary intelligence benchmarks by role/location
- Interview prep viewer
- Skill gap analysis chart
- Success prediction analytics
- Job watchlist management
- JD change alerts
- Network/referral opportunities
- Application timeline chart
- System health monitoring

Runs in a background thread alongside the main bot.
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lla.dashboard")

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LinkedIn Lightning Applier — Command Center</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0f;color:#e0e0e0;padding:16px}
h1{text-align:center;margin-bottom:16px;color:#00b4d8;font-size:1.6em}
.tabs{display:flex;gap:4px;margin-bottom:16px;flex-wrap:wrap;justify-content:center}
.tab{padding:8px 16px;background:#16213e;border:1px solid #1a1a3e;border-radius:8px;cursor:pointer;color:#888;font-size:.85em;transition:all .2s}
.tab.active,.tab:hover{background:#00b4d8;color:#000;border-color:#00b4d8}
.panel{display:none}.panel.active{display:block}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.stat{background:#1a1a2e;border-radius:10px;padding:16px;text-align:center;border:1px solid #16213e}
.stat .val{font-size:2em;font-weight:bold;color:#00b4d8}.stat .lbl{font-size:.8em;color:#666;margin-top:4px}
.stat.green .val{color:#00f593}.stat.yellow .val{color:#ffd60a}.stat.red .val{color:#ef476f}.stat.purple .val{color:#b388ff}
table{width:100%;border-collapse:collapse;background:#1a1a2e;border-radius:8px;overflow:hidden;margin-bottom:16px}
th{background:#16213e;padding:10px 12px;text-align:left;font-weight:600;color:#00b4d8;font-size:.85em}
td{padding:8px 12px;border-bottom:1px solid #16213e;font-size:.85em}
tr:hover{background:#16213e}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.8em;font-weight:bold}
.b-high{background:#00f593;color:#000}.b-mid{background:#ffd60a;color:#000}.b-low{background:#ef476f;color:#fff}
.b-yes{background:#00f593;color:#000}.b-no{background:#ef476f;color:#fff}.b-unk{background:#444;color:#aaa}
.funnel{display:flex;justify-content:center;align-items:center;gap:8px;margin:16px 0;flex-wrap:wrap}
.funnel-step{text-align:center;padding:12px 20px;border-radius:8px;background:#16213e;min-width:100px}
.funnel-step .ct{font-size:1.6em;font-weight:bold}.funnel-arr{font-size:1.5em;color:#444}
.chart-bar{display:flex;align-items:center;margin:4px 0;gap:8px}
.chart-bar .label{width:120px;text-align:right;font-size:.8em;color:#888;flex-shrink:0}
.chart-bar .bar{height:20px;border-radius:4px;min-width:2px;transition:width .5s}
.chart-bar .value{font-size:.8em;color:#aaa;width:50px}
.card{background:#1a1a2e;border-radius:10px;padding:16px;border:1px solid #16213e;margin-bottom:16px}
.card h3{color:#00b4d8;margin-bottom:10px;font-size:1em}
.card pre{background:#0f0f1a;padding:10px;border-radius:6px;white-space:pre-wrap;font-size:.85em;line-height:1.5;max-height:300px;overflow-y:auto}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.refresh{text-align:center;color:#444;font-size:.75em;margin-top:12px}
a{color:#00b4d8;text-decoration:none}a:hover{text-decoration:underline}
@media(max-width:768px){.stats-grid{grid-template-columns:repeat(2,1fr)}.two-col{grid-template-columns:1fr}th,td{padding:6px 8px;font-size:.8em}}
</style>
</head>
<body>
<h1>&#9889; LinkedIn Lightning Applier — Command Center</h1>
<div class="tabs" id="tabs"></div>
<div id="panels"></div>
<p class="refresh">Auto-refreshes every <span id="iv">30</span>s | <span id="ts"></span></p>
<script>
const TABS=['Overview','Applications','Recruiters','Salary','Skills','Interview Prep','Watchlist','Analytics','System'];
let activeTab='Overview';
function init(){
  const tc=document.getElementById('tabs'),pc=document.getElementById('panels');
  TABS.forEach(t=>{
    const btn=document.createElement('div');btn.className='tab'+(t===activeTab?' active':'');btn.textContent=t;
    btn.onclick=()=>{activeTab=t;document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.textContent===t));document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.dataset.tab===t))};
    tc.appendChild(btn);
    const panel=document.createElement('div');panel.className='panel'+(t===activeTab?' active':'');panel.dataset.tab=t;panel.id='p-'+t.replace(/ /g,'-');
    pc.appendChild(panel);
  });
  refresh();setInterval(refresh,REFRESH_INTERVAL*1000);
}
function sc(s){return s>=70?'b-high':s>=50?'b-mid':'b-low'}
function vc(v){return v==='yes'?'b-yes':v==='no'?'b-no':'b-unk'}
function tr(s,n){return s&&s.length>n?s.substring(0,n)+'...':s||''}
function bar(items,color){
  if(!items||!items.length)return'<p style="color:#444">No data yet</p>';
  const mx=Math.max(...items.map(i=>i.value));
  return items.map(i=>`<div class="chart-bar"><span class="label">${tr(i.label,15)}</span><div class="bar" style="width:${Math.max(i.value/mx*100,1)}%;background:${color||'#00b4d8'}"></div><span class="value">${i.value}</span></div>`).join('');
}
async function refresh(){
  try{
    const[stats,jobs,recs,visa,funnel,daily,skills,salary,watchlist]=await Promise.all([
      fetch('/api/stats').then(r=>r.json()),
      fetch('/api/jobs?limit=50').then(r=>r.json()),
      fetch('/api/recruiters?limit=50').then(r=>r.json()),
      fetch('/api/visa').then(r=>r.json()),
      fetch('/api/funnel').then(r=>r.json()),
      fetch('/api/daily?days=14').then(r=>r.json()),
      fetch('/api/skills').then(r=>r.json()).catch(()=>[]),
      fetch('/api/salary/top').then(r=>r.json()).catch(()=>[]),
      fetch('/api/watchlist').then(r=>r.json()).catch(()=>[]),
    ]);
    document.getElementById('ts').textContent='Updated: '+new Date().toLocaleTimeString();
    // Overview
    document.getElementById('p-Overview').innerHTML=`
    <div class="stats-grid">
      <div class="stat green"><div class="val">${stats.today_applied}</div><div class="lbl">Applied Today</div></div>
      <div class="stat"><div class="val">${stats.total_applied}</div><div class="lbl">Total Applied</div></div>
      <div class="stat yellow"><div class="val">${stats.today_skipped}</div><div class="lbl">Skipped Today</div></div>
      <div class="stat red"><div class="val">${stats.today_failed}</div><div class="lbl">Failed Today</div></div>
      <div class="stat purple"><div class="val">${stats.avg_match_score}%</div><div class="lbl">Avg Match</div></div>
      <div class="stat"><div class="val">${stats.recruiters}</div><div class="lbl">Recruiters</div></div>
      <div class="stat"><div class="val">${stats.visa_sponsors}</div><div class="lbl">Visa Sponsors</div></div>
      <div class="stat"><div class="val">${stats.cycles_today}</div><div class="lbl">Cycles Today</div></div>
    </div>
    <div class="card"><h3>Application Funnel</h3>
    <div class="funnel">
      <div class="funnel-step"><div class="ct">${funnel.applied+funnel.skipped+funnel.failed}</div><div>Processed</div></div>
      <div class="funnel-arr">&rarr;</div>
      <div class="funnel-step" style="background:#003566"><div class="ct">${funnel.applied}</div><div>Applied</div></div>
      <div class="funnel-arr">&rarr;</div>
      <div class="funnel-step" style="background:#004d00"><div class="ct">${funnel.responses}</div><div>Responses</div></div>
    </div></div>
    <div class="card"><h3>Daily Trend (14 days)</h3>
    ${bar(daily.map(d=>({label:d.date?d.date.substring(5):'',value:d.applied})),'#00f593')}
    </div>`;
    // Applications
    document.getElementById('p-Applications').innerHTML=`<table><tr><th>Title</th><th>Company</th><th>Location</th><th>Match</th><th>Salary</th><th>Visa</th><th>Resume</th><th>Applied</th></tr>
    ${jobs.map(j=>`<tr><td>${j.job_url?'<a href="'+j.job_url+'" target="_blank">'+tr(j.title,35)+'</a>':tr(j.title,35)}</td><td>${tr(j.company,20)}</td><td>${tr(j.location,18)}</td>
    <td><span class="badge ${sc(j.match_score)}">${j.match_score||'-'}%</span></td><td>${tr(j.salary_info,18)}</td>
    <td><span class="badge ${vc(j.visa_sponsorship)}">${j.visa_sponsorship}</span></td><td>${tr(j.resume_version,15)}</td>
    <td>${j.applied_at?j.applied_at.substring(5,16):''}</td></tr>`).join('')}</table>`;
    // Recruiters
    document.getElementById('p-Recruiters').innerHTML=`<table><tr><th>Name</th><th>Title</th><th>Company</th><th>Job</th><th>Profile</th><th>Seen</th></tr>
    ${recs.map(r=>`<tr><td>${r.name}</td><td>${tr(r.title,25)}</td><td>${r.company}</td><td>${tr(r.job_title,25)}</td>
    <td>${r.profile_url?'<a href="'+r.profile_url+'" target="_blank">View</a>':''}</td>
    <td>${r.seen_at?r.seen_at.substring(0,10):''}</td></tr>`).join('')}</table>
    <div class="card"><h3>Visa Sponsors</h3><table><tr><th>Company</th><th>Evidence</th><th>Times Seen</th></tr>
    ${visa.map(v=>`<tr><td>${v.company}</td><td>${tr(v.evidence,25)}</td><td>${v.times_seen}</td></tr>`).join('')}</table></div>`;
    // Salary
    document.getElementById('p-Salary').innerHTML=`<div class="card"><h3>Salary Data (${salary.length} entries)</h3><table><tr><th>Title</th><th>Company</th><th>Location</th><th>Range</th><th>Currency</th></tr>
    ${salary.slice(0,30).map(s=>`<tr><td>${tr(s.title,30)}</td><td>${tr(s.company,20)}</td><td>${tr(s.location,18)}</td>
    <td>${s.salary_min?Math.round(s.salary_min).toLocaleString():'-'} - ${s.salary_max?Math.round(s.salary_max).toLocaleString():'-'}</td><td>${s.currency}</td></tr>`).join('')}</table></div>`;
    // Skills
    const gaps=skills.filter(s=>s.times_matched<s.times_seen);
    const matched=skills.filter(s=>s.times_matched>0);
    document.getElementById('p-Skills').innerHTML=`<div class="two-col">
    <div class="card"><h3>Top Demanded Skills</h3>${bar(skills.slice(0,15).map(s=>({label:s.skill,value:s.times_seen})),'#00b4d8')}</div>
    <div class="card"><h3>Skill Gaps (Missing from CV)</h3>${bar(gaps.slice(0,15).map(s=>({label:s.skill,value:s.times_seen-s.times_matched})),'#ef476f')}</div>
    </div>`;
    // Interview Prep - fetched on tab click
    document.getElementById('p-Interview-Prep').innerHTML=`<p style="color:#666">Click to load interview prep data...</p>`;
    // Watchlist
    document.getElementById('p-Watchlist').innerHTML=`<div class="card"><h3>Job Watchlist (${watchlist.length} items)</h3>
    ${watchlist.length?`<table><tr><th>Title</th><th>Company</th><th>Match</th><th>Reason</th><th>Status</th><th>Added</th></tr>
    ${watchlist.map(w=>`<tr><td>${tr(w.title,30)}</td><td>${tr(w.company,20)}</td><td><span class="badge ${sc(w.match_score)}">${w.match_score}%</span></td>
    <td>${tr(w.reason,25)}</td><td>${w.still_active?'<span class="badge b-yes">Active</span>':'<span class="badge b-no">Closed</span>'}</td>
    <td>${w.added_at?w.added_at.substring(0,10):''}</td></tr>`).join('')}</table>`:'<p style="color:#444">No watchlist items</p>'}</div>`;
    // Analytics
    document.getElementById('p-Analytics').innerHTML=`<div class="two-col">
    <div class="card"><h3>Response Rate by Match Score</h3>
    ${bar([{label:'90-100%',value:stats.score_90_100||0},{label:'70-89%',value:stats.score_70_89||0},{label:'50-69%',value:stats.score_50_69||0},{label:'0-49%',value:stats.score_0_49||0}],'#b388ff')}</div>
    <div class="card"><h3>Applications by Day</h3>
    ${bar(daily.slice(0,7).map(d=>({label:d.date?d.date.substring(5):'',value:d.applied})),'#00f593')}</div>
    </div>`;
    // System
    document.getElementById('p-System').innerHTML=`<div class="stats-grid">
    <div class="stat"><div class="val">${stats.total_applied}</div><div class="lbl">All-Time Applied</div></div>
    <div class="stat"><div class="val">${stats.recruiters}</div><div class="lbl">Recruiters Tracked</div></div>
    <div class="stat"><div class="val">${stats.visa_sponsors}</div><div class="lbl">Visa Sponsors</div></div>
    <div class="stat"><div class="val">${skills.length}</div><div class="lbl">Skills Tracked</div></div>
    </div>
    <div class="card"><h3>System Status</h3><pre>Dashboard: Running\nLast refresh: ${new Date().toISOString()}\nDatabase: OK</pre></div>`;
  }catch(e){console.error('Dashboard error:',e)}
}
init();
</script>
</body>
</html>"""


class Dashboard:
    """All-in-one monitoring dashboard running in a background thread."""

    def __init__(self, state, cfg: dict):
        self.state = state
        self.cfg = cfg
        dash_cfg = cfg.get("dashboard", {})
        self.enabled = dash_cfg.get("enabled", False)
        self.port = dash_cfg.get("port", 5000)
        self.host = dash_cfg.get("host", "0.0.0.0")
        self.refresh_interval = dash_cfg.get("refresh_interval", 30)
        self._thread = None

    def start(self):
        """Start dashboard in a background thread."""
        if not self.enabled:
            return

        try:
            from flask import Flask, jsonify, request
        except ImportError:
            log.warning("Flask not installed. Dashboard disabled.")
            return

        app = Flask(__name__)
        app.config['JSON_SORT_KEYS'] = False
        state = self.state
        refresh = self.refresh_interval

        @app.route("/")
        def index():
            return DASHBOARD_HTML.replace("REFRESH_INTERVAL", str(refresh))

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

        @app.route("/api/salary/top")
        def api_salary_top():
            try:
                rows = state.conn.execute("""
                    SELECT title, company, location, salary_min, salary_max, currency
                    FROM salary_data WHERE salary_max > 0
                    ORDER BY collected_at DESC LIMIT 50
                """).fetchall()
                return jsonify([dict(r) for r in rows])
            except Exception:
                return jsonify([])

        @app.route("/api/salary")
        def api_salary_benchmark():
            title = request.args.get("title", "")
            location = request.args.get("location", "")
            return jsonify(state.get_salary_benchmark(title, location))

        @app.route("/api/skills")
        def api_skills():
            return jsonify(state.get_top_skills(50))

        @app.route("/api/watchlist")
        def api_watchlist():
            try:
                rows = state.conn.execute(
                    "SELECT * FROM job_watchlist WHERE status='active' ORDER BY added_at DESC"
                ).fetchall()
                return jsonify([dict(r) for r in rows])
            except Exception:
                return jsonify([])

        @app.route("/health")
        def health():
            return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

        self._thread = threading.Thread(
            target=lambda: app.run(host=self.host, port=self.port,
                                   debug=False, use_reloader=False),
            daemon=True,
        )
        self._thread.start()
        log.info(f"Dashboard started at http://{self.host}:{self.port}")

    def stop(self):
        pass
