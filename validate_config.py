"""
Configuration Validator.

Validates config.yaml on startup, catching common mistakes before
they cause runtime errors:
- Missing/empty credentials
- Conflicting settings
- Invalid provider names
- Negative numeric values
- Missing dependencies for enabled features
- File paths that don't exist
"""

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("lla.config_validator")

VALID_AI_PROVIDERS = {"openai", "anthropic", "gemini", "deepseek", "groq",
                       "together", "ollama", "lmstudio"}

REQUIRED_SECTIONS = ["search", "personal"]


class ConfigValidator:
    """Validate configuration and report issues."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def validate(self) -> bool:
        """Run all checks. Returns True if config is valid (may have warnings)."""
        self.errors.clear()
        self.warnings.clear()

        self._check_required_sections()
        self._check_credentials()
        self._check_search()
        self._check_ai()
        self._check_scheduling()
        self._check_filters()
        self._check_resume()
        self._check_feature_deps()
        self._check_file_paths()
        self._check_numeric_values()
        self._check_conflicting_settings()

        for w in self.warnings:
            log.warning(f"Config: {w}")
        for e in self.errors:
            log.error(f"Config: {e}")

        if self.errors:
            log.error(f"Validation failed: {len(self.errors)} error(s)")
        return len(self.errors) == 0

    def _check_required_sections(self):
        for section in REQUIRED_SECTIONS:
            if section not in self.cfg:
                self.errors.append(f"Missing required section: '{section}'")

    def _check_credentials(self):
        li = self.cfg.get("linkedin", {})
        if not li.get("email") and not self.cfg.get("browser", {}).get("user_data_dir"):
            self.warnings.append("No email and no user_data_dir — will need manual login")
        if li.get("email") and not li.get("password"):
            self.warnings.append("Email set but password empty")

    def _check_search(self):
        search = self.cfg.get("search", {})
        if not search.get("search_terms"):
            self.errors.append("No search_terms configured")
        if not search.get("search_locations"):
            self.errors.append("No search_locations configured")
        terms = search.get("search_terms", [])
        if len(terms) > 20:
            self.warnings.append(f"{len(terms)} search terms — consider <15 for faster cycles")

    def _check_ai(self):
        ai = self.cfg.get("ai", {})
        if not ai.get("enabled"):
            return
        provider = ai.get("provider", "").lower()
        if provider and provider not in VALID_AI_PROVIDERS:
            self.warnings.append(f"Unknown AI provider: '{provider}'")
        cloud = {"openai", "anthropic", "gemini", "deepseek", "groq", "together"}
        if provider in cloud and not ai.get("api_key"):
            self.warnings.append(f"Provider '{provider}' requires api_key")
        if not ai.get("cv_text") and not ai.get("cv_text_file"):
            self.warnings.append("No cv_text — AI will lack CV context")

    def _check_scheduling(self):
        s = self.cfg.get("scheduling", {})
        if s.get("max_applies_per_day", 40) < s.get("max_applies_per_cycle", 15):
            self.warnings.append("max_applies_per_day < max_applies_per_cycle")
        if s.get("scan_interval_minutes", 15) < 5:
            self.warnings.append("scan_interval_minutes < 5 — risk of rate limiting")
        if s.get("max_applies_per_day", 40) > 100:
            self.warnings.append("max_applies_per_day > 100 — risk of account ban")

    def _check_filters(self):
        f = self.cfg.get("filters", {})
        pos = set(k.lower() for k in f.get("visa_positive_keywords", []))
        neg = set(k.lower() for k in f.get("visa_negative_keywords", []))
        overlap = pos & neg
        if overlap:
            self.warnings.append(f"Visa keywords in both lists: {overlap}")

    def _check_resume(self):
        path = self.cfg.get("resume", {}).get("default_resume_path", "")
        if path and not os.path.exists(path):
            self.warnings.append(f"Resume not found: {path}")
        rt = self.cfg.get("resume_tailoring", {})
        if rt.get("enabled") and rt.get("format") == "pdf":
            try:
                import fpdf  # noqa: F401
            except ImportError:
                self.warnings.append("resume_tailoring pdf requires fpdf2")

    def _check_feature_deps(self):
        checks = [
            ("dashboard", "flask", "flask"),
            ("alerts", "requests", "requests"),
            ("google_jobs", "bs4", "beautifulsoup4"),
        ]
        for key, module, package in checks:
            if self.cfg.get(key, {}).get("enabled"):
                try:
                    __import__(module)
                except ImportError:
                    self.warnings.append(f"{key} enabled but {package} not installed")

    def _check_file_paths(self):
        for d in [self.cfg.get("export", {}).get("export_dir", "data"),
                  self.cfg.get("logging", {}).get("log_dir", "logs")]:
            if d:
                try:
                    Path(d).mkdir(parents=True, exist_ok=True)
                except PermissionError:
                    self.warnings.append(f"Cannot create: {d}")

    def _check_numeric_values(self):
        checks = [
            ("scheduling.max_applies_per_day", self.cfg.get("scheduling", {}).get("max_applies_per_day"), 1, 200),
            ("scheduling.scan_interval_minutes", self.cfg.get("scheduling", {}).get("scan_interval_minutes"), 1, 1440),
            ("match_scoring.minimum_score", self.cfg.get("match_scoring", {}).get("minimum_score"), 0, 100),
            ("dashboard.port", self.cfg.get("dashboard", {}).get("port"), 1, 65535),
        ]
        for name, val, lo, hi in checks:
            if val is not None:
                try:
                    v = int(val)
                    if v < lo or v > hi:
                        self.warnings.append(f"{name}={v} outside [{lo}-{hi}]")
                except (ValueError, TypeError):
                    self.warnings.append(f"{name}={val} not a number")

    def _check_conflicting_settings(self):
        if (self.cfg.get("search", {}).get("easy_apply_only", True) and
                self.cfg.get("external_apply", {}).get("enabled")):
            self.warnings.append("easy_apply_only=true + external_apply=true conflict")
        if (self.cfg.get("resume_tailoring", {}).get("enabled") and
                not self.cfg.get("match_scoring", {}).get("enabled")):
            self.warnings.append("resume_tailoring without match_scoring loses match data")

    def get_report(self) -> str:
        lines = ["Config Validation", "=" * 30]
        if self.errors:
            lines.append(f"\nERRORS ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"  [!] {e}")
        if self.warnings:
            lines.append(f"\nWARNINGS ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  [~] {w}")
        if not self.errors and not self.warnings:
            lines.append("All checks passed.")
        return "\n".join(lines)


def validate_and_report(cfg: dict) -> bool:
    v = ConfigValidator(cfg)
    valid = v.validate()
    if not valid:
        print(v.get_report())
    return valid
