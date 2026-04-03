"""
LinkedIn Network Leverage.

Before applying, checks if you have 1st/2nd-degree connections at the company.
Surfaces referral opportunities. Can send connection requests to hiring managers.
Referred candidates are 5-10x more likely to get hired.
"""

import logging
import random
import time
from typing import Optional

log = logging.getLogger("lla.network")


class NetworkLeverage:
    """Discover and leverage LinkedIn connections at target companies."""

    def __init__(self, cfg: dict, state):
        self.cfg = cfg
        self.state = state
        nl_cfg = cfg.get("network_leverage", {})
        self.enabled = nl_cfg.get("enabled", False)
        self.check_connections = nl_cfg.get("check_connections", True)
        self.send_connection_requests = nl_cfg.get("send_connection_requests", False)
        self.max_requests_per_day = nl_cfg.get("max_requests_per_day", 10)
        self.request_note_enabled = nl_cfg.get("request_note_enabled", True)
        self._requests_today = 0
        self._today = ""

    def check_company_network(self, driver, company: str, job_id: str = "",
                               job_title: str = "") -> list[dict]:
        """
        Check if you have connections at a company.

        Returns list of connections found: [{name, title, url, degree}]
        """
        if not self.enabled or not self.check_connections:
            return []

        # Check cache first
        cached = self.state.get_company_connections(company)
        if cached:
            return cached

        from selenium.webdriver.common.by import By

        connections = []
        original_url = driver.current_url

        try:
            # Search for company employees in your network
            company_clean = company.replace("&", "and").replace(",", "")
            search_url = (
                f"https://www.linkedin.com/search/results/people/"
                f"?keywords={company_clean}&network=%5B%22F%22%2C%22S%22%5D"
                f"&origin=FACETED_SEARCH"
            )
            # network=["F","S"] = 1st and 2nd degree connections

            driver.get(search_url)
            time.sleep(random.uniform(3, 5))

            # Extract connection cards
            cards = driver.find_elements(By.CSS_SELECTOR,
                'li.reusable-search__result-container, '
                'div[data-chameleon-result-urn]')

            for card in cards[:10]:  # Limit to avoid slowness
                try:
                    conn = self._extract_connection(card)
                    if conn and conn["name"]:
                        # Determine degree
                        degree_text = card.text.lower()
                        if "1st" in degree_text:
                            conn["degree"] = 1
                        elif "2nd" in degree_text:
                            conn["degree"] = 2
                        else:
                            conn["degree"] = 3

                        connections.append(conn)

                        # Save to DB
                        self.state.save_company_connection(
                            company=company,
                            name=conn["name"],
                            title=conn.get("title", ""),
                            url=conn.get("url", ""),
                            degree=conn["degree"],
                            job_id=job_id,
                        )
                except Exception:
                    continue

            if connections:
                first_degree = [c for c in connections if c["degree"] == 1]
                second_degree = [c for c in connections if c["degree"] == 2]
                log.info(f"   Network: {len(first_degree)} 1st-degree, "
                        f"{len(second_degree)} 2nd-degree at {company}")

        except Exception as e:
            log.debug(f"Network check failed for {company}: {e}")
        finally:
            try:
                driver.get(original_url)
                time.sleep(2)
            except Exception:
                pass

        return connections

    def _extract_connection(self, card) -> Optional[dict]:
        """Extract connection info from a search result card."""
        from selenium.webdriver.common.by import By

        conn = {"name": "", "title": "", "url": "", "degree": 0}

        # Name and profile URL
        for sel in ['a.app-aware-link span[aria-hidden="true"]',
                    'span.entity-result__title-text a span',
                    'a[href*="/in/"] span']:
            try:
                el = card.find_element(By.CSS_SELECTOR, sel)
                if el.text.strip() and len(el.text.strip()) > 1:
                    conn["name"] = el.text.strip()
                    # Get profile URL from parent <a>
                    try:
                        parent_a = el.find_element(By.XPATH, "./ancestor::a[contains(@href,'/in/')]")
                        conn["url"] = parent_a.get_attribute("href") or ""
                    except Exception:
                        pass
                    break
            except Exception:
                continue

        # Title/headline
        for sel in ['div.entity-result__primary-subtitle',
                    'p.entity-result__summary', 'div.linked-area p']:
            try:
                el = card.find_element(By.CSS_SELECTOR, sel)
                if el.text.strip():
                    conn["title"] = el.text.strip()[:100]
                    break
            except Exception:
                continue

        return conn if conn["name"] else None

    def send_connection_request(self, driver, profile_url: str,
                                 name: str = "", note: str = "") -> bool:
        """Send a LinkedIn connection request with optional note."""
        if not self.enabled or not self.send_connection_requests:
            return False

        # Rate limit
        from datetime import date
        today = date.today().isoformat()
        if self._today != today:
            self._today = today
            self._requests_today = 0
        if self._requests_today >= self.max_requests_per_day:
            return False

        from selenium.webdriver.common.by import By
        original_url = driver.current_url

        try:
            driver.get(profile_url)
            time.sleep(random.uniform(3, 5))

            # Find "Connect" button
            connect_btn = None
            for sel in [
                'button[aria-label*="Connect"]',
                'button[aria-label*="connect"]',
                'button.pvs-profile-actions__action',
            ]:
                btns = driver.find_elements(By.CSS_SELECTOR, sel)
                for btn in btns:
                    text = btn.text.lower()
                    label = (btn.get_attribute("aria-label") or "").lower()
                    if "connect" in text or "connect" in label:
                        if btn.is_displayed() and btn.is_enabled():
                            connect_btn = btn
                            break
                if connect_btn:
                    break

            # Also check "More" dropdown for Connect option
            if not connect_btn:
                more_btns = driver.find_elements(By.CSS_SELECTOR,
                    'button[aria-label="More actions"], button[class*="artdeco-dropdown"]')
                for more in more_btns:
                    if more.is_displayed():
                        more.click()
                        time.sleep(1)
                        items = driver.find_elements(By.CSS_SELECTOR,
                            'div[role="menuitem"], li[role="menuitem"]')
                        for item in items:
                            if "connect" in item.text.lower():
                                connect_btn = item
                                break
                        break

            if not connect_btn:
                log.debug(f"No Connect button found for {name}")
                return False

            connect_btn.click()
            time.sleep(random.uniform(1.5, 3))

            # Add note if enabled
            if self.request_note_enabled and note:
                add_note_btn = None
                for btn in driver.find_elements(By.TAG_NAME, "button"):
                    if "add a note" in btn.text.lower() and btn.is_displayed():
                        add_note_btn = btn
                        break

                if add_note_btn:
                    add_note_btn.click()
                    time.sleep(1)

                    # Type the note
                    textarea = driver.find_elements(By.CSS_SELECTOR,
                        'textarea[name="message"], textarea#custom-message')
                    if textarea:
                        textarea[0].clear()
                        textarea[0].send_keys(note[:300])  # LinkedIn limits to 300 chars
                        time.sleep(0.5)

            # Click Send
            send_btn = None
            for btn in driver.find_elements(By.TAG_NAME, "button"):
                text = btn.text.lower()
                if ("send" in text or "connect" in text) and btn.is_displayed():
                    send_btn = btn
                    break

            if send_btn:
                send_btn.click()
                time.sleep(2)
                self._requests_today += 1
                log.info(f"   Connection request sent to {name}")
                return True

        except Exception as e:
            log.debug(f"Connection request failed: {e}")
        finally:
            try:
                driver.get(original_url)
                time.sleep(2)
            except Exception:
                pass

        return False

    def generate_connection_note(self, name: str, company: str,
                                  job_title: str, ai=None) -> str:
        """Generate a brief connection request note (max 300 chars)."""
        first_name = name.split()[0] if name else "there"

        if not ai or not ai.enabled:
            return (
                f"Hi {first_name}, I recently applied for the {job_title} "
                f"role at {company} and would love to connect. "
                f"Looking forward to learning more about the team!"
            )[:300]

        system = f"""Write a very brief LinkedIn connection request note (max 250 characters).
Be genuine, mention the specific role. No AI-sounding language.

{ai.profile_context}"""

        user = f"Connection note to {name} at {company} about the {job_title} role."

        try:
            old_max = ai.max_tokens
            ai.max_tokens = 100
            result = ai._call_llm(system, user)
            ai.max_tokens = old_max
            return (result or "")[:300]
        except Exception:
            return f"Hi {first_name}, applied for {job_title} at {company}. Would love to connect!"[:300]

    def get_referral_opportunities(self) -> list[dict]:
        """Get jobs where you have 1st-degree connections (best referral chances)."""
        rows = self.state.conn.execute("""
            SELECT cc.company, cc.connection_name, cc.connection_title,
                   cc.connection_url, cc.degree, aj.title as job_title, aj.job_id
            FROM company_connections cc
            JOIN applied_jobs aj ON cc.company = aj.company
            WHERE cc.degree = 1 AND cc.referral_requested = 0
            ORDER BY aj.applied_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
