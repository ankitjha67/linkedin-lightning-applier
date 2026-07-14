"""
Answer RAG — semantic memory for application-form answers.

The exact-hash cache in ai.py only fires when a question repeats verbatim.
Real ATS forms rephrase constantly: "How many years of Python experience do you
have?" vs "Years of experience with Python?" — same answer, new tokens burned.

This module remembers every question/answer pair and retrieves by MEANING:

  * Reuse    — similarity >= reuse_threshold: return the stored answer directly,
               zero LLM tokens. Options-aware: for choice questions the cached
               answer must match one of the offered options, else no reuse.
  * Context  — similarity >= context_threshold: inject the top-k similar Q&As
               into the LLM prompt so answers stay consistent and short.
  * Learn    — every fresh LLM answer is saved back into the memory.

Similarity is TF-IDF cosine over question tokens, computed in pure Python
(no numpy/embedding dependency, works offline with any provider). IDF makes the
discriminating words dominate — "python" vs "java", "visa" vs "relocate" — so
near-identical phrasing matches but different subjects do not.

Storage is a SQLite table on the same connection as the rest of the app.
"""

import json
import logging
import math
import re

log = logging.getLogger("lla.rag")

_TOKEN_RE = re.compile(r"[a-z][a-z0-9+#.]*")
_STOP = {
    "a", "an", "the", "of", "to", "in", "on", "for", "with", "do", "does",
    "you", "your", "have", "has", "is", "are", "be", "this", "that", "any",
    "please", "what", "how", "many", "much", "if", "or", "and", "at", "we",
    "our", "can", "will", "would", "there",
    # NOTE: "us" is deliberately NOT a stopword — it collides with the country
    # (US) and dropping it caused UK answers to be reused for US questions.
}


def tokenize(text: str) -> list:
    return [t for t in _TOKEN_RE.findall((text or "").lower())
            if t not in _STOP and len(t) > 1]


class AnswerRAG:
    """Semantic question→answer memory backed by SQLite."""

    def __init__(self, db_conn, cfg: dict = None):
        self.db = db_conn
        rag_cfg = (cfg or {}).get("rag", {})
        self.enabled = rag_cfg.get("enabled", True)
        self.reuse_threshold = rag_cfg.get("reuse_threshold", 0.85)
        self.context_threshold = rag_cfg.get("context_threshold", 0.50)
        self.max_context = rag_cfg.get("max_context", 3)
        self.reuses = 0
        self._corpus = []          # [{id, question, tokens(list), answer, options(list)}]
        self._df = {}              # document frequency per token
        if self.db is not None:
            self._init_table()
            self._load()

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _init_table(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS answer_rag (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                question     TEXT NOT NULL,
                tokens       TEXT NOT NULL,
                answer       TEXT NOT NULL,
                options      TEXT DEFAULT '[]',
                host         TEXT DEFAULT '',
                times_reused INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT (datetime('now','localtime')),
                last_used    TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        self.db.commit()

    def _load(self):
        self._corpus, self._df = [], {}
        try:
            rows = self.db.execute(
                "SELECT id, question, tokens, answer, options FROM answer_rag").fetchall()
        except Exception:
            rows = []
        for rid, q, toks, ans, opts in rows:
            try:
                tokens = json.loads(toks)
                options = json.loads(opts or "[]")
            except json.JSONDecodeError:
                continue
            self._add_to_index(rid, q, tokens, ans, options)

    def _add_to_index(self, rid, question, tokens, answer, options):
        self._corpus.append({"id": rid, "question": question, "tokens": tokens,
                             "answer": answer, "options": options})
        for t in set(tokens):
            self._df[t] = self._df.get(t, 0) + 1

    def save(self, question: str, answer: str, options: list = None,
             host: str = "") -> bool:
        """Remember a fresh answer. Skips empty input and near-duplicates."""
        if not (self.enabled and self.db is not None and question and answer):
            return False
        tokens = tokenize(question)
        if not tokens:
            return False
        # Don't store a second copy of something we'd already reuse.
        best = self._best_match(tokens)
        if best and best[1] >= self.reuse_threshold and \
                best[0]["answer"].strip().lower() == answer.strip().lower():
            return False
        try:
            cur = self.db.execute(
                "INSERT INTO answer_rag (question, tokens, answer, options, host) "
                "VALUES (?, ?, ?, ?, ?)",
                (question, json.dumps(tokens), answer,
                 json.dumps(options or []), host))
            self.db.commit()
            self._add_to_index(cur.lastrowid, question, tokens, answer, options or [])
            return True
        except Exception as exc:
            log.debug("rag save failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Similarity
    # ------------------------------------------------------------------

    def _idf(self, token: str) -> float:
        n = max(1, len(self._corpus))
        return math.log((n + 1) / (self._df.get(token, 0) + 1)) + 1.0

    def _vec(self, tokens: list) -> dict:
        tf = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        return {t: c * self._idf(t) for t, c in tf.items()}

    @staticmethod
    def _cosine(a: dict, b: dict) -> float:
        common = set(a) & set(b)
        num = sum(a[t] * b[t] for t in common)
        den = math.sqrt(sum(v * v for v in a.values())) * \
            math.sqrt(sum(v * v for v in b.values()))
        return num / den if den else 0.0

    def _best_match(self, tokens: list):
        """Return (entry, similarity) of the closest stored question, or None."""
        if not self._corpus or not tokens:
            return None
        qv = self._vec(tokens)
        best, best_sim = None, 0.0
        for entry in self._corpus:
            sim = self._cosine(qv, self._vec(entry["tokens"]))
            if sim > best_sim:
                best, best_sim = entry, sim
        return (best, best_sim) if best else None

    def _rank(self, tokens: list, k: int, min_sim: float) -> list:
        qv = self._vec(tokens)
        scored = []
        for entry in self._corpus:
            sim = self._cosine(qv, self._vec(entry["tokens"]))
            if sim >= min_sim:
                scored.append((sim, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def lookup(self, question: str, options: list = None):
        """Reuse a stored answer for a semantically-equivalent question.

        Returns {answer, similarity, matched_question} or None. For choice
        questions the stored answer must map onto one of the offered options
        (the matching option text is returned).
        """
        if not (self.enabled and question):
            return None
        match = self._best_match(tokenize(question))
        if not match:
            return None
        entry, sim = match
        if sim < self.reuse_threshold:
            return None
        answer = entry["answer"]
        if options:
            answer = self._fit_options(answer, options)
            if answer is None:
                return None
        self.reuses += 1
        self._touch(entry["id"])
        return {"answer": answer, "similarity": round(sim, 3),
                "matched_question": entry["question"]}

    @staticmethod
    def _fit_options(answer: str, options: list):
        """Map a cached answer onto the offered options, or None if it doesn't fit."""
        al = answer.strip().lower()
        for opt in options:
            if opt.strip().lower() == al:
                return opt
        for opt in options:
            ol = opt.strip().lower()
            if al in ol or ol in al:
                return opt
        return None

    def retrieve_context(self, question: str, k: int = None) -> list:
        """Top-k similar past Q&As for prompt augmentation.

        Returns [(question, answer, similarity)], best first. Excludes matches
        above the reuse threshold (those are handled by lookup()).
        """
        if not (self.enabled and question):
            return []
        k = k or self.max_context
        out = []
        for sim, entry in self._rank(tokenize(question), k + 2, self.context_threshold):
            if sim >= self.reuse_threshold:
                continue
            out.append((entry["question"], entry["answer"], round(sim, 3)))
        return out[:k]

    def context_block(self, question: str) -> str:
        """Formatted prompt block of similar past answers ('' when none)."""
        ctx = self.retrieve_context(question)
        if not ctx:
            return ""
        lines = ["Previously answered similar questions (stay consistent with these):"]
        for q, a, _ in ctx:
            lines.append(f'- Q: "{q}" -> A: "{a}"')
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    def _touch(self, rid: int):
        try:
            self.db.execute(
                "UPDATE answer_rag SET times_reused = times_reused + 1, "
                "last_used = datetime('now','localtime') WHERE id = ?", (rid,))
            self.db.commit()
        except Exception:
            pass

    def stats(self) -> dict:
        total = len(self._corpus)
        try:
            reused = self.db.execute(
                "SELECT COALESCE(SUM(times_reused),0) FROM answer_rag").fetchone()[0]
        except Exception:
            reused = 0
        return {"stored": total, "total_reuses": int(reused),
                "session_reuses": self.reuses}
