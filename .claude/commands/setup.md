---
description: Onboard your profile — build ai.cv_text + personal fields from your documents, a pasted CV, or an interview
argument-hint: (optional) --section search | paste your CV text
---

# /setup — profile onboarding

Populate the candidate profile the rest of the system relies on: `ai.cv_text`
(the master CV summary used for tailoring), `personal.*` (name, email, phone,
city, country), and the search targets. Richer input → sharper applications.

`$ARGUMENTS` may be empty, a pasted CV, or `--section search`.

## Choose a path (auto-detect, then confirm with the user)

1. **Documents folder (preferred).** If `documents/` has files, gather them:
   ```
   python -c "import json,profile_setup; print(json.dumps(profile_setup.gather_profile_text('documents')))"
   ```
   Read the returned `text` (CV, LinkedIn export, diplomas, references, past
   applications). This mode is idempotent — safe to re-run as the user adds
   material. See `documents/README.md` for the layout.

2. **Pasted CV.** If `$ARGUMENTS` contains CV text, use it directly.

3. **Interview.** If neither, ask the user a short structured interview:
   name and contacts; education; each role (what they *did* — projects, tools,
   measurable results, not just titles); skills in context; target roles,
   locations, and what energizes vs. drains them.

## Build the profile

From whatever source, synthesize and then **show the user for confirmation
before writing anything**:

- `personal`: first_name, last_name, full_name, email, phone, city, country.
- `ai.cv_text`: a dense, factual master-CV summary (roles with concrete
  achievements, skills in context, education, certs). This feeds `lla docs` and
  the resume tailorer, so depth matters.
- `search.search_terms` + `search.search_locations`: target roles/skills and
  locations. With `--section search`, update ONLY these and suggest role types
  the profile supports that the user may not have considered.

Then update `config.yaml` in place (preserve every other setting and all
comments; edit only the keys above). Never write secrets. Confirm the diff with
the user first.

## Rules
- Only record facts the source material supports — never invent experience.
- `config.yaml` is gitignored; keep it that way. Do not echo API keys.
- After writing, suggest: `/expand` to enrich from linked public sources, then
  `/scrape` or `lla run` to start; `lla docs` to draft an application.
