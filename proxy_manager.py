"""
Proxy Rotation and Multi-Profile Management.

Rotates residential proxies per session. Supports multiple LinkedIn accounts
with separate browser fingerprints, cookie stores, and rate limits.
"""

import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("lla.proxy")


class ProxyManager:
    """Manage proxy rotation and multi-profile browser sessions."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        proxy_cfg = cfg.get("proxy", {})
        self.enabled = proxy_cfg.get("enabled", False)
        self.proxy_list = proxy_cfg.get("proxy_list", [])
        self.rotate_per_session = proxy_cfg.get("rotate_per_session", True)
        self.proxy_file = proxy_cfg.get("proxy_file", "")
        self.current_proxy = None
        self.profiles_dir = proxy_cfg.get("profiles_dir", "data/profiles")

        if self.enabled:
            self._load_proxies()
            Path(self.profiles_dir).mkdir(parents=True, exist_ok=True)

    def _load_proxies(self):
        """Load proxy list from file if configured."""
        if self.proxy_file and Path(self.proxy_file).exists():
            try:
                proxies = Path(self.proxy_file).read_text().strip().split("\n")
                self.proxy_list.extend([p.strip() for p in proxies if p.strip()])
            except Exception as e:
                log.warning(f"Could not load proxy file: {e}")

        if self.proxy_list:
            log.info(f"Loaded {len(self.proxy_list)} proxies")

    def get_next_proxy(self) -> Optional[str]:
        """Get next proxy from the rotation."""
        if not self.enabled or not self.proxy_list:
            return None

        if self.rotate_per_session:
            self.current_proxy = random.choice(self.proxy_list)
        elif not self.current_proxy:
            self.current_proxy = self.proxy_list[0]

        return self.current_proxy

    def configure_browser(self, chrome_options) -> None:
        """Add proxy configuration to Chrome options."""
        if not self.enabled:
            return

        proxy = self.get_next_proxy()
        if proxy:
            chrome_options.add_argument(f"--proxy-server={proxy}")
            log.info(f"Using proxy: {proxy}")

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
    """Manage multiple LinkedIn accounts with separate sessions."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        mp_cfg = cfg.get("multi_profile", {})
        self.enabled = mp_cfg.get("enabled", False)
        self.profiles = mp_cfg.get("profiles", [])
        # Each profile: {name, email, password, proxy, locations, search_terms}

    def get_profiles(self) -> list[dict]:
        """Get list of configured profiles."""
        if not self.enabled:
            return [{"name": "default"}]
        return self.profiles

    def get_profile_config(self, profile: dict) -> dict:
        """Override main config with profile-specific settings."""
        cfg = dict(self.cfg)

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

        return cfg
