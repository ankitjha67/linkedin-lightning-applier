# documents/

Drop your career source material here, then run `/setup` (in Claude Code) or use
`profile_setup.gather_profile_text("documents")`. Everything here is **gitignored
except this README and the folder structure** — your personal files never get
committed.

## Layout

| Folder | Put here |
|--------|----------|
| `cv/` | Your master CV (PDF, .tex, .txt, or .md) |
| `linkedin/` | LinkedIn profile export (PDF) |
| `diplomas/` | Degree certificates / transcripts (PDF) |
| `references/` | Reference / recommendation letters |
| `applications/` | Past application records, one folder per role |

## Notes
- Text files (.txt/.md/.tex) are read directly; PDFs need `pdftotext` (poppler)
  or `pip install pdfminer.six` to extract text. Without them, PDFs are skipped.
- `/setup` is idempotent — add more material and re-run any time.
- The richer and more concrete your material (projects, tools, measurable
  results — not just job titles), the sharper every tailored application.
