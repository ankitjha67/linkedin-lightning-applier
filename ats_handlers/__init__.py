"""
ATS handler registry.

Central place that maps an application URL to the right handler class. The rest
of the app only needs three things from here:

    detect_ats(url)                 -> "workday" | "greenhouse" | ... | None
    get_handler(name, ai, cfg)      -> ATSHandler instance | None
    handler_for_url(url, ai, cfg)   -> ATSHandler instance | None

Adding a new ATS is two lines: a URL pattern in ``_PATTERNS`` and a class in
``_HANDLERS``. Everything else (detection, instantiation, the apply loop) is
already wired.
"""

import re

from .base import ATSHandler
from .generic import GenericHandler, MultiStepHandler, SinglePageHandler
from .handlers import (
    ADPHandler,
    AshbyHandler,
    BambooHRHandler,
    GreenhouseHandler,
    ICIMSHandler,
    JobviteHandler,
    LeverHandler,
    SmartRecruitersHandler,
    SuccessFactorsHandler,
    TaleoHandler,
    WorkableHandler,
)
from .workday import WorkdayHandler

__all__ = [
    "ATSHandler", "SinglePageHandler", "MultiStepHandler", "GenericHandler",
    "detect_ats", "get_handler", "handler_for_url",
    "ALL_ATS", "SUPPORTED_ATS",
]

# URL host/path fragments → ATS name. First match wins; ordered most-specific
# first so, e.g., a Workday tenant never falls through to a broader pattern.
_PATTERNS = [
    ("workday", [r"myworkdayjobs\.com", r"myworkday\.com", r"wd\d+\.myworkdayjobs\.com",
                 r"\.workday\.com"]),
    ("greenhouse", [r"boards\.greenhouse\.io", r"job-boards\.greenhouse\.io",
                    r"greenhouse\.io"]),
    ("lever", [r"jobs\.lever\.co", r"\.lever\.co"]),
    ("ashby", [r"jobs\.ashbyhq\.com", r"ashbyhq\.com"]),
    ("smartrecruiters", [r"smartrecruiters\.com"]),
    ("workable", [r"apply\.workable\.com", r"jobs\.workable\.com", r"\.workable\.com"]),
    ("jobvite", [r"jobs\.jobvite\.com", r"app\.jobvite\.com", r"\.jobvite\.com"]),
    ("bamboohr", [r"\.bamboohr\.com"]),
    ("icims", [r"\.icims\.com"]),
    ("taleo", [r"\.taleo\.net", r"taleo\.net"]),
    ("successfactors", [r"\.successfactors\.com", r"\.successfactors\.eu",
                        r"\.sapsf\.com", r"\.sapsf\.eu"]),
    ("adp", [r"workforcenow\.adp\.com", r"recruiting\.adp\.com", r"myjobs\.adp\.com",
             r"\.adp\.com"]),
]

_HANDLERS = {
    "workday": WorkdayHandler,
    "greenhouse": GreenhouseHandler,
    "lever": LeverHandler,
    "ashby": AshbyHandler,
    "smartrecruiters": SmartRecruitersHandler,
    "workable": WorkableHandler,
    "jobvite": JobviteHandler,
    "bamboohr": BambooHRHandler,
    "icims": ICIMSHandler,
    "taleo": TaleoHandler,
    "successfactors": SuccessFactorsHandler,
    "adp": ADPHandler,
    "generic": GenericHandler,
}

#: every ATS this package can drive (excludes the "generic" fallback)
ALL_ATS = [name for name, _ in _PATTERNS]
#: alias kept for readability at call sites
SUPPORTED_ATS = ALL_ATS


def detect_ats(url: str):
    """Return the ATS name for a URL, or None if it matches no known platform."""
    if not url:
        return None
    for name, pats in _PATTERNS:
        for pat in pats:
            if re.search(pat, url, re.IGNORECASE):
                return name
    return None


def get_handler(name: str, ai, cfg: dict):
    """Instantiate a handler by ATS name. Returns None for unknown names."""
    cls = _HANDLERS.get((name or "").lower())
    if cls is None:
        return None
    return cls(ai, cfg)


def handler_for_url(url: str, ai, cfg: dict):
    """Detect the ATS from a URL and return an instantiated handler (or None)."""
    name = detect_ats(url)
    if not name:
        return None
    return get_handler(name, ai, cfg)
