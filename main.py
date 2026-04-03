#!/usr/bin/env python3
"""
LinkedIn Lightning Applier v2 — Main Orchestrator

Features: recruiter tracking, visa sponsorship detection, full job detail
persistence, CSV auto-export, custom time filters, skip reason logging,
AI match scoring, resume tailoring, recruiter messaging, Google Jobs scraping,
activity simulation, external ATS apply, alerts, dashboard, salary intelligence,
interview prep, success tracking, smart scheduling.

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
import os
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
    human_sleep, safe_click, click_job_card, get_external_apply_url,
)
from ai import AIAnswerer

# Graceful imports for new modules (all optional)
try:
    from match_scorer import MatchScorer
except ImportError:
    MatchScorer = None

try:
    from resume_tailor import ResumeTailor
except ImportError:
    ResumeTailor = None

try:
    from google_jobs_scraper import GoogleJobsScraper
except ImportError:
    GoogleJobsScraper = None

try:
    from activity_sim import simulate_activity
except ImportError:
    simulate_activity = None

try:
    from external_apply import ExternalApplier
except ImportError:
    ExternalApplier = None

try:
    from recruiter_messenger import RecruiterMessenger
except ImportError:
    RecruiterMessenger = None

try:
    from alerts import AlertManager
except ImportError:
    AlertManager = None

try:
    from salary_intel import SalaryIntel
except ImportError:
    SalaryIntel = None

try:
    from interview_prep import InterviewPrepGenerator
except ImportError:
    InterviewPrepGenerator = None

try:
    from dashboard import Dashboard
except ImportError:
    Dashboard = None

try:
    from smart_scheduler import SmartScheduler
except ImportError:
    SmartScheduler = None

try:
    from success_tracker import SuccessTracker
except ImportError:
    SuccessTracker = None

try:
    from follow_up_engine import FollowUpEngine
except ImportError:
    FollowUpEngine = None

try:
    from network_leverage import NetworkLeverage
except ImportError:
    NetworkLeverage = None

try:
    from resume_ab_testing import ResumeABTester
except ImportError:
    ResumeABTester = None

try:
    from cover_letter_gen import CoverLetterGenerator
except ImportError:
    CoverLetterGenerator = None

try:
    from skill_gap_analysis import SkillGapAnalyzer
except ImportError:
    SkillGapAnalyzer = None

try:
    from company_intel import CompanyIntel
except ImportError:
    CompanyIntel = None

try:
    from apply_timing import ApplyTimingOptimizer
except ImportError:
    ApplyTimingOptimizer = None

try:
    from email_monitor import EmailMonitor
except ImportError:
    EmailMonitor = None

try:
    from profile_optimizer import ProfileOptimizer
except ImportError:
    ProfileOptimizer = None

try:
    from fingerprint_rotator import FingerprintRotator
except ImportError:
    FingerprintRotator = None


# ===================================================================
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


# ===================================================================
# FILTERING
# ===================================================================

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
        if re.search(r"\b" + re.escape(wl) + r"\b", dl):
            return f"bad word: {w}"
    exp = app.get("years_of_experience", -1)
    buf = filters.get("experience_buffer", 2)
    if exp > -1:
        matches = re.findall(r"(\d+)\s*[+\-–]?\s*(?:to\s*\d+\s*)?year", dl)
        if matches:
            years = [int(y) for y in matches if int(y) <= 15]
            if years and max(years) > exp + buf:
                return f"needs {max(years)}yr (you: {exp}+{buf})"
    return None


# ===================================================================
# PROCESS ONE SEARCH PAGE
# ===================================================================

def process_page(drv, cfg: dict, st: State, sched: dict,
                 search_term: str = "", search_location: str = "",
                 ai: "AIAnswerer | None" = None,
                 cycle_seen_ids: set = None,
                 scorer=None, tailor=None, ext_applier=None,
                 messenger=None, alert_mgr=None, salary_eng=None,
                 prep_gen=None, scheduler=None) -> dict:
    """Process all job cards on the current search results page."""
    result = {"applied": 0, "skipped": 0, "failed": 0}
    log = logging.getLogger("lla")
    if cycle_seen_ids is None:
        cycle_seen_ids = set()

    cards = get_job_cards(drv)
    if not cards:
        return result

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
    resume_path = cfg.get("resume", {}).get("default_resume_path", "")

    log.info(f"  {num_cards} job IDs collected, processing up to {max_per}")

    for i in range(max_per):
        if shutdown_requested:
            break
        if st.daily_applied_count() >= sched.get("max_applies_per_day", 40):
            log.warning("Daily limit reached.")
            break

        job_id = job_ids[i]
        if job_id in cycle_seen_ids:
            result["skipped"] += 1
            continue
        cycle_seen_ids.add(job_id)

        if st.is_applied(job_id):
            result["skipped"] += 1
            continue

        if not click_job_card(drv, job_id):
            log.debug(f"  [{i}] Could not click card {job_id}")
            result["failed"] += 1
            continue

        human_sleep(1.5, 2.5)

        # EXTRACT job info from the RIGHT PANE
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
                    location = loc_text.split("·")[0].strip() if "·" in loc_text else loc_text
                    break
        except Exception:
            pass

        if not title:
            title = f"Job {job_id}"

        log.info(f"▶  [{i}] {title} @ {company} (ID: {job_id})")

        # Check for "already applied" badge
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
                applied_badges = None
            if applied_badges:
                continue
        except Exception:
            pass

        # Filter: blacklisted companies / bad titles
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

        # EXTRACT FULL DETAILS
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

        # Track hiring velocity
        if scheduler:
            scheduler.track_job_posting(company, title)

        # Store salary data
        if salary_eng and salary:
            salary_eng.parse_and_store(job_id, title, company, location, salary)

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

        # MATCH SCORING
        match_score = 0
        match_result = None
        if scorer and scorer.enabled:
            match_result = scorer.score_job(title, company, desc, location)
            match_score = match_result.get("score", 0)
            log.info(f"   🎯 Match score: {match_score}% ({match_result.get('explanation', '')})")
            st.save_match_score(job_id, title, company, match_score,
                               ", ".join(match_result.get("skill_matches", [])),
                               ", ".join(match_result.get("missing_skills", [])),
                               match_result.get("explanation", ""))

            if not scorer.should_apply(match_score):
                log.info(f"   ⏭️  Low match score: {match_score}% (min: {scorer.minimum_score}%)")
                st.mark_skipped(job_id, title, company, location,
                               f"low match score: {match_score}%", visa_status, "",
                               search_term, search_location, match_score=match_score)
                result["skipped"] += 1
                continue

        # RESUME TAILORING
        tailored_resume = None
        resume_version = ""
        if tailor and tailor.enabled:
            tailored_resume = tailor.tailor_resume(title, company, desc, match_result)
            if tailored_resume:
                resume_version = os.path.basename(tailored_resume)
                log.info(f"   📄 Tailored resume: {resume_version}")

        # Determine resume path for this application
        active_resume = tailored_resume or resume_path

        # APPLY
        try:
            easy_apply_clicked = click_easy_apply(drv)

            if easy_apply_clicked:
                # Pass tailored resume through job_context
                jc = {"title": title, "company": company, "description": desc[:500]}
                if tailored_resume:
                    jc["tailored_resume_path"] = tailored_resume

                success = process_easy_apply(drv, cfg, ai=ai, job_context=jc)
            elif ext_applier and ext_applier.can_apply():
                # Try external apply
                ext_url = get_external_apply_url(drv)
                if ext_url:
                    log.info(f"   🌐 Trying external apply: {ext_url[:60]}")
                    jc = {"title": title, "company": company,
                          "description": desc[:500], "location": location}
                    success = ext_applier.apply_external(drv, ext_url, jc, active_resume)
                else:
                    log.info("   ⏭️  No Easy Apply or external apply button")
                    st.mark_skipped(job_id, title, company, location,
                                   "no apply button", visa_status, "",
                                   search_term, search_location, match_score=match_score)
                    result["skipped"] += 1
                    continue
            else:
                log.info("   ⏭️  No Easy Apply button")
                st.mark_skipped(job_id, title, company, location,
                               "no Easy Apply button", visa_status, "",
                               search_term, search_location, match_score=match_score)
                result["skipped"] += 1
                continue

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
                    match_score=match_score, resume_version=resume_version,
                )
                result["applied"] += 1
                log.info(f"   ✅  APPLIED!")

                # POST-APPLY ACTIONS
                # Queue recruiter message
                if messenger and hiring_team:
                    for person in hiring_team[:1]:
                        messenger.queue_message(
                            job_id=job_id,
                            recruiter_name=person["name"],
                            profile_url=person.get("profile_url", ""),
                            company=company, job_title=title, description=desc[:500],
                        )

                # Generate interview prep
                if prep_gen and prep_gen.enabled:
                    try:
                        prep_gen.generate(job_id, title, company, desc, st)
                    except Exception as e:
                        log.debug(f"Interview prep failed: {e}")

                # Send alert
                if alert_mgr:
                    try:
                        alert_mgr.send_applied(title, company, salary, visa_status,
                                              recruiter_name, match_score,
                                              f"https://www.linkedin.com/jobs/view/{job_id}/")
                    except Exception as e:
                        log.debug(f"Alert failed: {e}")

                human_sleep(sched.get("delay_after_apply",4), sched.get("delay_after_apply",4)+3)
            else:
                st.mark_failed(job_id, title, company, "modal failed")
                result["failed"] += 1
                log.info(f"   ❌  Modal failed")

        except Exception as e:
            log.warning(f"   💥  Error: {e}")
            st.mark_failed(job_id, title, company, str(e))
            result["failed"] += 1
            if alert_mgr:
                try:
                    alert_mgr.send_error(f"{title} @ {company}: {e}")
                except Exception:
                    pass
            discard_application(drv)

        human_sleep(sched.get("min_delay_between_jobs",3), sched.get("max_delay_between_jobs",8))

    return result


# ===================================================================
# FULL CYCLE
# ===================================================================

def run_cycle(drv, cfg: dict, st: State, ai=None,
              scorer=None, tailor=None, ext_applier=None,
              messenger=None, alert_mgr=None, salary_eng=None,
              prep_gen=None, scheduler=None, google_scraper=None):
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

    cycle_seen_ids = set()
    tot_a, tot_s, tot_f = 0, 0, 0

    TIME_CHAIN = ["Past hour", "Past 2 hours", "Past 6 hours", "Past 12 hours", "Past 24 hours", "Past week"]
    base_filter = sc.get("date_posted", "Past hour")
    base_filter_lower = str(base_filter).strip().lower()
    chain_start = 0
    for idx, f in enumerate(TIME_CHAIN):
        if f.lower() == base_filter_lower:
            chain_start = idx
            break

    # Verify session
    if not verify_session(drv):
        log.warning("Session expired before cycle. Re-logging in...")
        if not login(drv, cfg):
            log.error("Re-login failed. Skipping cycle.")
            return

    # GOOGLE JOBS SCRAPING (before LinkedIn search)
    if google_scraper and google_scraper.enabled:
        try:
            log.info("🔍 Scraping Google Jobs...")
            google_scraper.scrape_jobs(drv)

            # Process Google-discovered LinkedIn jobs
            gj_linkedin = google_scraper.get_queued_linkedin_jobs()
            if gj_linkedin:
                log.info(f"   Processing {len(gj_linkedin)} Google-discovered LinkedIn jobs")
                for gj in gj_linkedin[:5]:
                    if shutdown_requested:
                        break
                    lid = gj.get("linkedin_job_id", "")
                    if lid and not st.is_applied(lid):
                        try:
                            drv.get(f"https://www.linkedin.com/jobs/view/{lid}/")
                            human_sleep(2, 4)
                            cycle_seen_ids.add(lid)
                        except Exception as e:
                            log.debug(f"Google->LinkedIn nav failed: {e}")
                    google_scraper.state.update_google_job_status(gj["google_job_id"], "queued")
        except Exception as e:
            log.warning(f"Google Jobs scraping error: {e}")

    # LINKEDIN SEARCH
    for loc in locs:
        if shutdown_requested: break
        for term in terms:
            if shutdown_requested: break
            if st.daily_applied_count() >= sched.get("max_applies_per_day", 40):
                break

            sl = loc.split(",")[0].strip()
            found_results = False

            for filter_idx in range(chain_start, len(TIME_CHAIN)):
                if shutdown_requested: break
                time_filter = TIME_CHAIN[filter_idx]
                is_widened = filter_idx > chain_start

                if is_widened:
                    log.info(f"   ↳ Widening to \"{time_filter}\"...")
                if not is_widened:
                    log.info(f"🔍  \"{term}\" → \"{sl}\" ({time_filter})")

                try:
                    cfg_copy = dict(cfg)
                    cfg_copy["search"] = dict(sc)
                    cfg_copy["search"]["date_posted"] = time_filter

                    url = build_search_url(cfg_copy, term, loc)
                    navigate_to_search(drv, url)

                    from linkedin import get_job_cards as _peek_cards
                    cards = _peek_cards(drv)
                    new_cards = [c for c in cards
                                 if c.get_attribute("data-occludable-job-id") not in cycle_seen_ids]

                    if len(cards) == 0 and filter_idx < len(TIME_CHAIN) - 1:
                        log.info(f"   0 results for \"{time_filter}\"")
                        human_sleep(1, 2)
                        continue

                    r = process_page(drv, cfg, st, sched, term, loc, ai=ai,
                                    cycle_seen_ids=cycle_seen_ids,
                                    scorer=scorer, tailor=tailor,
                                    ext_applier=ext_applier,
                                    messenger=messenger, alert_mgr=alert_mgr,
                                    salary_eng=salary_eng, prep_gen=prep_gen,
                                    scheduler=scheduler)
                    tot_a += r["applied"]; tot_s += r["skipped"]; tot_f += r["failed"]
                    log.info(f"   Page: +{r['applied']}A +{r['skipped']}S +{r['failed']}F | Unique seen: {len(cycle_seen_ids)}")
                    found_results = True
                    break

                except Exception as e:
                    log.error(f"   Search error: {e}")
                    tot_f += 1
                    break

            human_sleep(sched.get("min_delay_between_searches",5), sched.get("max_delay_between_searches",15))

    # Process Google-discovered ATS jobs
    if google_scraper and google_scraper.enabled and ext_applier and ext_applier.can_apply():
        gj_ats = google_scraper.get_queued_ats_jobs()
        if gj_ats:
            log.info(f"🌐 Processing {len(gj_ats)} Google-discovered ATS jobs")
            for gj in gj_ats[:ext_applier.max_per_cycle]:
                if shutdown_requested:
                    break
                try:
                    jc = {"title": gj.get("title", ""), "company": gj.get("company", ""),
                          "description": gj.get("description", ""), "location": gj.get("location", "")}
                    resume_path = cfg.get("resume", {}).get("default_resume_path", "")
                    success = ext_applier.apply_external(drv, gj["source_url"], jc, resume_path)
                    status = "applied" if success else "skipped"
                    google_scraper.state.update_google_job_status(gj["google_job_id"], status)
                    if success:
                        tot_a += 1
                except Exception as e:
                    log.debug(f"ATS apply failed: {e}")
                    google_scraper.state.update_google_job_status(gj["google_job_id"], "skipped")

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


# ===================================================================
# MAIN LOOP
# ===================================================================

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

    # Initialize new feature modules (all gracefully degrade if disabled/missing)
    scorer = MatchScorer(ai_answerer, cfg) if MatchScorer else None
    tailor = ResumeTailor(ai_answerer, cfg) if ResumeTailor else None
    ext_applier = ExternalApplier(ai_answerer, cfg) if ExternalApplier else None
    messenger = RecruiterMessenger(ai_answerer, cfg, state) if RecruiterMessenger else None
    alert_mgr = AlertManager(cfg) if AlertManager else None
    salary_eng = SalaryIntel(state, ai_answerer, cfg) if SalaryIntel else None
    prep_gen = InterviewPrepGenerator(ai_answerer, cfg) if InterviewPrepGenerator else None
    scheduler = SmartScheduler(state, cfg) if SmartScheduler else None
    google_scraper = GoogleJobsScraper(cfg, state) if GoogleJobsScraper else None
    dash = Dashboard(state, cfg) if Dashboard else None
    follow_up = FollowUpEngine(ai_answerer, cfg, state) if FollowUpEngine else None
    network = NetworkLeverage(cfg, state) if NetworkLeverage else None
    ab_tester = ResumeABTester(ai_answerer, cfg, state) if ResumeABTester else None
    cover_gen = CoverLetterGenerator(ai_answerer, cfg) if CoverLetterGenerator else None
    skill_analyzer = SkillGapAnalyzer(ai_answerer, cfg, state) if SkillGapAnalyzer else None
    company_enricher = CompanyIntel(ai_answerer, cfg, state) if CompanyIntel else None
    timing_opt = ApplyTimingOptimizer(cfg) if ApplyTimingOptimizer else None
    email_mon = EmailMonitor(cfg, state) if EmailMonitor else None
    profile_opt = ProfileOptimizer(ai_answerer, cfg, state) if ProfileOptimizer else None
    fp_rotator = FingerprintRotator(cfg) if FingerprintRotator else None

    # Log enabled features
    features = []
    if scorer and scorer.enabled: features.append(f"Match Scoring (min: {scorer.minimum_score}%)")
    if tailor and tailor.enabled: features.append("Resume Tailoring")
    if ext_applier and ext_applier.enabled: features.append("External ATS Apply")
    if messenger and messenger.enabled: features.append(f"Recruiter Messaging ({messenger.delay_minutes}min delay)")
    if alert_mgr and alert_mgr.enabled: features.append("Alerts")
    if salary_eng and salary_eng.enabled: features.append("Salary Intelligence")
    if prep_gen and prep_gen.enabled: features.append("Interview Prep")
    if scheduler and scheduler.enabled: features.append("Smart Scheduling")
    if google_scraper and google_scraper.enabled: features.append("Google Jobs Scraping")
    if cfg.get("activity_simulation", {}).get("enabled"): features.append("Activity Simulation")
    if follow_up and follow_up.enabled: features.append("Follow-Up Engine")
    if network and network.enabled: features.append("Network Leverage")
    if ab_tester and ab_tester.enabled: features.append("Resume A/B Testing")
    if cover_gen and cover_gen.enabled: features.append("Cover Letter Gen")
    if skill_analyzer and skill_analyzer.enabled: features.append("Skill Gap Analysis")
    if company_enricher and company_enricher.enabled: features.append("Company Intel")
    if timing_opt and timing_opt.enabled: features.append("Apply Timing")
    if email_mon and email_mon.enabled: features.append("Email Monitor")
    if profile_opt and profile_opt.enabled: features.append("Profile Optimizer")
    if dash and dash.enabled: features.append(f"Dashboard (:{dash.port})")
    if features:
        log.info(f"🚀 Features: {', '.join(features)}")
    else:
        log.info("No additional features enabled.")

    # Start dashboard
    if dash:
        try:
            dash.start()
        except Exception as e:
            log.warning(f"Dashboard start failed: {e}")

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
            # Update modules with new config
            if scorer: scorer.__init__(ai_answerer, cfg)
            if tailor: tailor.__init__(ai_answerer, cfg)
            if messenger: messenger.__init__(ai_answerer, cfg, state)
            if ext_applier: ext_applier.__init__(ai_answerer, cfg)
        except Exception as e:
            log.warning(f"Config reload failed: {e}")

        sched = cfg.get("scheduling", {})
        interval = max(sched.get("scan_interval_minutes", 15), 1) * 60

        # Smart scheduling adjustment
        if scheduler and scheduler.enabled:
            adj = scheduler.get_scan_interval_adjustment()
            interval = int(interval * adj)

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

        # Activity simulation (before cycle)
        if simulate_activity and cfg.get("activity_simulation", {}).get("enabled", False):
            try:
                simulate_activity(driver, cfg)
            except Exception as e:
                log.debug(f"Activity simulation error: {e}")

        # Run cycle
        try:
            log.info(f"── Cycle start (every {interval//60}min) ──")
            run_cycle(driver, cfg, state, ai=ai_answerer,
                     scorer=scorer, tailor=tailor,
                     ext_applier=ext_applier, messenger=messenger,
                     alert_mgr=alert_mgr, salary_eng=salary_eng,
                     prep_gen=prep_gen, scheduler=scheduler,
                     google_scraper=google_scraper)
            errors = 0
        except Exception as e:
            errors += 1
            log.error(f"Cycle error ({errors}/5): {e}")
            if alert_mgr:
                try:
                    alert_mgr.send_error(f"Cycle error: {e}")
                except Exception:
                    pass
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

        # Process recruiter message queue
        if messenger and messenger.enabled:
            try:
                messenger.process_queue(driver)
            except Exception as e:
                log.debug(f"Message queue error: {e}")

        # Process follow-up queue
        if follow_up and follow_up.enabled:
            try:
                follow_up.process_follow_ups(driver)
            except Exception as e:
                log.debug(f"Follow-up queue error: {e}")

        # Check email for responses
        if email_mon and email_mon.enabled:
            try:
                email_mon.check_inbox()
            except Exception as e:
                log.debug(f"Email monitor error: {e}")

        # Check daily summary
        if alert_mgr:
            try:
                alert_mgr.check_daily_summary(state)
            except Exception:
                pass

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
