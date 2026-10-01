"""How fast a person actually does this — one definition, used everywhere.

    lla pacing                 # print the protocol to paste into Claude for Chrome
    lla pacing --profile fast  # or: careful (default: normal)

Two things drive this browser: the Selenium bot, and Claude for Chrome reading
a prompt. They should behave the same, and the numbers should live in one place
rather than being restated in prose that quietly drifts from the code. Every
figure in the Claude-for-Chrome protocol is generated from this module, and a
test fails if the document stops matching.

The design principle is worth stating plainly, because it is easy to get
backwards: **humanised means slower and less, not sneakier.** A person reads a
job description before deciding, types an essay answer at typing speed, does
five or six applications and then goes and does something else. Pacing a bot
that way is the same thing as being a light user of someone else's servers.
Anything that only disguises the timing while keeping the volume is not
humanisation, and this module deliberately does not offer it.

The one place that matters most is free text. `field.send_keys(value)` puts a
four-hundred-character answer into the box in a single event; a person takes
about a minute over it. Nothing else in a form fill is that far from human.

Hard stops live here too. If a CAPTCHA, a checkpoint, or an "unusual activity"
notice appears, the correct behaviour is to stop and tell the person — not to
wait longer and try again. Working around a challenge is the point at which
automating your own job search turns into something else.
"""

import random
import re
import time

# ── reading ────────────────────────────────────────────────────────────────
# People skim a posting rather than read every word; 200-250 wpm is the usual
# range for skimming prose on screen.
SKIM_WPM = 220
MIN_DWELL = 1.5          # even a glance takes a moment
# The ceiling has to clear a real posting, or it silently contradicts the
# protocol: job descriptions run 400-900 words, and 600 words at SKIM_WPM is
# already 164 seconds — the figure the generated document quotes. A 45-second
# cap would have held every posting to a quarter of what the prompt asks for.
# Past ~1000 words nobody is still reading carefully, so the cap bites there.
MAX_DWELL = 300.0

# ── typing ─────────────────────────────────────────────────────────────────
# 35-65 wpm covers most people typing prose they are composing as they go.
TYPING_WPM = (35, 65)
CHARS_PER_WORD = 5.0
# Pauses land where thought happens: after a sentence, and between words.
PAUSE_AFTER_SENTENCE = (0.35, 1.10)
PAUSE_AFTER_WORD = (0.02, 0.14)
# Occasionally someone stops to think mid-answer.
THINK_PAUSE_CHANCE = 0.02
THINK_PAUSE = (0.8, 2.6)

# ── rhythm between actions (mirrors config `scheduling` defaults) ───────────
DELAY_BETWEEN_JOBS = (3, 8)
DELAY_BETWEEN_SEARCHES = (5, 15)
DELAY_AFTER_APPLY = 4

# ── session shape ──────────────────────────────────────────────────────────
# Nobody submits forty applications in one unbroken sitting.
BURST_APPLICATIONS = (5, 8)          # before a break
BREAK_MINUTES = (5, 15)
MAX_APPLICATIONS_PER_HOUR = 12

# ── the things that mean stop ──────────────────────────────────────────────
# Phrases that mean the site is asking whether you are a person. The answer is
# to stop and hand back to one, not to try again more slowly.
ABORT_SIGNALS = [
    "captcha", "recaptcha", "hcaptcha", "are you a robot", "verify you are human",
    "security check", "checkpoint/challenge", "unusual activity",
    "suspicious activity", "we've restricted", "temporarily restricted",
    "account has been restricted", "please verify your identity",
    "too many requests", "rate limit",
]

_ABORT_RE = re.compile("|".join(re.escape(s) for s in ABORT_SIGNALS), re.I)

# How patient to be, as a multiplier on every delay. Higher is slower: a
# "careful" run waits 1.6x as long as normal everywhere. Naming this `patience`
# rather than `speed` is deliberate — as `speed` the arithmetic reads
# backwards, and "careful" silently became the fastest setting.
PROFILES = {
    "careful": 1.6,
    "normal": 1.0,
    "fast": 0.65,      # still human, just someone in a hurry
}


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

def _scaled(lo, hi, patience=1.0):
    """Stretch a delay range by `patience`. Higher patience means longer."""
    return lo * patience, hi * patience


def pause(lo: float, hi: float, patience: float = 1.0, sleep=True) -> float:
    """A delay a person might take. Returns the seconds waited."""
    lo, hi = _scaled(lo, hi, patience)
    seconds = lo + random.random() * max(hi - lo, 0)
    if sleep:
        time.sleep(seconds)
    return seconds


def dwell_for_text(text: str, patience: float = 1.0) -> float:
    """How long someone would spend reading this before acting on it.

    Used before clicking Apply on a posting: a person who applies 200ms after
    the page paints has not read it, and that is visible in the timing whether
    or not anyone is looking for it.
    """
    words = len((text or "").split())
    if not words:
        return MIN_DWELL
    seconds = (words / SKIM_WPM) * 60.0 * patience
    # People do not read at a constant rate.
    seconds *= random.uniform(0.75, 1.35)
    return max(MIN_DWELL, min(seconds, MAX_DWELL))


def typing_delays(text: str, patience: float = 1.0):
    """Per-character delays for typing `text`, as a generator of (char, delay).

    The rhythm matters more than the average: bursts of characters, a beat
    after each word, a longer one after a sentence ends, and occasionally a
    pause where someone stopped to think.
    """
    wpm = random.uniform(*TYPING_WPM) / patience
    base = 60.0 / (wpm * CHARS_PER_WORD)
    for index, char in enumerate(text or ""):
        delay = base * random.uniform(0.55, 1.7)
        if char in ".!?" and index < len(text) - 1:
            delay += random.uniform(*PAUSE_AFTER_SENTENCE)
        elif char == " ":
            delay += random.uniform(*PAUSE_AFTER_WORD)
        elif random.random() < THINK_PAUSE_CHANCE:
            delay += random.uniform(*THINK_PAUSE)
        yield char, delay


def typing_seconds(text: str, patience: float = 1.0) -> float:
    """One sampled duration for typing `text` — random, like the real thing."""
    return sum(delay for _c, delay in typing_delays(text, patience))


def estimate_typing_seconds(text: str, patience: float = 1.0) -> float:
    """The expected duration, computed rather than sampled.

    `typing_seconds` rolls the dice, which is right when pacing a real fill and
    wrong when generating documentation: a protocol whose numbers changed on
    every render could not be checked against the copy in the repo.
    """
    text = text or ""
    if not text:
        return 0.0
    wpm = (TYPING_WPM[0] + TYPING_WPM[1]) / 2 / patience
    base = 60.0 / (wpm * CHARS_PER_WORD)
    mean_jitter = (0.55 + 1.7) / 2
    total = len(text) * base * mean_jitter

    sentence_ends = sum(1 for i, c in enumerate(text)
                        if c in ".!?" and i < len(text) - 1)
    total += sentence_ends * sum(PAUSE_AFTER_SENTENCE) / 2
    total += text.count(" ") * sum(PAUSE_AFTER_WORD) / 2
    # The occasional pause to think, averaged over the whole string.
    total += len(text) * THINK_PAUSE_CHANCE * sum(THINK_PAUSE) / 2
    return total


def type_like_human(field, text: str, patience: float = 1.0,
                    max_seconds: float = 180.0):
    """Type into a Selenium element the way a person would. Returns seconds spent.

    `send_keys(whole_string)` arrives as one event no matter how long the
    answer is. This sends it a character at a time with the rhythm above.
    `max_seconds` is a safety valve: a pathologically long answer should not
    hold the whole run hostage.
    """
    text = text or ""
    spent = 0.0
    for index, (char, delay) in enumerate(typing_delays(text, patience)):
        field.send_keys(char)
        if spent + delay > max_seconds:
            # Out of budget: send whatever is left in one go rather than stall.
            remaining = text[index + 1:]
            if remaining:
                field.send_keys(remaining)
            break
        time.sleep(delay)
        spent += delay
    return spent


# ---------------------------------------------------------------------------
# Session shape
# ---------------------------------------------------------------------------

def needs_break(applications_since_break: int, burst=None) -> bool:
    """Has this run done enough in a row that a person would stop?"""
    low, high = burst or BURST_APPLICATIONS
    return applications_since_break >= random.randint(low, high)


def break_seconds(patience: float = 1.0) -> float:
    lo, hi = BREAK_MINUTES
    return random.uniform(lo, hi) * 60.0 * patience


def looks_like_a_challenge(text: str) -> bool:
    """Is the page asking whether we are a person, or saying we are restricted?"""
    return bool(_ABORT_RE.search(text or ""))


def active_hours_warning(cfg: dict) -> str:
    """'' if the configured hours look like a person's, otherwise why not.

    `active_hours_start: 0` / `active_hours_end: 24` means the account applies
    for jobs at four in the morning, every morning. That is the loudest signal
    available, and no amount of per-click pacing hides it. Running the bot 24/7
    and *acting* 24/7 are separate choices: the supervisor can keep it alive
    while the loop idles outside waking hours.
    """
    sched = (cfg or {}).get("scheduling", {}) or {}
    start = sched.get("active_hours_start", 0)
    end = sched.get("active_hours_end", 24)
    span = end - start
    if span >= 24:
        return ("active_hours is 0-24, so the account applies at 3am as readily "
                "as at 3pm. No per-click pacing disguises that. Set something "
                "like active_hours_start: 8 / active_hours_end: 23 — autopilot "
                "keeps the process alive either way, it just idles overnight.")
    if span >= 20:
        return (f"active_hours spans {span} hours, which is a longer day than "
                "most people keep. 14-16 hours reads as normal.")
    return ""


# ---------------------------------------------------------------------------
# The protocol, rendered for whoever is driving the browser
# ---------------------------------------------------------------------------

def render_protocol(profile: str = "normal") -> str:
    """The pacing rules as text, to paste into Claude for Chrome.

    Generated rather than written by hand so the numbers cannot drift from the
    ones the bot uses; `tests/test_human_pacing.py` fails if the copy in
    tests/e2e/CLAUDE_IN_CHROME.md stops matching this output.
    """
    import textwrap

    patience = PROFILES.get(profile, 1.0)
    jobs_lo, jobs_hi = _scaled(*DELAY_BETWEEN_JOBS, patience)
    search_lo, search_hi = _scaled(*DELAY_BETWEEN_SEARCHES, patience)
    burst_lo, burst_hi = BURST_APPLICATIONS
    break_lo, break_hi = BREAK_MINUTES
    example = ("I'm drawn to this role because the team owns the risk models "
               "end to end, which is what I did at my last job.")
    example_seconds = estimate_typing_seconds(example, patience)
    read_600 = 600 / SKIM_WPM * 60 * patience

    def para(text):
        return textwrap.fill(" ".join(text.split()), width=78)

    def bullet(text):
        return textwrap.fill(" ".join(text.split()), width=78,
                             initial_indent="- ", subsequent_indent="  ")

    bullets = [
        f"""**Read before you act.** Spend time on a posting proportional to its
        length — roughly {SKIM_WPM} words a minute, so a 600-word description is
        about {read_600:.0f} seconds. Never click Apply on a page you have just
        opened. If you would not have finished reading it, you are early.""",

        f"""**Type, do not paste.** Enter free-text answers a character at a
        time, around {int(TYPING_WPM[0] / patience)}-{int(TYPING_WPM[1] / patience)}
        words a minute, with a beat between words and a longer one after each
        sentence. A sentence like "{example}" should take roughly
        {example_seconds:.0f} seconds, not an instant. Short fields — name,
        email, phone — can be quick; nobody composes those.""",

        f"""**Leave gaps between jobs.** {jobs_lo:.0f}-{jobs_hi:.0f} seconds
        between postings, {search_lo:.0f}-{search_hi:.0f} seconds between
        searches. Vary them: identical gaps are as distinctive as no gaps.""",

        """**Scroll to what you click.** Bring an element into view, let the
        page settle, then click it. Do not click something that was never on
        screen.""",

        """**One tab, one thing at a time.** Never open several postings in
        parallel or act on a background tab. Doing two things at once is the
        clearest possible signal that nobody is at the keyboard.""",

        f"""**Take breaks.** After {burst_lo}-{burst_hi} applications, stop for
        {break_lo}-{break_hi} minutes. Keep it under
        {MAX_APPLICATIONS_PER_HOUR} an hour. When my daily cap is reached, stop
        for the day — do not go looking for more.""",

        """**Keep human hours.** Work inside the hours a person would be awake
        and job hunting. Applying at 4am every night is not disguised by
        anything above it.""",
    ]

    return "\n".join([
        "**Pace yourself like a person — this is not optional**",
        "",
        para("""You are acting in my real browser, signed into my real account,
             applying to real employers. Speed is what gives automation away,
             and it is also what makes automation rude. Slower and fewer is the
             whole idea — do not try to be quick."""),
        "",
        "\n".join(bullet(b) for b in bullets),
        "",
        "**Stop immediately and tell me if you see any of these**",
        "",
        "\n".join("  - " + s for s in ABORT_SIGNALS[:9]),
        "",
        para("""Do not wait and retry, do not reload, do not try another route,
             and do not attempt to solve a CAPTCHA. The site is asking whether a
             person is here. Hand it back to me and stop — that is the line
             between automating my own job search and something I did not ask
             for."""),
        "",
        para("""**Never fake mistakes.** Do not invent typos or wrong answers to
             look human. This is a real application to a real employer, and a
             plausible-looking wrong answer is the worst outcome there is."""),
    ])
