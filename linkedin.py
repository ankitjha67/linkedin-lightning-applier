"""
LinkedIn page interactions v2.
Browser setup, login, search, job extraction, recruiter detection,
visa sponsorship analysis, Easy Apply flow.
"""

import os
import re
import time
import random
import logging
from typing import Optional
from urllib.parse import urlencode

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.remote.webelement import WebElement
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, ElementClickInterceptedException,
    StaleElementReferenceException, ElementNotInteractableException,
    WebDriverException,
)

log = logging.getLogger("lla.linkedin")

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def human_sleep(lo: float = 1.0, hi: float = 3.0):
    time.sleep(lo + random.random() * (hi - lo))

def safe_click(driver, el: WebElement):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.3)
        el.click()
    except (ElementClickInterceptedException, ElementNotInteractableException):
        driver.execute_script("arguments[0].click();", el)

def safe_find(driver, by, val, timeout=5) -> Optional[WebElement]:
    try:
        return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, val)))
    except (TimeoutException, NoSuchElementException):
        return None

def find_button(driver, text: str, scope=None) -> Optional[WebElement]:
    root = scope or driver
    for btn in root.find_elements(By.TAG_NAME, "button"):
        try:
            if text.lower() in btn.text.strip().lower() and btn.is_enabled() and btn.is_displayed():
                return btn
        except StaleElementReferenceException:
            continue
    return None

def text_input(driver, el: WebElement, text: str):
    try:
        el.click()
        time.sleep(0.2)
        el.send_keys(Keys.CONTROL + "a")
        time.sleep(0.1)
        el.send_keys(str(text))
        time.sleep(0.2)
    except Exception:
        driver.execute_script(f"arguments[0].value = arguments[1];", el, str(text))
        el.send_keys(" " + Keys.BACKSPACE)


# ═══════════════════════════════════════════════════════════════
# BROWSER
# ═══════════════════════════════════════════════════════════════

def create_browser(cfg: dict):
    bc = cfg.get("browser", {})
    opts = uc.ChromeOptions()
    if bc.get("headless"):
        opts.add_argument("--headless=new")
    opts.add_argument(f"--window-size={bc.get('window_width',1280)},{bc.get('window_height',900)}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    ud = bc.get("user_data_dir", "")
    if ud:
        opts.add_argument(f"--user-data-dir={ud}")
    # Pin ChromeDriver to match installed Chrome version
    version_main = bc.get("chrome_version", None)
    driver = uc.Chrome(options=opts, use_subprocess=True, version_main=version_main)
    driver.implicitly_wait(5)
    return driver



# ═══════════════════════════════════════════════════════════════
# LOGIN — Robust, based on GodsScion patterns + DOM verification
# ═══════════════════════════════════════════════════════════════

def _type_into_field(driver, field_id: str, value: str, timeout: float = 5.0) -> bool:
    """Type value into field by ID. Uses Ctrl+A then type (original repo pattern)."""
    try:
        field = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, field_id))
        )
        return _fill_field(driver, field, value)
    except Exception:
        return False


def _type_into_css(driver, css_selector: str, value: str, timeout: float = 5.0) -> bool:
    """Type value into field by CSS selector. Fallback for React dynamic IDs."""
    try:
        field = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
        )
        return _fill_field(driver, field, value)
    except Exception:
        return False


def _fill_field(driver, field, value: str) -> bool:
    """Fill a field reliably with value. Tries send_keys, then ActionChains."""
    try:
        field.click()
        time.sleep(0.2)
        field.send_keys(Keys.CONTROL + "a")
        time.sleep(0.1)
        field.send_keys(value)
        time.sleep(0.3)
        actual = field.get_attribute("value") or ""
        if actual == value:
            return True
        # Retry with ActionChains
        field.clear()
        actions = ActionChains(driver)
        actions.click(field).pause(0.2)
        actions.key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).pause(0.1)
        actions.send_keys(value).perform()
        time.sleep(0.3)
        return True
    except Exception:
        return False


def is_logged_in(driver) -> bool:
    """
    Check if actually logged in by looking for DOM elements, not just URL.
    The key indicator: logged-in LinkedIn has a navigation bar with user photo.
    Logged-out LinkedIn has 'Sign in' and 'Join now' buttons.
    """
    try:
        current = driver.current_url

        # If not on LinkedIn at all, go there
        if "linkedin.com" not in current:
            driver.get("https://www.linkedin.com/feed/")
            time.sleep(4)

        # Check for LOGGED OUT indicators (these are definitive)
        logged_out_selectors = [
            "//a[contains(text(), 'Sign in')]",
            "//a[contains(text(), 'Join now')]",
            "//button[contains(text(), 'Sign in')]",
            "//a[@class='nav__button-secondary']",     # "Sign in" nav button
            "//a[@class='nav__button-primary']",        # "Join now" nav button
            "//a[contains(@href, '/login')]",
        ]
        for xpath in logged_out_selectors:
            try:
                els = driver.find_elements(By.XPATH, xpath)
                for el in els:
                    if el.is_displayed():
                        log.debug(f"Found logged-out indicator: {xpath}")
                        return False
            except Exception:
                continue

        # If on login/authwall page, definitely not logged in
        url = driver.current_url
        if any(x in url for x in ["/login", "/authwall", "/uas/login", "signin"]):
            return False

        # Check for LOGGED IN indicators
        logged_in_selectors = [
            ".global-nav__me",                          # Nav "Me" dropdown
            ".global-nav__me-photo",                    # Profile photo in nav
            "img.global-nav__me-photo",
            ".feed-identity-module",                    # Feed sidebar
            "[data-control-name='identity_welcome_message']",
            ".nav-item__profile-member-photo",
        ]
        for css in logged_in_selectors:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, css)
                if els and els[0].is_displayed():
                    log.debug(f"Found logged-in indicator: {css}")
                    return True
            except Exception:
                continue

        # Navigate to feed to force a definitive check
        if "/feed" not in url:
            driver.get("https://www.linkedin.com/feed/")
            time.sleep(4)
            url = driver.current_url

        # After navigation, check again for login redirect
        if any(x in url for x in ["/login", "/authwall", "/uas/login"]):
            return False

        # Final check: try to find the nav bar
        try:
            nav = driver.find_elements(By.CSS_SELECTOR, ".global-nav__content, .global-nav")
            if nav:
                return True
        except Exception:
            pass

        log.debug("Could not confirm login status — assuming NOT logged in.")
        return False

    except Exception as e:
        log.debug(f"is_logged_in error: {e}")
        return False


def verify_session(driver) -> bool:
    """Quick check: can we access a protected page?"""
    try:
        driver.get("https://www.linkedin.com/jobs/")
        time.sleep(3)
        # Check for login indicators on the jobs page
        sign_in = driver.find_elements(By.XPATH, "//a[contains(text(), 'Sign in')]")
        if sign_in:
            for el in sign_in:
                if el.is_displayed():
                    return False
        if any(x in driver.current_url for x in ["/login", "/authwall"]):
            return False
        return True
    except Exception:
        return False


def login(driver, cfg: dict) -> bool:
    """
    Login to LinkedIn.
    1. Check if already logged in (saved cookies from undetected_chromedriver)
    2. Navigate to /login and fill credentials
    3. Handle security challenges
    4. Fall back to manual login
    """
    creds = cfg.get("linkedin", {})
    email = str(creds.get("email", "")).strip()
    pwd = str(creds.get("password", "")).strip()

    log.info(f"Credentials: email={'YES' if email else 'EMPTY'}, password={'YES' if pwd else 'EMPTY'}")

    # Step 1: Check if already authenticated
    if is_logged_in(driver):
        log.info("Already logged in!")
        return True

    log.info("Not logged in. Starting login flow...")

    # Step 2: Navigate to login page
    driver.get("https://www.linkedin.com/login")
    time.sleep(5)
    log.info(f"Login page: {driver.current_url[:80]}")

    if not email or not pwd:
        log.error("No credentials in config.yaml! Fill in linkedin.email and linkedin.password")
        return _wait_for_manual_login(driver)

    # Step 3: Fill credentials
    try:
        # Wait for page to be fully loaded
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type=password], #password, #session_password"))
            )
            log.info("Login form detected.")
        except TimeoutException:
            log.warning("Login form not found after 10s!")

        # Email — try IDs first, then CSS selectors (for React dynamic IDs)
        email_ok = _type_into_field(driver, "username", email, 3)
        if not email_ok:
            email_ok = _type_into_field(driver, "session_key", email, 3)
        if not email_ok:
            # React pages use dynamic IDs like :r1: — find by type
            email_ok = _type_into_css(driver, 'input[type="text"]:not([type="hidden"])', email, 3)
        if not email_ok:
            email_ok = _type_into_css(driver, 'input[autocomplete="username"]', email, 3)
        log.info(f"  Email: {'✓ entered' if email_ok else '✗ FAILED'}")

        # Password — try IDs first, then CSS selectors
        pwd_ok = _type_into_field(driver, "password", pwd, 3)
        if not pwd_ok:
            pwd_ok = _type_into_field(driver, "session_password", pwd, 3)
        if not pwd_ok:
            pwd_ok = _type_into_css(driver, 'input[type="password"]', pwd, 3)
        log.info(f"  Password: {'✓ entered' if pwd_ok else '✗ FAILED'}")

        if not email_ok or not pwd_ok:
            log.error("Could not fill credentials. Page might have changed.")
            # Dump what input fields exist
            try:
                inputs_info = driver.execute_script("""
                    return Array.from(document.querySelectorAll('input')).map(i => 
                        i.id + '|' + i.name + '|' + i.type + '|' + i.placeholder
                    ).join('\\n');
                """)
                log.info(f"  Page inputs:\\n{inputs_info}")
            except Exception:
                pass
            return _wait_for_manual_login(driver)

        # Click Sign in
        time.sleep(0.5)
        clicked = False
        for xpath in [
            '//button[@type="submit" and contains(text(), "Sign in")]',
            '//button[@type="submit"]',
            '//button[contains(text(), "Sign in")]',
        ]:
            try:
                btn = driver.find_element(By.XPATH, xpath)
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    clicked = True
                    log.info(f"  Sign in: ✓ clicked")
                    break
            except NoSuchElementException:
                continue

        if not clicked:
            log.warning("  Sign in button not found. Pressing Enter...")
            ActionChains(driver).send_keys(Keys.ENTER).perform()

        # Wait for redirect
        time.sleep(6)
        post_url = driver.current_url
        log.info(f"  Post-login URL: {post_url[:80]}")

        # If we landed on feed/jobs/mynetwork — we ARE logged in. Trust the redirect.
        if any(x in post_url for x in ["/feed", "/jobs", "/mynetwork", "/messaging"]):
            log.info("🎉 Login successful!")
            return True

        # Security challenge?
        if any(x in post_url for x in ["checkpoint", "challenge", "two-step"]):
            log.warning("=" * 50)
            log.warning("SECURITY VERIFICATION REQUIRED")
            log.warning("Complete it in the browser, then wait...")
            log.warning("=" * 50)
            for i in range(60):
                try:
                    time.sleep(5)
                except KeyboardInterrupt:
                    return False
                if is_logged_in(driver):
                    log.info("Verification complete! Logged in.")
                    return True
                if i % 12 == 0 and i > 0:
                    log.info(f"  Waiting for verification... ({i*5}s)")
            log.error("Verification timeout.")

        # Still on login page = credentials wrong
        if any(x in post_url for x in ["/login", "/uas"]):
            log.error("Login FAILED — wrong email/password in config.yaml?")

    except Exception as e:
        log.error(f"Login error: {e}")

    # Step 4: Manual fallback
    return _wait_for_manual_login(driver)


def _wait_for_manual_login(driver, timeout_sec: int = 180) -> bool:
    """Wait for user to login manually. Interruptible with Ctrl+C."""
    log.warning("=" * 50)
    log.warning("PLEASE LOGIN MANUALLY in the browser window!")
    log.warning(f"Waiting up to {timeout_sec // 60} minutes...")
    log.warning("=" * 50)

    try:
        if not any(x in driver.current_url for x in ["/login", "/uas"]):
            driver.get("https://www.linkedin.com/login")
            time.sleep(2)
    except Exception:
        pass

    elapsed = 0
    while elapsed < timeout_sec:
        try:
            time.sleep(3)
            elapsed += 3
        except KeyboardInterrupt:
            log.info("Interrupted. Exiting login wait.")
            return False

        try:
            if is_logged_in(driver):
                log.info("Manual login detected! Continuing...")
                return True
        except Exception:
            pass

        if elapsed % 30 == 0 and elapsed > 0:
            log.info(f"  Still waiting... ({elapsed}s / {timeout_sec}s)")

    log.error("Login timeout.")
    return False


# ═══════════════════════════════════════════════════════════════
# SEARCH URL — CUSTOM TIME FILTERS
# ═══════════════════════════════════════════════════════════════

def _resolve_time_filter(date_posted) -> str:
    """
    Convert date_posted setting to LinkedIn's f_TPR parameter.
    Supports: "Past hour", "Past 2 hours", "Past 6 hours", "Past 12 hours",
              "Past 24 hours", "Past week", "Past month", "Any time",
              or raw seconds as int/str (e.g. 1800 for 30 min).
    """
    if isinstance(date_posted, (int, float)):
        return f"r{int(date_posted)}"

    named = {
        "past hour":      "r3600",
        "past 1 hour":    "r3600",
        "past 2 hours":   "r7200",
        "past 3 hours":   "r10800",
        "past 4 hours":   "r14400",
        "past 6 hours":   "r21600",
        "past 8 hours":   "r28800",
        "past 12 hours":  "r43200",
        "past 24 hours":  "r86400",
        "past week":      "r604800",
        "past month":     "r2592000",
        "any time":       "",
    }

    key = str(date_posted).strip().lower()
    if key in named:
        return named[key]

    # Try parsing as raw seconds
    try:
        return f"r{int(date_posted)}"
    except (ValueError, TypeError):
        return "r86400"  # Default to 24 hours


def build_search_url(cfg: dict, term: str, location: str) -> str:
    sc = cfg.get("search", {})
    exp_map = {"Internship":"1","Entry level":"2","Associate":"3","Mid-Senior level":"4","Director":"5","Executive":"6"}
    type_map = {"Full-time":"F","Part-time":"P","Contract":"C","Temporary":"T","Internship":"I"}
    site_map = {"On-site":"1","Remote":"2","Hybrid":"3"}

    params = {"keywords": term, "location": location}
    if sc.get("sort_by") == "Most recent":
        params["sortBy"] = "DD"

    tpr = _resolve_time_filter(sc.get("date_posted", "Past 24 hours"))
    if tpr:
        params["f_TPR"] = tpr

    if sc.get("easy_apply_only", True):
        params["f_AL"] = "true"

    el = sc.get("experience_level", [])
    if el: params["f_E"] = ",".join(exp_map.get(e,"") for e in el if e in exp_map)
    jt = sc.get("job_type", [])
    if jt: params["f_JT"] = ",".join(type_map.get(t,"") for t in jt if t in type_map)
    wl = sc.get("work_location", [])
    if wl: params["f_WT"] = ",".join(site_map.get(s,"") for s in wl if s in site_map)

    return f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"


def navigate_to_search(driver, url: str):
    log.debug(f"Navigating: {url[:120]}...")
    driver.get(url)
    time.sleep(4)
    safe_find(driver, By.CSS_SELECTOR,
              "ul.scaffold-layout__list-container, .jobs-search-results-list", timeout=15)
    time.sleep(2)


# ═══════════════════════════════════════════════════════════════
# JOB CARDS
# ═══════════════════════════════════════════════════════════════

def scroll_job_list(driver):
    for sel in [".scaffold-layout__list", ".jobs-search-results-list", "[class*='scaffold-layout__list']"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            for _ in range(10):
                driver.execute_script("arguments[0].scrollTop += 300;", els[0])
                time.sleep(0.4)
            driver.execute_script("arguments[0].scrollTop = 0;", els[0])
            time.sleep(0.5)
            return
    for _ in range(5):
        driver.execute_script("window.scrollBy(0, 500);")
        time.sleep(0.3)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)


def click_job_card(driver, job_id: str) -> bool:
    """
    Click a specific job card by job_id to load its details in the right pane.
    Handles LinkedIn's virtual scrolling / occlusion system.

    The left pane uses virtual scroll — cards outside the viewport are stripped
    of their inner content (<a> links gone). We must scroll the LEFT PANE CONTAINER
    (not the window) to bring the card into view, wait for re-hydration, then click.
    """
    if not job_id:
        return False

    # Step 1: Find the left pane scroll container
    list_container = None
    for sel in [".scaffold-layout__list", ".jobs-search-results-list",
                "[class*='scaffold-layout__list']"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            list_container = els[0]
            break

    # Step 2: Find the card <li> element
    card_sel = f'li[data-occludable-job-id="{job_id}"]'
    try:
        card = driver.find_element(By.CSS_SELECTOR, card_sel)
    except NoSuchElementException:
        log.warning(f"  Card not found in DOM: {job_id}")
        return False

    # Step 3: Scroll the LEFT PANE to bring this card into view
    if list_container:
        try:
            driver.execute_script("""
                var container = arguments[0];
                var card = arguments[1];
                // Calculate card position relative to the scrollable container
                var cardRect = card.getBoundingClientRect();
                var containerRect = container.getBoundingClientRect();
                // Scroll so card is centered in the container
                var scrollTarget = container.scrollTop + (cardRect.top - containerRect.top) - (containerRect.height / 3);
                container.scrollTo({top: Math.max(0, scrollTarget), behavior: 'instant'});
            """, list_container, card)
            time.sleep(1.0)  # Wait for LinkedIn to re-hydrate the card content
        except Exception as e:
            log.debug(f"  Container scroll failed: {e}")

    # Step 4: Re-find the card (it may have been replaced during re-hydration)
    try:
        card = driver.find_element(By.CSS_SELECTOR, card_sel)
    except NoSuchElementException:
        return False

    # Step 5: Click using multiple strategies
    # Strategy A: Find and click the <a> link inside (best — triggers LinkedIn properly)
    for link_sel in ['a[href*="/jobs/view/"]', 'a.job-card-list__title',
                     'a.job-card-container__link', 'a[data-control-name]', 'a']:
        try:
            link = card.find_element(By.CSS_SELECTOR, link_sel)
            if link.is_displayed():
                safe_click(driver, link)
                time.sleep(1.5)
                return True
        except (NoSuchElementException, StaleElementReferenceException,
                ElementNotInteractableException):
            continue

    # Strategy B: Click the card <li> itself via JavaScript
    try:
        driver.execute_script("arguments[0].click();", card)
        time.sleep(1.5)
        return True
    except Exception:
        pass

    # Strategy C: Use JavaScript to dispatch a full click event chain
    try:
        driver.execute_script("""
            var card = arguments[0];
            card.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
            card.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
            card.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        """, card)
        time.sleep(1.5)
        return True
    except Exception:
        pass

    log.debug(f"  All click strategies failed for job {job_id}")
    return False


def has_no_results(driver) -> bool:
    """Check if LinkedIn shows 'No matching jobs found' or similar messages."""
    no_result_indicators = [
        "No matching jobs found",
        "no results found",
        "No jobs found",
        "0 results",
        "we didn't find",
    ]
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text[:3000].lower()
        for indicator in no_result_indicators:
            if indicator.lower() in page_text:
                log.info(f"  LinkedIn says: '{indicator}'")
                return True

        # Also check for "Recommended for you" or "Similar jobs" without actual results
        # These appear when the filter returns nothing
        headers = driver.find_elements(By.CSS_SELECTOR, "h2, h3")
        for h in headers:
            txt = h.text.strip().lower()
            if any(x in txt for x in ["recommended", "similar jobs", "you might also like", "jobs you might be interested"]):
                # Check if there's a "no results" section above it
                try:
                    prev = h.find_element(By.XPATH, "./preceding-sibling::*[1]")
                    if "no" in prev.text.lower() and ("result" in prev.text.lower() or "match" in prev.text.lower()):
                        log.info(f"  No results — only showing suggested/recommended jobs")
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


def get_job_cards(driver) -> list:
    """Find actual job cards. Returns empty if LinkedIn shows 'no results'."""
    # Check for "no results" BEFORE scrolling
    if has_no_results(driver):
        return []

    scroll_job_list(driver)
    for sel in [
        "li[data-occludable-job-id]",
        "ul.scaffold-layout__list-container > li",
        "div.job-card-container",
        ".jobs-search-results__list-item",
    ]:
        cards = driver.find_elements(By.CSS_SELECTOR, sel)
        if cards:
            log.info(f"Found {len(cards)} cards via '{sel}'")
            return cards
    log.warning("No job cards found!")
    log.debug(f"  URL: {driver.current_url}")
    log.debug(f"  Links to /jobs/view/: {len(driver.find_elements(By.CSS_SELECTOR, 'a[href*=\"/jobs/view/\"]'))}")
    return []


def extract_job_info(driver, card: WebElement) -> Optional[dict]:
    try:
        link = None
        for sel in ['a[href*="/jobs/view/"]', "a.job-card-list__title", "a.job-card-container__link", "a"]:
            links = card.find_elements(By.CSS_SELECTOR, sel)
            if links:
                link = links[0]
                break
        if not link:
            return None

        title = link.text.strip().split("\n")[0].strip()
        if not title or len(title) < 2:
            return None

        job_id = card.get_attribute("data-occludable-job-id") or card.get_attribute("data-job-id") or ""
        if not job_id:
            href = link.get_attribute("href") or ""
            m = re.search(r"/jobs/view/(\d+)", href) or re.search(r"currentJobId=(\d+)", href)
            job_id = m.group(1) if m else ""

        job_url = link.get_attribute("href") or ""

        sub_el = None
        for sel in ['[class*="entity-lockup__subtitle"]', '[class*="primary-description"]']:
            subs = card.find_elements(By.CSS_SELECTOR, sel)
            if subs:
                sub_el = subs[0]
                break
        raw = sub_el.text.strip() if sub_el else ""
        dot = raw.find("·")
        company = raw[:dot].strip() if dot > 0 else raw.strip() or "Unknown"
        location = raw[dot+1:].strip() if dot > 0 else ""

        work_style = ""
        ws_match = re.search(r'\(([^)]+)\)', location)
        if ws_match:
            work_style = ws_match.group(1)
            location = location[:ws_match.start()].strip()

        # Posted time (e.g., "2 hours ago", "Just now")
        posted_time = ""
        for sel in ['[class*="listed-time"]', '[class*="time"]', 'time']:
            time_els = card.find_elements(By.CSS_SELECTOR, sel)
            if time_els:
                posted_time = time_els[0].text.strip()
                break

        applied = False
        for el in card.find_elements(By.CSS_SELECTOR, '[class*="footer"], [class*="job-state"]'):
            if "applied" in el.text.lower():
                applied = True
                break

        return {
            "title": title, "company": company, "location": location,
            "work_style": work_style, "job_id": job_id, "job_url": job_url,
            "posted_time": posted_time, "applied": applied, "link": link,
        }
    except (StaleElementReferenceException, NoSuchElementException):
        return None


# ═══════════════════════════════════════════════════════════════
# JOB DETAILS — DESCRIPTION, SALARY, RECRUITER, VISA
# ═══════════════════════════════════════════════════════════════

def get_job_description(driver) -> str:
    time.sleep(1.5)
    for sel in [".jobs-box__html-content", ".jobs-description__content",
                ".jobs-description-content__text", "#job-details", "[class*='jobs-description']"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            return els[0].text.strip()
    return ""


def get_salary_info(driver) -> str:
    """Extract salary info from the job details panel."""
    for sel in [
        '[class*="salary"]', '[class*="compensation"]',
        '[class*="job-details-jobs-unified-top-card__job-insight"]',
    ]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        for el in els:
            txt = el.text.strip()
            if any(c in txt for c in ['$', '₹', '£', '€', 'salary', 'yr', 'per year', 'per month', 'LPA', 'CTC']):
                return txt
    return ""


def extract_experience_requirement(desc: str) -> str:
    """Pull experience requirements from description text."""
    matches = re.findall(r'(\d+)\s*[+\-–]?\s*(?:to\s*\d+\s*)?year', desc.lower())
    if matches:
        years = [int(y) for y in matches if int(y) <= 15]
        if years:
            return f"{min(years)}-{max(years)} years" if min(years) != max(years) else f"{years[0]} years"
    return ""


def extract_hiring_team(driver) -> list[dict]:
    """
    Extract recruiter / hiring team from the 'Meet the hiring team' section.
    Returns list of dicts: [{name, title, profile_url}, ...]
    """
    people = []

    # Try "Meet the hiring team" section
    for sel in [
        '[class*="hiring-team"]',
        '[class*="hirer-card"]',
        '[class*="jobs-poster"]',
        '.jobs-unified-top-card__primary-description',
    ]:
        containers = driver.find_elements(By.CSS_SELECTOR, sel)
        for container in containers:
            # Look for name + title pairs
            name_els = container.find_elements(By.CSS_SELECTOR,
                'a[href*="/in/"], [class*="name"], [class*="actor-name"], strong')
            for name_el in name_els:
                name = name_el.text.strip()
                if not name or len(name) < 2 or name.lower() in ("linkedin", "member"):
                    continue

                profile_url = ""
                if name_el.tag_name == "a":
                    profile_url = name_el.get_attribute("href") or ""

                # Try to find title near the name
                title = ""
                try:
                    parent = name_el.find_element(By.XPATH, "..")
                    siblings = parent.find_elements(By.CSS_SELECTOR, "[class*='subtitle'], [class*='title'], span")
                    for sib in siblings:
                        t = sib.text.strip()
                        if t and t != name and len(t) > 2:
                            title = t
                            break
                except Exception:
                    pass

                if name and name not in [p["name"] for p in people]:
                    people.append({"name": name, "title": title, "profile_url": profile_url})

    # Also check job description text for "Contact: ..." or "Recruiter: ..." patterns
    # This catches names mentioned in the description body
    desc = get_job_description(driver)
    recruiter_patterns = [
        r'(?:recruiter|hiring manager|contact|posted by|reach out to)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)',
        r'(?:recruiter|hiring manager|contact):\s*([A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)?)',
    ]
    for pat in recruiter_patterns:
        for match in re.finditer(pat, desc, re.IGNORECASE):
            name = match.group(1).strip()
            if name and len(name) > 3 and name not in [p["name"] for p in people]:
                people.append({"name": name, "title": "from description", "profile_url": ""})

    if people:
        log.info(f"   👥 Hiring team: {', '.join(p['name'] for p in people)}")

    return people


def detect_visa_sponsorship(desc: str, cfg: dict) -> str:
    """
    Analyze job description for visa sponsorship signals.
    Returns: "yes", "no", or "unknown"
    """
    if not desc:
        return "unknown"

    filters = cfg.get("filters", {})
    desc_lower = desc.lower()

    # Check positive keywords
    for kw in filters.get("visa_positive_keywords", []):
        if kw.lower() in desc_lower:
            return "yes"

    # Check negative keywords
    for kw in filters.get("visa_negative_keywords", []):
        if kw.lower() in desc_lower:
            return "no"

    return "unknown"


# ═══════════════════════════════════════════════════════════════
# EASY APPLY
# ═══════════════════════════════════════════════════════════════

def click_easy_apply(driver) -> bool:
    """Click Easy Apply button. Scrolls details pane to top first."""
    time.sleep(0.5)

    # Scroll the job details pane back to top (hiring team extraction may have scrolled down)
    for sel in [".jobs-search__job-details--container", ".scaffold-layout__detail",
                "[class*='job-details']", "[class*='jobs-details']"]:
        try:
            pane = driver.find_element(By.CSS_SELECTOR, sel)
            driver.execute_script("arguments[0].scrollTop = 0;", pane)
            time.sleep(0.3)
            break
        except Exception:
            continue

    time.sleep(0.5)

    # Look for Easy Apply button
    btn = find_button(driver, "Easy Apply")
    if not btn:
        # Try scrolling the whole page to top
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)
        btn = find_button(driver, "Easy Apply")

    if not btn:
        return False

    safe_click(driver, btn)
    time.sleep(2)
    modal = safe_find(driver, By.CSS_SELECTOR,
                      '[class*="easy-apply-modal"], div[role="dialog"]', timeout=5)
    return modal is not None


def get_modal(driver) -> Optional[WebElement]:
    for sel in ['[class*="easy-apply-modal"]', 'div[role="dialog"][class*="artdeco-modal"]', 'div[role="dialog"]']:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            return els[0]
    return None


def process_easy_apply(driver, cfg: dict, ai=None, job_context: dict = None) -> bool:
    consecutive_errors = 0
    max_consecutive_errors = 3  # Discard after 3 pages with unfillable errors

    for page in range(15):
        time.sleep(1.2)
        modal = get_modal(driver)
        if not modal:
            log.warning(f"Modal gone at page {page}")
            return False

        answer_questions(driver, modal, cfg, ai=ai, job_context=job_context)

        # Submit?
        sub = find_button(driver, "Submit application", modal) or find_button(driver, "Submit", modal)
        if sub:
            if not cfg.get("browser", {}).get("follow_company", False):
                try:
                    fc = modal.find_element(By.CSS_SELECTOR, 'input[id*="follow"]')
                    if fc.is_selected():
                        safe_click(driver, fc)
                        time.sleep(0.2)
                except NoSuchElementException:
                    pass
            safe_click(driver, sub)
            time.sleep(3)
            for txt in ["Done", "Close"]:
                b = find_button(driver, txt)
                if b:
                    time.sleep(0.5)
                    safe_click(driver, b)
                    break
            return True

        # Next?
        nxt = find_button(driver, "Next", modal) or find_button(driver, "Review", modal) or find_button(driver, "Continue", modal)
        if nxt:
            safe_click(driver, nxt)
            time.sleep(1.5)
            errors = modal.find_elements(By.CSS_SELECTOR, '[class*="error"]')
            if errors:
                consecutive_errors += 1
                log.warning(f"  {len(errors)} errors — refilling (attempt {consecutive_errors}/{max_consecutive_errors})")

                if consecutive_errors >= max_consecutive_errors:
                    log.warning(f"  Too many unfillable errors. Discarding application.")
                    discard_application(driver)
                    return False

                answer_questions(driver, modal, cfg, ai=ai, job_context=job_context)
                time.sleep(0.5)
                retry = find_button(driver, "Next", modal) or find_button(driver, "Review", modal)
                if retry:
                    safe_click(driver, retry)
                    time.sleep(1.5)
                    # Check if errors persist after refill
                    still_errors = modal.find_elements(By.CSS_SELECTOR, '[class*="error"]')
                    if still_errors:
                        log.warning(f"  Errors persist after refill. Discarding.")
                        discard_application(driver)
                        return False
                else:
                    discard_application(driver)
                    return False
            else:
                consecutive_errors = 0  # Reset on successful page
            continue

        log.warning(f"Stuck at page {page}")
        discard_application(driver)
        return False

    discard_application(driver)
    return False


def discard_application(driver):
    try:
        d = safe_find(driver, By.CSS_SELECTOR, 'button[aria-label="Dismiss"]', 2)
        if d: safe_click(driver, d); time.sleep(1)
        dc = find_button(driver, "Discard")
        if dc: safe_click(driver, dc); time.sleep(0.5)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# QUESTION ANSWERING
# ═══════════════════════════════════════════════════════════════

def answer_questions(driver, modal: WebElement, cfg: dict, ai=None, job_context: dict = None):
    """
    Fill all form fields. Uses keyword matching first, then AI fallback.
    ai: AIAnswerer instance (or None to skip AI)
    job_context: {title, company, description} for AI context
    """
    app = cfg.get("application", {})
    personal = cfg.get("personal", {})
    qa = cfg.get("question_answers", {})
    resume_path = cfg.get("resume", {}).get("default_resume_path", "")
    jc = job_context or {}

    # Text inputs
    for inp in modal.find_elements(By.CSS_SELECTOR, "input, textarea"):
        try:
            t = inp.get_attribute("type") or "text"
            if t in ("file","hidden","checkbox","radio","submit","button"): continue
            if (inp.get_attribute("value") or "").strip(): continue
            lbl = _get_label(driver, inp, modal)
            if not lbl: continue

            # 1) Keyword matching (free, instant)
            ans = _find_answer(lbl, personal, app, qa)

            # 2) AI fallback
            if not ans and ai:
                is_textarea = (inp.tag_name.lower() == "textarea")
                if is_textarea and ("cover" in lbl or "letter" in lbl):
                    ans = ai.answer_cover_letter(jc.get("title",""), jc.get("company",""), jc.get("description",""))
                else:
                    ans = ai.answer(lbl, job_title=jc.get("title",""),
                                   company=jc.get("company",""),
                                   job_description=jc.get("description","")[:500])

            if ans:
                text_input(driver, inp, ans)
                source = "🤖" if not _find_answer(lbl, personal, app, qa) else "📝"
                log.debug(f'  {source} "{lbl}" → "{ans}"')
                time.sleep(0.2)
        except (StaleElementReferenceException, ElementNotInteractableException):
            continue

    # Selects — extract options for AI
    for sel in modal.find_elements(By.TAG_NAME, "select"):
        try:
            lbl = _get_label(driver, sel, modal)
            if not lbl or "country code" in lbl: continue

            # Get available options
            select_obj = Select(sel)
            options = [o.text.strip() for o in select_obj.options if o.text.strip() and o.text.strip() != "Select an option"]

            # 1) Keyword match
            ans = _find_answer(lbl, personal, app, qa)

            # 2) AI fallback with options
            if not ans and ai and options:
                ans = ai.answer(lbl, options=options,
                               job_title=jc.get("title",""),
                               company=jc.get("company",""))

            if ans:
                _pick_option(sel, ans)
                source = "🤖" if not _find_answer(lbl, personal, app, qa) else "📝"
                log.debug(f'  {source} [sel] "{lbl}" → "{ans}"')
                time.sleep(0.2)
        except Exception: continue

    # Radio buttons — extract options for AI
    for fs in modal.find_elements(By.TAG_NAME, "fieldset"):
        try:
            leg = fs.find_elements(By.CSS_SELECTOR, "legend span, legend, [class*='label']")
            legend = leg[0].text.strip() if leg else ""
            if not legend: continue

            # Get available options
            radio_labels = [l.text.strip() for l in fs.find_elements(By.TAG_NAME, "label") if l.text.strip()]

            # 1) Keyword match
            ans = _find_answer(legend.lower(), personal, app, qa)

            # 2) AI fallback with options
            if not ans and ai and radio_labels:
                ans = ai.answer(legend, options=radio_labels,
                               job_title=jc.get("title",""),
                               company=jc.get("company",""))

            if ans:
                for label in fs.find_elements(By.TAG_NAME, "label"):
                    if ans.lower() in label.text.strip().lower():
                        radios = label.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                        safe_click(driver, radios[0] if radios else label)
                        source = "🤖" if not _find_answer(legend.lower(), personal, app, qa) else "📝"
                        log.debug(f'  {source} [radio] "{legend}" → "{ans}"')
                        time.sleep(0.15)
                        break
        except Exception: continue

    # Checkboxes (acknowledge/agree — no AI needed)
    for cb in modal.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
        try:
            if cb.is_selected(): continue
            p = cb.find_element(By.XPATH, "..")
            txt = p.text.lower()
            if any(w in txt for w in ["acknowledg","agree","certif","confirm","consent"]):
                safe_click(driver, cb)
                time.sleep(0.1)
        except Exception: continue

    # Resume upload
    if resume_path and os.path.exists(resume_path):
        for fi in modal.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            try:
                fi.send_keys(os.path.abspath(resume_path))
                time.sleep(1)
            except Exception: continue


def _get_label(driver, el, scope) -> str:
    lbl = el.get_attribute("aria-label")
    if lbl and lbl.strip(): return lbl.strip().lower()
    eid = el.get_attribute("id")
    if eid:
        labels = scope.find_elements(By.CSS_SELECTOR, f'label[for="{eid}"]')
        if labels: return labels[0].text.strip().lower()
    try:
        parent = el.find_element(By.XPATH, "./ancestor::*[contains(@class,'form-element')][1]")
        le = parent.find_elements(By.CSS_SELECTOR, "label span, label, [class*='__label']")
        if le: return le[0].text.strip().lower()
    except NoSuchElementException: pass
    try:
        pl = el.find_element(By.XPATH, "./ancestor::label[1]")
        return pl.text.strip().lower()
    except NoSuchElementException: pass
    return ""


def _find_answer(l: str, personal: dict, app: dict, qa: dict) -> str:
    if "first name" in l: return personal.get("first_name","")
    if "last name" in l: return personal.get("last_name","")
    if "full name" in l or "your name" in l: return personal.get("full_name","")
    if "email" in l: return personal.get("email","")
    if "phone" in l or "mobile" in l or "contact number" in l: return personal.get("phone","")
    if "city" in l and "country" not in l: return personal.get("city","")
    if "state" in l or "province" in l: return personal.get("state","")
    if "zip" in l or "postal" in l: return personal.get("zip_code","")
    if "country" in l: return personal.get("country","")
    if "headline" in l: return personal.get("linkedin_headline","")
    if "salary" in l or "compensation" in l or "pay" in l or "expected" in l: return app.get("desired_salary","")
    if "notice" in l: return app.get("notice_period_days","")
    if "relocat" in l: return app.get("willing_to_relocate","")
    if "authorized" in l or "legally" in l or "eligible" in l or "right to work" in l: return app.get("authorized_to_work","")
    if "sponsorship" in l or "visa" in l or "work permit" in l: return app.get("require_visa","")
    if "experience" in l and ("year" in l or "many" in l): return str(app.get("years_of_experience",""))
    if "ctc" in l or "current salary" in l or "current comp" in l: return app.get("current_ctc","")
    if any(w in l for w in ["hear","find","learn","come across"]) and any(w in l for w in ["job","position","role","opportunity"]): return "LinkedIn"
    for k, v in qa.items():
        if k.lower() in l: return str(v)
    if re.match(r"^(do you|are you|have you|will you|can you|would you|were you|is your)", l): return "Yes"
    return ""


def _pick_option(sel_el, text):
    sel = Select(sel_el)
    tl = text.lower()
    for opt in sel.options:
        if opt.text.strip().lower() == tl:
            sel.select_by_visible_text(opt.text.strip()); return
    for opt in sel.options:
        if tl in opt.text.strip().lower():
            sel.select_by_visible_text(opt.text.strip()); return
    for opt in sel.options:
        if opt.text.strip().lower() == "yes":
            sel.select_by_visible_text(opt.text.strip()); return
