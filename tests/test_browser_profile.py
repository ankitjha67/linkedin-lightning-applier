"""The Chrome profile the bot signs in through — browser_profile.py.

`browser.user_data_dir` is the better way to authenticate an unattended bot:
sign in once by hand, and no LinkedIn password ever goes in config.yaml. The
documentation always said to point it at a directory — but nothing created that
directory and nothing checked whether the profile behind it held a session, so
an empty path passed every check and then dead-ended at the login page three
minutes at a time, looking healthy to the watchdog throughout.

A profile is verified by reading cookie NAMES, never values: `li_at`'s presence
and expiry is all that is needed and all that is read. The cookie database is
opened read-only and immutable so it is safe even while Chrome holds it.
"""

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import browser_profile as bp  # noqa: E402
from tools import autopilot as ap  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def chrome_us(when: datetime) -> int:
    return int((when - CHROME_EPOCH).total_seconds() * 1_000_000)


def make_profile(initialised=True, cookies=None, locked=False) -> Path:
    """A directory shaped like a Chrome profile.

    `cookies` is [(host, name, expiry_datetime_or_None)].
    """
    profile = Path(tempfile.mkdtemp()) / "prof"
    profile.mkdir(parents=True)
    if locked:
        (profile / "SingletonLock").write_text("", encoding="utf-8")
    if not initialised:
        return profile
    default = profile / "Default"
    default.mkdir()
    if cookies is None:
        return profile

    con = sqlite3.connect(default / "Cookies")
    con.execute(
        "CREATE TABLE cookies (creation_utc INTEGER NOT NULL, host_key TEXT "
        "NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL, path TEXT NOT "
        "NULL, expires_utc INTEGER NOT NULL)")
    now = chrome_us(datetime.now(timezone.utc))
    for host, name, expires in cookies:
        con.execute(
            "INSERT INTO cookies VALUES (?,?,?,?,?,?)",
            (now, host, name, "", "/",
             chrome_us(expires) if expires else 0))
    con.commit()
    con.close()
    return profile


def signed_in(days=30) -> Path:
    return make_profile(cookies=[
        (".linkedin.com", "li_at",
         datetime.now(timezone.utc) + timedelta(days=days))])


class TestSessionDetection(unittest.TestCase):
    def test_a_signed_in_profile_is_recognised(self):
        session = bp.linkedin_session(signed_in())
        self.assertTrue(session["present"])
        self.assertAlmostEqual(session["days_left"], 30, delta=1)

    def test_the_expiry_is_decoded_from_chromes_epoch(self):
        # Chrome counts microseconds from 1601-01-01, not the unix epoch.
        session = bp.linkedin_session(signed_in(days=10))
        self.assertAlmostEqual(session["days_left"], 10, delta=1)
        self.assertGreater(session["expires_at"].year, 2000)

    def test_an_expired_session_reports_negative_days(self):
        profile = make_profile(cookies=[
            (".linkedin.com", "li_at",
             datetime.now(timezone.utc) - timedelta(days=5))])
        session = bp.linkedin_session(profile)
        self.assertTrue(session["present"])
        self.assertLess(session["days_left"], 0)

    def test_a_profile_with_other_cookies_is_not_signed_in(self):
        profile = make_profile(cookies=[
            (".google.com", "SID", datetime.now(timezone.utc) + timedelta(days=30)),
            (".linkedin.com", "bcookie",
             datetime.now(timezone.utc) + timedelta(days=30)),
        ])
        session = bp.linkedin_session(profile)
        self.assertFalse(session["present"], "a non-auth cookie counted as a session")

    def test_an_li_at_for_another_host_does_not_count(self):
        profile = make_profile(cookies=[
            (".example.com", "li_at",
             datetime.now(timezone.utc) + timedelta(days=30))])
        self.assertFalse(bp.linkedin_session(profile)["present"])

    def test_an_empty_profile_is_reported_not_raised(self):
        session = bp.linkedin_session(make_profile(cookies=None))
        self.assertFalse(session["present"])
        self.assertIn("never been used", session["error"])

    def test_a_missing_directory_is_reported_not_raised(self):
        session = bp.linkedin_session(Path(tempfile.mkdtemp()) / "nope")
        self.assertFalse(session["present"])
        self.assertTrue(session["error"])

    def test_a_corrupt_cookie_database_is_reported_not_raised(self):
        profile = make_profile(cookies=[])
        (profile / "Default" / "Cookies").write_text("not sqlite", encoding="utf-8")
        session = bp.linkedin_session(profile)
        self.assertFalse(session["present"])
        self.assertTrue(session["error"])

    def test_cookie_values_are_never_read(self):
        """Only names and expiries. Values are encrypted and none of our business."""
        source = (REPO / "browser_profile.py").read_text(encoding="utf-8")
        self.assertNotIn("encrypted_value", source)
        self.assertIn("SELECT host_key, expires_utc", source)

    def test_the_database_is_opened_read_only(self):
        # Chrome may hold the file; writing to it would be reckless.
        source = (REPO / "browser_profile.py").read_text(encoding="utf-8")
        self.assertIn("mode=ro&immutable=1", source)

    def test_the_newer_network_cookies_location_is_found(self):
        profile = Path(tempfile.mkdtemp()) / "prof"
        (profile / "Default" / "Network").mkdir(parents=True)
        con = sqlite3.connect(profile / "Default" / "Network" / "Cookies")
        con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, "
                    "expires_utc INTEGER)")
        con.execute("INSERT INTO cookies VALUES (?,?,?)",
                    (".linkedin.com", "li_at",
                     chrome_us(datetime.now(timezone.utc) + timedelta(days=9))))
        con.commit()
        con.close()
        self.assertTrue(bp.linkedin_session(profile)["present"])


class TestProfileState(unittest.TestCase):
    def test_a_signed_in_profile_is_usable(self):
        self.assertTrue(bp.usable(bp.profile_state(signed_in())))

    def test_an_expired_profile_is_not_usable(self):
        profile = make_profile(cookies=[
            (".linkedin.com", "li_at",
             datetime.now(timezone.utc) - timedelta(days=1))])
        self.assertFalse(bp.usable(bp.profile_state(profile)))

    def test_an_empty_profile_is_not_usable(self):
        self.assertFalse(bp.usable(bp.profile_state(make_profile(cookies=None))))

    def test_a_missing_profile_is_not_usable(self):
        state = bp.profile_state(Path(tempfile.mkdtemp()) / "nope")
        self.assertFalse(state["exists"])
        self.assertFalse(bp.usable(state))

    def test_a_locked_profile_is_detected(self):
        # Chrome locks a profile to one instance; the bot would lose that race.
        self.assertTrue(bp.profile_state(make_profile(locked=True))["locked"])

    def test_an_uninitialised_directory_is_flagged(self):
        state = bp.profile_state(make_profile(initialised=False))
        self.assertTrue(state["exists"])
        self.assertFalse(state["initialised"])


class TestCreateAndLogin(unittest.TestCase):
    def test_creating_the_directory(self):
        path = Path(tempfile.mkdtemp()) / "chrome-lla-profile"
        ok, msg = bp.create(path)
        self.assertTrue(ok, msg)
        self.assertTrue(path.exists())

    def test_creating_an_existing_directory_is_fine(self):
        path = Path(tempfile.mkdtemp())
        ok, _msg = bp.create(path)
        self.assertTrue(ok)

    def test_the_login_command_is_not_headless(self):
        # A person has to see the window to sign in — that is the whole point.
        command = bp.login_command("/tmp/prof", binary="/usr/bin/chromium")
        self.assertNotIn("--headless", " ".join(command))

    def test_the_login_command_targets_the_profile_and_linkedin(self):
        command = bp.login_command("/tmp/prof", binary="/usr/bin/chromium")
        self.assertIn("--user-data-dir=/tmp/prof", command)
        self.assertTrue(any("linkedin.com/login" in c for c in command))

    def test_the_default_profile_directory_is_gitignored(self):
        # It holds a live session; it must never be committable.
        ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(bp.DEFAULT_PROFILE_DIRNAME, ignored)

    def test_the_security_guards_protect_it(self):
        guards = (REPO / "tools" / "security_guards.py").read_text(encoding="utf-8")
        self.assertIn(bp.DEFAULT_PROFILE_DIRNAME, guards)

    def test_waiting_returns_the_session_when_one_appears(self):
        session = bp.wait_for_login(signed_in(), timeout_sec=1, poll_sec=1)
        self.assertTrue(session["present"])

    def test_waiting_gives_up_without_raising(self):
        session = bp.wait_for_login(make_profile(cookies=None),
                                    timeout_sec=1, poll_sec=1)
        self.assertFalse(session["present"])


class TestReporting(unittest.TestCase):
    def test_a_missing_profile_tells_you_how_to_make_one(self):
        text = bp.format_state(bp.profile_state(Path("/nope/nowhere")))
        self.assertIn("--create", text)
        self.assertIn("--login", text)

    def test_an_unsigned_profile_tells_you_to_sign_in(self):
        text = bp.format_state(bp.profile_state(make_profile(cookies=None)))
        self.assertIn("--login", text)
        self.assertIn("cannot authenticate", text)

    def test_a_signed_in_profile_reports_the_days_left(self):
        text = bp.format_state(bp.profile_state(signed_in(days=30)))
        self.assertIn("signed in", text)
        self.assertIn("day(s) left", text)

    def test_an_expiring_session_is_called_out(self):
        text = bp.format_state(bp.profile_state(signed_in(days=3)))
        self.assertIn("expiring soon", text)

    def test_an_expired_session_says_so(self):
        profile = make_profile(cookies=[
            (".linkedin.com", "li_at",
             datetime.now(timezone.utc) - timedelta(days=4))])
        self.assertIn("EXPIRED", bp.format_state(bp.profile_state(profile)))

    def test_a_locked_profile_is_mentioned(self):
        self.assertIn("locked", bp.format_state(bp.profile_state(
            make_profile(locked=True))))


class TestPreflightUsesTheProfile(unittest.TestCase):
    """The check that would have caught this run before it started."""

    BASE = ("personal:\n  first_name: Ada\n"
            "search:\n  search_terms: [python]\n  search_locations: [London]\n"
            "scheduling:\n  max_applies_per_day: 5\n"
            "  active_hours_start: 8\n  active_hours_end: 23\n")

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "main.py").write_text("pass\n", encoding="utf-8")
        self._saved = (ap.ROOT, ap.LOCK_PATH, ap.LOG_DIR)
        ap.ROOT = self.dir
        ap.LOCK_PATH = self.dir / "data" / "autopilot.lock"
        ap.LOG_DIR = self.dir / "logs"
        self.addCleanup(self._restore)

    def _restore(self):
        ap.ROOT, ap.LOCK_PATH, ap.LOG_DIR = self._saved

    def _write(self, profile, headless=True):
        (self.dir / "config.yaml").write_text(
            self.BASE + f"browser:\n  headless: {str(headless).lower()}\n"
                        f"  user_data_dir: {profile}\n", encoding="utf-8")

    def _blocking(self):
        return [p for p, _f in ap.preflight()
                if not p.startswith("ADVISORY:")]

    def test_a_profile_with_no_session_blocks(self):
        self._write(make_profile(cookies=None))
        blocking = self._blocking()
        self.assertTrue(any("no usable LinkedIn session" in p for p in blocking),
                        blocking)

    def test_the_fix_names_the_login_command(self):
        self._write(make_profile(cookies=None))
        fixes = [f for p, f in ap.preflight() if "LinkedIn session" in p]
        self.assertTrue(fixes)
        self.assertIn("lla profile --login", fixes[0])

    def test_a_signed_in_profile_passes(self):
        self._write(signed_in())
        self.assertEqual(
            [p for p in self._blocking() if "session" in p or "sign in" in p], [])

    def test_an_expired_profile_blocks(self):
        self._write(make_profile(cookies=[
            (".linkedin.com", "li_at",
             datetime.now(timezone.utc) - timedelta(days=2))]))
        self.assertTrue(any("no usable LinkedIn session" in p
                            for p in self._blocking()))

    def test_a_missing_profile_directory_blocks(self):
        self._write(self.dir / "never-made")
        self.assertTrue(any("LinkedIn session" in p for p in self._blocking()))

    def test_a_locked_signed_in_profile_is_only_an_advisory(self):
        profile = signed_in()
        (profile / "SingletonLock").write_text("", encoding="utf-8")
        self._write(profile)
        problems = ap.preflight()
        self.assertTrue(any(p.startswith("ADVISORY:") and "another window" in p
                            for p, _f in problems))
        self.assertEqual([p for p in self._blocking() if "session" in p], [])

    def test_the_overlapping_no_auth_message_is_suppressed(self):
        # One root cause should not produce two problems.
        self._write(make_profile(cookies=None))
        blocking = self._blocking()
        self.assertEqual(
            len([p for p in blocking if "cannot sign in on its own" in p]), 0,
            f"redundant message alongside the profile problem: {blocking}")

    def test_no_profile_and_no_credentials_still_blocks_when_headless(self):
        (self.dir / "config.yaml").write_text(
            self.BASE + "browser:\n  headless: true\n", encoding="utf-8")
        self.assertTrue(any("cannot sign in" in p for p in self._blocking()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
