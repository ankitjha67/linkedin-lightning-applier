#!/usr/bin/env python3
"""Check the Claude skills and slash commands this repo ships.

    python tools/lint_skills.py          # exit 0 = clean, 1 = something is wrong

`.claude/skills/*/SKILL.md` and `.claude/commands/*.md` are executable
documentation: Claude reads them and acts on what they say. They are also the
least-tested files here — nothing imports them, so a broken one fails silently,
at the moment someone is trying to use it.

The checks are the ones that actually bite:

  * the YAML frontmatter parses, and has the fields the loader needs;
  * `name` matches the directory, because that is how a skill is addressed;
  * the description says *when* to use the skill, not only what it is — a
    description with no trigger never gets selected;
  * every repo file the document points at exists, so instructions do not
    send Claude to a module that was renamed or deleted;
  * commands documenting a shell command name a script that is really there.

A stale path is the common failure and the expensive one: the instructions
still read plausibly, so the error surfaces as confused behaviour rather than
as a missing file.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / ".claude" / "skills"
COMMANDS_DIR = ROOT / ".claude" / "commands"

REQUIRED_SKILL_FIELDS = ("name", "description")
REQUIRED_COMMAND_FIELDS = ("description",)

MAX_DESCRIPTION = 1024
MAX_NAME = 64
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# A description that never says when to use the skill will not be selected.
TRIGGER_WORDS = ("use when", "use this when", "when the user", "when you",
                 "for when", "invoke when", "use for")

# Paths mentioned in prose. Only repo-relative, file-looking things.
PATH_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|md|yaml|yml|json|html|js|tex|txt))`")
# `python foo.py ...` / `python3 tools/bar.py`
SCRIPT_RE = re.compile(r"\bpython3?\s+([A-Za-z0-9_./-]+\.py)\b")

problems = []
notes = []


def fail(where, msg, fix=""):
    problems.append((where, msg, fix))


def split_frontmatter(text: str):
    """(frontmatter_dict, body, error). Frontmatter must open on line 1."""
    if not text.startswith("---"):
        return {}, text, "no YAML frontmatter (the file must start with '---')"
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text, "frontmatter is never closed with '---'"
    raw = text[3:end]
    body = text[end + 4:]
    try:
        import yaml
        data = yaml.safe_load(raw)
    except Exception as exc:
        return {}, body, f"frontmatter is not valid YAML ({exc})"
    if data is None:
        return {}, body, "frontmatter is empty"
    if not isinstance(data, dict):
        return {}, body, "frontmatter must be a mapping of fields"
    return data, body, ""


def referenced_paths(text: str):
    """Repo files the document tells Claude to use."""
    found = set()
    for m in PATH_RE.finditer(text):
        found.add(m.group(1))
    for m in SCRIPT_RE.finditer(text):
        found.add(m.group(1))
    # A bare "foo.md" in prose is usually a filename, not a path we can check.
    return {p for p in found if not p.startswith(("http", "//"))}


def is_intentionally_absent(rel: str) -> bool:
    """Is this path missing on purpose rather than by mistake?

    config.yaml, .env and everything under data/ are gitignored: they hold
    credentials and personal documents, so they are absent from a fresh clone
    by design. Asking git means the rule cannot drift from .gitignore.
    """
    if rel.startswith(("data/", "documents/", "templates/", "output/")):
        return True
    try:
        import subprocess
        out = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT,
                             capture_output=True, timeout=15)
        return out.returncode == 0
    except Exception:
        return False


def check_referenced_paths(where, text):
    for rel in sorted(referenced_paths(text)):
        if (ROOT / rel).exists() or is_intentionally_absent(rel):
            continue
        fail(where, f"points at '{rel}', which does not exist",
             "fix the path, or remove the reference — a stale path makes the "
             "instructions read fine while sending Claude nowhere")


def check_skill(skill_md: Path):
    where = str(skill_md.relative_to(ROOT))
    text = skill_md.read_text(encoding="utf-8")
    data, body, err = split_frontmatter(text)
    if err:
        fail(where, err, "every SKILL.md opens with a '---' YAML block")
        return

    for field in REQUIRED_SKILL_FIELDS:
        if not str(data.get(field, "") or "").strip():
            fail(where, f"frontmatter has no '{field}'",
                 f"add '{field}:' — the loader needs it")

    name = str(data.get("name", "") or "").strip()
    directory = skill_md.parent.name
    if name:
        if name != directory:
            fail(where, f"name '{name}' does not match the directory '{directory}'",
                 "they must agree — the directory is how the skill is addressed")
        if len(name) > MAX_NAME:
            fail(where, f"name is {len(name)} characters (max {MAX_NAME})",
                 "shorten it")
        if not NAME_RE.match(name):
            fail(where, f"name '{name}' is not lowercase-with-hyphens",
                 "use letters, digits and hyphens only")

    desc = " ".join(str(data.get("description", "") or "").split())
    if desc:
        if len(desc) > MAX_DESCRIPTION:
            fail(where, f"description is {len(desc)} characters (max {MAX_DESCRIPTION})",
                 "trim it")
        if not any(t in desc.lower() for t in TRIGGER_WORDS):
            fail(where, "description never says when to use the skill",
                 "add a 'Use when …' clause — a description with no trigger "
                 "is never selected, however good the skill is")

    if not body.strip():
        fail(where, "the skill has no body", "document what it does")
    check_referenced_paths(where, body)


def check_command(cmd_md: Path):
    where = str(cmd_md.relative_to(ROOT))
    text = cmd_md.read_text(encoding="utf-8")
    data, body, err = split_frontmatter(text)
    if err:
        fail(where, err, "every command file opens with a '---' YAML block")
        return
    for field in REQUIRED_COMMAND_FIELDS:
        if not str(data.get(field, "") or "").strip():
            fail(where, f"frontmatter has no '{field}'",
                 f"add '{field}:' — it is what the user sees in the command list")
    desc = str(data.get("description", "") or "")
    if len(desc) > MAX_DESCRIPTION:
        fail(where, f"description is {len(desc)} characters (max {MAX_DESCRIPTION})",
             "trim it")
    if not body.strip():
        fail(where, "the command has no body", "document what it should do")
    check_referenced_paths(where, body)


def main():
    print("⚡ Lightning Applier — skills & commands\n")

    skills = sorted(SKILLS_DIR.glob("*/SKILL.md")) if SKILLS_DIR.exists() else []
    commands = sorted(COMMANDS_DIR.glob("*.md")) if COMMANDS_DIR.exists() else []

    if not skills and not commands:
        print("  No skills or commands found — nothing to check.")
        return 0

    for path in skills:
        check_skill(path)
    for path in commands:
        check_command(path)

    # A skill directory with no SKILL.md is invisible to the loader.
    if SKILLS_DIR.exists():
        for d in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
            if not (d / "SKILL.md").exists():
                fail(str(d.relative_to(ROOT)), "directory has no SKILL.md",
                     "add one, or remove the directory — the loader ignores it")

    print(f"  {len(skills)} skill(s), {len(commands)} command(s) checked")
    for n in notes:
        print(f"  · {n}")

    if not problems:
        print("\n  ✅ all clean")
        return 0

    print(f"\n  ❌ {len(problems)} problem(s):\n")
    for where, msg, fix in problems:
        print(f"  [{where}] {msg}")
        if fix:
            print(f"      fix: {fix}")
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
