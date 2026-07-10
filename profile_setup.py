"""
Profile onboarding helper — turn a `documents/` folder into profile text.

Backs the `/setup` command: reads whatever career material the user has dropped
into `documents/` (CV, LinkedIn export, diplomas, reference letters, past
applications) and returns concatenated text that Claude (or the AI) can turn
into a structured profile written to `ai.cv_text` and `personal.*` in config.

Text files (.txt/.md/.tex) are read directly; PDFs go through the shared
`ats_pdf_check.extract_pdf_text` (pdftotext → pdfminer, graceful). Anything
unreadable is skipped and reported, never fatal.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger("lla.profile_setup")

_TEXT_EXT = {".txt", ".md", ".markdown", ".tex", ".rst"}
_PDF_EXT = {".pdf"}
_SKIP_DIRS = {".git", "__pycache__", "node_modules"}


def read_file_text(path: str) -> str:
    """Return the text of a single document, or "" if unreadable."""
    ext = Path(path).suffix.lower()
    try:
        if ext in _TEXT_EXT:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        if ext in _PDF_EXT:
            from ats_pdf_check import extract_pdf_text
            return extract_pdf_text(path) or ""
    except Exception as exc:
        log.debug("could not read %s: %s", path, exc)
    return ""


def gather_profile_text(documents_dir: str = "documents",
                        max_chars: int = 20000) -> dict:
    """Walk `documents_dir`, read supported files, return combined text.

    Returns {found: [rel_paths], read: [rel_paths], skipped: [rel_paths],
             text: str, truncated: bool}.
    """
    root = Path(documents_dir)
    found, read, skipped, chunks = [], [], [], []
    total = 0
    truncated = False

    if not root.exists():
        return {"found": [], "read": [], "skipped": [], "text": "",
                "truncated": False}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in sorted(filenames):
            if fn.startswith(".") or fn == ".gitkeep":
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            ext = Path(fn).suffix.lower()
            found.append(rel)
            if ext not in _TEXT_EXT and ext not in _PDF_EXT:
                skipped.append(rel)
                continue
            text = read_file_text(full).strip()
            if not text:
                skipped.append(rel)
                continue
            read.append(rel)
            header = f"\n\n===== {rel} =====\n"
            remaining = max_chars - total
            if remaining <= 0:
                truncated = True
                break
            body = text[:remaining]
            if len(text) > remaining:
                truncated = True
            chunks.append(header + body)
            total += len(header) + len(body)
        if truncated:
            break

    return {"found": found, "read": read, "skipped": skipped,
            "text": "".join(chunks).strip(), "truncated": truncated}
