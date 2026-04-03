"""
Proxy Rotation and Multi-Profile Management.

Rotates residential proxies per session with health checking and failover.
Supports multiple LinkedIn accounts with separate browser fingerprints,
cookie stores, rate limits, and independent scheduling.
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("lla.proxy")


class ProxyHealth:
    """Track proxy health metrics."""

    def __init__(self):
        self.successes = 0
        self.failures = 0
        self.last_used = None
        self.last_success = None
        self.last_failure = None
        self.avg_latency_ms = 0
        self.banned = False
        self.cooldown_until = None

    @property
    def score(self) -> float:
        """Health score from 0.0 (dead) to 1.0 (perfect)."""
        if self.banned:
            return 0.0
        if self.cooldown_until and datetime.now() < self.cooldown_until:
            return 0.05  # Small score so it's deprioritized but not dead
        total = self.successes + self.failures
        if total == 0:
            return 0.5  # Unknown — neutral score
        success_rate = self.successes / total
        # Decay old failures: recent failures weight more
        recency_bonus = 0.0
        if self.last_success and self.last_failure:
            if self.last_success > self.last_failure:
                recency_bonus = 0.1
        # Latency penalty
        latency_penalty = min(self.avg_latency_ms / 10000, 0.3)
        return max(0.0, min(1.0, success_rate + recency_bonus - latency_penalty))

    def record_success(self, latency_ms: float = 0):
        self.successes += 1
        self.last_used = datetime.now()
        self.last_success = datetime.now()
        if latency_ms > 0:
            if self.avg_latency_ms == 0:
                self.avg_latency_ms = latency_ms
            else:
                self.avg_latency_ms = self.avg_latency_ms * 0.7 + latency_ms * 0.3

    def record_failure(self, cooldown_minutes: int = 5):
        self.failures += 1
        self.last_used = datetime.now()
        self.last_failure = datetime.now()
        # Exponential backoff: more failures = longer cooldown
        consecutive = self._recent_consecutive_failures()
        cooldown = cooldown_minutes * (2 ** min(consecutive, 5))
        self.cooldown_until = datetime.now() + timedelta(minutes=cooldown)
        if consecutive >= 10:
            self.banned = True

    def _recent_consecutive_failures(self) -> int:
        if self.last_success and self.last_failure:
            return 0 if self.last_success > self.last_failure else min(self.failures, 10)
        return min(self.failures, 10)

    def to_dict(self) -> dict:
        return {
            "successes": self.successes, "failures": self.failures,
            "score": round(self.score, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "banned": self.banned,
        }


class ProxyManager:
    """Manage proxy rotation with health checking and intelligent failover."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        proxy_cfg = cfg.get("proxy", {})
        self.enabled = proxy_cfg.get("enabled", False)
        self.proxy_list = list(proxy_cfg.get("proxy_list", []))
        self.rotate_per_session = proxy_cfg.get("rotate_per_session", True)
        self.proxy_file = proxy_cfg.get("proxy_file", "")
        self.profiles_dir = proxy_cfg.get("profiles_dir", "data/profiles")
        self.validate_on_start = proxy_cfg.get("validate_on_start", True)
        self.sticky_session_minutes = proxy_cfg.get("sticky_session_minutes", 30)
        self.current_proxy = None
        self._health: dict[str, ProxyHealth] = {}
        self._sticky_until = None
        self._health_file = os.path.join(self.profiles_dir, "proxy_health.json")

        if self.enabled:
            self._load_proxies()
            Path(self.profiles_dir).mkdir(parents=True, exist_ok=True)
            self._load_health()

    def _load_proxies(self):
        """Load proxy list from file if configured."""
        if self.proxy_file and Path(self.proxy_file).exists():
            try:
                raw = Path(self.proxy_file).read_text().strip().split("\n")
                self.proxy_list.extend([p.strip() for p in raw if p.strip() and not p.startswith("#")])
            except Exception as e:
                log.warning(f"Could not load proxy file: {e}")

        # Deduplicate
        self.proxy_list = list(dict.fromkeys(self.proxy_list))

        # Initialize health tracking
        for proxy in self.proxy_list:
            if proxy not in self._health:
                self._health[proxy] = ProxyHealth()

        if self.proxy_list:
            log.info(f"Loaded {len(self.proxy_list)} proxies")

    def _load_health(self):
        """Load proxy health data from disk."""
        if not os.path.exists(self._health_file):
            return
        try:
            data = json.loads(Path(self._health_file).read_text())
            for proxy, stats in data.items():
                if proxy in self._health:
                    h = self._health[proxy]
                    h.successes = stats.get("successes", 0)
                    h.failures = stats.get("failures", 0)
                    h.avg_latency_ms = stats.get("avg_latency_ms", 0)
                    h.banned = stats.get("banned", False)
        except Exception:
            pass

    def _save_health(self):
        """Persist proxy health data to disk."""
        try:
            data = {p: h.to_dict() for p, h in self._health.items()}
            Path(self._health_file).write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def get_next_proxy(self) -> Optional[str]:
        """Get the best available proxy based on health scores."""
        if not self.enabled or not self.proxy_list:
            return None

        # Sticky session: reuse current proxy within time window
        if (self.current_proxy and self._sticky_until and
                datetime.now() < self._sticky_until and
                self._health.get(self.current_proxy, ProxyHealth()).score > 0.3):
            return self.current_proxy

        # Score and sort available proxies
        available = []
        for proxy in self.proxy_list:
            health = self._health.get(proxy, ProxyHealth())
            score = health.score
            if score > 0:
                available.append((proxy, score))

        if not available:
            log.warning("No healthy proxies available! Using random.")
            proxy = random.choice(self.proxy_list)
        else:
            # Weighted random selection (higher score = more likely)
            total_score = sum(s for _, s in available)
            r = random.uniform(0, total_score)
            cumulative = 0
            proxy = available[0][0]
            for p, s in available:
                cumulative += s
                if cumulative >= r:
                    proxy = p
                    break

        self.current_proxy = proxy
        self._sticky_until = datetime.now() + timedelta(minutes=self.sticky_session_minutes)
        return proxy

    def record_success(self, latency_ms: float = 0):
        """Record successful request through current proxy."""
        if self.current_proxy and self.current_proxy in self._health:
            self._health[self.current_proxy].record_success(latency_ms)
            self._save_health()

    def record_failure(self):
        """Record failed request through current proxy."""
        if self.current_proxy and self.current_proxy in self._health:
            self._health[self.current_proxy].record_failure()
            self._save_health()
            # Force rotation on failure
            self._sticky_until = None

    def configure_browser(self, chrome_options) -> None:
        """Add proxy configuration to Chrome options."""
        if not self.enabled:
            return

        proxy = self.get_next_proxy()
        if proxy:
            # Handle authenticated proxies: user:pass@host:port
            if "@" in proxy:
                # For authenticated proxies, need a Chrome extension
                log.info(f"Using authenticated proxy: {proxy.split('@')[1]}")
                chrome_options.add_argument(f"--proxy-server={proxy}")
            else:
                chrome_options.add_argument(f"--proxy-server={proxy}")
            log.info(f"Using proxy: {proxy} (score: {self._health.get(proxy, ProxyHealth()).score:.2f})")

    def validate_proxy(self, proxy: str, timeout: int = 10) -> bool:
        """Test if a proxy is working by making a request."""
        try:
            import requests
            proxies = {"http": proxy, "https": proxy}
            start = time.time()
            resp = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=timeout)
            latency = (time.time() - start) * 1000
            if resp.status_code == 200:
                self._health[proxy].record_success(latency)
                return True
        except Exception:
            pass
        self._health[proxy].record_failure()
        return False

    def validate_all(self) -> dict:
        """Validate all proxies and return health report."""
        results = {}
        for proxy in self.proxy_list:
            ok = self.validate_proxy(proxy)
            results[proxy] = "healthy" if ok else "failed"
            log.info(f"  Proxy {proxy}: {'OK' if ok else 'FAIL'}")
        self._save_health()
        return results

    def get_health_report(self) -> str:
        """Generate proxy health report."""
        lines = ["Proxy Health Report", "=" * 40]
        for proxy in self.proxy_list:
            h = self._health.get(proxy, ProxyHealth())
            status = "BANNED" if h.banned else f"score={h.score:.2f}"
            lines.append(f"  {proxy}: {status} "
                        f"({h.successes}ok/{h.failures}fail, {h.avg_latency_ms:.0f}ms)")
        return "\n".join(lines)

    # ── Profile Management ────────────────────────────────────

    def get_profile_dir(self, profile_name: str = "default") -> str:
        """Get browser profile directory for a specific account."""
        profile_dir = os.path.join(self.profiles_dir, profile_name)
        Path(profile_dir).mkdir(parents=True, exist_ok=True)
        return profile_dir

    def get_all_profiles(self) -> list[str]:
        """List all available browser profiles."""
        if not Path(self.profiles_dir).exists():
            return []
        return [d.name for d in Path(self.profiles_dir).iterdir() if d.is_dir()]


class MultiProfileManager:
    """Manage multiple LinkedIn accounts with independent sessions."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        mp_cfg = cfg.get("multi_profile", {})
        self.enabled = mp_cfg.get("enabled", False)
        self.profiles = mp_cfg.get("profiles", [])
        self.rate_limits = {}  # profile_name -> {daily_applied, last_cycle}

    def get_profiles(self) -> list[dict]:
        """Get list of configured profiles."""
        if not self.enabled:
            return [{"name": "default"}]
        return self.profiles

    def get_profile_config(self, profile: dict) -> dict:
        """Build config with profile-specific overrides."""
        cfg = json.loads(json.dumps(self.cfg))  # Deep copy

        if profile.get("email"):
            cfg.setdefault("linkedin", {})["email"] = profile["email"]
        if profile.get("password"):
            cfg["linkedin"]["password"] = profile["password"]
        if profile.get("locations"):
            cfg.setdefault("search", {})["search_locations"] = profile["locations"]
        if profile.get("search_terms"):
            cfg["search"]["search_terms"] = profile["search_terms"]
        if profile.get("proxy"):
            cfg.setdefault("proxy", {})["proxy_list"] = [profile["proxy"]]
        if profile.get("max_applies_per_day"):
            cfg.setdefault("scheduling", {})["max_applies_per_day"] = profile["max_applies_per_day"]

        # Separate user_data_dir per profile
        profile_name = profile.get("name", "default")
        cfg.setdefault("browser", {})["user_data_dir"] = os.path.join(
            "data/profiles", profile_name, "chrome"
        )

        return cfg

    def check_rate_limit(self, profile_name: str, max_daily: int = 40) -> bool:
        """Check if a profile has hit its daily rate limit."""
        limits = self.rate_limits.get(profile_name, {})
        today = date.today().isoformat()
        if limits.get("date") != today:
            self.rate_limits[profile_name] = {"date": today, "applied": 0}
            return True
        return limits.get("applied", 0) < max_daily

    def increment_applied(self, profile_name: str):
        """Increment the daily applied count for a profile."""
        today = date.today().isoformat()
        if profile_name not in self.rate_limits or self.rate_limits[profile_name].get("date") != today:
            self.rate_limits[profile_name] = {"date": today, "applied": 0}
        self.rate_limits[profile_name]["applied"] += 1
