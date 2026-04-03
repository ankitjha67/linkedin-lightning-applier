"""
LinkedIn Activity Simulation.

Between apply cycles: view random profiles, like posts, scroll the feed,
react to articles. Makes the account look like a real human browsing,
not a bot that only visits /jobs/.
"""

import logging
import random
import time

log = logging.getLogger("lla.activity_sim")


def simulate_activity(driver, cfg: dict):
    """
    Run simulated human activity on LinkedIn.
    Call between apply cycles to make account look natural.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.common.exceptions import (
        NoSuchElementException, ElementClickInterceptedException,
        StaleElementReferenceException, ElementNotInteractableException,
    )

    sim_cfg = cfg.get("activity_simulation", {})
    if not sim_cfg.get("enabled", False):
        return

    actions_count = sim_cfg.get("actions_per_cycle", 5)
    if isinstance(actions_count, str) and "-" in actions_count:
        lo, hi = actions_count.split("-")
        actions_count = random.randint(int(lo), int(hi))

    actions = []
    if sim_cfg.get("scroll_feed", True):
        actions.append(_scroll_feed)
    if sim_cfg.get("like_posts", True):
        actions.append(_like_random_post)
    if sim_cfg.get("view_profiles", True):
        actions.append(_view_random_profile)

    if not actions:
        return

    log.info(f"🧑 Simulating {actions_count} human activities...")

    # Save current URL
    original_url = driver.current_url

    try:
        for i in range(actions_count):
            action = random.choice(actions)
            try:
                action(driver)
            except Exception as e:
                log.debug(f"  Activity action failed: {e}")

            # Random delay between actions
            time.sleep(random.uniform(3, 10))

    except Exception as e:
        log.warning(f"Activity simulation error: {e}")
    finally:
        # Return to jobs page
        try:
            driver.get(original_url if "linkedin.com" in original_url
                       else "https://www.linkedin.com/jobs/")
            time.sleep(3)
        except Exception:
            pass


def _scroll_feed(driver):
    """Visit and scroll through the LinkedIn feed."""
    from selenium.webdriver.common.by import By

    log.debug("  📜 Scrolling feed...")
    driver.get("https://www.linkedin.com/feed/")
    time.sleep(random.uniform(3, 5))

    # Scroll down randomly
    scroll_count = random.randint(3, 8)
    for _ in range(scroll_count):
        scroll_amount = random.randint(300, 700)
        driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
        time.sleep(random.uniform(1.5, 4))

    # Sometimes scroll back up
    if random.random() < 0.3:
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(random.uniform(1, 2))


def _like_random_post(driver):
    """Like a random post in the feed."""
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import ElementClickInterceptedException

    log.debug("  👍 Liking a post...")

    # Make sure we're on the feed
    if "/feed" not in driver.current_url:
        driver.get("https://www.linkedin.com/feed/")
        time.sleep(random.uniform(3, 5))

    # Scroll a bit first
    driver.execute_script(f"window.scrollBy(0, {random.randint(200, 800)});")
    time.sleep(random.uniform(1, 3))

    try:
        # Find like buttons (not already liked)
        like_buttons = driver.find_elements(By.CSS_SELECTOR,
            'button[aria-label*="Like"], button.react-button__trigger, '
            'button[aria-pressed="false"][class*="react"]')

        unliked = [b for b in like_buttons if b.is_displayed() and
                   b.get_attribute("aria-pressed") != "true"]

        if unliked:
            btn = random.choice(unliked[:5])
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(random.uniform(0.5, 1.5))
            try:
                btn.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", btn)
            log.debug("    ✓ Liked a post")
            time.sleep(random.uniform(1, 3))
    except Exception as e:
        log.debug(f"    Like failed: {e}")


def _view_random_profile(driver):
    """View a random profile from feed or network suggestions."""
    from selenium.webdriver.common.by import By

    log.debug("  👤 Viewing a profile...")

    # Try network page for profile suggestions
    if random.random() < 0.5:
        driver.get("https://www.linkedin.com/mynetwork/")
    else:
        driver.get("https://www.linkedin.com/feed/")
    time.sleep(random.uniform(3, 5))

    # Scroll a bit
    driver.execute_script(f"window.scrollBy(0, {random.randint(200, 600)});")
    time.sleep(random.uniform(1, 2))

    try:
        # Find profile links
        profile_links = driver.find_elements(By.CSS_SELECTOR,
            'a[href*="/in/"]')

        # Filter to visible, non-tiny links
        visible = [l for l in profile_links if l.is_displayed() and
                   l.text.strip() and len(l.text.strip()) > 2]

        if visible:
            link = random.choice(visible[:10])
            href = link.get_attribute("href") or ""
            if "/in/" in href:
                driver.get(href)
                time.sleep(random.uniform(3, 7))

                # Scroll through the profile
                for _ in range(random.randint(2, 5)):
                    driver.execute_script(f"window.scrollBy(0, {random.randint(200, 500)});")
                    time.sleep(random.uniform(1, 3))

                log.debug(f"    ✓ Viewed profile: {href.split('/in/')[-1].split('/')[0]}")
    except Exception as e:
        log.debug(f"    Profile view failed: {e}")
