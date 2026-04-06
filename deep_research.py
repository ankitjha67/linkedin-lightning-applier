"""
Deep Researcher — 6-Axis Deep Company Research.

Before or after applying, generates a comprehensive research report
covering six axes of analysis for a target company and role:

  1. AI/Tech Strategy — products, tech stack, engineering blog, papers
  2. Recent Moves — hires, acquisitions, partnerships, launches, funding
  3. Engineering Culture — deploy cadence, languages, remote policy, reviews
  4. Probable Challenges — scaling, reliability, cost, migrations, pain points
  5. Competitors & Differentiation — main competitors, moat, positioning
  6. Candidate Angle — unique value you bring, relevant projects, story to tell

Each axis is a separate AI call with a focused prompt, using the candidate's
CV as context. Results are stored in the deep_research table.
"""

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("lla.deep_research")


class DeepResearcher:
    """6-axis deep company and role research using AI."""

    def __init__(self, ai, cfg: dict, state):
        self.ai = ai
        self.cfg = cfg
        self.state = state
        dr_cfg = cfg.get("deep_research", {})
        self.enabled = dr_cfg.get("enabled", False)
        self.max_description_chars = dr_cfg.get("max_description_chars", 3000)
        self.cache_days = dr_cfg.get("cache_days", 14)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def research(self, job_id: str, title: str, company: str,
                 description: str) -> dict:
        """
        Generate a 6-axis deep research report for a job/company.

        Returns a dict with keys for each axis plus metadata.
        """
        if not self.enabled:
            log.debug("DeepResearcher disabled, skipping research")
            return {}

        if not self.ai or not self.ai.enabled:
            log.warning("AI not available, cannot perform deep research")
            return {}

        # Check for cached research
        existing = self.get_research(job_id)
        if existing:
            log.info(f"Returning cached research for {job_id}")
            return existing

        log.info(f"Starting deep research: {title} @ {company} (job_id={job_id})...")

        desc = description[:self.max_description_chars] if description else ""
        cv_text = getattr(self.ai, "profile_context", "")

        # Generate each axis via dedicated AI call
        ai_strategy = self._research_ai_strategy(company, title, desc, cv_text)
        recent_moves = self._research_recent_moves(company, title, desc, cv_text)
        eng_culture = self._research_eng_culture(company, title, desc, cv_text)
        challenges = self._research_challenges(company, title, desc, cv_text)
        competitors = self._research_competitors(company, title, desc, cv_text)
        candidate_angle = self._research_candidate_angle(
            company, title, desc, cv_text
        )

        # Assemble full report
        full_report = self._format_full_report(
            company, title, ai_strategy, recent_moves, eng_culture,
            challenges, competitors, candidate_angle
        )

        result = {
            "job_id": job_id,
            "company": company,
            "title": title,
            "ai_strategy": ai_strategy,
            "recent_moves": recent_moves,
            "eng_culture": eng_culture,
            "challenges": challenges,
            "competitors": competitors,
            "candidate_angle": candidate_angle,
            "full_report": full_report,
        }

        # Persist
        self._save_research(result)
        log.info(f"Deep research complete for {title} @ {company}")
        return result

    def get_research(self, job_id: str) -> Optional[dict]:
        """Retrieve saved research from the database."""
        try:
            row = self.state.conn.execute(
                "SELECT * FROM deep_research WHERE job_id = ?",
                (job_id,)
            ).fetchone()
            if not row:
                return None
            return {
                "job_id": row["job_id"],
                "company": row["company"],
                "title": row["title"],
                "ai_strategy": row["ai_strategy"],
                "recent_moves": row["recent_moves"],
                "eng_culture": row["eng_culture"],
                "challenges": row["challenges"],
                "competitors": row["competitors"],
                "candidate_angle": row["candidate_angle"],
                "full_report": row["full_report"],
                "researched_at": row["researched_at"],
            }
        except Exception as e:
            log.error(f"Failed to retrieve research for {job_id}: {e}")
            return None

    def generate_report(self, job_id: str) -> str:
        """
        Format saved research as readable text.

        Retrieves from database and returns the full_report field.
        If no research exists, returns empty string.
        """
        research = self.get_research(job_id)
        if not research:
            log.debug(f"No research found for {job_id}")
            return ""
        return research.get("full_report", "")

    # ------------------------------------------------------------------
    # Axis 1: AI / Tech Strategy
    # ------------------------------------------------------------------

    def _research_ai_strategy(self, company: str, title: str,
                              desc: str, cv_text: str) -> str:
        """Axis 1 -- AI/Tech Strategy: products, tech stack, blog, papers."""
        system = (
            "You are a technology strategy analyst. Research the company's "
            "AI and technology strategy. Cover:\n\n"
            "1. PRODUCTS & FEATURES USING AI: What products or features does this "
            "company offer that leverage AI/ML? Be specific about use cases.\n"
            "2. TECH STACK: Based on the job description, job listings, and known "
            "information, what technologies does this company use? Languages, "
            "frameworks, cloud providers, databases, ML tools.\n"
            "3. ENGINEERING BLOG & PAPERS: Does this company have an engineering blog? "
            "Any notable technical papers, open-source contributions, or conference talks?\n"
            "4. TECH DIRECTION: Based on recent job listings and public information, "
            "where is their technology heading? Any strategic bets?\n\n"
            "Be specific and factual. Clearly mark anything that is inference vs known fact.\n"
            "If you have limited information about this company, say so honestly."
        )
        user = (
            f"Company: {company}\n"
            f"Role: {title}\n\n"
            f"JOB DESCRIPTION:\n{desc}\n\n"
            f"CANDIDATE CONTEXT (for relevance):\n{cv_text[:1500]}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Axis 1 (AI Strategy) failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Axis 2: Recent Moves (6 months)
    # ------------------------------------------------------------------

    def _research_recent_moves(self, company: str, title: str,
                               desc: str, cv_text: str) -> str:
        """Axis 2 -- Recent Moves: hires, acquisitions, partnerships, launches, funding."""
        system = (
            "You are a business intelligence analyst. Based on your knowledge, "
            "summarize the company's recent moves (approximately the last 6 months). "
            "Cover:\n\n"
            "1. KEY HIRES: Any notable executive or leadership hires? New team formations?\n"
            "2. ACQUISITIONS & PARTNERSHIPS: Any companies acquired, strategic partnerships "
            "announced, or major vendor changes?\n"
            "3. PRODUCT LAUNCHES: New products, major feature releases, or pivots?\n"
            "4. FUNDING & FINANCIALS: Recent funding rounds, IPO plans, revenue milestones, "
            "or layoffs/restructuring?\n"
            "5. WHAT THIS MEANS FOR THE ROLE: How do these moves create context or urgency "
            "for the position being hired?\n\n"
            "Be honest about uncertainty. If the company is private or you lack recent data, "
            "note what is inferrable from the job description and general industry knowledge."
        )
        user = (
            f"Company: {company}\n"
            f"Role: {title}\n\n"
            f"JOB DESCRIPTION:\n{desc}\n\n"
            f"CANDIDATE CONTEXT:\n{cv_text[:1000]}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Axis 2 (Recent Moves) failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Axis 3: Engineering Culture
    # ------------------------------------------------------------------

    def _research_eng_culture(self, company: str, title: str,
                              desc: str, cv_text: str) -> str:
        """Axis 3 -- Engineering Culture: deploy cadence, languages, remote, reviews."""
        system = (
            "You are an engineering culture analyst. Based on the job description "
            "and your knowledge of this company, analyze their engineering culture:\n\n"
            "1. DEPLOYMENT CADENCE: How often do they ship? CI/CD maturity, "
            "feature flags, canary releases, or waterfall?\n"
            "2. LANGUAGES & FRAMEWORKS: Primary tech stack, any legacy systems, "
            "polyglot vs monolingual approach?\n"
            "3. REMOTE POLICY: Remote-first, hybrid, in-office? Based on JD signals.\n"
            "4. TEAM STRUCTURE: Squad-based, functional teams, matrix? "
            "How are engineering teams organized?\n"
            "5. ENGINEERING REPUTATION: Glassdoor engineering reviews sentiment, "
            "known pros/cons, work-life balance signals.\n"
            "6. GROWTH & LEARNING: Conferences, training budget, internal mobility, "
            "open-source time, hackathons?\n\n"
            "Base your analysis on JD language, known company info, and industry patterns. "
            "Flag when you are inferring vs stating known facts."
        )
        user = (
            f"Company: {company}\n"
            f"Role: {title}\n\n"
            f"JOB DESCRIPTION:\n{desc}\n\n"
            f"CANDIDATE CONTEXT:\n{cv_text[:1000]}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Axis 3 (Eng Culture) failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Axis 4: Probable Challenges
    # ------------------------------------------------------------------

    def _research_challenges(self, company: str, title: str,
                             desc: str, cv_text: str) -> str:
        """Axis 4 -- Probable Challenges: scaling, reliability, cost, migrations, pain."""
        system = (
            "You are a senior engineering consultant. Based on the job description "
            "and company context, identify the probable technical and organizational "
            "challenges this team faces:\n\n"
            "1. SCALING CHALLENGES: What scaling problems are they likely dealing with? "
            "User growth, data volume, transaction throughput?\n"
            "2. RELIABILITY & INCIDENTS: What reliability concerns does the JD hint at? "
            "SLA requirements, on-call mentions, incident response?\n"
            "3. COST OPTIMIZATION: Any signals about cloud cost concerns, "
            "efficiency initiatives, or infrastructure rationalization?\n"
            "4. MIGRATIONS & TECH DEBT: Legacy system migrations, language/framework "
            "transitions, monolith-to-microservice moves?\n"
            "5. ORGANIZATIONAL PAIN: Hiring challenges, team scaling, cross-team "
            "coordination issues, process gaps?\n"
            "6. WHY THEY ARE HIRING: Based on these challenges, what is the most likely "
            "reason this specific role exists right now?\n\n"
            "Read between the lines of the JD. Phrases like 'fast-paced', 'greenfield', "
            "'modernize', 'scale' all signal specific challenges."
        )
        user = (
            f"Company: {company}\n"
            f"Role: {title}\n\n"
            f"JOB DESCRIPTION:\n{desc}\n\n"
            f"CANDIDATE CONTEXT:\n{cv_text[:1000]}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Axis 4 (Challenges) failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Axis 5: Competitors & Differentiation
    # ------------------------------------------------------------------

    def _research_competitors(self, company: str, title: str,
                              desc: str, cv_text: str) -> str:
        """Axis 5 -- Competitors & Differentiation: competitors, moat, positioning."""
        system = (
            "You are a competitive intelligence analyst. Analyze this company's "
            "competitive landscape:\n\n"
            "1. MAIN COMPETITORS: Who are the top 3-5 direct competitors? "
            "Include both established players and emerging threats.\n"
            "2. COMPETITIVE MOAT: What is this company's defensible advantage? "
            "Technology, data, network effects, brand, regulatory, talent?\n"
            "3. MARKET POSITIONING: Where does this company sit in the market? "
            "Leader, challenger, niche player, disruptor?\n"
            "4. RECENT COMPETITIVE MOVES: Any recent competitive dynamics? "
            "Price wars, feature parity races, talent poaching, market expansion?\n"
            "5. INTERVIEW RELEVANCE: How can the candidate reference competitive "
            "dynamics in interviews to show industry awareness?\n\n"
            "Be specific about company names and market segments."
        )
        user = (
            f"Company: {company}\n"
            f"Role: {title}\n\n"
            f"JOB DESCRIPTION:\n{desc}\n\n"
            f"CANDIDATE CONTEXT:\n{cv_text[:1000]}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Axis 5 (Competitors) failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Axis 6: Candidate Angle
    # ------------------------------------------------------------------

    def _research_candidate_angle(self, company: str, title: str,
                                  desc: str, cv_text: str) -> str:
        """Axis 6 -- Candidate Angle: unique value, relevant projects, story to tell."""
        system = (
            "You are a career strategist. Given the company research context and "
            "the candidate's CV, identify the candidate's unique angle for this role:\n\n"
            "1. UNIQUE VALUE PROPOSITION: What specific combination of skills and "
            "experience makes this candidate uniquely suited? Not generic strengths — "
            "specific intersections between their background and this company's needs.\n"
            "2. RELEVANT PROJECTS: Which 2-3 projects from the CV are most directly "
            "relevant to this company's challenges? For each, explain the connection.\n"
            "3. STORY TO TELL: What narrative should the candidate craft for 'why this "
            "company?' that goes beyond 'I like your mission'? Reference specific "
            "company initiatives, products, or challenges.\n"
            "4. KNOWLEDGE GAPS TO FILL: What should the candidate research or learn "
            "before the interview to appear deeply informed?\n"
            "5. CONVERSATION STARTERS: 3-5 insightful questions the candidate could "
            "ask that demonstrate genuine interest and deep research.\n\n"
            "Be specific. Reference actual CV items and actual company details."
        )
        user = (
            f"Company: {company}\n"
            f"Role: {title}\n\n"
            f"JOB DESCRIPTION:\n{desc}\n\n"
            f"FULL CANDIDATE CV:\n{cv_text}"
        )
        try:
            return self.ai._call_llm(system, user)
        except Exception as e:
            log.warning(f"Axis 6 (Candidate Angle) failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Report formatting
    # ------------------------------------------------------------------

    def _format_full_report(self, company: str, title: str,
                            ai_strategy: str, recent_moves: str,
                            eng_culture: str, challenges: str,
                            competitors: str, candidate_angle: str) -> str:
        """Assemble all axes into a single formatted report."""
        sep = "=" * 60
        section_sep = "-" * 40
        return (
            f"{sep}\n"
            f"DEEP RESEARCH REPORT: {title} @ {company}\n"
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"{sep}\n\n"
            f"AXIS 1: AI / TECH STRATEGY\n"
            f"{section_sep}\n"
            f"{ai_strategy}\n\n"
            f"AXIS 2: RECENT MOVES (6 MONTHS)\n"
            f"{section_sep}\n"
            f"{recent_moves}\n\n"
            f"AXIS 3: ENGINEERING CULTURE\n"
            f"{section_sep}\n"
            f"{eng_culture}\n\n"
            f"AXIS 4: PROBABLE CHALLENGES\n"
            f"{section_sep}\n"
            f"{challenges}\n\n"
            f"AXIS 5: COMPETITORS & DIFFERENTIATION\n"
            f"{section_sep}\n"
            f"{competitors}\n\n"
            f"AXIS 6: CANDIDATE ANGLE\n"
            f"{section_sep}\n"
            f"{candidate_angle}\n\n"
            f"{sep}\n"
            f"END OF DEEP RESEARCH REPORT\n"
            f"{sep}\n"
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_research(self, result: dict) -> None:
        """Persist research to deep_research table."""
        try:
            self.state.conn.execute(
                """INSERT OR REPLACE INTO deep_research
                   (job_id, company, title, ai_strategy, recent_moves,
                    eng_culture, challenges, competitors, candidate_angle,
                    full_report, researched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result["job_id"], result["company"], result["title"],
                    result["ai_strategy"], result["recent_moves"],
                    result["eng_culture"], result["challenges"],
                    result["competitors"], result["candidate_angle"],
                    result["full_report"],
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            )
            self.state.conn.commit()
            log.debug(f"Saved deep research for job_id={result['job_id']}")
        except Exception as e:
            log.error(f"Failed to save research: {e}")
