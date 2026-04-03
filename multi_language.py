"""Multi-Language Resume / Cover Letter module.

Detects the language of a job description and generates translated or
localised versions of resumes and cover letters using AI.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Supported languages and their detection heuristics.
# Each entry maps a language name to a list of regex patterns that, if found
# with sufficient frequency, suggest the text is written in that language.
LANGUAGE_PATTERNS = {
    "German": [
        re.compile(r"\b(der|die|das|und|ist|für|wir|mit|auf|ein|eine|nicht|sich|bei|Stellenangebot|Aufgaben|Anforderungen)\b", re.IGNORECASE),
    ],
    "French": [
        re.compile(r"\b(le|la|les|des|nous|vous|est|dans|pour|avec|sur|une|poste|entreprise|recherchons|candidat)\b", re.IGNORECASE),
    ],
    "Spanish": [
        re.compile(r"\b(el|la|los|las|del|para|con|por|una|nos|esta|empresa|puesto|requisitos|experiencia)\b", re.IGNORECASE),
    ],
    "Portuguese": [
        re.compile(r"\b(o|os|as|da|do|dos|das|em|para|com|uma|vaga|empresa|requisitos|experiência)\b", re.IGNORECASE),
    ],
    "Dutch": [
        re.compile(r"\b(de|het|een|van|en|in|voor|met|op|wij|zijn|wordt|functie|vacature|werkzaamheden)\b", re.IGNORECASE),
    ],
    "Arabic": [
        re.compile(r"[\u0600-\u06FF]{3,}"),
    ],
    "Hindi": [
        re.compile(r"[\u0900-\u097F]{3,}"),
    ],
    "Japanese": [
        re.compile(r"[\u3040-\u309F]"),  # Hiragana
        re.compile(r"[\u30A0-\u30FF]"),  # Katakana
    ],
    "Mandarin": [
        re.compile(r"[\u4E00-\u9FFF]{2,}"),
    ],
}

# Minimum match count to consider a language detected (for word-boundary patterns)
MIN_MATCH_THRESHOLD = 5

# Threshold for script-based detection (Arabic, Hindi, Japanese, Mandarin)
SCRIPT_MATCH_THRESHOLD = 3

# Languages that use script-range detection rather than word matching
SCRIPT_LANGUAGES = {"Arabic", "Hindi", "Japanese", "Mandarin"}

SUPPORTED_LANGUAGES = [
    "English", "German", "French", "Spanish", "Portuguese",
    "Dutch", "Arabic", "Hindi", "Japanese", "Mandarin",
]


class MultiLanguageGenerator:
    """Detects JD language and produces translated resumes and cover letters."""

    def __init__(self, ai, cfg):
        self.ai = ai
        self.cfg = cfg
        self.enabled = cfg.get("multi_language", {}).get("enabled", False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_language(self, text):
        """Detect the primary language of the given text.

        Uses regex heuristics for common languages.  Falls back to English
        if no other language scores above the threshold.

        Returns the language name as a string (e.g. ``"German"``).
        """
        if not text or not text.strip():
            return "English"

        scores = {}
        for language, patterns in LANGUAGE_PATTERNS.items():
            count = 0
            for pat in patterns:
                count += len(pat.findall(text))
            scores[language] = count

        # For script-based languages, use a lower threshold
        best_language = None
        best_score = 0
        for language, score in scores.items():
            threshold = SCRIPT_MATCH_THRESHOLD if language in SCRIPT_LANGUAGES else MIN_MATCH_THRESHOLD
            if score >= threshold and score > best_score:
                best_score = score
                best_language = language

        if best_language:
            logger.info("Detected language: %s (score %d).", best_language, best_score)
            return best_language

        logger.debug("No strong language signal detected; defaulting to English.")
        return "English"

    def translate_resume(self, resume_text, target_language):
        """Translate / adapt a resume into the target language using AI.

        Returns the translated resume text or the original on failure.
        """
        if not self.enabled:
            logger.debug("MultiLanguageGenerator disabled; returning original resume.")
            return resume_text

        if target_language == "English":
            return resume_text

        try:
            prompt = self._build_resume_translation_prompt(resume_text, target_language)
            translated = self.ai.generate(prompt)
            if translated and translated.strip():
                logger.info("Resume translated to %s (%d chars).", target_language, len(translated))
                return translated
            logger.warning("AI returned empty translation; using original.")
            return resume_text
        except Exception as exc:
            logger.error("Error translating resume to %s: %s", target_language, exc)
            return resume_text

    def translate_cover_letter(self, cover_letter_text, target_language):
        """Translate / adapt a cover letter into the target language using AI.

        Returns translated text or the original on failure.
        """
        if not self.enabled:
            logger.debug("MultiLanguageGenerator disabled; returning original cover letter.")
            return cover_letter_text

        if target_language == "English":
            return cover_letter_text

        try:
            prompt = self._build_cover_letter_translation_prompt(
                cover_letter_text, target_language
            )
            translated = self.ai.generate(prompt)
            if translated and translated.strip():
                logger.info("Cover letter translated to %s (%d chars).", target_language, len(translated))
                return translated
            logger.warning("AI returned empty cover letter translation; using original.")
            return cover_letter_text
        except Exception as exc:
            logger.error("Error translating cover letter to %s: %s", target_language, exc)
            return cover_letter_text

    def get_supported_languages(self):
        """Return the list of supported languages."""
        return list(SUPPORTED_LANGUAGES)

    def generate_localized_resume(self, job_title, company, description,
                                  target_language=None):
        """Full pipeline: detect language (or use explicit target), then translate.

        Reads the base resume from config, translates it, and returns the
        localised version.

        Returns a dict with ``language``, ``resume``, and ``cover_letter`` keys,
        or None on failure.
        """
        if not self.enabled:
            logger.debug("MultiLanguageGenerator disabled; skipping localisation.")
            return None

        try:
            if not target_language:
                target_language = self.detect_language(description)

            base_resume = self.cfg.get("multi_language", {}).get("base_resume", "")
            base_cover = self.cfg.get("multi_language", {}).get("base_cover_letter", "")

            if not base_resume:
                logger.warning("No base_resume found in config; cannot localise.")
                return None

            # If target is English and source is English, skip translation
            if target_language == "English":
                logger.info("Target language is English; no translation needed.")
                return {
                    "language": "English",
                    "resume": base_resume,
                    "cover_letter": base_cover,
                }

            translated_resume = self.translate_resume(base_resume, target_language)

            translated_cover = ""
            if base_cover:
                translated_cover = self.translate_cover_letter(base_cover, target_language)

            result = {
                "language": target_language,
                "resume": translated_resume,
                "cover_letter": translated_cover,
            }

            logger.info(
                "Generated localised resume for %s at %s in %s.",
                job_title, company, target_language,
            )
            return result
        except Exception as exc:
            logger.error("Error in generate_localized_resume: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_resume_translation_prompt(self, resume_text, target_language):
        """Build the AI prompt for resume translation."""
        locale_hints = self._get_locale_hints(target_language)
        return (
            f"You are a professional resume translator. Translate the following "
            f"resume into {target_language}. {locale_hints}\n\n"
            f"Important guidelines:\n"
            f"- Preserve the original formatting and structure\n"
            f"- Adapt job titles and section headings to local conventions\n"
            f"- Keep proper nouns (company names, technologies) in their original form\n"
            f"- Use professional, formal register appropriate for {target_language} CVs\n"
            f"- Adapt date formats to the local convention\n\n"
            f"Resume to translate:\n\n{resume_text}"
        )

    def _build_cover_letter_translation_prompt(self, cover_letter_text, target_language):
        """Build the AI prompt for cover letter translation."""
        locale_hints = self._get_locale_hints(target_language)
        return (
            f"You are a professional cover letter translator. Translate the following "
            f"cover letter into {target_language}. {locale_hints}\n\n"
            f"Important guidelines:\n"
            f"- Use the formal greeting and closing conventions of {target_language}\n"
            f"- Maintain a professional yet personable tone\n"
            f"- Adapt any culturally specific references\n"
            f"- Keep company names and technologies in their original form\n\n"
            f"Cover letter to translate:\n\n{cover_letter_text}"
        )

    @staticmethod
    def _get_locale_hints(target_language):
        """Return locale-specific hints for the translator."""
        hints = {
            "German": (
                "Use 'Sie' (formal). Date format: DD.MM.YYYY. "
                "CV is called 'Lebenslauf'. Include a 'Bewerbungsschreiben' style."
            ),
            "French": (
                "Use 'vous' (formal). Date format: DD/MM/YYYY. "
                "CV is called 'Curriculum Vitae'. Use 'Madame, Monsieur' for salutation."
            ),
            "Spanish": (
                "Use 'usted' (formal). Date format: DD/MM/YYYY. "
                "Distinguish Latin American vs European Spanish based on context."
            ),
            "Portuguese": (
                "Date format: DD/MM/YYYY. "
                "Consider whether Brazilian or European Portuguese is more appropriate."
            ),
            "Dutch": (
                "Date format: DD-MM-YYYY. "
                "CV is called 'Curriculum Vitae'. Use formal 'u' address."
            ),
            "Arabic": (
                "Write right-to-left. Use Modern Standard Arabic for professional contexts. "
                "Date format may vary by country."
            ),
            "Hindi": (
                "Use formal Hindi (Shuddh Hindi where appropriate). "
                "Technical terms may be kept in English."
            ),
            "Japanese": (
                "Use keigo (formal/polite language). "
                "Follow Japanese CV (rirekisho) conventions where possible. "
                "Date format: YYYY/MM/DD."
            ),
            "Mandarin": (
                "Use Simplified Chinese characters. "
                "Follow Chinese CV conventions. Date format: YYYY-MM-DD."
            ),
        }
        return hints.get(target_language, "")
