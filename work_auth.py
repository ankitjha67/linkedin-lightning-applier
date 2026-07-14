"""
Country-aware work-authorization answers.

A single global "authorized_to_work: Yes" is wrong the moment you apply across
borders: an Indian citizen is authorized in India with no visa, needs
sponsorship in the UK — unless they hold a UK visa. Authorization is a function
of (job country) x (citizenship + visas held), so this module derives the
answer per job instead of parroting one config value.

Config:

    work_authorization:
      citizenship: ["India"]              # countries where you work by right
      visas:                              # work authorizations you hold
        - country: "United Kingdom"
          type: "Skilled Worker"          # optional, informational
      # When neither the question nor the job location names a country,
      # fall through (None) so legacy config/AI answers instead.

Question handling (deterministic, zero tokens):
  * "Are you (legally) authorized/eligible to work in X?"  → Yes iff X is
    covered by citizenship or a visa.
  * "Do you require visa sponsorship (for X)?"             → inverted: No iff
    covered, Yes otherwise — exactly the behavior requested: applying to any
    country not in your visa list answers that you are NOT authorized / DO
    need sponsorship.
  * "Are you a citizen of X?"                              → citizenship only.
  * Country comes from the question text first, else the job's location.
    No country found → returns None (never guesses).

This runs BEFORE the answer cache/RAG/LLM: authorization answers are
job-dependent, so a cached "Yes" from an Indian posting must never leak into a
UK application.
"""

import logging
import re

log = logging.getLogger("lla.work_auth")

# Canonical country -> aliases (matched as whole words, case-insensitive).
# Covers the bot's target markets plus common ATS phrasings.
COUNTRY_ALIASES = {
    "india": ["india", "in", "bharat"],
    "united kingdom": ["united kingdom", "uk", "u.k", "great britain", "britain",
                       "england", "scotland", "wales", "northern ireland"],
    "united states": ["united states", "usa", "us", "u.s", "u.s.a", "america",
                      "united states of america"],
    "singapore": ["singapore", "sg"],
    "canada": ["canada", "ca"],
    "australia": ["australia", "au"],
    "united arab emirates": ["united arab emirates", "uae", "u.a.e", "emirates",
                             "dubai", "abu dhabi"],
    "hong kong": ["hong kong", "hong kong sar", "hk"],
    "germany": ["germany", "deutschland", "de"],
    "switzerland": ["switzerland", "ch"],
    "netherlands": ["netherlands", "holland", "nl"],
    "france": ["france", "fr"],
    "ireland": ["ireland", "republic of ireland", "ie"],
    "japan": ["japan", "jp"],
    "china": ["china", "prc", "mainland china"],
    "new zealand": ["new zealand", "nz"],
    "saudi arabia": ["saudi arabia", "ksa", "saudi"],
    "qatar": ["qatar"],
    "spain": ["spain", "es"],
    "italy": ["italy", "it"],
    "poland": ["poland", "pl"],
    "sweden": ["sweden", "se"],
    "brazil": ["brazil", "br"],
    "mexico": ["mexico", "mx"],
}

# Major-hub cities -> country, for job locations like "London" or "Dubai, UAE"
# whose country half may be missing.
CITY_COUNTRY = {
    "london": "united kingdom", "manchester": "united kingdom",
    "edinburgh": "united kingdom", "leeds": "united kingdom",
    "cardiff": "united kingdom", "belfast": "united kingdom",
    "new york": "united states", "san francisco": "united states",
    "seattle": "united states", "chicago": "united states",
    "austin": "united states", "boston": "united states",
    "toronto": "canada", "vancouver": "canada", "montreal": "canada",
    "sydney": "australia", "melbourne": "australia",
    "dubai": "united arab emirates", "abu dhabi": "united arab emirates",
    "frankfurt": "germany", "berlin": "germany", "munich": "germany",
    "paris": "france", "amsterdam": "netherlands", "zurich": "switzerland",
    "dublin": "ireland", "tokyo": "japan", "shanghai": "china",
    "mumbai": "india", "bangalore": "india", "bengaluru": "india",
    "gurugram": "india", "gurgaon": "india", "delhi": "india",
    "new delhi": "india", "hyderabad": "india", "pune": "india",
    "chennai": "india", "noida": "india", "kolkata": "india",
}

# Short aliases that collide with common words — only match when uppercase in
# the original text ("US", "IN", "IT"...), so "in the office" never means India.
_AMBIGUOUS_SHORT = {"in", "us", "it", "de", "ie", "ca", "au", "sg", "ch",
                    "nl", "fr", "jp", "se", "es", "pl", "br", "mx", "hk"}

_AUTH_PAT = re.compile(
    r"authori[sz]ed|authori[sz]ation|right to work|eligible to work|"
    r"legally (?:able|permitted|allowed|entitled) to work|work permit|"
    r"permission to work|lawfully work", re.I)
_SPONSOR_PAT = re.compile(r"sponsor|require.{0,20}visa|need.{0,20}visa|visa status|"
                          r"visa sponsorship|immigration support", re.I)
_CITIZEN_PAT = re.compile(r"citizen", re.I)


def _norm_country(name: str) -> str:
    """Map any alias/spelling to the canonical country key ('' if unknown)."""
    n = (name or "").strip().lower().rstrip(".")
    for canon, aliases in COUNTRY_ALIASES.items():
        if n == canon or n in aliases:
            return canon
    return ""


class WorkAuthorization:
    """Derive work-authorization answers from citizenship + visas held."""

    def __init__(self, cfg: dict):
        wa = (cfg or {}).get("work_authorization", {}) or {}
        self.enabled = bool(wa.get("citizenship") or wa.get("visas"))
        self.citizenship = set()
        for c in wa.get("citizenship", []) or []:
            canon = _norm_country(c)
            if canon:
                self.citizenship.add(canon)
            else:
                log.warning("work_authorization: unknown citizenship country %r", c)
        self.visa_countries = set()
        self.visas = []
        for v in wa.get("visas", []) or []:
            country = v.get("country", "") if isinstance(v, dict) else str(v)
            canon = _norm_country(country)
            if canon:
                self.visa_countries.add(canon)
                self.visas.append({"country": canon,
                                   "type": (v.get("type", "") if isinstance(v, dict) else "")})
            else:
                log.warning("work_authorization: unknown visa country %r", country)

    # ------------------------------------------------------------------
    # Country detection
    # ------------------------------------------------------------------

    def country_from_text(self, text: str) -> str:
        """Find the country a question/location refers to ('' if none)."""
        if not text:
            return ""
        low = text.lower()
        # Longest aliases first so "united arab emirates" beats "emirates".
        candidates = []
        for canon, aliases in COUNTRY_ALIASES.items():
            for alias in aliases:
                candidates.append((alias, canon))
        candidates.sort(key=lambda x: -len(x[0]))
        for alias, canon in candidates:
            if alias in _AMBIGUOUS_SHORT:
                # Only match as an uppercase standalone word in the ORIGINAL text.
                if re.search(rf"\b{alias.upper()}\b", text):
                    return canon
                continue
            if re.search(rf"\b{re.escape(alias)}\b", low):
                return canon
        for city, canon in CITY_COUNTRY.items():
            if re.search(rf"\b{re.escape(city)}\b", low):
                return canon
        return ""

    # ------------------------------------------------------------------
    # Authorization logic
    # ------------------------------------------------------------------

    def is_authorized(self, country: str) -> bool:
        canon = _norm_country(country) or country
        return canon in self.citizenship or canon in self.visa_countries

    def authorized_countries(self) -> list:
        return sorted(self.citizenship | self.visa_countries)

    def recognizes(self, question: str) -> bool:
        """True if this looks like a work-auth/citizenship/sponsorship question.

        Callers use this to keep recognized questions OUT of the exact cache and
        RAG even when answer() couldn't produce a value (unknown country,
        options that can't express Yes/No) — a stored answer for these must
        never be reused across countries.
        """
        if not (self.enabled and question):
            return False
        return bool(_CITIZEN_PAT.search(question) or _AUTH_PAT.search(question)
                    or _SPONSOR_PAT.search(question))

    def answer(self, question: str, job_location: str = "",
               options: list = None):
        """Deterministic answer for a work-auth question, or None if not one.

        The country is taken from the question text first (an explicit
        "...in the US?" always wins), else from the job's location. If neither
        names a country, returns None so legacy config/AI can answer.
        """
        if not (self.enabled and question):
            return None
        is_citizen = bool(_CITIZEN_PAT.search(question))
        is_auth = bool(_AUTH_PAT.search(question))
        is_sponsor = bool(_SPONSOR_PAT.search(question))
        if not (is_citizen or is_auth or is_sponsor):
            return None

        country = self.country_from_text(question) or \
            self.country_from_text(job_location)
        if not country:
            return None

        if is_citizen and not (is_auth or is_sponsor):
            verdict = country in self.citizenship
        elif is_auth:
            # Authorization polarity wins for mixed questions like
            # "authorized to work in X without sponsorship?"
            verdict = self.is_authorized(country)
        else:
            # Pure sponsorship question: "do you require a visa/sponsorship?"
            # is the INVERSE of being authorized.
            verdict = not self.is_authorized(country)
            return self._fit("Yes" if verdict else "No", options)

        return self._fit("Yes" if verdict else "No", options)

    @staticmethod
    def _fit(answer: str, options: list = None):
        """Map Yes/No onto the offered options when present."""
        if not options:
            return answer
        al = answer.lower()
        for opt in options:
            if opt.strip().lower() == al:
                return opt
        for opt in options:
            ol = opt.strip().lower()
            # "No, I do not require sponsorship" / "Yes, I will require..."
            if ol.startswith(al) or f" {al} " in f" {ol} ":
                return opt
        return None  # can't express the truthful answer in these options
