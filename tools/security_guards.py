#!/usr/bin/env python3
"""Supply-chain guards for this repo's riskiest surfaces.

    python tools/security_guards.py          # exit 0 = clean, 1 = a guard fired

This bot stores more sensitive material than most projects: real ATS account
passwords (`data/ats_accounts.xlsx`), LinkedIn credentials and API keys
(`config.yaml`, `.env`), a database of everywhere you have applied
(`data/state.db`), and your CV and personal documents (`documents/`). All of it
is kept out of git by `.gitignore` alone — one careless edit and it is public
and permanent.

These guards make the dangerous changes LOUD, not impossible. A change that
genuinely needs one of them must update the allowlist in this file in the same
diff, so it is explicit and reviewable rather than silent.

Checks
  1. .gitignore still carries every personal-data rule, and nothing re-includes
     them via a negation (`!pattern`).
  2. No secret-bearing file is tracked by git right now.
  3. .claude/settings.json permissions/hooks stay inside the allowlist —
     a widened permission (or any hook) auto-approves commands for every user
     of this repo.
  4. No obvious hardcoded secret in tracked source.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 1. .gitignore rules that must never disappear ──────────────────────────
# Each entry: (pattern that must be present, what it protects)
REQUIRED_IGNORES = [
    ("config.yaml", "LinkedIn password, API keys, personal profile"),
    (".env", "API keys (OpenRouter, Gemini, …)"),
    ("data/", "state.db, ats_accounts.xlsx (real ATS passwords), exported CSVs"),
    ("documents/*/*", "your CV, diplomas, references"),
    ("chrome-lla-profile/", "browser profile — live session cookies"),
]

# Negations are how an ignore rule gets silently undone. Only these are allowed.
ALLOWED_NEGATIONS = {
    "!documents/*/.gitkeep",     # keep the folder structure, not the contents
    "!data/.gitkeep",
}

# ── 2. files that must never be tracked, even if .gitignore looks right ────
FORBIDDEN_TRACKED = [
    re.compile(r"^config\.yaml$"),
    re.compile(r"^\.env$"),
    re.compile(r"^data/.*\.(db|xlsx|csv)$"),
    re.compile(r"^documents/(?!README\.md$).*/(?!\.gitkeep$).+"),
    re.compile(r".*ats_accounts.*"),
    re.compile(r".*\.pem$|.*\.key$|.*_rsa$"),
]

# ── 3. Claude Code permissions allowlist ──────────────────────────────────
# A permission here is pre-approved for anyone running this repo. Broad entries
# (Bash(*), Bash(curl:*)) would auto-approve arbitrary commands.
ALLOWED_PERMISSIONS = set()          # we ship none; adding any must be deliberate
ALLOW_HOOKS = False                  # a hook runs with no prompt at all

FORBIDDEN_PERMISSION_RE = re.compile(
    r"Bash\(\*\)|Bash\(\s*\)|Bash\((curl|wget|rm|sudo|chmod|ssh|nc)[: (]|"
    r"Write\(\*\)|Edit\(\*\)|WebFetch\(\*\)", re.I)

# ── 4. hardcoded secrets in tracked source ────────────────────────────────
SECRET_PATTERNS = [
    (re.compile(r"sk-(or-v1-|ant-|proj-)?[A-Za-z0-9]{24,}"), "OpenAI/Anthropic/OpenRouter key"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "Google API key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}"), "NVIDIA NIM key"),
    (re.compile(r"xai-[A-Za-z0-9]{20,}"), "xAI key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
]
# Files that legitimately contain key-shaped placeholder text.
SECRET_SCAN_SKIP = re.compile(
    r"^(\.env\.example|.*/?tools/security_guards\.py|.*\.lock|.*/CHANGELOG\.md)$")

failures = []
notes = []


def fail(check, msg, fix):
    failures.append((check, msg, fix))


def git(*args):
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, timeout=60)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


# ───────────────────────────── checks ─────────────────────────────────────

def check_gitignore():
    path = ROOT / ".gitignore"
    if not path.exists():
        fail("gitignore", ".gitignore is missing entirely",
             "restore it — every secret in this repo depends on it")
        return
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    entries = {ln for ln in lines if ln and not ln.startswith("#")}

    for pattern, protects in REQUIRED_IGNORES:
        if not any(e == pattern or e.rstrip("/") == pattern.rstrip("/") for e in entries):
            fail("gitignore", f"missing rule '{pattern}' (protects: {protects})",
                 f"add '{pattern}' back to .gitignore")

    for e in entries:
        if e.startswith("!") and e not in ALLOWED_NEGATIONS:
            fail("gitignore", f"un-allowlisted negation '{e}' re-includes ignored files",
                 f"remove it, or add it to ALLOWED_NEGATIONS in {Path(__file__).name} "
                 "in the same commit")


def check_tracked_files():
    tracked = [ln for ln in git("ls-files").splitlines() if ln]
    if not tracked:
        notes.append("git ls-files returned nothing — skipped tracked-file check")
        return
    for f in tracked:
        for rx in FORBIDDEN_TRACKED:
            if rx.match(f):
                fail("tracked", f"secret-bearing file is tracked by git: {f}",
                     f"git rm --cached '{f}' and confirm it is in .gitignore")
                break


def check_claude_settings():
    path = ROOT / ".claude" / "settings.json"
    if not path.exists():
        notes.append(".claude/settings.json absent — nothing pre-approved (good)")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail("settings", f".claude/settings.json is not valid JSON ({exc})",
             "fix the JSON — a broken settings file is not a safe default")
        return
    allow = (data.get("permissions") or {}).get("allow") or []
    for entry in allow:
        if FORBIDDEN_PERMISSION_RE.search(str(entry)):
            fail("settings", f"dangerous pre-approved permission: {entry!r}",
                 "remove it — this auto-approves commands for every user of the repo")
        elif entry not in ALLOWED_PERMISSIONS:
            fail("settings", f"permission {entry!r} is not in the allowlist",
                 f"add it to ALLOWED_PERMISSIONS in {Path(__file__).name} in the same diff")
    if data.get("hooks") and not ALLOW_HOOKS:
        fail("settings", "settings.json defines hooks",
             "hooks run automatically with no prompt — set ALLOW_HOOKS deliberately")


def check_no_hardcoded_secrets():
    tracked = [ln for ln in git("ls-files").splitlines() if ln]
    if not tracked:
        return
    exts = (".py", ".js", ".json", ".yaml", ".yml", ".md", ".sh", ".html", ".toml")
    for rel in tracked:
        if not rel.endswith(exts) or SECRET_SCAN_SKIP.match(rel):
            continue
        p = ROOT / rel
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for rx, label in SECRET_PATTERNS:
            m = rx.search(text)
            if m:
                line = text[:m.start()].count("\n") + 1
                fail("secrets", f"possible {label} in {rel}:{line}",
                     "remove it, rotate the key, and read it from an env var instead")
                break


def main():
    print("⚡ Lightning Applier — security guards\n")
    for fn in (check_gitignore, check_tracked_files, check_claude_settings,
               check_no_hardcoded_secrets):
        fn()

    for n in notes:
        print(f"  · {n}")

    if not failures:
        print("\n  ✅ all guards passed — secrets stay out of git")
        return 0

    print(f"\n  ❌ {len(failures)} guard(s) fired:\n")
    for check, msg, fix in failures:
        print(f"  [{check}] {msg}")
        print(f"      fix: {fix}\n")
    print("  These guard real credentials (ATS passwords, API keys, your CV).")
    print("  If a change is intentional, update the allowlist in this file in the")
    print("  same commit so the decision is explicit and reviewable.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
