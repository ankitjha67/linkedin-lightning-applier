"""
Story Bank — Persistent STAR+R Story Accumulator.

Collects, stores, and retrieves STAR+R (Situation, Task, Action, Result,
Reflection) stories from interview prep evaluations and manual additions.
Stories are reusable across job applications and interview prep.

Supports:
  - Manual story addition
  - Automatic extraction from job evaluation Block F
  - Theme-based retrieval
  - AI-powered question-to-story matching
  - Duplicate detection
  - Usage tracking
  - Narrative generation ("tell me about yourself")
  - Export to markdown
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

log = logging.getLogger("lla.story_bank")


class StoryBank:
    """Persistent bank of STAR+R interview stories."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        sb_cfg = cfg.get("story_bank", {})
        self.enabled = sb_cfg.get("enabled", False)
        self.max_stories = sb_cfg.get("max_stories", 200)
        self.similarity_threshold = sb_cfg.get("similarity_threshold", 0.7)
        self.default_limit = sb_cfg.get("default_limit", 20)

    # ------------------------------------------------------------------
    # Add / extract
    # ------------------------------------------------------------------

    def add_story(self, theme: str, title: str, source_job_id: str,
                  source_company: str, source_role: str,
                  situation: str, task: str, action: str,
                  result: str, reflection: str, best_for: str) -> Optional[int]:
        """
        Add a new STAR+R story to the bank.

        Returns the new story ID, or None on failure.
        """
        if not self.enabled:
            log.debug("StoryBank disabled, skipping add_story")
            return None

        # Check for duplicates before adding
        combined_text = f"{situation} {task} {action} {result}"
        if self.find_similar_stories(combined_text):
            log.info(f"Similar story already exists, skipping: {title}")
            return None

        try:
            cursor = self.state.conn.execute(
                """INSERT INTO story_bank
                   (theme, title, source_job_id, source_company, source_role,
                    situation, task, action, result, reflection, best_for,
                    times_used, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    theme, title, source_job_id, source_company, source_role,
                    situation, task, action, result, reflection, best_for,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            )
            self.state.conn.commit()
            story_id = cursor.lastrowid
            log.info(f"Added story #{story_id}: '{title}' (theme={theme})")
            return story_id
        except Exception as e:
            log.error(f"Failed to add story: {e}")
            return None

    def extract_stories_from_evaluation(self, job_id: str,
                                        evaluation: dict) -> list:
        """
        Parse Block F (interview_plan) from an evaluation and extract
        individual STAR+R stories into the story bank.

        Returns list of newly added story IDs.
        """
        if not self.enabled:
            return []

        interview_plan = evaluation.get("interview_plan", "")
        if not interview_plan:
            log.debug(f"No interview plan in evaluation for {job_id}")
            return []

        company = evaluation.get("company", "Unknown")
        title = evaluation.get("title", "Unknown")

        # Use AI to parse the interview plan into structured stories
        stories = self._parse_stories_with_ai(interview_plan, job_id, company, title)
        if not stories:
            # Fallback: try regex-based parsing
            stories = self._parse_stories_regex(interview_plan, job_id, company, title)

        added_ids = []
        for story in stories:
            story_id = self.add_story(
                theme=story.get("theme", "general"),
                title=story.get("title", "Untitled Story"),
                source_job_id=job_id,
                source_company=company,
                source_role=title,
                situation=story.get("situation", ""),
                task=story.get("task", ""),
                action=story.get("action", ""),
                result=story.get("result", ""),
                reflection=story.get("reflection", ""),
                best_for=story.get("best_for", ""),
            )
            if story_id is not None:
                added_ids.append(story_id)

        log.info(f"Extracted {len(added_ids)} stories from evaluation for {job_id}")
        return added_ids

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_stories(self, theme: str = None, limit: int = None) -> list:
        """Retrieve stories, optionally filtered by theme."""
        limit = limit or self.default_limit
        try:
            if theme:
                rows = self.state.conn.execute(
                    "SELECT * FROM story_bank WHERE theme = ? ORDER BY created_at DESC LIMIT ?",
                    (theme, limit)
                ).fetchall()
            else:
                rows = self.state.conn.execute(
                    "SELECT * FROM story_bank ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            log.error(f"Failed to get stories: {e}")
            return []

    def get_best_stories_for_question(self, question: str) -> list:
        """Use AI to match an interview question to the best stories in the bank."""
        if not self.enabled or not self.ai or not self.ai.enabled:
            return []

        all_stories = self.get_stories(limit=50)
        if not all_stories:
            return []

        # Build a summary of available stories for the AI
        story_summaries = []
        for i, s in enumerate(all_stories):
            story_summaries.append(
                f"Story #{s['id']} ({s['theme']}): \"{s['title']}\"\n"
                f"  Situation: {s['situation'][:100]}...\n"
                f"  Result: {s['result'][:100]}...\n"
                f"  Best for: {s['best_for']}"
            )
        summaries_text = "\n\n".join(story_summaries)

        system = (
            "You are an interview preparation coach. Given an interview question "
            "and a bank of STAR+R stories, select the 2-3 best stories to answer "
            "the question. Return ONLY the story IDs as a comma-separated list.\n"
            "Example: 3, 7, 12\n"
            "If no stories are a good match, return: NONE"
        )
        user = (
            f"INTERVIEW QUESTION: {question}\n\n"
            f"AVAILABLE STORIES:\n{summaries_text}"
        )

        try:
            response = self.ai._call_llm(system, user)
            if not response or response.strip().upper() == "NONE":
                return []

            # Parse story IDs from response
            id_strings = re.findall(r'\d+', response)
            selected_ids = [int(x) for x in id_strings]

            # Fetch the selected stories
            matched = [s for s in all_stories if s["id"] in selected_ids]
            return matched
        except Exception as e:
            log.warning(f"AI story matching failed: {e}")
            return []

    def find_similar_stories(self, new_story_text: str) -> list:
        """
        Check if a similar story already exists in the bank.
        Uses keyword overlap for fast comparison. Returns matching stories.
        """
        if not new_story_text:
            return []

        try:
            all_stories = self.get_stories(limit=100)
            if not all_stories:
                return []

            new_words = set(new_story_text.lower().split())
            # Remove common stop words
            stop_words = {
                "the", "a", "an", "is", "was", "were", "are", "been", "be",
                "have", "has", "had", "do", "does", "did", "will", "would",
                "could", "should", "may", "might", "shall", "can", "need",
                "to", "of", "in", "for", "on", "with", "at", "by", "from",
                "as", "into", "through", "during", "before", "after", "and",
                "but", "or", "nor", "not", "so", "yet", "both", "either",
                "neither", "each", "every", "all", "any", "few", "more",
                "most", "other", "some", "such", "no", "only", "own", "same",
                "than", "too", "very", "just", "because", "i", "my", "we",
                "our", "they", "their", "this", "that", "it", "its",
            }
            new_keywords = new_words - stop_words

            if len(new_keywords) < 3:
                return []

            similar = []
            for story in all_stories:
                existing_text = (
                    f"{story.get('situation', '')} {story.get('task', '')} "
                    f"{story.get('action', '')} {story.get('result', '')}"
                )
                existing_words = set(existing_text.lower().split()) - stop_words
                if not existing_words:
                    continue
                overlap = len(new_keywords & existing_words)
                similarity = overlap / max(len(new_keywords), len(existing_words))
                if similarity >= self.similarity_threshold:
                    similar.append(story)

            return similar
        except Exception as e:
            log.warning(f"Similarity check failed: {e}")
            return []

    def get_story_themes(self) -> list:
        """Return unique themes with counts."""
        try:
            rows = self.state.conn.execute(
                "SELECT theme, COUNT(*) as count FROM story_bank GROUP BY theme ORDER BY count DESC"
            ).fetchall()
            return [{"theme": row["theme"], "count": row["count"]} for row in rows]
        except Exception as e:
            log.error(f"Failed to get story themes: {e}")
            return []

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    def mark_story_used(self, story_id: int) -> bool:
        """Increment the times_used counter for a story."""
        try:
            self.state.conn.execute(
                "UPDATE story_bank SET times_used = times_used + 1 WHERE id = ?",
                (story_id,)
            )
            self.state.conn.commit()
            log.debug(f"Marked story #{story_id} as used")
            return True
        except Exception as e:
            log.error(f"Failed to mark story used: {e}")
            return False

    # ------------------------------------------------------------------
    # Narrative generation
    # ------------------------------------------------------------------

    def generate_narrative(self, stories: list) -> str:
        """
        Combine 2-3 stories into a coherent 'tell me about yourself' answer.
        Uses AI to weave them together.
        """
        if not stories:
            return ""
        if not self.ai or not self.ai.enabled:
            return self._fallback_narrative(stories)

        stories_text = []
        for s in stories[:3]:
            stories_text.append(
                f"Title: {s.get('title', 'Untitled')}\n"
                f"Theme: {s.get('theme', 'general')}\n"
                f"Situation: {s.get('situation', '')}\n"
                f"Task: {s.get('task', '')}\n"
                f"Action: {s.get('action', '')}\n"
                f"Result: {s.get('result', '')}\n"
                f"Reflection: {s.get('reflection', '')}"
            )
        combined = "\n---\n".join(stories_text)

        system = (
            "You are an interview coach. Combine the following 2-3 STAR+R stories "
            "into a single, natural 90-second 'tell me about yourself' answer.\n\n"
            "Rules:\n"
            "- Start with a brief positioning statement (who you are)\n"
            "- Weave the stories as career highlights, not separate anecdotes\n"
            "- End with why you are excited about the next opportunity\n"
            "- Keep it under 300 words\n"
            "- Sound natural and conversational, not rehearsed"
        )
        user = f"STORIES TO COMBINE:\n{combined}"

        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"AI narrative generation failed: {e}")
            return self._fallback_narrative(stories)

    def _fallback_narrative(self, stories: list) -> str:
        """Simple non-AI narrative assembly."""
        parts = []
        for s in stories[:3]:
            parts.append(
                f"In my role at {s.get('source_company', 'a previous company')}, "
                f"{s.get('situation', '')} {s.get('action', '')} "
                f"The result was {s.get('result', 'positive')}."
            )
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_story_bank(self) -> str:
        """Export all stories as formatted markdown text."""
        stories = self.get_stories(limit=self.max_stories)
        if not stories:
            return "# Story Bank\n\nNo stories recorded yet."

        lines = [
            "# Story Bank",
            f"Total stories: {len(stories)}",
            f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        # Group by theme
        themes = {}
        for s in stories:
            theme = s.get("theme", "uncategorized")
            themes.setdefault(theme, []).append(s)

        for theme, theme_stories in sorted(themes.items()):
            lines.append(f"## {theme.replace('_', ' ').title()} ({len(theme_stories)} stories)")
            lines.append("")
            for s in theme_stories:
                lines.append(f"### {s.get('title', 'Untitled')}")
                lines.append(f"*Source: {s.get('source_role', '')} @ {s.get('source_company', '')}*")
                lines.append(f"*Used {s.get('times_used', 0)} times | Best for: {s.get('best_for', 'general')}*")
                lines.append("")
                lines.append(f"**Situation:** {s.get('situation', '')}")
                lines.append(f"**Task:** {s.get('task', '')}")
                lines.append(f"**Action:** {s.get('action', '')}")
                lines.append(f"**Result:** {s.get('result', '')}")
                lines.append(f"**Reflection:** {s.get('reflection', '')}")
                lines.append("")
                lines.append("---")
                lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return story bank statistics."""
        try:
            total_row = self.state.conn.execute(
                "SELECT COUNT(*) as total FROM story_bank"
            ).fetchone()
            total = total_row["total"] if total_row else 0

            themes = self.get_story_themes()

            most_used_row = self.state.conn.execute(
                "SELECT title, times_used FROM story_bank ORDER BY times_used DESC LIMIT 1"
            ).fetchone()
            most_used = None
            if most_used_row and most_used_row["times_used"] > 0:
                most_used = {
                    "title": most_used_row["title"],
                    "times_used": most_used_row["times_used"],
                }

            return {
                "total_stories": total,
                "themes": themes,
                "most_used": most_used,
            }
        except Exception as e:
            log.error(f"Failed to get stats: {e}")
            return {"total_stories": 0, "themes": [], "most_used": None}

    # ------------------------------------------------------------------
    # Internal parsing helpers
    # ------------------------------------------------------------------

    def _parse_stories_with_ai(self, interview_plan: str, job_id: str,
                               company: str, title: str) -> list:
        """Use AI to extract structured stories from Block F text."""
        if not self.ai or not self.ai.enabled:
            return []

        system = (
            "You are a data extraction assistant. Parse the following interview plan "
            "and extract each STAR+R story as a JSON array.\n\n"
            "Each story object must have:\n"
            '  {"theme": "...", "title": "...", "situation": "...", "task": "...", '
            '   "action": "...", "result": "...", "reflection": "...", "best_for": "..."}\n\n'
            "Return ONLY the JSON array. No other text."
        )
        user = f"INTERVIEW PLAN:\n{interview_plan}"

        try:
            response = self.ai._call_llm(system, user)
            if not response:
                return []
            # Try to parse JSON from response
            # Strip markdown code fences if present
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
                cleaned = re.sub(r'\s*```$', '', cleaned)
            stories = json.loads(cleaned)
            if isinstance(stories, list):
                return stories
            return []
        except (json.JSONDecodeError, Exception) as e:
            log.debug(f"AI story parsing failed, will try regex: {e}")
            return []

    def _parse_stories_regex(self, interview_plan: str, job_id: str,
                             company: str, title: str) -> list:
        """Fallback regex-based story extraction from Block F text."""
        stories = []

        # Look for STAR+R patterns in the text
        # Common patterns: "Situation:", "Story 1:", numbered items
        story_blocks = re.split(
            r'(?:Story\s*#?\d+|STAR\+?R?\s*(?:Story\s*)?\d+)[:\s]*',
            interview_plan, flags=re.IGNORECASE
        )

        for block in story_blocks:
            if len(block.strip()) < 50:
                continue

            story = {
                "theme": "general",
                "title": "Extracted Story",
                "situation": "",
                "task": "",
                "action": "",
                "result": "",
                "reflection": "",
                "best_for": "",
            }

            # Extract theme from "Theme:" line
            theme_match = re.search(r'Theme[:\s]+(.+?)(?:\n|$)', block, re.IGNORECASE)
            if theme_match:
                story["theme"] = theme_match.group(1).strip().lower().replace(" ", "_")

            # Extract title
            title_match = re.search(r'Title[:\s]+(.+?)(?:\n|$)', block, re.IGNORECASE)
            if title_match:
                story["title"] = title_match.group(1).strip()

            # Extract STAR+R components
            for field in ["situation", "task", "action", "result", "reflection"]:
                pattern = rf'{field}[:\s]+(.+?)(?=\n(?:situation|task|action|result|reflection|best.for|theme|title|$)|\Z)'
                match = re.search(pattern, block, re.IGNORECASE | re.DOTALL)
                if match:
                    story[field] = match.group(1).strip()

            # Extract best_for
            best_match = re.search(r'Best\s*for[:\s]+(.+?)(?:\n|$)', block, re.IGNORECASE)
            if best_match:
                story["best_for"] = best_match.group(1).strip()

            # Only add if we got at least situation and action
            if story["situation"] and story["action"]:
                stories.append(story)

        return stories
