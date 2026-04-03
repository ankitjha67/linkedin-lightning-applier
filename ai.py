"""
AI-powered question answering for job applications.

Supports every major LLM provider through a unified interface:
  PAID:       OpenAI, Anthropic Claude, Google Gemini, DeepSeek
  OPEN-SOURCE: Ollama (local), LM Studio (local), Groq, Together, any OpenAI-compatible

Flow:
  1. Check answer cache (SQLite) — instant, free
  2. Try keyword matching (config.yaml) — instant, free
  3. If both miss AND ai_enabled=true → call LLM with CV context
  4. Cache the LLM answer for future use

All providers use the OpenAI client library (they all support OpenAI-compatible APIs).
"""

import re
import json
import time
import logging
import hashlib
import sqlite3
from pathlib import Path
from typing import Optional

log = logging.getLogger("lla.ai")

# Provider base URLs — all OpenAI-compatible
PROVIDER_URLS = {
    "openai":    "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",    # Claude supports OpenAI-compat
    "gemini":    "https://generativelanguage.googleapis.com/v1beta/openai",
    "deepseek":  "https://api.deepseek.com",
    "groq":      "https://api.groq.com/openai/v1",
    "together":  "https://api.together.xyz/v1",
    "ollama":    "http://localhost:11434/v1",
    "lmstudio":  "http://localhost:1234/v1",
}

# Default models per provider
DEFAULT_MODELS = {
    "openai":    "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-20250514",
    "gemini":    "gemini-2.0-flash",
    "deepseek":  "deepseek-chat",
    "groq":      "llama-3.1-70b-versatile",
    "together":  "meta-llama/Llama-3.1-70B-Instruct-Turbo",
    "ollama":    "llama3.1",
    "lmstudio":  "local-model",
}


class AIAnswerer:
    """
    Unified LLM answerer with caching.

    Usage:
        ai = AIAnswerer(config)
        answer = ai.answer("How many years of Python?", options=["1-3", "3-5", "5+"])
    """

    def __init__(self, cfg: dict, db_conn: sqlite3.Connection = None):
        self.cfg = cfg
        ai_cfg = cfg.get("ai", {})
        self.enabled = ai_cfg.get("enabled", False)

        # Primary provider
        self.provider = ai_cfg.get("provider", "openai").lower()
        self.api_key = ai_cfg.get("api_key", "")
        self.model = ai_cfg.get("model", "") or DEFAULT_MODELS.get(self.provider, "")
        self.base_url = ai_cfg.get("base_url", "") or PROVIDER_URLS.get(self.provider, "")
        self.temperature = ai_cfg.get("temperature", 0.3)
        self.max_tokens = ai_cfg.get("max_tokens", 200)
        self.timeout = ai_cfg.get("timeout_seconds", 30)

        # Fallback provider (optional)
        self.fallback_enabled = ai_cfg.get("fallback_enabled", False)
        self.fallback_provider = ai_cfg.get("fallback_provider", "").lower()
        self.fallback_model = ai_cfg.get("fallback_model", "") or DEFAULT_MODELS.get(self.fallback_provider, "")
        self.fallback_base_url = ai_cfg.get("fallback_base_url", "") or PROVIDER_URLS.get(self.fallback_provider, "")

        # CV / profile context
        self.profile_context = self._build_profile_context(cfg)

        # Answer cache (SQLite)
        self.db = db_conn
        if self.db:
            self._init_cache_table()

        # Clients (lazy init)
        self._client = None
        self._fallback_client = None

        if self.enabled:
            log.info(f"AI enabled: primary={self.provider}/{self.model} @ {self.base_url}")
            if self.fallback_enabled and self.fallback_provider:
                log.info(f"  Fallback: {self.fallback_provider}/{self.fallback_model} @ {self.fallback_base_url}")
            if not self.api_key and self.provider not in ("ollama", "lmstudio"):
                log.warning(f"  No API key set for {self.provider}!")

    def _init_cache_table(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS ai_answer_cache (
                question_hash TEXT PRIMARY KEY,
                question      TEXT,
                options       TEXT DEFAULT '',
                answer        TEXT,
                provider      TEXT,
                model         TEXT,
                created_at    TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        self.db.commit()

    @property
    def client(self):
        """Lazy-init the primary OpenAI-compatible client."""
        if self._client is None:
            self._client = self._make_client(self.provider, self.api_key, self.base_url)
        return self._client

    @property
    def fallback_client(self):
        """Lazy-init the fallback client."""
        if self._fallback_client is None and self.fallback_enabled and self.fallback_provider:
            self._fallback_client = self._make_client(self.fallback_provider, "", self.fallback_base_url)
        return self._fallback_client

    def _make_client(self, provider: str, api_key: str, base_url: str):
        """Create an OpenAI-compatible client for any provider."""
        try:
            from openai import OpenAI
        except ImportError:
            log.error("openai package not installed. Run: pip install openai")
            return None

        # Anthropic native SDK
        if provider == "anthropic":
            try:
                import anthropic
                self._use_anthropic = True
                return anthropic.Anthropic(api_key=api_key)
            except ImportError:
                log.warning("anthropic package not installed, trying OpenAI-compat")

        kwargs = {"base_url": base_url, "timeout": self.timeout}

        # Local providers don't need API keys
        if provider in ("ollama", "lmstudio"):
            kwargs["api_key"] = "not-needed"
        else:
            kwargs["api_key"] = api_key

        self._use_anthropic = False
        return OpenAI(**kwargs)

    def _build_profile_context(self, cfg: dict) -> str:
        """Build candidate profile text from config + optional CV file."""
        parts = []

        # From personal info
        personal = cfg.get("personal", {})
        if personal:
            parts.append("CANDIDATE PROFILE:")
            for k, v in personal.items():
                if v:
                    parts.append(f"  {k.replace('_',' ').title()}: {v}")

        # From application defaults
        app = cfg.get("application", {})
        if app:
            parts.append("\nWORK DETAILS:")
            for k, v in app.items():
                if v:
                    parts.append(f"  {k.replace('_',' ').title()}: {v}")

        # From existing Q&A (shows candidate's typical answers)
        qa = cfg.get("question_answers", {})
        if qa:
            parts.append("\nKNOWN ANSWERS:")
            for k, v in qa.items():
                if v:
                    parts.append(f"  Q: {k} → A: {v}")

        # From CV file (if provided)
        cv_path = cfg.get("ai", {}).get("cv_text_file", "")
        if cv_path and Path(cv_path).exists():
            try:
                cv_text = Path(cv_path).read_text(encoding="utf-8")[:4000]  # Cap at 4K chars
                parts.append(f"\nFULL CV/RESUME:\n{cv_text}")
            except Exception as e:
                log.warning(f"Could not read CV file {cv_path}: {e}")

        # From inline CV text
        cv_inline = cfg.get("ai", {}).get("cv_text", "")
        if cv_inline:
            parts.append(f"\nFULL CV/RESUME:\n{cv_inline[:4000]}")

        return "\n".join(parts)

    def _cache_key(self, question: str, options: list = None) -> str:
        raw = question.strip().lower()
        if options:
            raw += "|" + "|".join(sorted(o.strip().lower() for o in options))
        return hashlib.md5(raw.encode()).hexdigest()

    def _check_cache(self, question: str, options: list = None) -> Optional[str]:
        if not self.db:
            return None
        key = self._cache_key(question, options)
        row = self.db.execute(
            "SELECT answer FROM ai_answer_cache WHERE question_hash=?", (key,)
        ).fetchone()
        if row:
            log.debug(f"  🧠 Cache hit: \"{question[:50]}\" → \"{row[0]}\"")
            return row[0]
        return None

    def _save_cache(self, question: str, answer: str, options: list = None):
        if not self.db:
            return
        key = self._cache_key(question, options)
        self.db.execute("""
            INSERT OR REPLACE INTO ai_answer_cache
            (question_hash, question, options, answer, provider, model)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (key, question, json.dumps(options or []), answer, self.provider, self.model))
        self.db.commit()

    def answer(self, question: str, options: list = None, job_title: str = "",
               company: str = "", job_description: str = "") -> str:
        """
        Get an AI-generated answer for a job application question.

        Args:
            question: The question text (from form label)
            options: Available options for select/radio questions
            job_title: Current job being applied to
            company: Company name
            job_description: First 500 chars of job description for context

        Returns:
            Answer string, or empty string if AI is disabled/fails
        """
        if not self.enabled:
            return ""

        # Check cache first
        cached = self._check_cache(question, options)
        if cached:
            return cached

        # Build the prompt
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(question, options, job_title, company, job_description)

        # Call the LLM
        try:
            answer = self._call_llm(system_prompt, user_prompt)
            if answer:
                # If options were provided, ensure answer matches one
                if options:
                    answer = self._match_to_option(answer, options)

                self._save_cache(question, answer, options)
                log.info(f"  🤖 AI answer: \"{question[:50]}\" → \"{answer}\"")
                return answer
        except Exception as e:
            log.warning(f"  AI error: {e}")

        return ""

    def _build_system_prompt(self) -> str:
        return f"""You are a job application assistant. You answer application form questions on behalf of a candidate.

RULES:
- Give ONLY the answer — no explanations, no quotes, no formatting
- Keep answers concise: 1-3 words for simple questions, 1-2 sentences for text areas
- For yes/no or multiple choice: respond with EXACTLY one of the given options
- For numeric questions (years, salary): give just the number
- Be truthful based on the candidate's profile below
- If unsure, give the most favorable truthful answer
- For "cover letter" or "summary" questions: write 2-3 personalized sentences max
- Never mention you are an AI

{self.profile_context}"""

    def _build_user_prompt(self, question: str, options: list = None,
                           job_title: str = "", company: str = "",
                           job_description: str = "") -> str:
        parts = [f"Question: {question}"]

        if options:
            parts.append(f"Options: {', '.join(options)}")
            parts.append("Reply with EXACTLY one of the options above.")

        if job_title:
            parts.append(f"Job: {job_title}")
        if company:
            parts.append(f"Company: {company}")
        if job_description:
            parts.append(f"Job context: {job_description[:500]}")

        parts.append("\nAnswer (concise, no explanation):")
        return "\n".join(parts)

    def _call_llm(self, system: str, user: str) -> str:
        """Call primary LLM, fall back to secondary if primary fails."""

        # Try primary provider
        result = self._call_provider(
            self.client, self.provider, self.model, system, user
        )
        if result:
            return result

        # Try fallback provider
        if self.fallback_enabled and self.fallback_provider:
            log.info(f"  Primary ({self.provider}) failed. Trying fallback ({self.fallback_provider})...")
            result = self._call_provider(
                self.fallback_client, self.fallback_provider, self.fallback_model, system, user
            )
            if result:
                return result

        return ""

    def _call_provider(self, client, provider: str, model: str,
                       system: str, user: str) -> str:
        """Call a single LLM provider. Returns empty string on failure."""
        if not client:
            return ""

        # Anthropic native SDK
        if provider == "anthropic" and getattr(self, '_use_anthropic', False):
            return self._call_anthropic(system, user)

        # OpenAI-compatible (covers all other providers)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            raw = response.choices[0].message.content
            if raw is None:
                log.debug(f"  [{provider}/{model}] returned None content")
                return ""

            answer = raw.strip()

            # Handle qwen "thinking" models — strip <think>...</think> tags
            if "<think>" in answer:
                import re as _re
                answer = _re.sub(r'<think>.*?</think>', '', answer, flags=_re.DOTALL).strip()

            if answer:
                log.debug(f"  [{provider}/{model}] → {answer[:80]}")
            else:
                log.debug(f"  [{provider}/{model}] returned empty after stripping (raw len={len(raw)})")
            return answer
        except Exception as e:
            log.warning(f"  {provider}/{model} failed: {e}")
            return ""

    def _call_anthropic(self, system: str, user: str) -> str:
        """Call Anthropic's native API."""
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return response.content[0].text.strip()
        except Exception as e:
            log.warning(f"Anthropic call failed: {e}")
            return ""

    def _match_to_option(self, answer: str, options: list) -> str:
        """Ensure AI answer matches one of the given options."""
        answer_lower = answer.strip().lower()

        # Exact match
        for opt in options:
            if opt.strip().lower() == answer_lower:
                return opt.strip()

        # Partial match (AI said "yes" and option is "Yes, I am willing")
        for opt in options:
            if answer_lower in opt.strip().lower() or opt.strip().lower() in answer_lower:
                return opt.strip()

        # First word match ("Yes" matches "Yes - I have...")
        answer_first = answer_lower.split()[0] if answer_lower else ""
        for opt in options:
            if opt.strip().lower().startswith(answer_first):
                return opt.strip()

        # Fallback: return as-is (might not match, but better than empty)
        log.debug(f"  AI answer '{answer}' didn't match options: {options}")
        return answer.strip()

    def answer_cover_letter(self, job_title: str, company: str,
                            job_description: str = "") -> str:
        """Generate a short, personalized cover letter snippet."""
        if not self.enabled:
            return ""

        cache_key = f"cover_letter:{company}:{job_title}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        system = f"""You write brief cover letter snippets for job applications.
Write 3-4 sentences maximum. Be specific to the job and company.
Sound human — not corporate or AI-generated. Show genuine interest.

{self.profile_context}"""

        user = f"""Write a brief cover letter for:
Job: {job_title}
Company: {company}
Description: {job_description[:800]}

3-4 sentences only. No greeting, no sign-off, just the pitch."""

        try:
            result = self._call_llm(system, user)
            if result:
                self._save_cache(cache_key, result)
                return result
        except Exception as e:
            log.warning(f"Cover letter generation failed: {e}")

        return ""

    def extract_skills_from_jd(self, job_description: str) -> list[str]:
        """Use AI to extract key skills from a job description."""
        if not self.enabled or not job_description:
            return []

        system = "Extract the top 5-10 technical skills/requirements from this job description. Return ONLY a comma-separated list of skills, nothing else."
        user = f"Job description:\n{job_description[:1500]}\n\nSkills (comma-separated):"

        try:
            result = self._call_llm(system, user)
            if result:
                return [s.strip() for s in result.split(",") if s.strip()]
        except Exception:
            pass
        return []
