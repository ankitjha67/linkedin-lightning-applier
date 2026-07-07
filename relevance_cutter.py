"""
Relevance-weighted CV trimming.

When a CV overflows its page budget, cutting the "oldest" section is crude — an
older bullet that hits the posting's keywords can matter more than a recent one
that doesn't. This scores each line by three signals and drops the lowest-value
lines first (from MadsLorentzen/ai-job-search's relevance-weighted cutting):

  relevance          how many job-description keywords the line contains
  uniqueness         how little it duplicates other lines (deduping filler)
  cover_dependency   whether the cover letter leans on this line's content

Pure logic, deterministic, no AI or binaries — trivially testable.
"""

import re

# Same word shape as ats_pdf_check: internal . / - join alphanumerics, trailing
# + / # kept (c++, c#), but no trailing sentence punctuation.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[.\-][A-Za-z0-9]+)*[+#]*")


def _tokens(text: str) -> set:
    return {t for t in (_WORD_RE.findall((text or "").lower())) if len(t) > 2}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def score_line(line: str, jd_keywords: set, cover_tokens: set,
               other_token_sets: list) -> dict:
    """Score one CV line. Returns {relevance, uniqueness, cover_dependency, total}."""
    toks = _tokens(line)
    if not toks:
        return {"relevance": 0.0, "uniqueness": 0.0, "cover_dependency": 0.0, "total": 0.0}

    relevance = len(toks & jd_keywords) / max(1, len(toks))
    # uniqueness = 1 - max overlap with any other line
    max_overlap = max((_jaccard(toks, o) for o in other_token_sets if o is not toks),
                      default=0.0)
    uniqueness = 1.0 - max_overlap
    # cover dependency: distinctive tokens (len>4) that the cover letter uses
    distinctive = {t for t in toks if len(t) > 4}
    cover_dependency = 1.0 if (distinctive & cover_tokens) else 0.0

    total = 3.0 * relevance + 1.0 * uniqueness + 2.0 * cover_dependency
    return {"relevance": round(relevance, 3), "uniqueness": round(uniqueness, 3),
            "cover_dependency": cover_dependency, "total": round(total, 3)}


def rank_lines(lines: list, jd_text: str, cover_text: str = "") -> list:
    """Return [(index, line, score_dict)] sorted by total score descending."""
    from ats_pdf_check import extract_keywords
    jd_keywords = set(extract_keywords(jd_text, top=40))
    cover_tokens = _tokens(cover_text)
    token_sets = [_tokens(ln) for ln in lines]
    scored = []
    for i, ln in enumerate(lines):
        s = score_line(ln, jd_keywords, cover_tokens, token_sets)
        scored.append((i, ln, s))
    scored.sort(key=lambda x: x[2]["total"], reverse=True)
    return scored


def trim_to(lines: list, max_lines: int, jd_text: str, cover_text: str = "") -> dict:
    """Keep the `max_lines` highest-value lines, preserving original order.

    Returns {kept: list[str], dropped: list[str], ranking: [(idx,line,score)]}.
    """
    ranking = rank_lines(lines, jd_text, cover_text)
    if len(lines) <= max_lines:
        return {"kept": list(lines), "dropped": [], "ranking": ranking}
    keep_idx = {idx for idx, _, _ in ranking[:max_lines]}
    kept = [ln for i, ln in enumerate(lines) if i in keep_idx]
    dropped = [ln for i, ln in enumerate(lines) if i not in keep_idx]
    return {"kept": kept, "dropped": dropped, "ranking": ranking}
