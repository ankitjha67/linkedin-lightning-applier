"""Let a browser agent drive the engine — hands in the browser, brain here.

    lla chrome-session              # the runbook to paste into Claude for Chrome
    lla chrome-session --status     # where the current session stands

Claude for Chrome can see and act in your real browser, signed into your real
account. What it cannot do is import this project: it has no match scoring, no
work-authorisation logic, no memory of how you answered a question last time,
no deduplication, and no idea how many applications you have already sent
today. The Selenium bot has all of that and none of the browser.

This is the handoff. The agent is the hands; the engine stays the brain.

    session_open()   what to do, how fast, and when to stop
    consider(job)    should this one be applied to? (dedup, score, cap, hours)
    answers(job, qs) what to type in each field, and where each value came from
    report(job)      what happened — recorded, and the next step returned

Three things are deliberately enforced here rather than in the prompt:

**The daily cap.** `consider` returns "stop" once `max_applies_per_day` is
reached, counted from `daily_stats` in SQLite. A prompt can be forgotten,
truncated, or summarised away halfway through a long session; a row in the
database cannot.

**One application per job.** A claim is taken between "the engine said apply"
and "the agent reported back", so a browser agent and the Selenium bot running
at the same time cannot both take the same posting. Two applications to one job
looks worse to an employer than none.

**No invented answers.** `answers()` returns a `source` for every value —
work_auth, remembered, config, cv — and marks anything it cannot source as
`unanswered` instead of guessing. An agent asked to fill a form will otherwise
produce something plausible, and a plausible wrong answer on a real
application is the worst outcome available.

What the engine cannot verify is what the agent tells it. `report(submitted=
True)` is taken at its word, because nothing here can see the browser. That is
the one place this design trusts its caller, and it is why the runbook asks for
field-level reporting rather than a summary.
"""

import logging
from datetime import datetime, timedelta

log = logging.getLogger("lla.chrome_bridge")

DRIVER = "claude-in-chrome"
# A claim this old belonged to a session that went away — a closed tab, a
# crashed agent, a person who wandered off. Release it rather than letting one
# abandoned claim keep a job out of circulation for ever.
CLAIM_STALE_MINUTES = 90

TIME_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

def _now():
    return datetime.now().strftime(TIME_FMT)


def release_stale_claims(state, minutes: int = CLAIM_STALE_MINUTES) -> int:
    cutoff = (datetime.now() - timedelta(minutes=minutes)).strftime(TIME_FMT)
    cur = state.conn.execute(
        "UPDATE agent_claims SET status='abandoned' "
        "WHERE status='claimed' AND claimed_at < ?", (cutoff,))
    state.conn.commit()
    return cur.rowcount or 0


def claim(state, job_id: str, url: str = "", title: str = "", company: str = "",
          driver: str = DRIVER) -> bool:
    """Take this job, unless somebody already has it. True if we got it."""
    release_stale_claims(state)
    row = state.conn.execute(
        "SELECT status FROM agent_claims WHERE job_id=?", (job_id,)).fetchone()
    if row and row["status"] in ("claimed", "applied"):
        return False
    state.conn.execute(
        "INSERT INTO agent_claims (job_id, job_url, title, company, claimed_at, "
        "status, driver) VALUES (?,?,?,?,?, 'claimed', ?) "
        "ON CONFLICT(job_id) DO UPDATE SET status='claimed', claimed_at=?, "
        "driver=?, job_url=?, title=?, company=?",
        (job_id, url, title, company, _now(), driver,
         _now(), driver, url, title, company))
    state.conn.commit()
    return True


def close_claim(state, job_id: str, status: str, notes: str = ""):
    state.conn.execute(
        "UPDATE agent_claims SET status=?, notes=? WHERE job_id=?",
        (status, notes, job_id))
    state.conn.commit()


def open_claims(state) -> list:
    release_stale_claims(state)
    return [dict(r) for r in state.conn.execute(
        "SELECT * FROM agent_claims WHERE status='claimed' ORDER BY claimed_at")]


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def job_id_for(url: str, title: str = "", company: str = "") -> str:
    """A stable id for a posting the agent found in the browser.

    LinkedIn job URLs carry the numeric id, which is the best key available.
    Everything else falls back to a hash of company+title, so the same posting
    reached by two different URLs still deduplicates.
    """
    import hashlib
    import re
    m = re.search(r"(?:currentJobId=|/jobs/view/)(\d{6,})", url or "")
    if m:
        return f"li-{m.group(1)}"
    for pattern in (r"/job/(\d{5,})", r"[?&]gh_jid=(\d+)", r"/jobs/(\d{5,})"):
        m = re.search(pattern, url or "")
        if m:
            return f"ats-{m.group(1)}"
    seed = f"{(company or '').strip().lower()}|{(title or '').strip().lower()}"
    if seed == "|":
        seed = (url or "").strip().lower()
    return "h-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Should we apply to this?
# ---------------------------------------------------------------------------

def _sched(cfg):
    return (cfg or {}).get("scheduling", {}) or {}


def within_active_hours(cfg) -> bool:
    s = _sched(cfg)
    hour = datetime.now().hour
    return s.get("active_hours_start", 0) <= hour < s.get("active_hours_end", 24)


def budget(state, cfg) -> dict:
    s = _sched(cfg)
    cap = s.get("max_applies_per_day", 40)
    done = state.daily_applied_count()
    return {"applied_today": done, "cap": cap,
            "remaining": max(0, cap - done), "cap_reached": done >= cap}


def consider(state, cfg, url: str, title: str = "", company: str = "",
             description: str = "", location: str = "", ai=None) -> dict:
    """Should the agent apply to this posting? Claims it if so.

    verdict is "apply", "skip" or "stop" — "stop" meaning end the session,
    not merely skip this one.
    """
    job_id = job_id_for(url, title, company)
    base = {"job_id": job_id, "url": url, "title": title, "company": company}

    if not within_active_hours(cfg):
        s = _sched(cfg)
        return {**base, "verdict": "stop", "reason": (
            f"outside active hours ({s.get('active_hours_start', 0)}:00-"
            f"{s.get('active_hours_end', 24)}:00) — stop for now")}

    money = budget(state, cfg)
    if money["cap_reached"]:
        return {**base, "verdict": "stop", "reason": (
            f"daily cap reached ({money['applied_today']} of {money['cap']}) "
            "— stop for the day"), **money}

    if state.is_applied(job_id):
        return {**base, "verdict": "skip",
                "reason": "already applied to this job"}
    if state.is_skipped(job_id):
        return {**base, "verdict": "skip",
                "reason": "already assessed and skipped"}

    existing = state.conn.execute(
        "SELECT status, driver FROM agent_claims WHERE job_id=?",
        (job_id,)).fetchone()
    if existing and existing["status"] in ("claimed", "applied"):
        release_stale_claims(state)
        again = state.conn.execute(
            "SELECT status FROM agent_claims WHERE job_id=?", (job_id,)).fetchone()
        if again and again["status"] in ("claimed", "applied"):
            return {**base, "verdict": "skip", "reason": (
                f"already claimed by {existing['driver'] or 'another driver'} "
                "— not applying twice")}

    # Score it, when there is a description and a model to score with.
    score, reasons = None, ""
    threshold = (cfg.get("matching", {}) or {}).get("min_match_score")
    if threshold is None:
        threshold = (cfg.get("ai", {}) or {}).get("min_match_score", 0)
    if description and ai is not None:
        try:
            from match_scorer import MatchScorer
            result = MatchScorer(ai, cfg).score_job(
                title, company, description, location) or {}
            score = result.get("score")
            reasons = result.get("explanation", "")
            gaps = result.get("missing_skills") or []
            if gaps:
                reasons = (reasons + " Gaps: " + ", ".join(map(str, gaps))).strip()
        except Exception as exc:
            log.debug("scoring unavailable: %s", exc)

    if score is not None:
        try:
            state.save_match_score(job_id, title=title, company=company,
                                   score=int(score))
        except Exception as exc:
            log.debug("could not save match score: %s", exc)
        if threshold and score < threshold:
            state.mark_skipped(job_id=job_id, title=title, company=company,
                               location=location,
                               reason=f"match {score} below threshold {threshold}",
                               match_score=int(score))
            return {**base, "verdict": "skip", "score": score,
                    "reason": f"match score {score} is below your "
                              f"threshold of {threshold}", "detail": reasons}

    if not claim(state, job_id, url, title, company):
        return {**base, "verdict": "skip",
                "reason": "another driver claimed it first"}

    out = {**base, "verdict": "apply", "score": score, "detail": reasons,
           "reason": "not applied before, within cap and hours"}
    out.update(money)
    return out


# ---------------------------------------------------------------------------
# What to type
# ---------------------------------------------------------------------------

def _config_answer(cfg, label: str) -> str:
    """An answer the user wrote in question_answers, matched loosely."""
    qa = (cfg or {}).get("question_answers", {}) or {}
    low = (label or "").lower()
    for key, value in qa.items():
        k = str(key).lower()
        if k and (k in low or low in k):
            return str(value)
    return ""


def answers(state, cfg, questions, job_location: str = "",
            job_id: str = "") -> dict:
    """What to put in each field, with the provenance of every value.

    `questions` is [{label, type?, options?}]. Every item comes back with a
    `source`: work_auth, remembered, config, or unanswered. Nothing is
    invented — an unanswered field is reported as such so a person decides,
    which is the whole point of doing this here rather than letting an agent
    improvise into a real application.
    """
    out = []
    auth = rag = None
    try:
        from work_auth import WorkAuthorization
        auth = WorkAuthorization(cfg)
    except Exception as exc:
        log.debug("work_auth unavailable: %s", exc)
    try:
        from answer_rag import AnswerRAG
        rag = AnswerRAG(state.conn, cfg)
    except Exception as exc:
        log.debug("answer memory unavailable: %s", exc)

    prose_guard = None
    try:
        from ats_handlers.base import is_prose_label
        prose_guard = is_prose_label
    except Exception:
        pass

    for item in questions or []:
        label = (item.get("label") or "").strip() if isinstance(item, dict) else str(item)
        options = (item.get("options") if isinstance(item, dict) else None) or []
        entry = {"label": label, "value": "", "source": "unanswered",
                 "confidence": "none", "note": ""}

        if not label:
            entry["note"] = "no label — cannot answer a field we cannot identify"
            out.append(entry)
            continue

        # A consent paragraph is not a question. Filling it with a notice
        # period or a salary is a bug this project has already had once.
        if prose_guard and prose_guard(label):
            entry["note"] = ("reads as prose or a consent notice, not a "
                             "question — leave it alone")
            out.append(entry)
            continue

        if auth is not None and auth.recognizes(label):
            # Configured but unresolvable means it would answer "No" to every
            # country, which is worse than not answering: it tells an employer
            # you cannot work for them. Send it back to the person instead.
            if getattr(auth, "enabled", False) and not getattr(auth, "usable", True):
                bad = ", ".join(getattr(auth, "unresolved", [])[:3]) or "the values given"
                out.append({**entry, "source": "unanswered", "confidence": "none",
                            "note": ("work_authorization is set but no country "
                                     f"was recognised ({bad}) — answering from it "
                                     "would say you cannot work anywhere. Fix "
                                     "config.yaml, or tell me the answer.")})
                continue
            value = auth.answer(label, job_location=job_location, options=options)
            if value:
                out.append({**entry, "value": str(value), "source": "work_auth",
                            "confidence": "high",
                            "note": "derived from your citizenship and visas "
                                    "against the job's country"})
                continue

        if rag is not None:
            try:
                hit = rag.lookup(label, options or None)
            except Exception:
                hit = None
            if hit and hit.get("answer"):
                sim = hit.get("similarity", 0)
                out.append({**entry, "value": str(hit["answer"]),
                            "source": "remembered",
                            "confidence": "high" if sim >= 0.9 else "medium",
                            "note": f"you answered \"{hit.get('matched_question','')}\""
                                    f" this way before (similarity {sim:.2f})"})
                continue

        configured = _config_answer(cfg, label)
        if configured:
            out.append({**entry, "value": configured, "source": "config",
                        "confidence": "high",
                        "note": "from question_answers in config.yaml"})
            continue

        entry["note"] = ("nothing on record answers this — ask me rather than "
                         "guessing")
        out.append(entry)

    answered = [a for a in out if a["source"] != "unanswered"]
    return {
        "job_id": job_id,
        "answers": out,
        "answered": len(answered),
        "unanswered": [a["label"] for a in out if a["source"] == "unanswered"],
        "reminder": ("Type these values; do not improvise. Anything listed in "
                     "`unanswered` must come back to me — a plausible wrong "
                     "answer on a real application is the worst outcome."),
    }


def remember(state, cfg, pairs) -> int:
    """Store answers a person supplied, so the next form reuses them."""
    try:
        from answer_rag import AnswerRAG
        rag = AnswerRAG(state.conn, cfg)
    except Exception as exc:
        log.debug("answer memory unavailable: %s", exc)
        return 0
    saved = 0
    for item in pairs or []:
        label = (item.get("label") or "").strip()
        value = item.get("value")
        if not label or value in (None, ""):
            continue
        try:
            rag.save(label, str(value), item.get("options") or None)
            saved += 1
        except Exception as exc:
            log.debug("could not remember %r: %s", label, exc)
    return saved


# ---------------------------------------------------------------------------
# What happened
# ---------------------------------------------------------------------------

def report(state, cfg, job_id: str, submitted: bool, title: str = "",
           company: str = "", url: str = "", location: str = "",
           fields_filled: int = 0, notes: str = "", failed_reason: str = "",
           match_score: int = 0) -> dict:
    """Record the outcome of one application and say what to do next."""
    claimed = state.conn.execute(
        "SELECT * FROM agent_claims WHERE job_id=?", (job_id,)).fetchone()
    claimed = dict(claimed) if claimed else {}
    title = title or claimed.get("title", "")
    company = company or claimed.get("company", "")
    url = url or claimed.get("job_url", "")

    if submitted:
        state.mark_applied(job_id, title=title, company=company,
                           location=location, job_url=url,
                           match_score=int(match_score or 0))
        close_claim(state, job_id, "applied",
                    notes or f"{fields_filled} field(s) filled")
        recorded = "applied"
    else:
        reason = failed_reason or notes or "not submitted"
        state.mark_failed(job_id, title=title, company=company, reason=reason)
        close_claim(state, job_id, "failed", reason)
        recorded = "failed"

    money = budget(state, cfg)
    pacing = next_step(state, cfg)
    return {"job_id": job_id, "recorded": recorded, **money, **pacing}


def next_step(state, cfg) -> dict:
    """How long to wait, and whether to carry on at all."""
    money = budget(state, cfg)
    if not within_active_hours(cfg):
        return {"next_action": "stop",
                "why": "outside the hours you configured as active",
                "wait_seconds": 0}
    if money["cap_reached"]:
        return {"next_action": "stop",
                "why": f"daily cap of {money['cap']} reached",
                "wait_seconds": 0}

    try:
        import human_pacing as hp
        burst = hp.BURST_APPLICATIONS
        if money["applied_today"] and money["applied_today"] % burst[0] == 0:
            return {"next_action": "break",
                    "why": f"{money['applied_today']} in a row — a person "
                           "would stop for a bit",
                    "wait_seconds": round(hp.break_seconds(), 1)}
        lo, hi = hp.DELAY_BETWEEN_JOBS
        return {"next_action": "continue",
                "why": "within cap and hours",
                "wait_seconds": round(hp.pause(lo, hi, sleep=False), 1)}
    except Exception as exc:
        log.debug("pacing unavailable: %s", exc)
        return {"next_action": "continue", "why": "", "wait_seconds": 5}


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

def session_status(state, cfg) -> dict:
    money = budget(state, cfg)
    claims = open_claims(state)
    return {
        **money,
        "within_active_hours": within_active_hours(cfg),
        "open_claims": [{"job_id": c["job_id"], "company": c["company"],
                         "title": c["title"], "claimed_at": c["claimed_at"]}
                        for c in claims],
        **next_step(state, cfg),
    }


def session_open(state, cfg, search_terms=None) -> dict:
    """Everything a browser agent needs to run a session end to end."""
    money = budget(state, cfg)
    search = (cfg or {}).get("search", {}) or {}
    terms = search_terms or search.get("search_terms") or []
    locations = search.get("search_locations") or []
    try:
        import human_pacing as hp
        protocol = hp.render_protocol()
    except Exception:
        protocol = ""
    return {
        **money,
        "within_active_hours": within_active_hours(cfg),
        "search_terms": list(terms),
        "search_locations": list(locations),
        "pacing_protocol": protocol,
        "open_claims": len(open_claims(state)),
    }


def render_runbook(opened: dict) -> str:
    """The end-to-end instructions, with this session's live numbers in them."""
    import textwrap

    def para(text):
        return textwrap.fill(" ".join(text.split()), width=78)

    terms = ", ".join(opened.get("search_terms") or []) or "(none configured)"
    places = ", ".join(opened.get("search_locations") or []) or "(none configured)"
    remaining = opened.get("remaining", 0)
    cap = opened.get("cap", 0)
    done = opened.get("applied_today", 0)

    head = [
        "# Run a job-application session in this browser",
        "",
        para("""You are driving my real browser, signed into my real LinkedIn.
             I have a job-application engine running alongside you, exposed as
             MCP tools. You are the hands; it is the brain. Do not decide
             anything it can decide for you, and do not invent anything it can
             tell you."""),
        "",
        f"**Today so far:** {done} of {cap} applications. {remaining} left.",
        f"**Searching:** {terms}" + (f" in {places}" if places else ""),
        "",
    ]

    if not opened.get("within_active_hours", True):
        head += [para("""**It is outside the hours configured as active, so do
                      not apply to anything now.** Tell me, and stop."""), ""]
    if remaining <= 0:
        head += [para("""**The daily cap is already reached, so do not apply to
                      anything.** Tell me, and stop."""), ""]
    if opened.get("open_claims"):
        head += [para(f"""There are {opened['open_claims']} job(s) claimed by an
                     earlier session that never reported back. Ask me about
                     those before starting new ones."""), ""]

    loop = [
        "## The loop, for each posting",
        "",
        para("""1. **Open one posting** and read it. Copy out the title,
             company, location and the full description text."""),
        "",
        para("""2. **Ask the engine whether to apply**: call `consider_job`
             with the URL, title, company, location and description. It
             answers `apply`, `skip` or `stop`, and it is the authority — it
             knows what I have already applied to, how the posting scores
             against my CV, how many applications I have left today, and
             whether we are inside my active hours. If it says `skip`, move
             on and do not argue. If it says `stop`, stop the session and tell
             me why."""),
        "",
        para("""3. **Start the application** and list every field on the form:
             the exact label, the type, and the options for anything that is a
             choice."""),
        "",
        para("""4. **Ask the engine what to type**: call `answers_for_job` with
             that list. Every answer comes back with a `source` — work_auth,
             remembered, config — and a note saying where it came from. Type
             those values exactly. Anything in `unanswered` you must bring back
             to me: do not improvise, and do not fill a field with something
             that merely looks right. A wrong answer that looks plausible is
             the worst outcome available here. Fields the engine says read as
             prose or consent notices should be left alone."""),
        "",
        para("""5. **Attach the resume** if the form takes one, and confirm the
             page shows the filename."""),
        "",
        para("""6. **Stop before the final submit.** Show me the complete
             field-by-field list — label, value, and where it came from — and
             wait. I click Submit, or I tell you to. Never click it
             yourself."""),
        "",
        para("""7. **Report back**: once I confirm, call `report_application`
             with whether it was submitted, how many fields you filled, and
             anything that went wrong. It records the application, closes the
             claim and returns how long to wait and whether to carry on. Wait
             that long before the next posting — the figure comes from the
             pacing rules below, and the engine is counting."""),
        "",
        para("""8. **If I supplied an answer** the engine did not have, call
             `remember_answers` with it so the next form fills itself."""),
        "",
    ]

    stop = [
        "## Stop the session immediately if",
        "",
        "  - the engine returns `stop` for any reason;",
        "  - a CAPTCHA, checkpoint, or \"unusual activity\" notice appears —"
        " do not retry, reload, or try to solve it;",
        "  - a posting asks for something I have not given you an answer for"
        " and I am not responding;",
        "  - anything about the page makes you unsure whether an application"
        " was already sent.",
        "",
        para("""In every one of those cases: say what happened, say which job
             it was, and stop. Do not work around it."""),
        "",
    ]

    pacing = opened.get("pacing_protocol") or ""
    if pacing:
        pacing = "## Pacing\n\n" + pacing + "\n"

    return "\n".join(head + loop + stop + [pacing]).rstrip() + "\n"
