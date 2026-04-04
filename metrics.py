"""
Prometheus-Compatible Metrics Endpoint.

Exposes application metrics in Prometheus text format at /metrics.
Graph in Grafana: application rates, error rates, match scores,
response rates, cycle duration, and system health over time.

Also provides an in-memory metrics collector for internal use.
"""

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime

log = logging.getLogger("lla.metrics")


class MetricsCollector:
    """Thread-safe metrics collector with Prometheus export."""

    def __init__(self, cfg: dict = None, state=None):
        self.cfg = cfg or {}
        self.state = state
        m_cfg = self.cfg.get("metrics", {})
        self.enabled = m_cfg.get("enabled", True)
        self.port = m_cfg.get("port", 9090)
        self.host = m_cfg.get("host", "0.0.0.0")

        # Counters (monotonically increasing)
        self._counters: dict[str, float] = defaultdict(float)
        # Gauges (current value)
        self._gauges: dict[str, float] = defaultdict(float)
        # Histograms (list of observations)
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._thread = None

    # ── Counter operations ────────────────────────────────────

    def inc(self, name: str, value: float = 1, labels: dict = None):
        """Increment a counter."""
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += value

    # ── Gauge operations ──────────────────────────────────────

    def set_gauge(self, name: str, value: float, labels: dict = None):
        """Set a gauge to a specific value."""
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = value

    # ── Histogram operations ──────────────────────────────────

    def observe(self, name: str, value: float, labels: dict = None):
        """Record a histogram observation."""
        key = self._key(name, labels)
        with self._lock:
            self._histograms[key].append(value)
            # Keep last 1000 observations
            if len(self._histograms[key]) > 1000:
                self._histograms[key] = self._histograms[key][-500:]

    # ── Convenience methods ───────────────────────────────────

    def record_application(self, company: str = "", match_score: int = 0):
        """Record a successful application."""
        self.inc("lla_applications_total")
        self.inc("lla_applications_total", labels={"company": company})
        self.observe("lla_match_score", match_score)

    def record_skip(self, reason: str = ""):
        """Record a skipped job."""
        self.inc("lla_skips_total")
        self.inc("lla_skips_total", labels={"reason": reason[:30]})

    def record_error(self, error_type: str = ""):
        """Record an error."""
        self.inc("lla_errors_total")
        self.inc("lla_errors_total", labels={"type": error_type[:30]})

    def record_cycle(self, duration_s: float, applied: int, skipped: int, failed: int):
        """Record cycle completion."""
        self.inc("lla_cycles_total")
        self.observe("lla_cycle_duration_seconds", duration_s)
        self.set_gauge("lla_cycle_applied", applied)
        self.set_gauge("lla_cycle_skipped", skipped)
        self.set_gauge("lla_cycle_failed", failed)

    def record_ai_call(self, provider: str, duration_ms: float):
        """Record an AI API call."""
        self.inc("lla_ai_calls_total", labels={"provider": provider})
        self.observe("lla_ai_latency_ms", duration_ms, labels={"provider": provider})

    def update_from_state(self):
        """Pull latest metrics from state database."""
        if not self.state:
            return
        try:
            self.set_gauge("lla_total_applied", self.state.total_applied())
            self.set_gauge("lla_daily_applied", self.state.daily_applied_count())

            recruiters = self.state.conn.execute(
                "SELECT COUNT(*) as c FROM recruiters"
            ).fetchone()["c"]
            self.set_gauge("lla_recruiters_tracked", recruiters)

            sponsors = self.state.conn.execute(
                "SELECT COUNT(*) as c FROM visa_sponsors"
            ).fetchone()["c"]
            self.set_gauge("lla_visa_sponsors", sponsors)

            avg = self.state.conn.execute(
                "SELECT AVG(score) as s FROM match_scores WHERE score > 0"
            ).fetchone()
            if avg and avg["s"]:
                self.set_gauge("lla_avg_match_score", round(avg["s"], 1))
        except Exception:
            pass

    # ── Prometheus export ─────────────────────────────────────

    def to_prometheus(self) -> str:
        """Export all metrics in Prometheus text format."""
        lines = []
        with self._lock:
            # Counters
            for key, value in sorted(self._counters.items()):
                name, labels = self._parse_key(key)
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name}{labels} {value}")

            # Gauges
            for key, value in sorted(self._gauges.items()):
                name, labels = self._parse_key(key)
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name}{labels} {value}")

            # Histograms (as summary)
            for key, values in sorted(self._histograms.items()):
                if not values:
                    continue
                name, labels = self._parse_key(key)
                n = len(values)
                total = sum(values)
                sorted_v = sorted(values)
                lines.append(f"# TYPE {name} summary")
                lines.append(f'{name}_count{labels} {n}')
                lines.append(f'{name}_sum{labels} {total}')
                if n > 0:
                    p50 = sorted_v[n // 2]
                    p95 = sorted_v[min(int(n * 0.95), n - 1)]
                    p99 = sorted_v[min(int(n * 0.99), n - 1)]
                    q_labels = labels.lstrip("{").rstrip("}") if labels else ""
                    sep = "," if q_labels else ""
                    lines.append('{}_quantile{{quantile="0.5"{}{}}} {}'.format(name, sep, q_labels, p50))
                    lines.append('{}_quantile{{quantile="0.95"{}{}}} {}'.format(name, sep, q_labels, p95))
                    lines.append('{}_quantile{{quantile="0.99"{}{}}} {}'.format(name, sep, q_labels, p99))

        return "\n".join(lines) + "\n"

    # ── HTTP server ───────────────────────────────────────────

    def start_server(self):
        """Start metrics HTTP server in background thread."""
        if not self.enabled:
            return

        try:
            from flask import Flask, Response
        except ImportError:
            log.debug("Flask not available for metrics endpoint")
            return

        app = Flask("lla-metrics")
        collector = self

        @app.route("/metrics")
        def metrics():
            collector.update_from_state()
            return Response(collector.to_prometheus(), mimetype="text/plain")

        @app.route("/health")
        def health():
            return "ok"

        self._thread = threading.Thread(
            target=lambda: app.run(host=self.host, port=self.port,
                                   debug=False, use_reloader=False),
            daemon=True,
        )
        self._thread.start()
        log.info(f"Metrics endpoint at http://{self.host}:{self.port}/metrics")

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _key(name: str, labels: dict = None) -> str:
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    @staticmethod
    def _parse_key(key: str) -> tuple[str, str]:
        if "{" in key:
            name = key[:key.index("{")]
            labels = key[key.index("{"):]
            return name, labels
        return key, ""
