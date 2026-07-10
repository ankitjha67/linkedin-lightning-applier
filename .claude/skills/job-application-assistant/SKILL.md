---
name: job-application-assistant
description: >
  Craft high-quality, honest, ATS-optimized job applications with this repo's
  Python modules. Use when tailoring a CV or cover letter, checking ATS keyword
  coverage / PDF parseability, running a drafter-reviewer critique, or trimming a
  CV to a page budget. Complements the autonomous bot (main.py) and the batch
  applier (apply_urls.py) with a human-in-the-loop path.
---

# Job Application Assistant

A toolkit for producing tailored, truthful application documents. Inspired by the
craftsmanship of MadsLorentzen/ai-job-search (LaTeX documents, drafter-reviewer
loop, ATS text-layer verification, relevance-weighted cutting), implemented as
testable Python modules that fit this repo.

## The modules

| Module | Use it to |
|--------|-----------|
| `latex_docs.py` | Render a `moderncv` CV + cover letter to `.tex` and compile to PDF (lualatex/xelatex/pdflatex). `render_cv_tex(data)` / `render_cover_tex(data)` are pure functions; `LaTeXDocsBuilder(cfg)` reads identity from config. Degrades to `.tex` when no engine is installed. |
| `ats_pdf_check.py` | Verify a CV the way an ATS parser sees it. `extract_pdf_text(pdf)` (pdftotext→pdfminer), `check_parseability(text, profile)` (contact details present, reading order, glyph garbage), `keyword_coverage(cv, jd)`, `ats_report(cv, jd, profile)`. |
| `relevance_cutter.py` | Trim a CV to a line budget by value, not age. `trim_to(lines, n, jd, cover)` keeps the highest relevance×uniqueness×cover-dependency lines and preserves order. |
| `doc_reviewer.py` | Second-pass critique + revision. `DocumentReviewer(ai).draft_review_revise(text, kind, title, company, jd, profile)` returns `{revised, critique, honesty_flags}`. |

## The workflow (what `/apply` runs)

1. Get the job description (URL or pasted text); extract title/company.
2. `python cli.py score ...` — check fit; be honest if it's weak.
3. `python cli.py docs --title ... --company ... --jd-file jd.txt` — generate the
   tailored CV + cover letter, ATS report, reviewer critique, and honesty check.
4. Iterate: apply the reviewer's fixes, add only *genuinely supported* missing
   keywords, re-run until ATS coverage passes and honesty is clean. If a PDF
   compiled, confirm 2-page CV / 1-page cover letter; use `relevance_cutter` to
   trim rather than cutting the oldest section.
5. Confirm with the user, then optionally submit via `apply_urls.py`.

## Non-negotiable: honesty

Only claims the candidate's profile supports (`ai.cv_text` in config plus anything
in `documents/`) may appear in the CV or cover letter. `doc_reviewer.check_honesty`
flags unsupported claims. Real gaps stay visible — never fabricate skills or
keyword-stuff.

## Prerequisites (on the user's machine, optional but recommended)
- A LaTeX distribution (TeX Live / MiKTeX) with `moderncv` for PDF compilation.
- `pdftotext` (poppler) or the `pdfminer.six` Python package for the ATS text-layer
  check on compiled PDFs. Without them, the check runs on the plain-text content.
