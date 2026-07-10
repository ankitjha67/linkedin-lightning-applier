---
description: Human-in-the-loop apply — evaluate a job, tailor CV + cover letter, review, then optionally submit
argument-hint: <job URL or pasted job description>
---

# /apply — guided application workflow

Run the full drafter → reviewer → submit workflow for one job, keeping the human
(the user) in the loop at each decision point. This complements the autonomous
bot (`lla run`) and the batch submitter (`lla apply`).

The input is in `$ARGUMENTS` — either a job URL or a pasted job description.

## Steps

1. **Get the job description.**
   - If `$ARGUMENTS` is a URL, fetch it (WebFetch). If the site blocks automated
     access, ask the user to paste the description.
   - If it's already text, use it directly.
   - Extract: title, company, location, and the full description.

2. **Score the fit.** Run:
   ```
   python cli.py score --title "<title>" --company "<company>" --description "<jd>"
   ```
   Show the score and the matched/missing skills. If the score is low, tell the
   user honestly and ask whether to continue.

3. **Generate tailored documents.** Run:
   ```
   python cli.py docs --title "<title>" --company "<company>" --jd-file <path-to-jd.txt>
   ```
   This produces a LaTeX CV + cover letter (compiled to PDF if TeX Live is
   installed), an **ATS keyword-coverage report**, a **reviewer critique**, and an
   **honesty check**. Report all of it to the user.

4. **Iterate on the drafts.** If the ATS coverage is below target or the reviewer
   flagged issues:
   - Read the generated `.tex` files under `data/latex_docs/`.
   - Apply the reviewer's fixes and add genuinely-supported missing keywords.
     **Never** add a keyword the candidate's profile (`ai.cv_text` in config, plus
     `documents/`) does not actually support — surface real gaps instead.
   - Re-run `docs` and re-check until ATS coverage passes and the honesty check is
     clean.
   - If a LaTeX engine is present, open the compiled PDF and confirm the CV is 2
     pages and the cover letter is 1 page. If the CV overflows, use the
     relevance-weighted cutter (`relevance_cutter.trim_to`) rather than cutting the
     oldest section.

5. **Confirm, then submit (optional).** Ask the user before submitting anything.
   If they approve and the apply URL is a supported ATS:
   ```
   python apply_urls.py "<apply-url>" --resume data/latex_docs/cv.pdf
   ```
   Otherwise hand them the tailored PDFs to submit manually.

## Rules
- One job per invocation. Keep the user in control of every submit.
- Honesty first: the CV and cover letter must contain only claims the profile
  supports. Genuine gaps stay visible; they are never fabricated or keyword-stuffed.
- Prefer `--jd-file` over inlining long descriptions on the command line.
