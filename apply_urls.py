#!/usr/bin/env python3
"""
Batch external-apply runner — the "last mile" that actually submits.

Job *discovery* tools (Indeed, Google Jobs, careers pages) can find roles and
hand you an apply link, but they can't click submit. This runner closes that
gap: give it a list of apply URLs and it drives a real browser through each
ATS form and submits — using the same ats_handlers/ engine as the LinkedIn bot,
but WITHOUT needing LinkedIn. It works on any Greenhouse / Lever / Workday /
Ashby / SmartRecruiters / Workable / Jobvite / BambooHR / iCIMS / Taleo /
SuccessFactors / ADP link.

Typical loop:
    1. Find jobs however you like (Indeed search, a careers page, a spreadsheet).
    2. Collect the apply URLs into a file (one per line, or a CSV with metadata).
    3. Run this. It fills identity fields from your config for free, uses AI for
       the open-ended questions, uploads your resume, and submits.

Usage:
    python apply_urls.py https://boards.greenhouse.io/acme/jobs/123
    python apply_urls.py --file urls.txt --resume ~/cv.pdf
    python apply_urls.py --file jobs.csv               # header: url,title,company,description
    python apply_urls.py --file urls.txt --dry-run     # detect only — no browser, no submit
    python apply_urls.py --file urls.txt --max 10 --headless

Safety:
    * --dry-run does pure URL detection (no browser, nothing submitted) so you
      can see the plan first.
    * Already-applied URLs are skipped (dedup by URL) unless you pass --force.
    * Exit status is 0 only if every attempted URL submitted.
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    import yaml
except ImportError:
    print("PyYAML is required.  Run:  pip install pyyaml")
    sys.exit(1)

from ats_handlers import ALL_ATS, detect_ats  # noqa: E402  (after sys.path setup)

log = logging.getLogger("lla.apply_urls")


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def _job_id_for(url: str) -> str:
    """Stable per-URL id so re-runs dedup and results persist in state."""
    return "ext-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def load_jobs(urls, file_path=None):
    """Return a list of job dicts {url, title, company, description, location}.

    Accepts inline URLs plus an optional file:
      * .csv  — header row with at least a `url` column (title/company/... optional)
      * .json — a list of URL strings or a list of job objects
      * else  — one URL per line ('#' comments and blanks ignored)
    """
    jobs = []

    def _add(url, title="", company="", description="", location=""):
        url = (url or "").strip()
        if not url or not url.lower().startswith("http"):
            return
        jobs.append({"url": url, "title": title, "company": company,
                     "description": description, "location": location})

    for u in urls or []:
        _add(u)

    if file_path:
        p = Path(file_path)
        if not p.exists():
            log.error("Input file not found: %s", file_path)
            sys.exit(1)
        suffix = p.suffix.lower()
        text = p.read_text(encoding="utf-8")
        if suffix == ".csv":
            for row in csv.DictReader(text.splitlines()):
                row = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
                _add(row.get("url", ""), row.get("title", ""),
                     row.get("company", ""), row.get("description", ""),
                     row.get("location", ""))
        elif suffix == ".json":
            data = json.loads(text)
            for item in (data if isinstance(data, list) else [data]):
                if isinstance(item, str):
                    _add(item)
                elif isinstance(item, dict):
                    _add(item.get("url") or item.get("apply_url", ""),
                         item.get("title", ""), item.get("company", ""),
                         item.get("description", ""), item.get("location", ""))
        else:
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    _add(line)

    # De-dupe by URL, preserving order.
    seen, unique = set(), []
    for j in jobs:
        if j["url"] not in seen:
            seen.add(j["url"])
            unique.append(j)
    return unique


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class BatchApplier:
    """Drive a browser through a list of external ATS apply URLs."""

    def __init__(self, cfg: dict, resume_path: str = "", headless=None,
                 max_apply=None, force=False):
        self.cfg = cfg
        self.force = force
        self.max_apply = max_apply
        self.resume_path = resume_path or self._default_resume()
        if headless is not None:
            cfg.setdefault("browser", {})["headless"] = headless

        from ai import AIAnswerer
        from external_apply import ExternalApplier
        from state import State

        self.state = State(db_path=cfg.get("state", {}).get("db_path", "data/state.db"))
        try:
            self.ai = AIAnswerer(cfg, db_conn=self.state.conn)
        except Exception as exc:
            log.warning("AI unavailable (%s) — only keyword-matched fields will fill", exc)
            self.ai = None

        self.applier = ExternalApplier(self.ai, cfg)
        # This tool applies to whatever URLs you hand it, so enable the engine and
        # accept every ATS we have a handler for, regardless of the config subset.
        self.applier.enabled = True
        self.applier.supported_ats = set(ALL_ATS)

    def _default_resume(self) -> str:
        for section, key in (("resume", "default_resume_path"),
                             ("resume_tailoring", "master_resume_path")):
            v = self.cfg.get(section, {}).get(key, "")
            if v and os.path.exists(v):
                return v
        return ""

    # ---- planning (no browser) -------------------------------------------

    def plan(self, jobs):
        """Detect the ATS for each URL without launching a browser."""
        rows = []
        for j in jobs:
            ats = detect_ats(j["url"])
            already = self.state.is_applied(_job_id_for(j["url"]))
            rows.append((j, ats, already))
        return rows

    # ---- real submission --------------------------------------------------

    def run(self, jobs):
        """Launch a browser and submit each supported URL. Returns (ok, fail, skip)."""
        from linkedin import create_browser

        supported = [(j, detect_ats(j["url"])) for j in jobs]
        applyable = [(j, a) for j, a in supported if a]
        unsupported = [j for j, a in supported if not a]
        for j in unsupported:
            log.info("⏭️  Unsupported ATS, skipping: %s", j["url"][:80])

        if not applyable:
            log.warning("No supported ATS URLs to apply to.")
            return 0, 0, len(unsupported)

        cap = self.max_apply or len(applyable)
        self.applier.max_per_cycle = cap
        self.applier.applied_this_cycle = 0

        if self.resume_path:
            log.info("📎 Resume: %s", self.resume_path)
        else:
            log.warning("No resume configured — file-upload fields will be left blank.")

        log.info("🚀 Applying to %d job(s) across %d ATS platform(s)...",
                 len(applyable), len({a for _, a in applyable}))

        driver = None
        ok = fail = skip = len(unsupported)
        skip -= len(unsupported)  # keep skip counting only dedup skips
        skip = 0
        try:
            driver = create_browser(self.cfg)
            driver.get("about:blank")
            for i, (job, ats) in enumerate(applyable, 1):
                if self.applier.applied_this_cycle >= cap:
                    log.info("Reached --max %d; stopping.", cap)
                    break
                job_id = _job_id_for(job["url"])
                if not self.force and self.state.is_applied(job_id):
                    log.info("[%d/%d] ↩️  Already applied, skipping: %s",
                             i, len(applyable), job["url"][:70])
                    skip += 1
                    continue

                log.info("[%d/%d] %s → %s", i, len(applyable), ats, job["url"][:70])
                jc = {"title": job["title"], "company": job["company"],
                      "description": job["description"], "location": job["location"]}
                try:
                    success = self.applier.apply_external(
                        driver, job["url"], jc, self.resume_path)
                except Exception as exc:
                    log.warning("   handler crashed: %s", exc)
                    success = False

                if success:
                    ok += 1
                    self._record_success(job_id, job)
                    log.info("   ✅ submitted")
                else:
                    fail += 1
                    self.state.mark_failed(job_id, job["title"], job["company"],
                                          reason=f"{ats} apply failed")
                    log.info("   ❌ not submitted")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        return ok, fail, skip + len(unsupported)

    def _record_success(self, job_id, job):
        self.state.mark_applied(
            job_id=job_id, title=job["title"], company=job["company"],
            location=job["location"], job_url=job["url"],
            description=job["description"],
            resume_version=os.path.basename(self.resume_path) if self.resume_path else "",
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_plan(rows):
    print(f"\n  {'ATS':16} {'STATUS':14} URL")
    print(f"  {'-'*16} {'-'*14} {'-'*40}")
    supported = 0
    for job, ats, already in rows:
        status = "already applied" if already else ("ready" if ats else "unsupported")
        if ats and not already:
            supported += 1
        print(f"  {(ats or '—'):16} {status:14} {job['url'][:60]}")
    print(f"\n  {supported} of {len(rows)} ready to apply "
          f"({len(rows) - supported} skipped).\n")


def build_parser():
    p = argparse.ArgumentParser(
        prog="apply_urls",
        description="Submit applications to external ATS forms from a list of URLs "
                    "(no LinkedIn needed).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("urls", nargs="*", help="Apply URL(s) to submit")
    p.add_argument("-f", "--file", help="File of URLs (.txt one-per-line, .csv, or .json)")
    p.add_argument("-c", "--config", default="config.yaml", help="Config YAML (default: config.yaml)")
    p.add_argument("-r", "--resume", help="Resume file to upload (overrides config)")
    p.add_argument("--max", type=int, dest="max_apply", help="Max applications this run")
    p.add_argument("--headless", action="store_true", help="Run the browser headless")
    p.add_argument("--force", action="store_true", help="Re-apply even if already applied")
    p.add_argument("--dry-run", action="store_true",
                   help="Detect the ATS for each URL and print a plan — no browser, no submit")
    return p


def main():
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
    args = build_parser().parse_args()

    if not args.urls and not args.file:
        build_parser().print_help()
        sys.exit(0)

    # --dry-run needs no config beyond detection; real runs need a valid config.
    cfg = {}
    cfg_path = Path(args.config)
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    elif not args.dry_run:
        print(f"Error: config file '{args.config}' not found. Run  python cli.py setup")
        sys.exit(1)

    jobs = load_jobs(args.urls, args.file)
    if not jobs:
        print("No valid URLs found.")
        sys.exit(1)

    if args.dry_run:
        rows = [(j, detect_ats(j["url"]), False) for j in jobs]
        _print_plan(rows)
        print("  (dry run — nothing was submitted. Note: URLs that redirect to an")
        print("   ATS are only detected once the browser follows them in a real run.)")
        return

    runner = BatchApplier(cfg, resume_path=args.resume or "",
                          headless=args.headless or None,
                          max_apply=args.max_apply, force=args.force)
    ok, fail, skip = runner.run(jobs)

    print(f"\n  Done: ✅ {ok} submitted   ❌ {fail} failed   ⏭️  {skip} skipped\n")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
