#!/usr/bin/env python3
"""robots.txt compliance check (RFC 9309), deliberately cautious.

    python tools/robots_check.py <url> [--agent NAME]
    exit 0 = the published policy permits this path
    exit 1 = it does not, or permission could not be confirmed

This bot reaches a lot of sites it does not own — LinkedIn, Google Jobs, Indeed,
Glassdoor and twelve ATS platforms. `robots.txt` is the machine-readable place a
site states what automated clients may fetch. Honouring it is the cheapest,
clearest line between "automating my own job search" and "abusing someone's
infrastructure", and it is the line a court or an abuse team will look at first.

This never overrides a site that has said no. Ambiguity fails CLOSED:

  * longest-matching rule wins; on equal specificity, Disallow wins (RFC 9309 §2.2.2)
  * a group matching our agent is used; otherwise the "*" group
  * `Allow:` with an empty value means nothing is allowed by that line
  * `Disallow:` with an empty value means everything is allowed
  * blank lines inside a group do NOT end it — Python's stdlib robotparser
    silently drops rules in that case, which fails OPEN, so this parses itself
  * 404/410 = no published policy = permission
  * any other failure (timeout, 5xx, unreadable) = permission NOT confirmed

Use as a library:
    from tools.robots_check import robots_allows
    robots_allows("https://example.com/jobs", agent="LightningApplier")
"""

import re
import sys
from urllib.parse import unquote, urlparse

DEFAULT_AGENT = "LightningApplier"
TIMEOUT = 15

# One robots.txt per host per run. The pipeline checks hundreds of job URLs
# across a handful of hosts; refetching each time would be its own abuse.
_CACHE = {}


def clear_cache():
    """Forget fetched robots.txt files (tests, or a long-lived process)."""
    _CACHE.clear()


class Verdict:
    """Why a URL was allowed or refused — the reason matters more than the bool."""

    def __init__(self, allowed: bool, reason: str, rule: str = ""):
        self.allowed = allowed
        self.reason = reason
        self.rule = rule

    def __bool__(self):
        return self.allowed

    def __repr__(self):
        return f"<Verdict {'ALLOW' if self.allowed else 'DENY'}: {self.reason}>"


def _norm(path: str) -> str:
    """Normalise a path for comparison (percent-decoded, always rooted)."""
    if not path:
        return "/"
    try:
        path = unquote(path)
    except Exception:
        pass
    return path if path.startswith("/") else "/" + path


def _pattern_matches(pattern: str, path: str) -> bool:
    """RFC 9309 path matching: '*' is any sequence, trailing '$' anchors the end."""
    if pattern == "":
        return False
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    # Build a regex from the literal pattern, honouring only '*' as a wildcard.
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern)
    rx = "^" + rx + ("$" if anchored else "")
    try:
        return re.search(rx, path) is not None
    except re.error:
        return False


def parse_robots(text: str, agent: str):
    """Return the (allow, disallow) rule lists for `agent`, else for '*'.

    Parsed by hand rather than with urllib.robotparser: that implementation
    treats a blank line as ending a group and drops the remaining rules, which
    silently turns a Disallow into permission.
    """
    agent_l = agent.lower()
    groups = []            # [(set_of_agents, [(is_allow, value), ...])]
    current_agents, current_rules, last_was_agent = set(), [], False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue                      # blank lines do NOT end a group
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()

        if field == "user-agent":
            if not last_was_agent and current_agents:
                groups.append((current_agents, current_rules))
                current_agents, current_rules = set(), []
            current_agents.add(value.lower())
            last_was_agent = True
        elif field in ("allow", "disallow"):
            if current_agents:
                current_rules.append((field == "allow", value))
            last_was_agent = False
    if current_agents:
        groups.append((current_agents, current_rules))

    # RFC 9309 §2.2.1: match on the product token, case-insensitively. A group
    # token matches if it *is* our token, or is a prefix of it ending on a
    # token boundary — the rule that makes "Googlebot-News" fall back to the
    # "Googlebot" group. The boundary matters: without it a group addressed to
    # "li" would capture "lightningapplier". Substring matching in the other
    # direction is not allowed either, for the same reason.
    def token_match(a: str) -> bool:
        if not a or a == "*" or not agent_l.startswith(a):
            return False
        return len(agent_l) == len(a) or agent_l[len(a)] in "-_/ ."

    # Most specific wins: the longest matching token. Every group addressed to
    # that token is merged (RFC 9309 §2.2.1 — a token may head several groups).
    best = ""
    for agents, _ in groups:
        for a in agents:
            if token_match(a) and len(a) > len(best):
                best = a
    if best:
        return [r for agents, rules in groups if best in agents for r in rules]
    star = [r for agents, rules in groups if "*" in agents for r in rules]
    return star


def evaluate(rules, path: str) -> Verdict:
    """Apply RFC 9309 precedence: longest match wins; ties go to Disallow."""
    path = _norm(path)
    best_len, best_allow, best_rule = -1, None, ""
    for is_allow, value in rules:
        if value == "":
            # "Disallow:" (empty) allows everything; "Allow:" (empty) grants nothing.
            if not is_allow:
                if best_len < 0:
                    best_len, best_allow, best_rule = 0, True, "Disallow: (empty)"
            continue
        if not _pattern_matches(value, path):
            continue
        length = len(value.rstrip("$"))
        if length > best_len or (length == best_len and not is_allow):
            best_len, best_allow, best_rule = length, is_allow, \
                f"{'Allow' if is_allow else 'Disallow'}: {value}"
    if best_allow is None:
        return Verdict(True, "no rule matches this path", "")
    return Verdict(bool(best_allow), "matched the most specific rule", best_rule)


def fetch_robots(base: str, agent: str):
    """(text, verdict_or_None). A non-None verdict means: stop, use that."""
    key = (base, agent)
    if key in _CACHE:
        return _CACHE[key]
    result = _fetch_robots_uncached(base, agent)
    _CACHE[key] = result
    return result


def _fetch_robots_uncached(base: str, agent: str):
    url = base.rstrip("/") + "/robots.txt"
    try:
        import requests
        r = requests.get(url, timeout=TIMEOUT,
                         headers={"User-Agent": agent}, allow_redirects=True)
    except Exception as exc:
        return None, Verdict(False, f"could not read robots.txt ({type(exc).__name__}) "
                                    "— permission not confirmed")
    if r.status_code in (404, 410):
        return None, Verdict(True, f"no robots.txt published (HTTP {r.status_code})")
    if r.status_code >= 400:
        return None, Verdict(False, f"robots.txt returned HTTP {r.status_code} "
                                    "— permission not confirmed")
    return r.text, None


def robots_allows(url: str, agent: str = DEFAULT_AGENT) -> Verdict:
    """Does the site's published policy permit fetching this URL?"""
    parts = urlparse(url)
    if not parts.scheme or not parts.netloc:
        return Verdict(False, f"not an absolute URL: {url!r}")
    base = f"{parts.scheme}://{parts.netloc}"
    text, early = fetch_robots(base, agent)
    if early is not None:
        return early
    rules = parse_robots(text, agent)
    if not rules:
        return Verdict(True, "robots.txt has no rules for this agent")
    return evaluate(rules, parts.path or "/")


def check_urls(urls, agent: str = DEFAULT_AGENT):
    """[(url, Verdict)] for a batch — robots.txt is fetched once per host."""
    return [(u, robots_allows(u, agent)) for u in urls]


def main(argv):
    agent, args, i = DEFAULT_AGENT, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--agent" and i + 1 < len(argv):
            agent, i = argv[i + 1], i + 2
            continue
        if a.startswith("--agent="):
            agent, i = a.split("=", 1)[1], i + 1
            continue
        if not a.startswith("--"):
            args.append(a)
        i += 1
    if not args:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    url = args[0]
    v = robots_allows(url, agent)
    mark = "✅ ALLOWED" if v.allowed else "⛔ NOT ALLOWED"
    print(f"{mark}  {url}")
    print(f"   agent : {agent}")
    print(f"   reason: {v.reason}")
    if v.rule:
        print(f"   rule  : {v.rule}")
    if not v.allowed:
        print("   → do not fetch this path automatically.")
    return 0 if v.allowed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
