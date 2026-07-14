"""
Environment doctor — detect, install, and update everything the bot needs.

The two errors that kill first launches are (1) missing Python packages and
(2) the Chrome ↔ ChromeDriver version mismatch ("This version of ChromeDriver
only supports Chrome version NNN"). This module fixes both automatically:

  * ``detect_chrome_version()`` finds the INSTALLED Chrome's major version on
    Windows (registry + known paths), macOS, and Linux — so the driver is
    always pinned to what the user actually has. ``linkedin.create_browser``
    calls it when ``browser.chrome_version`` isn't set, and self-heals on a
    mismatch by parsing the error and retrying with the right version.
  * ``check_packages()`` compares required/optional packages against what's
    importable; ``install_packages()`` pip-installs (or ``--upgrade``s) into
    the CURRENT interpreter (``sys.executable -m pip``), venv-safe.
  * ``check_optional_tools()`` reports LaTeX engines, pdftotext, and local LLM
    servers (Ollama/LM Studio) so degraded features are visible up front.
  * ``run_doctor(fix=..., upgrade=...)`` does the full sweep; exposed as
    ``python cli.py doctor [--fix] [--upgrade]``.

Stdlib-only on purpose: the doctor must run BEFORE dependencies are installed.
"""

import logging
import os
import platform
import re
import shutil
import subprocess
import sys

log = logging.getLogger("lla.doctor")

MIN_PYTHON = (3, 10)

# distribution name -> module import name (where they differ)
REQUIRED_PACKAGES = {
    "undetected-chromedriver": "undetected_chromedriver",
    "selenium": "selenium",
    "PyYAML": "yaml",
    "openai": "openai",
    "requests": "requests",
    "beautifulsoup4": "bs4",
}
OPTIONAL_PACKAGES = {
    "anthropic": "anthropic",
    "fpdf2": "fpdf",
    "python-docx": "docx",
    "flask": "flask",
    "pdfminer.six": "pdfminer",
}

_CHROME_WIN_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
_CHROME_NIX_NAMES = ["google-chrome", "google-chrome-stable", "chromium-browser",
                     "chromium", "chrome"]
_CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

_VERSION_RE = re.compile(r"(\d+)\.\d+\.\d+")
# The exact mismatch error undetected-chromedriver/selenium raise
MISMATCH_RE = re.compile(r"only supports Chrome version (\d+)", re.I)


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

def python_ok():
    """(ok, '3.11.4') — is the running interpreter new enough?"""
    v = sys.version_info
    return (v >= MIN_PYTHON, f"{v.major}.{v.minor}.{v.micro}")


# ---------------------------------------------------------------------------
# Chrome detection (cross-platform)
# ---------------------------------------------------------------------------

def _version_from_output(text: str):
    m = _VERSION_RE.search(text or "")
    return int(m.group(1)) if m else None


def _win_registry_version():
    try:
        import winreg  # noqa: F401 — Windows only
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(hive, r"Software\Google\Chrome\BLBeacon")
                version, _ = winreg.QueryValueEx(key, "version")
                v = _version_from_output(version)
                if v:
                    return v
            except OSError:
                continue
    except ImportError:
        pass
    return None


def _exe_version(path_or_name: str):
    try:
        out = subprocess.run([path_or_name, "--version"], capture_output=True,
                             timeout=10, text=True)
        return _version_from_output(out.stdout or out.stderr)
    except Exception:
        return None


def detect_chrome_version():
    """Major version of the installed Chrome/Chromium, or None if not found."""
    system = platform.system()
    if system == "Windows":
        v = _win_registry_version()
        if v:
            return v
        for path in _CHROME_WIN_PATHS:
            if os.path.exists(path):
                # chrome.exe --version doesn't print on Windows; read the
                # version folder next to the exe (e.g. .../Application/126.0.x.y/)
                app_dir = os.path.dirname(path)
                try:
                    for entry in os.listdir(app_dir):
                        v = _version_from_output(entry)
                        if v and os.path.isdir(os.path.join(app_dir, entry)):
                            return v
                except OSError:
                    continue
        return None
    if system == "Darwin":
        if os.path.exists(_CHROME_MAC):
            return _exe_version(_CHROME_MAC)
        return None
    # Linux / other
    for name in _CHROME_NIX_NAMES:
        if shutil.which(name):
            v = _exe_version(name)
            if v:
                return v
    return None


def chrome_version_from_error(error_text: str):
    """Parse the Chrome major version out of a driver-mismatch error."""
    m = MISMATCH_RE.search(str(error_text or ""))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Python packages
# ---------------------------------------------------------------------------

def _installed_version(dist_name: str):
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version(dist_name)
        except PackageNotFoundError:
            return None
    except Exception:
        return None


def check_packages(include_optional: bool = True):
    """[{name, module, installed, required}] for required (+optional) packages."""
    out = []
    groups = [("required", REQUIRED_PACKAGES)]
    if include_optional:
        groups.append(("optional", OPTIONAL_PACKAGES))
    for kind, mapping in groups:
        for dist, module in mapping.items():
            out.append({"name": dist, "module": module, "kind": kind,
                        "installed": _installed_version(dist)})
    return out


_build_tools_bootstrapped = False


def bootstrap_build_tools() -> bool:
    """Upgrade pip/setuptools/wheel once per run.

    Source-distributed packages (undetected-chromedriver ships as an sdist)
    fail to build on old system setuptools (e.g. Debian's `install_layout`
    AttributeError). Bringing the build tools current first prevents that
    whole class of install failures.
    """
    global _build_tools_bootstrapped
    if _build_tools_bootstrapped:
        return True
    try:
        rc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade",
             "pip", "setuptools", "wheel"],
            timeout=600).returncode
        _build_tools_bootstrapped = (rc == 0)
    except Exception as exc:
        log.debug("build-tools bootstrap failed: %s", exc)
    return _build_tools_bootstrapped


def _pip_install_one(name: str, upgrade: bool = False) -> bool:
    args = (["--upgrade", name] if upgrade else [name])
    cmd = [sys.executable, "-m", "pip", "install", *args]
    log.info("Running: %s", " ".join(cmd))
    try:
        return subprocess.run(cmd, timeout=900).returncode == 0
    except Exception as exc:
        log.warning("pip install %s failed: %s", name, exc)
        return False


def install_packages(names: list, upgrade: bool = False) -> bool:
    """pip install into the CURRENT interpreter, one package at a time.

    Build tooling is refreshed first (fixes most wheel-build failures), then
    each package is installed independently so a single failure does NOT block
    the rest. Returns True only if every package installed.
    """
    if not names:
        return True
    bootstrap_build_tools()
    all_ok = True
    for name in names:
        if not _pip_install_one(name, upgrade=upgrade):
            log.warning("Could not install %s (continuing with the rest)", name)
            all_ok = False
    return all_ok


# ---------------------------------------------------------------------------
# Optional external tools
# ---------------------------------------------------------------------------

def _port_open(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def check_optional_tools() -> dict:
    """Presence of the nice-to-haves that features degrade without."""
    return {
        "latex_engine": next((e for e in ("lualatex", "xelatex", "pdflatex")
                              if shutil.which(e)), None),
        "pdftotext": bool(shutil.which("pdftotext")),
        "ollama_running": _port_open(11434),
        "lmstudio_running": _port_open(1234),
    }


# ---------------------------------------------------------------------------
# The full sweep
# ---------------------------------------------------------------------------

def run_doctor(fix: bool = False, upgrade: bool = False,
               include_optional: bool = True) -> dict:
    """Detect everything; install/update when fix/upgrade is set.

    Returns {python: {ok, version}, chrome: {version}, packages: [...],
             installed_now: [...], upgraded_now: [...], tools: {...}, ok: bool}.
    """
    py_ok, py_ver = python_ok()
    chrome = detect_chrome_version()
    packages = check_packages(include_optional)
    tools = check_optional_tools()

    missing_required = [p["name"] for p in packages
                        if p["kind"] == "required" and not p["installed"]]
    missing_optional = [p["name"] for p in packages
                        if p["kind"] == "optional" and not p["installed"]]

    installed_now, upgraded_now = [], []
    if fix and missing_required:
        if install_packages(missing_required):
            installed_now = missing_required
    if fix and include_optional and missing_optional:
        if install_packages(missing_optional):
            installed_now += missing_optional
    if upgrade:
        # Keep the browser-facing stack current — new Chrome releases need a
        # current undetected-chromedriver/selenium more often than anything else.
        targets = ["undetected-chromedriver", "selenium"]
        if install_packages(targets, upgrade=True):
            upgraded_now = targets

    still_missing = [p for p in missing_required if p not in installed_now]
    return {
        "python": {"ok": py_ok, "version": py_ver},
        "chrome": {"version": chrome},
        "packages": packages,
        "missing_required": still_missing,
        "missing_optional": [p for p in missing_optional if p not in installed_now],
        "installed_now": installed_now,
        "upgraded_now": upgraded_now,
        "tools": tools,
        "ok": py_ok and not still_missing,
    }
