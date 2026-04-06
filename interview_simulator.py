"""
Interview Simulator — AI-Powered Mock Interview Practice.

Generates realistic interview questions based on job archetype, scores
candidate responses on multiple dimensions, tracks improvement over time.
Supports behavioral (STAR), technical, situational, and culture-fit questions.

Question mix per session (6 total):
  - 2 behavioral STAR questions
  - 2 technical questions
  - 1 situational question
  - 1 culture-fit question

Each response is scored on: relevance, specificity, structure, impact (1-10 each).
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("lla.interview_simulator")

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

QUESTION_GEN_SYSTEM = """You are a senior technical interviewer.
Generate exactly 6 interview questions for the role described below.
Return ONLY valid JSON — a list of 6 objects, each with keys:
  "type" (one of: behavioral, technical, situational, culture_fit),
  "question" (the full question text),
  "follow_up" (a probing follow-up question).

Distribution: 2 behavioral (STAR-style), 2 technical, 1 situational, 1 culture-fit.
Tailor questions to the archetype and seniority implied by the title."""

SCORING_SYSTEM = """You are an interview coach scoring a candidate's response.
Score each dimension from 1-10 and provide a one-sentence feedback.
Return ONLY valid JSON with keys:
  "relevance"   (1-10): Does the answer address the question directly?
  "specificity" (1-10): Does it cite concrete examples, metrics, or details?
  "structure"   (1-10): Is it well-organized (STAR for behavioral, logical for technical)?
  "impact"      (1-10): Does the answer demonstrate meaningful outcomes or depth?
  "feedback"    (string): One actionable improvement suggestion.
  "follow_up_needed" (bool): Should the interviewer probe further?"""

SUMMARY_SYSTEM = """You are an interview coach providing a post-session debrief.
Given the full Q&A with scores, produce a concise JSON summary:
  "overall_score" (1-10): Weighted average performance,
  "strengths" (list of strings): 2-3 things done well,
  "weaknesses" (list of strings): 2-3 areas for improvement,
  "feedback" (string): 3-4 sentence narrative feedback,
  "tip" (string): One concrete practice tip for next session."""


class InterviewSimulator:
    """AI-powered mock interview practice with scoring and progression tracking."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        sim_cfg = cfg.get("interview_simulator", {})
        self.enabled = sim_cfg.get("enabled", False)
        self.max_follow_ups = sim_cfg.get("max_follow_ups", 1)
        self.passing_score = sim_cfg.get("passing_score", 6)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_session(self, job_id: str, title: str, company: str,
                      archetype: str = "general") -> Optional[dict]:
        """Begin a new mock interview session.

        Generates 6 questions, persists the session, and returns the first question.
        """
        if not self.enabled:
            log.debug("InterviewSimulator disabled; skipping session start")
            return None

        if not self.ai or not self.ai.enabled:
            log.warning("AI not available for interview simulation")
            return None

        log.info("Starting interview session for %s at %s (archetype=%s)",
                 title, company, archetype)

        questions = self._generate_questions(title, company, archetype)
        if not questions:
            log.error("Failed to generate interview questions")
            return None

        session_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()

        try:
            self.state.conn.execute(
                """INSERT INTO interview_sessions
                   (id, job_id, company, title, archetype,
                    questions_asked, responses, scores,
                    overall_score, feedback, duration_min, session_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, job_id, company, title, archetype,
                 json.dumps(questions), json.dumps([]), json.dumps([]),
                 0.0, "", 0, now),
            )
            self.state.conn.commit()
        except Exception as exc:
            log.error("Failed to save interview session: %s", exc)
            return None

        first_q = questions[0]
        return {
            "session_id": session_id,
            "total_questions": len(questions),
            "current_index": 0,
            "question_type": first_q.get("type", "unknown"),
            "question": first_q.get("question", ""),
        }

    def answer_question(self, session_id: str, response: str) -> Optional[dict]:
        """Score a candidate's response and return the next question or follow-up.

        Returns dict with score breakdown, feedback, and the next question (if any).
        """
        if not self.enabled:
            return None

        session = self._load_session(session_id)
        if not session:
            log.warning("Session %s not found", session_id)
            return None

        questions = json.loads(session["questions_asked"])
        responses = json.loads(session["responses"])
        scores = json.loads(session["scores"])

        current_idx = len(responses)
        if current_idx >= len(questions):
            log.info("All questions already answered for session %s", session_id)
            return {"done": True, "message": "Session complete. Call end_session()."}

        current_q = questions[current_idx]

        # Score the response
        score_result = self._score_response(
            current_q.get("question", ""),
            current_q.get("type", "general"),
            response,
        )

        responses.append({
            "question_index": current_idx,
            "response": response,
            "answered_at": datetime.now(timezone.utc).isoformat(),
        })
        scores.append(score_result)

        # Persist updated session
        try:
            self.state.conn.execute(
                """UPDATE interview_sessions
                   SET responses = ?, scores = ?
                   WHERE id = ?""",
                (json.dumps(responses), json.dumps(scores), session_id),
            )
            self.state.conn.commit()
        except Exception as exc:
            log.error("Failed to update session %s: %s", session_id, exc)

        # Determine next action
        next_idx = current_idx + 1
        needs_follow_up = score_result.get("follow_up_needed", False)

        result = {
            "question_index": current_idx,
            "score": score_result,
            "avg_score": self._avg_score(score_result),
        }

        if needs_follow_up and current_q.get("follow_up"):
            result["follow_up"] = current_q["follow_up"]
            result["message"] = "Consider this follow-up before moving on."

        if next_idx < len(questions):
            next_q = questions[next_idx]
            result["next_question"] = {
                "index": next_idx,
                "type": next_q.get("type", "unknown"),
                "question": next_q.get("question", ""),
            }
            result["done"] = False
        else:
            result["done"] = True
            result["message"] = "All questions answered. Call end_session() for summary."

        return result

    def end_session(self, session_id: str) -> Optional[dict]:
        """Compute overall score and generate a session summary."""
        if not self.enabled:
            return None

        session = self._load_session(session_id)
        if not session:
            return None

        questions = json.loads(session["questions_asked"])
        responses = json.loads(session["responses"])
        scores = json.loads(session["scores"])

        if not scores:
            return {"session_id": session_id, "error": "No responses recorded yet."}

        # Generate AI summary
        summary = self._generate_summary(questions, responses, scores, session)
        overall_score = summary.get("overall_score", 0)
        feedback_text = json.dumps(summary)

        # Calculate duration
        session_start = session["session_at"]
        now = datetime.now(timezone.utc).isoformat()
        duration = self._calc_duration(session_start, now)

        try:
            self.state.conn.execute(
                """UPDATE interview_sessions
                   SET overall_score = ?, feedback = ?, duration_min = ?
                   WHERE id = ?""",
                (overall_score, feedback_text, duration, session_id),
            )
            self.state.conn.commit()
        except Exception as exc:
            log.error("Failed to finalise session %s: %s", session_id, exc)

        summary["session_id"] = session_id
        summary["duration_min"] = duration
        summary["questions_answered"] = len(responses)
        summary["total_questions"] = len(questions)
        return summary

    def get_session_history(self, session_id: str) -> Optional[dict]:
        """Return the full Q&A history with per-question scores."""
        if not self.enabled:
            return None

        session = self._load_session(session_id)
        if not session:
            return None

        questions = json.loads(session["questions_asked"])
        responses = json.loads(session["responses"])
        scores = json.loads(session["scores"])

        history = []
        for i, q in enumerate(questions):
            entry = {
                "index": i,
                "type": q.get("type", "unknown"),
                "question": q.get("question", ""),
            }
            if i < len(responses):
                entry["response"] = responses[i].get("response", "")
                entry["answered_at"] = responses[i].get("answered_at", "")
            if i < len(scores):
                entry["score"] = scores[i]
                entry["avg_score"] = self._avg_score(scores[i])
            history.append(entry)

        return {
            "session_id": session_id,
            "company": session["company"],
            "title": session["title"],
            "archetype": session["archetype"],
            "overall_score": session["overall_score"],
            "entries": history,
        }

    def get_practice_stats(self) -> dict:
        """Aggregate stats: total sessions, average score, improvement trend."""
        if not self.enabled:
            return {}

        try:
            row = self.state.conn.execute(
                """SELECT COUNT(*) as total,
                          AVG(overall_score) as avg_score,
                          MIN(overall_score) as min_score,
                          MAX(overall_score) as max_score,
                          SUM(duration_min) as total_min
                   FROM interview_sessions
                   WHERE overall_score > 0"""
            ).fetchone()

            if not row or row["total"] == 0:
                return {"total_sessions": 0, "avg_score": 0, "improvement": 0}

            # Improvement: compare last 3 vs first 3 sessions
            recent = self.state.conn.execute(
                """SELECT AVG(overall_score) as avg
                   FROM (SELECT overall_score FROM interview_sessions
                         WHERE overall_score > 0
                         ORDER BY session_at DESC LIMIT 3)"""
            ).fetchone()

            earliest = self.state.conn.execute(
                """SELECT AVG(overall_score) as avg
                   FROM (SELECT overall_score FROM interview_sessions
                         WHERE overall_score > 0
                         ORDER BY session_at ASC LIMIT 3)"""
            ).fetchone()

            recent_avg = recent["avg"] if recent and recent["avg"] else 0
            early_avg = earliest["avg"] if earliest and earliest["avg"] else 0
            improvement = round(recent_avg - early_avg, 2) if early_avg else 0

            return {
                "total_sessions": row["total"],
                "avg_score": round(row["avg_score"], 2),
                "min_score": round(row["min_score"], 2),
                "max_score": round(row["max_score"], 2),
                "total_practice_min": row["total_min"] or 0,
                "improvement": improvement,
                "trending": "up" if improvement > 0 else ("flat" if improvement == 0 else "down"),
            }
        except Exception as exc:
            log.error("Failed to compute practice stats: %s", exc)
            return {"total_sessions": 0, "avg_score": 0, "improvement": 0}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_questions(self, title: str, company: str,
                            archetype: str) -> list:
        """Use AI to generate 6 interview questions."""
        user_prompt = (
            f"Role: {title}\nCompany: {company}\nArchetype: {archetype}\n\n"
            f"Generate 6 interview questions following the required distribution:\n"
            f"2 behavioral (STAR), 2 technical, 1 situational, 1 culture-fit."
        )
        try:
            raw = self.ai._call_llm(QUESTION_GEN_SYSTEM, user_prompt)
            questions = json.loads(raw)
            if isinstance(questions, list) and len(questions) >= 1:
                return questions[:6]
        except (json.JSONDecodeError, TypeError) as exc:
            log.warning("Failed to parse generated questions: %s", exc)

        # Fallback: generic questions
        log.info("Using fallback generic questions")
        return [
            {"type": "behavioral", "question": f"Tell me about a time you led a project similar to what we do at {company}.", "follow_up": "What would you do differently?"},
            {"type": "behavioral", "question": "Describe a situation where you had to resolve a conflict within your team.", "follow_up": "How did the outcome affect team dynamics?"},
            {"type": "technical", "question": f"What technical approach would you take to solve a scaling challenge for a {archetype} system?", "follow_up": "What trade-offs would you consider?"},
            {"type": "technical", "question": f"Walk me through your experience with the core technologies listed for this {title} role.", "follow_up": "Which area do you feel strongest in?"},
            {"type": "situational", "question": f"If you joined {company} and discovered the main product had significant technical debt, how would you prioritise?", "follow_up": "How would you communicate this to stakeholders?"},
            {"type": "culture_fit", "question": f"What about {company}'s mission or culture attracted you, and how do you see yourself contributing?", "follow_up": "What kind of team environment do you thrive in?"},
        ]

    def _score_response(self, question: str, q_type: str,
                        response: str) -> dict:
        """Score a single response on four dimensions via AI."""
        user_prompt = (
            f"Question type: {q_type}\n"
            f"Question: {question}\n\n"
            f"Candidate response:\n{response}\n\n"
            f"Score the response on relevance, specificity, structure, and impact (1-10 each)."
        )
        try:
            raw = self.ai._call_llm(SCORING_SYSTEM, user_prompt)
            result = json.loads(raw)
            # Validate expected keys
            for key in ("relevance", "specificity", "structure", "impact"):
                if key not in result:
                    result[key] = 5
                result[key] = max(1, min(10, int(result[key])))
            result.setdefault("feedback", "")
            result.setdefault("follow_up_needed", False)
            return result
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("Failed to parse score response: %s", exc)
            return {
                "relevance": 5, "specificity": 5, "structure": 5, "impact": 5,
                "feedback": "Could not auto-score; review manually.",
                "follow_up_needed": False,
            }

    def _generate_summary(self, questions: list, responses: list,
                          scores: list, session: dict) -> dict:
        """Generate a narrative session summary via AI."""
        qa_text = []
        for i, q in enumerate(questions):
            entry = f"Q{i+1} ({q.get('type','?')}): {q.get('question','')}"
            if i < len(responses):
                entry += f"\nA: {responses[i].get('response', '(no answer)')}"
            if i < len(scores):
                s = scores[i]
                entry += (f"\nScores: rel={s.get('relevance',0)} spec={s.get('specificity',0)} "
                          f"str={s.get('structure',0)} imp={s.get('impact',0)}")
            qa_text.append(entry)

        user_prompt = (
            f"Role: {session['title']} at {session['company']}\n"
            f"Archetype: {session['archetype']}\n\n"
            f"Full session:\n" + "\n\n".join(qa_text)
        )

        try:
            raw = self.ai._call_llm(SUMMARY_SYSTEM, user_prompt)
            summary = json.loads(raw)
            summary.setdefault("overall_score", 5)
            summary["overall_score"] = max(1, min(10, round(float(summary["overall_score"]), 1)))
            return summary
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("Failed to parse session summary: %s", exc)

        # Fallback: calculate manually
        all_avgs = [self._avg_score(s) for s in scores]
        overall = round(sum(all_avgs) / len(all_avgs), 1) if all_avgs else 5.0
        return {
            "overall_score": overall,
            "strengths": ["Completed full session"],
            "weaknesses": ["Auto-summary unavailable"],
            "feedback": f"You scored an average of {overall}/10 across {len(scores)} questions.",
            "tip": "Practice structuring answers using the STAR method.",
        }

    def _load_session(self, session_id: str) -> Optional[dict]:
        """Load a session row from the database."""
        try:
            row = self.state.conn.execute(
                "SELECT * FROM interview_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            log.error("Failed to load session %s: %s", session_id, exc)
            return None

    @staticmethod
    def _avg_score(score_dict: dict) -> float:
        """Average the four scoring dimensions."""
        dims = ["relevance", "specificity", "structure", "impact"]
        vals = [score_dict.get(d, 0) for d in dims]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    @staticmethod
    def _calc_duration(start_iso: str, end_iso: str) -> int:
        """Calculate duration in minutes between two ISO timestamps."""
        try:
            fmt_a = start_iso.replace("Z", "+00:00")
            fmt_b = end_iso.replace("Z", "+00:00")
            start = datetime.fromisoformat(fmt_a)
            end = datetime.fromisoformat(fmt_b)
            return max(0, int((end - start).total_seconds() / 60))
        except Exception:
            return 0
