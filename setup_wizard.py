#!/usr/bin/env python3
"""LinkedIn Lightning Applier -- Interactive Setup Wizard.

Walks the user through creating a config.yaml step by step.
No external dependencies beyond PyYAML and the Python standard library.

Usage:
    python setup_wizard.py          # Run standalone
    python cli.py setup             # Run via CLI
"""

import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    import yaml
except ImportError:
    print("PyYAML is required.  Run:  pip install pyyaml")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════

BANNER = r"""
  _     _       _            _ ___         _     _       _
 | |   (_)_ __ | | _____  __| |_ _|_ __   | |   (_) __ _| |__ | |_ _ __
 | |   | | '_ \| |/ / _ \/ _` || || '_ \  | |   | |/ _` | '_ \| __| '_ \
 | |___| | | | |   <  __/ (_| || || | | | | |___| | (_| | | | | |_| | | |
 |_____|_|_| |_|_|\_\___|\__,_|___|_| |_| |_____|_|\__, |_| |_|\__|_| |_|
                                                     |___/
  Lightning Applier -- Setup Wizard
"""

AI_PROVIDERS = {
    "1": ("ollama", "Ollama (local, free)"),
    "2": ("lmstudio", "LM Studio (local, free)"),
    "3": ("openai", "OpenAI (GPT-4o-mini, paid)"),
    "4": ("anthropic", "Anthropic Claude (paid)"),
    "5": ("gemini", "Google Gemini (paid)"),
    "6": ("deepseek", "DeepSeek (paid)"),
    "7": ("groq", "Groq (fast, free tier)"),
    "8": ("together", "Together AI (paid)"),
}

DEFAULT_BAD_WORDS = [
    "polygraph", "top secret", "ts/sci", "security clearance required",
    "registered nurse", "clinical trial", "patient care",
    "must be a US citizen", "US citizens only",
]

DEFAULT_BAD_TITLES = [
    "Intern", "Internship", "Cashier", "Nurse", "Physician",
    "Teacher", "Chef", "Driver", "Warehouse", "Receptionist",
]

MIN_PYTHON = (3, 9)


# ═══════════════════════════════════════════════════════════════════════════
# Helper utilities
# ═══════════════════════════════════════════════════════════════════════════

def _clear_screen():
    """Clear the terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


def _prompt(label: str, default: str = "", required: bool = False) -> str:
    """Prompt the user for input, with optional default."""
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"  {label}{suffix}: ").strip()
        if not value and default:
            return default
        if not value and required:
            print("    This field is required.  Please enter a value.")
            continue
        return value


def _prompt_yes_no(label: str, default: bool = True) -> bool:
    """Prompt for yes/no, return bool."""
    hint = "Y/n" if default else "y/N"
    value = input(f"  {label} [{hint}]: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes", "1", "true")


def _prompt_choice(label: str, options: dict, default: str = "1") -> str:
    """Display numbered menu and return the selected key."""
    print(f"\n  {label}")
    for key, (_, description) in sorted(options.items()):
        print(f"    {key}. {description}")
    while True:
        value = input(f"\n  Choice [{default}]: ").strip()
        if not value:
            value = default
        if value in options:
            return value
        print(f"    Invalid choice '{value}'.  Pick a number from the list.")


def _prompt_list(label: str, hint: str = "one per line, blank to finish") -> list:
    """Prompt for a list of values, one per line."""
    print(f"\n  {label} ({hint}):")
    items = []
    while True:
        val = input("    > ").strip()
        if not val:
            break
        items.append(val)
    return items


def _check_command(cmd: str) -> bool:
    """Check if a command is available on PATH."""
    return shutil.which(cmd) is not None


def _detect_ollama() -> bool:
    """Check if Ollama is running locally."""
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception:
        return False


def _detect_lmstudio() -> bool:
    """Check if LM Studio local server is running."""
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:1234/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception:
        return False


def _step_header(step_num: int, total: int, title: str):
    """Print a step header."""
    print()
    bar_width = 40
    filled = int(bar_width * step_num / total)
    bar = "#" * filled + "-" * (bar_width - filled)
    print(f"  Step {step_num}/{total}: {title}")
    print(f"  [{bar}] {step_num * 100 // total}%")
    print()


# ═══════════════════════════════════════════════════════════════════════════
# Setup Wizard
# ═══════════════════════════════════════════════════════════════════════════

class SetupWizard:
    """Interactive setup wizard that generates config.yaml."""

    TOTAL_STEPS = 11

    def __init__(self, output_path: str = "config.yaml"):
        self.output_path = Path(output_path)
        self.cfg = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self):
        """Run the full wizard flow."""
        _clear_screen()
        print(BANNER)

        self._step_prereqs()
        self._step_linkedin_creds()
        self._step_personal_info()
        self._step_job_search()
        self._step_ai_provider()
        self._step_cv_text()
        self._step_resume_file()
        self._step_feature_toggles()
        self._step_generate_config()
        self._step_validate()
        self._step_summary()

    # ------------------------------------------------------------------
    # Step 1: Prerequisites
    # ------------------------------------------------------------------

    def _step_prereqs(self):
        _step_header(1, self.TOTAL_STEPS, "Prerequisites Check")

        # Python version
        py_ver = sys.version_info[:2]
        ok_py = py_ver >= MIN_PYTHON
        status_py = "OK" if ok_py else "FAIL"
        print(f"  Python {py_ver[0]}.{py_ver[1]}  ... {status_py}")
        if not ok_py:
            print(f"    Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required.")
            if not _prompt_yes_no("Continue anyway?", default=False):
                sys.exit(1)

        # Chrome / Chromium
        has_chrome = (
            _check_command("google-chrome")
            or _check_command("google-chrome-stable")
            or _check_command("chromium")
            or _check_command("chromium-browser")
        )
        # Also check macOS application paths
        if not has_chrome and platform.system() == "Darwin":
            has_chrome = Path("/Applications/Google Chrome.app").exists()
        # Check Windows default paths
        if not has_chrome and platform.system() == "Windows":
            for p in [
                Path(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
                Path(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
            ]:
                if p.exists():
                    has_chrome = True
                    break

        status_chrome = "OK" if has_chrome else "NOT FOUND"
        print(f"  Chrome/Chromium ... {status_chrome}")
        if not has_chrome:
            print("    Chrome is needed for browser automation.")
            print("    Install from: https://www.google.com/chrome/")
            if not _prompt_yes_no("Continue without Chrome?", default=True):
                sys.exit(1)

        # PyYAML (already imported if we got here)
        print("  PyYAML         ... OK")

        # Selenium
        try:
            import selenium  # noqa: F401
            print("  Selenium       ... OK")
        except ImportError:
            print("  Selenium       ... NOT FOUND (run: pip install selenium)")

        # undetected-chromedriver
        try:
            import undetected_chromedriver  # noqa: F401
            print("  UC Browser     ... OK")
        except ImportError:
            print("  UC Browser     ... NOT FOUND (run: pip install undetected-chromedriver)")

        print()
        input("  Press Enter to continue...")

    # ------------------------------------------------------------------
    # Step 2: LinkedIn credentials
    # ------------------------------------------------------------------

    def _step_linkedin_creds(self):
        _step_header(2, self.TOTAL_STEPS, "LinkedIn Credentials")
        print("  Your LinkedIn login.  Leave blank to use browser profile login.\n")

        email = _prompt("LinkedIn email", default="")
        password = ""
        if email:
            password = _prompt("LinkedIn password", default="")

        self.cfg["linkedin"] = {
            "email": email,
            "password": password,
        }

    # ------------------------------------------------------------------
    # Step 3: Personal info
    # ------------------------------------------------------------------

    def _step_personal_info(self):
        _step_header(3, self.TOTAL_STEPS, "Personal Information")
        print("  Used to auto-fill application forms.\n")

        first = _prompt("First name", required=True)
        last = _prompt("Last name", required=True)
        email = _prompt("Email address", required=True)
        phone = _prompt("Phone (with country code)", default="")
        city = _prompt("City", default="")
        state = _prompt("State / Province", default="")
        zip_code = _prompt("ZIP / Postal code", default="")
        country = _prompt("Country", default="")

        self.cfg["personal"] = {
            "first_name": first,
            "last_name": last,
            "full_name": f"{first} {last}",
            "email": email,
            "phone": phone,
            "city": city,
            "state": state,
            "zip_code": zip_code,
            "country": country,
        }

        # Populate question_answers with personal info
        self.cfg["question_answers"] = {
            "first name": first,
            "last name": last,
            "full name": f"{first} {last}",
            "email": email,
            "phone": phone,
            "mobile": phone,
            "city": city,
            "state": state,
            "zip": zip_code,
            "postal": zip_code,
            "country": country,
        }

    # ------------------------------------------------------------------
    # Step 4: Job search terms + locations
    # ------------------------------------------------------------------

    def _step_job_search(self):
        _step_header(4, self.TOTAL_STEPS, "Job Search Configuration")
        print("  What roles are you looking for and where?\n")

        terms = _prompt_list(
            "Search terms / job titles",
            hint="one per line, blank line to finish",
        )
        if not terms:
            terms = ["Software Engineer"]
            print(f"    Using default: {terms[0]}")

        locations = _prompt_list(
            "Search locations",
            hint="e.g. 'London, England, United Kingdom'",
        )
        if not locations:
            locations = ["United States"]
            print(f"    Using default: {locations[0]}")

        date_opts = {"1": "Past hour", "2": "Past 24 hours", "3": "Past week", "4": "Past month"}
        date_choice = _prompt_choice(
            "How recent should postings be?",
            {k: (v, v) for k, v in date_opts.items()},
            default="2",
        )
        date_posted = date_opts[date_choice]

        easy_apply = _prompt_yes_no("Easy Apply only?", default=True)

        self.cfg["search"] = {
            "search_terms": terms,
            "search_locations": locations,
            "date_posted": date_posted,
            "sort_by": "Most recent",
            "easy_apply_only": easy_apply,
            "experience_level": [],
            "job_type": ["Full-time"],
            "work_location": [],
            "randomize_order": True,
        }

        # Basic filters
        self.cfg["filters"] = {
            "bad_words": DEFAULT_BAD_WORDS,
            "blacklisted_companies": [],
            "bad_title_words": DEFAULT_BAD_TITLES,
        }

    # ------------------------------------------------------------------
    # Step 5: AI provider
    # ------------------------------------------------------------------

    def _step_ai_provider(self):
        _step_header(5, self.TOTAL_STEPS, "AI Provider")
        print("  AI is used for match scoring, question answering, and more.")
        print("  Local options (Ollama, LM Studio) are free and private.\n")

        # Auto-detect local providers
        ollama_up = _detect_ollama()
        lmstudio_up = _detect_lmstudio()

        if ollama_up:
            print("  >>> Ollama detected running on localhost:11434")
        if lmstudio_up:
            print("  >>> LM Studio detected running on localhost:1234")

        enable_ai = _prompt_yes_no("Enable AI features?", default=True)
        if not enable_ai:
            self.cfg["ai"] = {"enabled": False, "provider": "ollama"}
            return

        # Pick default based on detection
        default_choice = "1"
        if ollama_up:
            default_choice = "1"
        elif lmstudio_up:
            default_choice = "2"

        choice = _prompt_choice("Select AI provider:", AI_PROVIDERS, default=default_choice)
        provider_id, provider_name = AI_PROVIDERS[choice]

        api_key = ""
        if provider_id not in ("ollama", "lmstudio"):
            api_key = _prompt(f"API key for {provider_name}", default="")
            if not api_key:
                print("    Warning: AI calls will fail without an API key.")

        model = _prompt("Model name (blank for default)", default="")

        self.cfg["ai"] = {
            "enabled": True,
            "provider": provider_id,
            "api_key": api_key,
        }
        if model:
            self.cfg["ai"]["model"] = model

    # ------------------------------------------------------------------
    # Step 6: CV text
    # ------------------------------------------------------------------

    def _step_cv_text(self):
        _step_header(6, self.TOTAL_STEPS, "CV / Resume Text")
        print("  Paste your CV text so AI can match you to jobs.")
        print("  (Paste multiple lines, then enter a blank line to finish.)")
        print("  Or press Enter to skip.\n")

        lines = []
        while True:
            line = input("  ")
            if not line:
                break
            lines.append(line)

        cv_text = "\n".join(lines).strip()
        if cv_text:
            self.cfg.setdefault("ai", {})["cv_text"] = cv_text
            print(f"\n  CV text captured ({len(cv_text)} characters).")
        else:
            print("  Skipped.  You can add cv_text to config.yaml later.")

    # ------------------------------------------------------------------
    # Step 7: Resume file path
    # ------------------------------------------------------------------

    def _step_resume_file(self):
        _step_header(7, self.TOTAL_STEPS, "Resume File")
        print("  Path to your PDF resume for uploading to applications.\n")

        resume_path = _prompt("Resume file path (blank to skip)", default="")
        if resume_path:
            p = Path(resume_path).expanduser()
            if not p.exists():
                print(f"    Warning: file not found at '{p}'")
                if not _prompt_yes_no("Save this path anyway?", default=True):
                    resume_path = ""
            else:
                resume_path = str(p.resolve())

        if resume_path:
            self.cfg["resume"] = {"path": resume_path}

    # ------------------------------------------------------------------
    # Step 8: Feature toggles
    # ------------------------------------------------------------------

    def _step_feature_toggles(self):
        _step_header(8, self.TOTAL_STEPS, "Feature Toggles")
        print("  Enable or disable key features.\n")

        # Match scoring threshold
        match_enabled = _prompt_yes_no("Enable AI match scoring?", default=True)
        min_score = 0
        if match_enabled:
            score_str = _prompt("Minimum match score (0-100)", default="70")
            try:
                min_score = int(score_str)
                min_score = max(0, min(100, min_score))
            except ValueError:
                min_score = 70

        self.cfg["match_scoring"] = {
            "enabled": match_enabled,
            "minimum_score": min_score,
        }

        # Recruiter messaging
        msg_enabled = _prompt_yes_no("Enable recruiter messaging?", default=False)
        self.cfg["recruiter_messaging"] = {"enabled": msg_enabled}

        # Dashboard
        dash_enabled = _prompt_yes_no("Enable web dashboard?", default=True)
        dash_port = 5000
        if dash_enabled:
            port_str = _prompt("Dashboard port", default="5000")
            try:
                dash_port = int(port_str)
            except ValueError:
                dash_port = 5000

        self.cfg["dashboard"] = {
            "enabled": dash_enabled,
            "port": dash_port,
            "host": "0.0.0.0",
        }

        # Salary intelligence
        salary_enabled = _prompt_yes_no("Enable salary intelligence?", default=True)
        self.cfg["salary_intelligence"] = {"enabled": salary_enabled}

        # Interview prep
        interview_enabled = _prompt_yes_no("Enable auto interview prep?", default=True)
        self.cfg["interview_prep"] = {
            "enabled": interview_enabled,
            "auto_generate": interview_enabled,
        }

        # Skill gap analysis
        skill_gap_enabled = _prompt_yes_no("Enable skill gap analysis?", default=True)
        self.cfg["skill_gap_analysis"] = {"enabled": skill_gap_enabled}

    # ------------------------------------------------------------------
    # Step 9: Generate config.yaml
    # ------------------------------------------------------------------

    def _step_generate_config(self):
        _step_header(9, self.TOTAL_STEPS, "Generate Configuration")

        # Add application defaults
        self.cfg.setdefault("application", {
            "years_of_experience": 0,
            "notice_period_days": "30",
            "desired_salary": "Negotiable",
            "require_visa": "No",
            "authorized_to_work": "Yes",
            "willing_to_relocate": "Yes",
        })

        # Check for existing config
        if self.output_path.exists():
            overwrite = _prompt_yes_no(
                f"'{self.output_path}' already exists.  Overwrite?",
                default=False,
            )
            if not overwrite:
                # Save with a different name
                alt = Path("config.generated.yaml")
                self.output_path = alt
                print(f"    Saving as '{alt}' instead.")

        # Write YAML
        with open(self.output_path, "w", encoding="utf-8") as fh:
            fh.write("# LinkedIn Lightning Applier -- Generated Configuration\n")
            fh.write(f"# Created by Setup Wizard on {_get_timestamp()}\n")
            fh.write("#\n")
            fh.write("# Edit this file to fine-tune settings.\n")
            fh.write("# Run 'python cli.py validate-config' to check for errors.\n\n")
            yaml.dump(
                self.cfg,
                fh,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
                width=120,
            )

        print(f"  Configuration saved to: {self.output_path.resolve()}")

    # ------------------------------------------------------------------
    # Step 10: Validate
    # ------------------------------------------------------------------

    def _step_validate(self):
        _step_header(10, self.TOTAL_STEPS, "Validate Configuration")

        try:
            from validate_config import ConfigValidator
        except ImportError:
            print("  Config validator not available.  Skipping validation.")
            return

        validator = ConfigValidator(self.cfg)
        is_valid = validator.validate()

        if validator.warnings:
            print("  Warnings:")
            for w in validator.warnings:
                print(f"    - {w}")

        if validator.errors:
            print("\n  Errors:")
            for e in validator.errors:
                print(f"    - {e}")

        if is_valid:
            print("  Configuration is valid!")
        else:
            print(f"\n  Found {len(validator.errors)} error(s).")
            print("  Edit the config file to fix them before running the bot.")

    # ------------------------------------------------------------------
    # Step 11: Summary
    # ------------------------------------------------------------------

    def _step_summary(self):
        _step_header(11, self.TOTAL_STEPS, "Setup Complete")

        personal = self.cfg.get("personal", {})
        search = self.cfg.get("search", {})
        ai_cfg = self.cfg.get("ai", {})
        features = []

        if self.cfg.get("match_scoring", {}).get("enabled"):
            ms = self.cfg["match_scoring"].get("minimum_score", 0)
            features.append(f"Match scoring (min {ms}%)")
        if self.cfg.get("recruiter_messaging", {}).get("enabled"):
            features.append("Recruiter messaging")
        if self.cfg.get("dashboard", {}).get("enabled"):
            port = self.cfg["dashboard"].get("port", 5000)
            features.append(f"Dashboard (port {port})")
        if self.cfg.get("salary_intelligence", {}).get("enabled"):
            features.append("Salary intelligence")
        if self.cfg.get("interview_prep", {}).get("enabled"):
            features.append("Interview prep")
        if self.cfg.get("skill_gap_analysis", {}).get("enabled"):
            features.append("Skill gap analysis")

        print("  Summary:")
        print("  " + "-" * 50)
        print(f"  Name:       {personal.get('full_name', 'Not set')}")
        print(f"  Email:      {personal.get('email', 'Not set')}")
        print(f"  Location:   {personal.get('city', '?')}, {personal.get('country', '?')}")
        print()
        print(f"  Search terms:     {len(search.get('search_terms', []))}")
        for term in search.get("search_terms", [])[:5]:
            print(f"    - {term}")
        if len(search.get("search_terms", [])) > 5:
            print(f"    ... and {len(search['search_terms']) - 5} more")
        print()
        print(f"  Search locations: {len(search.get('search_locations', []))}")
        for loc in search.get("search_locations", [])[:5]:
            print(f"    - {loc}")
        if len(search.get("search_locations", [])) > 5:
            print(f"    ... and {len(search['search_locations']) - 5} more")
        print()
        print(f"  AI provider:      {ai_cfg.get('provider', 'none')}")
        print(f"  AI enabled:       {'Yes' if ai_cfg.get('enabled') else 'No'}")
        print()
        print(f"  Enabled features: {len(features)}")
        for feat in features:
            print(f"    + {feat}")

        print()
        print(f"  Config file: {self.output_path.resolve()}")
        print()
        print("  Next steps:")
        print("    1. Review and edit config.yaml as needed")
        print("    2. Run:  python cli.py validate-config")
        print("    3. Run:  python cli.py run")
        print()


# ═══════════════════════════════════════════════════════════════════════════
# Standalone helpers
# ═══════════════════════════════════════════════════════════════════════════

def _get_timestamp() -> str:
    """Return a human-readable timestamp."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """Run the setup wizard as a standalone script."""
    wizard = SetupWizard()
    try:
        wizard.run()
    except KeyboardInterrupt:
        print("\n\n  Setup cancelled.")
        sys.exit(130)
    except EOFError:
        print("\n\n  Input ended.  Setup incomplete.")
        sys.exit(1)


if __name__ == "__main__":
    main()
