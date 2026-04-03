#!/usr/bin/env python3
"""
LinkedIn Lightning Applier v2 — Main Orchestrator

Features: recruiter tracking, visa sponsorship detection, full job detail
persistence, CSV auto-export, custom time filters, skip reason logging.

Usage:
    python main.py                   # Run with config.yaml
    python main.py -c my_config.yaml # Custom config
"""

import sys
import signal
import time
import random
import logging
import argparse
import re
from datetime import datetime
from pathlib import Path

try:
    import yaml
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.by import By
except ImportError:
    print("Missing dependencies. Run: pip install selenium undetected-chromedriver pyyaml")
    sys.exit(1)

from state import State
from linkedin import (
    create_browser, login, verify_session, build_search_url, navigate_to_search,
    get_job_cards, extract_job_info, get_job_description, get_salary_info,
    extract_experience_requirement, extract_hiring_team, detect_visa_sponsorship,
    click_easy_apply, process_easy_apply, discard_application,
    human_sleep, safe_click, click_job_card,
)
from ai import AIAnswerer

# ═══════════════════════════════════════════════════════════════
shutdown_requested = False
driver = None
state = None

def _signal(sig, frame):
    global shutdown_requested
    logging.getLogger("lla").info("Shutdown requested. Finishing current action...")
    shutdown_requested = True

signal.signal(signal.SIGINT, _signal)
signal.signal(signal.SIGTERM, _signal)


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(cfg: dict):
    lc = cfg.get("logging", {})
    level = getattr(logging, lc.get("level", "INFO").upper(), logging.INFO)
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if lc.get("log_to_file", True):
        ld = Path(lc.get("log_dir", "logs"))
        ld.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(ld / f"lla_{datetime.now():%Y%m%d}.log", encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S", handlers=handlers)
    for lib in ["urllib3", "selenium", "undetected_chromedriver"]:
        logging.getLogger(lib).setLevel(logging.WARNING)


# ═══════════════════════════════════════════════════════════════
# FILTERING
# ═══════════════════════════════════════════════════════════════

def should_skip_job(job: dict, cfg: dict, st: State) -> str | None:
    if not job.get("job_id"): return "no job ID"
    if job.get("applied"): return "already applied (badge)"
    if st.is_applied(job["job_id"]): return "already applied (history)"

    filters = cfg.get("filters", {})
    cl = job["company"].lower()
    for bc in filters.get("blacklisted_companies", []):
        if bc.lower() in cl: return f"blacklisted: {bc}"
    for bw in filters.get("bad_title_words", []):
        if bw.lower() in job["title"].lower(): return f"bad title: {bw}"
    return None


def should_skip_description(desc: str, cfg: dict) -> str | None:
    if not desc: return None
    filters = cfg.get("filters", {})
    app = cfg.get("application", {})
    dl = desc.lower()

    for w in filters.get("bad_words", []):
        wl = w.lower()
        # Use word-boundary matching to avoid false positives
        # "clearance" should match "security clearance" but not "clearance sale" in unrelated context
        # "Driver" should match job title "Delivery Driver" but not "key driver of growth"
        if re.search(r'\b' + re.escape(wl) + r'\b', dl):
            return f"bad word: {w}"

    exp = app.get("years_of_experience", -1)
    buf = filters.get("experience_buffer", 2)
    if exp > -1:
        matches = re.findall(r'(\d+)\s*[+\-–]?\s*(?:to\s*\d+\s*)?year', dl)
        if matches:
            years = [int(y) for y in matches if int(y) <= 15]
            if years and max(years) > exp + buf:
                return f"needs {max(years)}yr (you: {exp}+{buf})"

    return None


# ═══════════════════════════════════════════════════════════════
# PROCESS ONE SEARCH PAGE
# ═══════════════════════════════════════════════════════════════

def process_page(drv, cfg: dict, st: State, sched: dict,
                 search_term: str = "", search_location: str = "",
                 ai: "AIAnswerer | None" = None,
                 cycle_seen_ids: set = None) -> dict:
    """
    Process all job cards on the current search results page.
    Single-pass: for each card, scroll it into view, extract info, click, process.
    NEVER navigates away from the search page.
    """
    result = {"applied": 0, "skipped": 0, "failed": 0}
    log = logging.getLogger("lla")
    if cycle_seen_ids is None:
        cycle_seen_ids = set()

    # Initial card discovery — just get the count and IDs
    cards = get_job_cards(drv)
    if not cards:
        return result

    # Collect just the job IDs from the card attributes (lightweight, no sub-element access)
    job_ids = []
    for card in cards:
        try:
            jid = card.get_attribute("data-occludable-job-id") or ""
            if jid:
                job_ids.append(jid)
        except Exception:
            continue

    num_cards = len(job_ids)
    max_per = min(num_cards, sched.get("max_applies_per_cycle", 15))
    filters = cfg.get("filters", {})
    visa_only = filters.get("visa_sponsorship_only", False)

    log.info(f"  {num_cards} job IDs collected, processing up to {max_per}")

    for i in range(max_per):
        if shutdown_requested:
            break
        if st.daily_applied_count() >= sched.get("max_applies_per_day", 40):
            log.warning("Daily limit reached.")
            break

        job_id = job_ids[i]

        # Skip if already seen this cycle
        if job_id in cycle_seen_ids:
            result["skipped"] += 1
            continue
        cycle_seen_ids.add(job_id)

        # Skip if already applied
        if st.is_applied(job_id):
            result["skipped"] += 1
            continue

        # SCROLL the card into view in the left pane and CLICK it
        if not click_job_card(drv, job_id):
            log.debug(f"  [{i}] Could not click card {job_id}")
            result["failed"] += 1
            continue

        human_sleep(1.5, 2.5)

        # EXTRACT job info from the RIGHT PANE (not from the card)
        # Title and company from the details header
        title = ""
        company = ""
        location = ""
        try:
            for sel in [".job-details-jobs-unified-top-card__job-title",
                        "h1.t-24", "h1.t-20", "h2.t-24",
                        ".jobs-unified-top-card__job-title", "h1"]:
                els = drv.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    title = els[0].text.strip().split("\n")[0]
                    break
            for sel in [".job-details-jobs-unified-top-card__company-name",
                        ".jobs-unified-top-card__company-name",
                        "[class*='company-name']", ".artdeco-entity-lockup__subtitle"]:
                els = drv.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    company = els[0].text.strip()
                    break
            for sel in [".job-details-jobs-unified-top-card__primary-description-container",
                        ".jobs-unified-top-card__bullet",
                        "[class*='primary-description']"]:
                els = drv.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].text.strip():
                    loc_text = els[0].text.strip()
                    # Extract location (usually first part before ·)
                    location = loc_text.split("·")[0].strip() if "·" in loc_text else loc_text
                    break
        except Exception:
            pass

        if not title:
            title = f"Job {job_id}"

        log.info(f"▶  [{i}] {title} @ {company} (ID: {job_id})")

        # Check for "already applied" badge in the right pane
        try:
            applied_badges = drv.find_elements(By.XPATH,
                "//*[contains(text(), 'Applied') and contains(@class, 'artdeco-inline-feedback')]")
            if not applied_badges:
                applied_badges = drv.find_elements(By.XPATH, "//*[contains(text(), 'Applied')]")
            for badge in applied_badges:
                if "applied" in badge.text.lower() and badge.is_displayed():
                    log.info(f"   ⏭️  Already applied (badge)")
                    st.mark_skipped(job_id, title, company, location, "already applied (badge)",
                                   search_term=search_term, search_location=search_location)
                    result["skipped"] += 1
                    break
            else:
                applied_badges = None  # Signal to continue processing
            if applied_badges:
                continue
        except Exception:
            pass

        # Filter: blacklisted companies
        skip_reason = None
        cl = company.lower()
        for bc in filters.get("blacklisted_companies", []):
            if bc.lower() in cl:
                skip_reason = f"blacklisted: {bc}"
                break
        if not skip_reason:
            for bw in filters.get("bad_title_words", []):
                if bw.lower() in title.lower():
                    skip_reason = f"bad title: {bw}"
                    break

        if skip_reason:
            log.info(f"   ⏭️  {skip_reason}")
            st.mark_skipped(job_id, title, company, location, skip_reason,
                           search_term=search_term, search_location=search_location)
            result["skipped"] += 1
            continue

        # EXTRACT FULL DETAILS from the right pane
        desc = get_job_description(drv)
        salary = get_salary_info(drv)
        exp_req = extract_experience_requirement(desc)
        hiring_team = extract_hiring_team(drv)
        visa_status = detect_visa_sponsorship(desc, cfg)

        if salary:
            log.info(f"   💰 Salary: {salary}")
        if visa_status != "unknown":
            log.info(f"   🛂 Visa: {visa_status}")

        # Save recruiters
        for person in hiring_team:
            st.save_recruiter(
                name=person["name"], title=person.get("title",""),
                company=company, job_id=job_id,
                job_title=title, profile_url=person.get("profile_url",""),
            )

        if visa_status == "yes":
            evidence = ""
            for kw in filters.get("visa_positive_keywords", []):
                if kw.lower() in desc.lower():
                    evidence = kw
                    break
            st.save_visa_sponsor(company, evidence, job_id)

        # Filter: description
        desc_skip = should_skip_description(desc, cfg)
        if desc_skip:
            log.info(f"   ⏭️  {desc_skip}")
            st.mark_skipped(job_id, title, company, location, desc_skip, visa_status,
                           hiring_team[0]["name"] if hiring_team else "",
                           search_term, search_location)
            result["skipped"] += 1
            continue

        # Filter: visa only
        if visa_only and visa_status != "yes":
            log.info(f"   ⏭️  no visa sponsorship ({visa_status})")
            st.mark_skipped(job_id, title, company, location,
                           f"no visa sponsorship ({visa_status})", visa_status, "",
                           search_term, search_location)
            result["skipped"] += 1
            continue

        # EASY APPLY
        try:
            if not click_easy_apply(drv):
                log.info("   ⏭️  No Easy Apply button")
                st.mark_skipped(job_id, title, company, location,
                               "no Easy Apply button", visa_status, "",
                               search_term, search_location)
                result["skipped"] += 1
                continue

            success = process_easy_apply(drv, cfg, ai=ai, job_context={
                "title": title, "company": company, "description": desc[:500]
            })
            if success:
                recruiter_name = hiring_team[0]["name"] if hiring_team else ""
                recruiter_title = hiring_team[0].get("title","") if hiring_team else ""
                hiring_mgr = hiring_team[1]["name"] if len(hiring_team) > 1 else ""

                st.mark_applied(
                    job_id=job_id, title=title, company=company,
                    location=location, work_style="",
                    job_url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                    description=desc, salary_info=salary, experience_req=exp_req,
                    recruiter_name=recruiter_name, recruiter_title=recruiter_title,
                    hiring_manager=hiring_mgr, visa_sponsorship=visa_status,
                    posted_time="", search_term=search_term,
                    search_location=search_location,
                )
                result["applied"] += 1
                log.info(f"   ✅  APPLIED!")
                human_sleep(sched.get("delay_after_apply",4), sched.get("delay_after_apply",4)+3)
            else:
                st.mark_failed(job_id, title, company, "modal failed")
                result["failed"] += 1
                log.info(f"   ❌  Modal failed")

        except Exception as e:
            log.warning(f"   💥  Error: {e}")
            st.mark_failed(job_id, title, company, str(e))
            result["failed"] += 1
            discard_application(drv)

        human_sleep(sched.get("min_delay_between_jobs",3), sched.get("max_delay_between_jobs",8))

    return result


# ═══════════════════════════════════════════════════════════════
# FULL CYCLE
# ═══════════════════════════════════════════════════════════════

def run_cycle(drv, cfg: dict, st: State, ai=None):
    log = logging.getLogger("lla")
    sc = cfg.get("search", {})
    sched = cfg.get("scheduling", {})

    terms = list(sc.get("search_terms", []))
    locs = list(sc.get("search_locations", []))
    if not terms or not locs:
        log.error("No search terms or locations!")
        return

    if sc.get("randomize_order", True):
        random.shuffle(terms)

    # Track all job IDs seen in this cycle to avoid re-processing across searches
    cycle_seen_ids = set()
    tot_a, tot_s, tot_f = 0, 0, 0

    # Adaptive time filter chain — widens if no results found
    TIME_CHAIN = ["Past hour", "Past 2 hours", "Past 6 hours", "Past 12 hours", "Past 24 hours", "Past week"]
    base_filter = sc.get("date_posted", "Past hour")

    # Find starting position in the chain
    base_filter_lower = str(base_filter).strip().lower()
    chain_start = 0
    for i, f in enumerate(TIME_CHAIN):
        if f.lower() == base_filter_lower:
            chain_start = i
            break

    # Verify session is still valid before starting
    if not verify_session(drv):
        log.warning("Session expired before cycle. Re-logging in...")
        if not login(drv, cfg):
            log.error("Re-login failed. Skipping cycle.")
            return

    for loc in locs:
        if shutdown_requested: break
        for term in terms:
            if shutdown_requested: break
            if st.daily_applied_count() >= sched.get("max_applies_per_day", 40):
                break

            sl = loc.split(",")[0].strip()
            found_results = False

            # Try progressively wider time filters
            for filter_idx in range(chain_start, len(TIME_CHAIN)):
                if shutdown_requested: break
                time_filter = TIME_CHAIN[filter_idx]
                is_widened = filter_idx > chain_start

                if is_widened:
                    log.info(f"   ↳ Widening to \"{time_filter}\"...")

                if not is_widened:
                    log.info(f"🔍  \"{term}\" → \"{sl}\" ({time_filter})")

                try:
                    # Build URL with current time filter (override config)
                    cfg_copy = dict(cfg)
                    cfg_copy["search"] = dict(sc)
                    cfg_copy["search"]["date_posted"] = time_filter

                    url = build_search_url(cfg_copy, term, loc)
                    navigate_to_search(drv, url)

                    # Check how many cards we got
                    from linkedin import get_job_cards as _peek_cards
                    cards = _peek_cards(drv)
                    new_cards = [c for c in cards
                                 if c.get_attribute("data-occludable-job-id") not in cycle_seen_ids]

                    if len(cards) == 0 and filter_idx < len(TIME_CHAIN) - 1:
                        log.info(f"   0 results for \"{time_filter}\"")
                        human_sleep(1, 2)
                        continue  # Try wider filter

                    # Process the page
                    r = process_page(drv, cfg, st, sched, term, loc, ai=ai, cycle_seen_ids=cycle_seen_ids)
                    tot_a += r["applied"]; tot_s += r["skipped"]; tot_f += r["failed"]
                    total_on_page = r["applied"] + r["skipped"] + r["failed"]
                    log.info(f"   Page: +{r['applied']}A +{r['skipped']}S +{r['failed']}F | Unique seen: {len(cycle_seen_ids)}")
                    found_results = True
                    break  # Got results, move to next term

                except Exception as e:
                    log.error(f"   Search error: {e}")
                    tot_f += 1
                    break  # Don't retry on errors

            human_sleep(sched.get("min_delay_between_searches",5), sched.get("max_delay_between_searches",15))

    st.inc_cycles()

    # Auto-export CSV
    if cfg.get("export", {}).get("auto_export_csv", True):
        try:
            export_dir = cfg.get("export", {}).get("export_dir", "data")
            st.export_csv(export_dir, cfg)
            log.info(f"📁  CSVs exported to {export_dir}/")
        except Exception as e:
            log.warning(f"CSV export failed: {e}")

    log.info(f"✅  Cycle: +{tot_a}A +{tot_s}S +{tot_f}F")
    log.info(f"📊  {st.session_summary()}")


# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def is_active_hours(cfg: dict) -> bool:
    sched = cfg.get("scheduling", {})
    return sched.get("active_hours_start", 0) <= datetime.now().hour < sched.get("active_hours_end", 24)


def run_forever(config_path: str):
    global driver, state, shutdown_requested
    log = logging.getLogger("lla")

    cfg = load_config(config_path)
    setup_logging(cfg)

    log.info("=" * 70)
    log.info("⚡  LinkedIn Lightning Applier v2")
    log.info("=" * 70)
    log.info(f"Time filter: {cfg.get('search',{}).get('date_posted','Past 24 hours')}")
    log.info(f"Locations: {len(cfg.get('search',{}).get('search_locations',[]))} configured")
    log.info(f"Terms: {cfg.get('search',{}).get('search_terms',[])}")
    log.info(f"Visa filter: {'ON' if cfg.get('filters',{}).get('visa_sponsorship_only') else 'OFF'}")

    state = State()
    ai_answerer = AIAnswerer(cfg, db_conn=state.conn)
    errors = 0

    log.info("Launching browser...")
    try:
        driver = create_browser(cfg)
    except Exception as e:
        log.error(f"Browser failed: {e}")
        sys.exit(1)

    log.info("Logging in...")
    if not login(driver, cfg):
        log.error("Login failed.")
        driver.quit()
        sys.exit(1)

    log.info("🚀  Running. Ctrl+C to stop.")

    sched = cfg.get("scheduling", {})
    interval = max(sched.get("scan_interval_minutes", 15), 1) * 60

    while not shutdown_requested:
        # Hot-reload config
        try:
            cfg = load_config(config_path)
            ai_answerer = AIAnswerer(cfg, db_conn=state.conn)
        except Exception as e: log.warning(f"Config reload failed: {e}")

        sched = cfg.get("scheduling", {})
        interval = max(sched.get("scan_interval_minutes", 15), 1) * 60

        if not is_active_hours(cfg):
            log.info(f"Outside active hours. Sleeping 10 min...")
            time.sleep(600)
            continue

        if state.daily_applied_count() >= sched.get("max_applies_per_day", 40):
            log.info(f"Daily limit ({state.daily_applied_count()}). Sleeping 30 min...")
            time.sleep(1800)
            continue

        # Browser alive?
        try:
            _ = driver.current_url
        except Exception:
            log.warning("Browser crashed! Restarting...")
            try: driver.quit()
            except: pass
            driver = create_browser(cfg)
            if not login(driver, cfg):
                time.sleep(60)
                continue

        # Run cycle
        try:
            log.info(f"── Cycle start (every {interval//60}min) ──")
            run_cycle(driver, cfg, state, ai=ai_answerer)
            errors = 0
        except Exception as e:
            errors += 1
            log.error(f"Cycle error ({errors}/5): {e}")
            if errors >= 5:
                log.error("Too many errors. Restarting browser...")
                try: driver.quit()
                except: pass
                try:
                    driver = create_browser(cfg)
                    login(driver, cfg)
                    errors = 0
                except Exception as e2:
                    log.error(f"Restart failed: {e2}")
                    time.sleep(120)

        if shutdown_requested:
            break

        # Sleep with jitter
        jitter = random.randint(-60, 60)
        wait = max(interval + jitter, 60)
        log.info(f"💤  Next cycle in {wait//60}m {wait%60}s")
        for _ in range(wait):
            if shutdown_requested: break
            time.sleep(1)

    # Shutdown
    log.info("Shutting down...")
    log.info(f"📊  Final: {state.session_summary()}")

    # Final export
    if cfg.get("export", {}).get("auto_export_csv", True):
        try:
            state.export_csv(cfg.get("export", {}).get("export_dir", "data"), cfg)
            log.info("📁  Final CSVs exported.")
        except Exception:
            pass

    if driver:
        try: driver.quit()
        except: pass
    if state:
        state.close()
    log.info("Goodbye! 👋")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn Lightning Applier v2")
    parser.add_argument("-c", "--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()
    run_forever(args.config)
