"""24/7 supervision — tools/autopilot.py and the cron installers.

The property that matters most is negative: the supervisor must never end up
running two bots. Two Chrome sessions signed into one LinkedIn account apply to
the same jobs twice and look exactly like automation, and a cron entry that
fires while the previous run is still going causes it by default.

After that: a dead bot must read as dead (including the awkward cases — a
recycled PID, a process that exited but has not been reaped), and a bot that
keeps dying must not be restarted in a loop.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import autopilot as ap  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# A config that passes ConfigValidator, so `start` gets past preflight. The
# supervisor tests are about locking and liveness, not configuration.
VALID_CONFIG = """\
personal:
  first_name: Ada
  last_name: Lovelace
  email: ada@example.com
search:
  search_terms: [python]
  search_locations: [London]
scheduling:
  max_applies_per_day: 40
  max_applies_per_cycle: 10
  active_hours_start: 8
  active_hours_end: 23
"""


class SandboxedAutopilot(unittest.TestCase):
    """Point the module at a throwaway project so tests never touch the repo."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "main.py").write_text(
            "import time\nwhile True:\n    time.sleep(1)\n", encoding="utf-8")
        (self.dir / "config.yaml").write_text(VALID_CONFIG, encoding="utf-8")
        self._saved = (ap.ROOT, ap.LOCK_PATH, ap.LOG_DIR)
        ap.ROOT = self.dir
        ap.LOCK_PATH = self.dir / "data" / "autopilot.lock"
        ap.LOG_DIR = self.dir / "logs"
        self.addCleanup(self._restore)

    def _restore(self):
        running, lock = ap.is_running()
        if running:
            try:
                ap.stop(timeout=5)
            except Exception:
                pass
        ap.ROOT, ap.LOCK_PATH, ap.LOG_DIR = self._saved


class TestLockFile(SandboxedAutopilot):
    def test_a_missing_lock_reads_as_empty(self):
        self.assertEqual(ap.read_lock(), {})

    def test_a_corrupt_lock_reads_as_empty_rather_than_raising(self):
        ap.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        ap.LOCK_PATH.write_text("{not json", encoding="utf-8")
        self.assertEqual(ap.read_lock(), {})

    def test_writing_then_reading_round_trips(self):
        ap.write_lock({"pid": 42, "started_at": "2026-01-01T00:00:00"})
        self.assertEqual(ap.read_lock()["pid"], 42)

    def test_the_lock_is_replaced_atomically(self):
        # A half-written lock would make the supervisor lose track of the bot.
        ap.write_lock({"pid": 1})
        ap.write_lock({"pid": 2})
        self.assertEqual(json.loads(ap.LOCK_PATH.read_text())["pid"], 2)
        self.assertFalse(ap.LOCK_PATH.with_suffix(".tmp").exists())

    def test_clearing_a_missing_lock_is_not_an_error(self):
        ap.clear_lock()
        ap.clear_lock()


class TestProcessDetection(SandboxedAutopilot):
    def test_this_process_is_alive(self):
        self.assertTrue(ap.pid_alive(os.getpid()))

    def test_an_unused_pid_is_not_alive(self):
        self.assertFalse(ap.pid_alive(999_999))

    def test_pid_zero_and_negatives_are_not_alive(self):
        self.assertFalse(ap.pid_alive(0))
        self.assertFalse(ap.pid_alive(-1))

    def test_the_command_line_is_readable(self):
        self.assertIn("python", ap.process_command(os.getpid()).lower())

    def test_an_unused_pid_has_no_command(self):
        self.assertEqual(ap.process_command(999_999), "")

    @unittest.skipIf(sys.platform.startswith("win"), "POSIX zombie semantics")
    def test_an_unreaped_process_counts_as_dead(self):
        """The case that made `stop` take 20s and need SIGKILL.

        A process that has exited but not been reaped still answers
        os.kill(pid, 0) and still has a /proc entry. Without this check the
        supervisor reports a dead bot as running — and so never restarts it.
        """
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.poll()                      # let it exit, deliberately unreaped
        deadline = time.time() + 10
        while time.time() < deadline and ap.process_state(proc.pid) != "Z":
            time.sleep(0.1)
        if ap.process_state(proc.pid) != "Z":
            proc.wait()
            self.skipTest("could not produce a zombie on this platform")
        self.assertFalse(ap.pid_alive(proc.pid), "a zombie read as alive")
        proc.wait()


class TestIsRunning(SandboxedAutopilot):
    def test_no_lock_means_not_running(self):
        self.assertFalse(ap.is_running()[0])

    def test_a_lock_pointing_at_a_dead_pid_means_not_running(self):
        ap.write_lock({"pid": 999_999, "started_at": "2026-01-01T00:00:00"})
        self.assertFalse(ap.is_running()[0])

    def test_a_recycled_pid_is_not_mistaken_for_the_bot(self):
        # After a reboot the number in the lock is very likely someone else's
        # process. Reporting it as "running" would stop the bot ever starting.
        ap.write_lock({"pid": 1, "started_at": "2026-01-01T00:00:00"})
        self.assertFalse(ap.is_running()[0])

    def test_a_real_bot_process_reads_as_running(self):
        ok, msg = ap.start()
        self.assertTrue(ok, msg)
        time.sleep(0.5)
        running, lock = ap.is_running()
        self.assertTrue(running)
        self.assertGreater(lock["pid"], 0)


class TestStartRefusesToDoubleUp(SandboxedAutopilot):
    def test_a_second_start_is_refused(self):
        """The whole point: never two bots on one LinkedIn account."""
        ok, _ = ap.start()
        self.assertTrue(ok)
        time.sleep(0.5)
        first_pid = ap.read_lock()["pid"]

        ok2, msg = ap.start()
        self.assertFalse(ok2)
        self.assertIn("already running", msg)
        self.assertEqual(ap.read_lock()["pid"], first_pid,
                         "the lock was overwritten by the refused start")

    def test_start_without_a_config_is_refused_with_a_next_step(self):
        (self.dir / "config.yaml").unlink()
        ok, msg = ap.start()
        self.assertFalse(ok)
        self.assertIn("lla setup", msg)

    def test_starting_records_the_pid_and_a_log_path(self):
        ap.start()
        time.sleep(0.3)
        lock = ap.read_lock()
        self.assertTrue(ap.pid_alive(lock["pid"]))
        self.assertTrue(Path(lock["log"]).exists())

    def test_env_file_values_are_loaded(self):
        (self.dir / ".env").write_text("LLA_TEST_TOKEN=from-dotenv\n", encoding="utf-8")
        os.environ.pop("LLA_TEST_TOKEN", None)
        self.addCleanup(os.environ.pop, "LLA_TEST_TOKEN", None)
        ap.start()
        # cron gives a process almost no environment, so .env is where the
        # API keys have to come from.
        self.assertEqual(os.environ.get("LLA_TEST_TOKEN"), "from-dotenv")


class TestPreflight(SandboxedAutopilot):
    """Catch what a restart cannot fix, before the first launch.

    The backoff message says a restart will not fix a bad config, a missing
    resume or a driver mismatch. Finding that out after four failed launches
    is worse than finding it out now.
    """

    def _write(self, text):
        (self.dir / "config.yaml").write_text(text, encoding="utf-8")

    def test_a_valid_config_has_no_blocking_problems(self):
        blocking = [p for p, _f in ap.preflight()
                    if not p.startswith("ADVISORY:")]
        self.assertEqual(blocking, [])

    def test_a_missing_config_is_reported(self):
        (self.dir / "config.yaml").unlink()
        problems = ap.preflight()
        self.assertIn("does not exist", problems[0][0])
        self.assertIn("lla setup", problems[0][1])

    def test_broken_yaml_is_reported_not_raised(self):
        self._write("personal: [unclosed\n")
        problems = ap.preflight()
        self.assertIn("not valid YAML", problems[0][0])

    def test_a_config_missing_required_sections_is_blocking(self):
        self._write("scheduling:\n  max_applies_per_day: 40\n")
        blocking = [p for p, _f in ap.preflight()
                    if not p.startswith("ADVISORY:")]
        self.assertTrue(blocking, "a config with no search section passed preflight")

    def test_a_missing_resume_file_is_blocking(self):
        self._write(VALID_CONFIG + "resume:\n  default_resume_path: nope/cv.pdf\n")
        blocking = [p for p, _f in ap.preflight() if "resume" in p.lower()]
        self.assertTrue(blocking, "a resume that does not exist passed preflight")

    def test_an_existing_resume_file_passes(self):
        (self.dir / "cv.pdf").write_bytes(b"%PDF-1.4")
        self._write(VALID_CONFIG + "resume:\n  default_resume_path: cv.pdf\n")
        self.assertEqual(
            [p for p, _f in ap.preflight() if "resume" in p.lower()], [])

    def test_round_the_clock_hours_are_advisory_not_blocking(self):
        # Worth saying; not a reason to refuse to start.
        self._write(VALID_CONFIG.replace("active_hours_start: 8", "active_hours_start: 0")
                                .replace("active_hours_end: 23", "active_hours_end: 24"))
        problems = ap.preflight()
        advisories = [p for p, _f in problems if p.startswith("ADVISORY:")]
        blocking = [p for p, _f in problems if not p.startswith("ADVISORY:")]
        self.assertTrue(advisories, "0-24 active hours went unmentioned")
        self.assertEqual(blocking, [])

    def test_a_failure_inside_the_validator_does_not_raise(self):
        """The except branch must survive being taken.

        It called `log.debug` in a module that had no logger, so any failure
        in validation raised NameError out of preflight — masking the real
        problem and taking `start` down with it.
        """
        import validate_config

        original = validate_config.ConfigValidator
        validate_config.ConfigValidator = property()   # not callable
        self.addCleanup(setattr, validate_config, "ConfigValidator", original)
        problems = ap.preflight()          # must not raise
        self.assertIsInstance(problems, list)

    def test_a_failure_in_the_pacing_advisory_does_not_raise(self):
        import human_pacing

        original = human_pacing.active_hours_warning

        def boom(_cfg):
            raise RuntimeError("nope")
        human_pacing.active_hours_warning = boom
        self.addCleanup(setattr, human_pacing, "active_hours_warning", original)
        self.assertIsInstance(ap.preflight(), list)

    def test_start_refuses_on_a_blocking_problem(self):
        self._write("scheduling:\n  max_applies_per_day: 40\n")
        ok, msg = ap.start()
        self.assertFalse(ok)
        self.assertIn("every restart", msg)
        self.assertFalse(ap.is_running()[0])

    def test_start_proceeds_despite_an_advisory(self):
        self._write(VALID_CONFIG.replace("active_hours_start: 8", "active_hours_start: 0")
                                .replace("active_hours_end: 23", "active_hours_end: 24"))
        ok, msg = ap.start()
        self.assertTrue(ok, msg)

    def test_preflight_can_be_skipped(self):
        self._write("scheduling:\n  max_applies_per_day: 40\n")
        ok, msg = ap.start(skip_preflight=True)
        self.assertTrue(ok, msg)

    def test_watch_reports_failure_rather_than_crash_looping(self):
        # Better than starting a bot that dies, four times, before backing off.
        self._write("scheduling:\n  max_applies_per_day: 40\n")
        action, msg = ap.watch()
        self.assertEqual(action, "failed")
        self.assertIn("every restart", msg)


class TestBrowserDiscovery(unittest.TestCase):
    """A browser the driver cannot find fails uselessly.

    undetected-chromedriver looks only in standard locations and reports
    "Binary Location Must be a String" when it finds nothing — naming neither
    the cause nor the fix. That is what anyone on Chromium, a snap install, or
    this project's own Docker image hits.
    """

    def test_detection_returns_a_path_or_an_empty_string(self):
        from env_doctor import detect_chrome_binary
        found = detect_chrome_binary()
        self.assertIsInstance(found, str)
        if found:
            self.assertTrue(Path(found).exists(), found)

    def test_a_found_binary_is_executable(self):
        from env_doctor import detect_chrome_binary
        found = detect_chrome_binary()
        if not found:
            self.skipTest("no browser on this machine")
        self.assertTrue(os.access(found, os.X_OK))

    def test_version_detection_falls_back_to_the_located_binary(self):
        # On a container Chrome is not on PATH, so without this the driver
        # cannot be pinned to the installed version either.
        from env_doctor import detect_chrome_binary, detect_chrome_version
        if not detect_chrome_binary():
            self.skipTest("no browser on this machine")
        version = detect_chrome_version()
        self.assertTrue(version is None or int(version) > 0)

    def test_the_launcher_sets_the_binary_location(self):
        source = (REPO / "linkedin.py").read_text(encoding="utf-8")
        self.assertIn("binary_location", source,
                      "create_browser no longer tells the driver where Chrome is")
        self.assertIn("detect_chrome_binary", source)

    def test_the_launcher_error_names_the_config_key(self):
        source = (REPO / "linkedin.py").read_text(encoding="utf-8")
        self.assertIn("browser.chrome_binary", source)

    def test_the_example_config_documents_the_option(self):
        import yaml
        cfg = yaml.safe_load((REPO / "config.example.yaml").read_text(encoding="utf-8"))
        self.assertIn("chrome_binary", cfg.get("browser", {}))


class TestUnattendedLoginPreflight(SandboxedAutopilot):
    """Headless with no way to sign in is worse than a crash.

    The bot reaches LinkedIn's login page, asks for a manual login into a
    window nobody can see, waits three minutes and repeats — looking healthy to
    the watchdog the whole time, so nothing restarts or reports it.
    """

    def _write(self, extra):
        (self.dir / "config.yaml").write_text(VALID_CONFIG + extra, encoding="utf-8")

    def test_headless_with_no_credentials_blocks(self):
        self._write("browser:\n  headless: true\n")
        blocking = [p for p, _f in ap.preflight()
                    if not p.startswith("ADVISORY:")]
        self.assertTrue(any("cannot sign in" in p for p in blocking), blocking)

    def test_the_fix_names_both_ways_out(self):
        self._write("browser:\n  headless: true\n")
        fixes = [f for p, f in ap.preflight() if "cannot sign in" in p]
        self.assertTrue(fixes)
        self.assertIn("user_data_dir", fixes[0])
        self.assertIn("linkedin.email", fixes[0])

    def test_credentials_satisfy_it(self):
        self._write("browser:\n  headless: true\n"
                    "linkedin:\n  email: a@b.com\n  password: secret\n")
        blocking = [p for p, _f in ap.preflight()
                    if not p.startswith("ADVISORY:")]
        self.assertEqual([p for p in blocking if "sign in" in p], [])

    def test_a_signed_in_profile_satisfies_it(self):
        self._write("browser:\n  headless: true\n"
                    f"  user_data_dir: {self.dir}/profile\n")
        blocking = [p for p, _f in ap.preflight()
                    if not p.startswith("ADVISORY:")]
        self.assertEqual([p for p in blocking if "sign in" in p], [])

    def test_not_headless_is_only_an_advisory(self):
        # A person can log in by hand when there is a window to do it in.
        self._write("browser:\n  headless: false\n")
        problems = ap.preflight()
        blocking = [p for p, _f in problems if not p.startswith("ADVISORY:")]
        advisory = [p for p, _f in problems if p.startswith("ADVISORY:")]
        self.assertEqual([p for p in blocking if "sign in" in p], [])
        self.assertTrue(any("sign in" in p for p in advisory))

    def test_a_missing_chrome_binary_path_blocks(self):
        self._write(f"browser:\n  chrome_binary: {self.dir}/no/such/chrome\n"
                    "linkedin:\n  email: a@b.com\n  password: s\n")
        blocking = [p for p, _f in ap.preflight()
                    if not p.startswith("ADVISORY:")]
        self.assertTrue(any("chrome_binary" in p for p in blocking), blocking)


class TestStopTakesTheBrowserWithIt(SandboxedAutopilot):
    """A forced stop that leaves Chrome behind fills the machine up.

    Thirteen browser processes survived one forced stop before this; on a 24/7
    autopilot that restarts, they accumulate until memory runs out.
    """

    def test_stop_signals_the_process_group(self):
        source = (REPO / "tools" / "autopilot.py").read_text(encoding="utf-8")
        self.assertIn("os.killpg", source,
                      "stop() signals only the bot, orphaning its browser")

    def test_the_bot_is_spawned_as_its_own_group_leader(self):
        # os.killpg only works because of this.
        source = (REPO / "tools" / "autopilot.py").read_text(encoding="utf-8")
        self.assertIn("start_new_session", source)

    def test_a_child_of_the_bot_is_killed_too(self):
        if sys.platform.startswith("win"):
            self.skipTest("POSIX process groups")
        # A bot that spawns a child, like Chrome.
        (self.dir / "main.py").write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', "
            "'import time\\nwhile True: time.sleep(1)'])\n"
            "while True:\n    time.sleep(1)\n", encoding="utf-8")
        ok, msg = ap.start(skip_preflight=True)
        self.assertTrue(ok, msg)
        time.sleep(2)
        pid = ap.read_lock()["pid"]
        group = os.getpgid(pid)
        children = [p for p in self._group_members(group) if p != pid]
        self.assertTrue(children, "the stand-in bot spawned no child")

        ap.stop(timeout=8)
        time.sleep(1)
        survivors = self._group_members(group)
        self.assertEqual(survivors, [],
                         f"{len(survivors)} process(es) survived the stop")

    @staticmethod
    def _group_members(group):
        """Live members of a process group — zombies excluded.

        os.getpgid succeeds on a process that has exited but not been reaped,
        and a zombie holds neither memory nor a browser, so counting them would
        report a clean shutdown as a leak.
        """
        out = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                if os.getpgid(pid) != group:
                    continue
            except Exception:
                continue
            if not ap.process_state(pid).startswith("Z"):
                out.append(pid)
        return out


class TestLoginWaitHonoursShutdown(unittest.TestCase):
    """SIGTERM must reach the manual-login wait.

    It did not, so `autopilot stop` waited out its whole grace period and then
    SIGKILLed — which skips driver.quit() and is what orphaned the browser.
    """

    def test_the_login_wait_checks_the_shutdown_flag(self):
        source = (REPO / "linkedin.py").read_text(encoding="utf-8")
        start = source.index("def _wait_for_manual_login")
        body = source[start:start + 2000]
        self.assertIn("shutdown_requested", body,
                      "the login wait ignores SIGTERM, forcing a SIGKILL that "
                      "orphans the browser")


class TestStop(SandboxedAutopilot):
    def test_stopping_when_not_running_says_so(self):
        ok, msg = ap.stop()
        self.assertFalse(ok)
        self.assertEqual(msg, "not running")

    def test_a_running_bot_is_stopped_and_the_lock_cleared(self):
        ap.start()
        time.sleep(0.5)
        pid = ap.read_lock()["pid"]
        ok, msg = ap.stop(timeout=10)
        self.assertTrue(ok, msg)
        self.assertFalse(ap.pid_alive(pid))
        self.assertFalse(ap.LOCK_PATH.exists())

    def test_a_well_behaved_bot_is_not_force_killed(self):
        # main.py handles SIGTERM to close Chrome and the database cleanly;
        # if we always escalated to SIGKILL that would never run.
        ap.start()
        time.sleep(0.5)
        ok, msg = ap.stop(timeout=10)
        self.assertTrue(ok)
        self.assertNotIn("forced", msg)


class TestCrashLoopBackoff(unittest.TestCase):
    """A bot that dies instantly does not need restarting, it needs looking at."""

    def _lock(self, *offsets_seconds):
        now = datetime.now()
        return {"pid": 0, "starts": [
            (now - timedelta(seconds=s)).strftime(ap.TIME_FMT)
            for s in sorted(offsets_seconds, reverse=True)]}, now

    def test_no_history_is_not_a_failure(self):
        self.assertEqual(ap.recent_fast_failures({}, datetime.now()), 0)
        self.assertEqual(ap.recent_fast_failures({"starts": []}, datetime.now()), 0)

    def test_one_start_is_not_a_failure(self):
        lock, now = self._lock(10)
        self.assertEqual(ap.recent_fast_failures(lock, now), 0)

    def test_repeated_short_runs_are_counted(self):
        lock, now = self._lock(90, 60, 30, 5)
        self.assertEqual(ap.recent_fast_failures(lock, now), 4)

    def test_a_long_healthy_run_resets_the_count(self):
        lock, now = self._lock(6 * 3600, 3 * 3600)
        self.assertEqual(ap.recent_fast_failures(lock, now), 0)

    def test_only_the_trailing_run_of_failures_counts(self):
        # Two fast failures long ago, then a healthy run: not a crash loop now.
        lock, now = self._lock(10 * 3600, 10 * 3600 - 5, 3600)
        self.assertEqual(ap.recent_fast_failures(lock, now), 0)

    def test_backoff_kicks_in_after_the_threshold(self):
        lock, now = self._lock(90, 60, 30, 5)
        wait, why = ap.backing_off(lock, now)
        self.assertTrue(wait)
        self.assertIn("not restarting until", why)

    def test_backoff_explains_that_restarting_will_not_help(self):
        lock, now = self._lock(90, 60, 30, 5)
        _wait, why = ap.backing_off(lock, now)
        self.assertIn("config", why)

    def test_backoff_expires(self):
        old = datetime.now() - timedelta(minutes=ap.BACKOFF_MINUTES + 5)
        lock = {"pid": 0, "starts": [
            (old - timedelta(seconds=s)).strftime(ap.TIME_FMT)
            for s in (90, 60, 30, 0)]}
        self.assertFalse(ap.backing_off(lock, datetime.now())[0])

    def test_a_healthy_bot_never_backs_off(self):
        lock, now = self._lock(6 * 3600)
        self.assertFalse(ap.backing_off(lock, now)[0])

    def test_unparseable_timestamps_are_ignored(self):
        self.assertEqual(
            ap.recent_fast_failures({"starts": ["nonsense", None, 42]},
                                    datetime.now()), 0)


class TestWatch(SandboxedAutopilot):
    """What cron actually calls, every few minutes, forever."""

    def test_watch_starts_a_bot_that_is_not_running(self):
        action, msg = ap.watch()
        self.assertEqual(action, "started", msg)
        time.sleep(0.5)
        self.assertTrue(ap.is_running()[0])

    def test_watch_does_nothing_when_the_bot_is_up(self):
        ap.start()
        time.sleep(0.5)
        pid = ap.read_lock()["pid"]
        action, _msg = ap.watch()
        self.assertEqual(action, "running")
        self.assertEqual(ap.read_lock()["pid"], pid,
                         "watch started a second bot")

    def test_watch_backs_off_during_a_crash_loop(self):
        now = datetime.now()
        ap.write_lock({"pid": 0, "starts": [
            (now - timedelta(seconds=s)).strftime(ap.TIME_FMT)
            for s in (90, 60, 30, 2)]})
        action, why = ap.watch()
        self.assertEqual(action, "backoff")
        self.assertIn("died within", why)
        self.assertFalse(ap.is_running()[0])

    def test_watch_reports_failure_when_the_config_is_missing(self):
        (self.dir / "config.yaml").unlink()
        action, _msg = ap.watch()
        self.assertEqual(action, "failed")


class TestStatus(SandboxedAutopilot):
    def test_status_of_a_stopped_bot(self):
        s = ap.status()
        self.assertFalse(s["running"])
        self.assertIsNone(s["pid"])
        self.assertIn("Not running", ap.format_status(s))

    def test_status_of_a_running_bot(self):
        ap.start()
        time.sleep(0.5)
        s = ap.status()
        self.assertTrue(s["running"])
        self.assertIn("Running", ap.format_status(s))

    def test_the_daily_cap_is_read_from_the_config(self):
        _applied, cap = ap.applies_today()
        self.assertEqual(cap, 40)

    def test_a_missing_config_does_not_crash_status(self):
        (self.dir / "config.yaml").unlink()
        s = ap.status()
        self.assertFalse(s["running"])
        self.assertIsNone(s["daily_cap"])

    def test_uptime_is_human_readable(self):
        started = (datetime.now() - timedelta(hours=26)).strftime(ap.TIME_FMT)
        self.assertEqual(ap._uptime(started).split()[0], "1d")

    def test_an_unreadable_start_time_does_not_crash(self):
        self.assertEqual(ap._uptime("nonsense"), "unknown")


class TestDailyCapSurvivesRestarts(unittest.TestCase):
    """Restarting is only safe because the cap is not held in memory.

    A supervisor that reset the day's count on every restart would turn a
    crash loop into an application flood, which is the one failure here that
    cannot be undone.
    """

    def test_the_count_is_read_from_the_database_not_a_counter(self):
        from state import State
        db = Path(tempfile.mkdtemp()) / "s.db"
        state = State(db_path=str(db))
        state.conn.execute(
            "INSERT INTO applied_jobs (job_id,title,company) VALUES ('j1','T','C')")
        state.conn.commit()
        before = state.daily_applied_count()
        state.close()

        # A "restart": a brand-new State over the same file.
        again = State(db_path=str(db))
        self.assertEqual(again.daily_applied_count(), before,
                         "the daily count reset across a restart")

    def test_main_checks_the_cap_from_state_on_every_cycle(self):
        source = (REPO / "main.py").read_text(encoding="utf-8")
        self.assertIn("daily_applied_count() >= sched.get(\"max_applies_per_day\"",
                      source.replace("'", '"'),
                      "main.py no longer gates the loop on the persisted cap")


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class TestCronInstaller(unittest.TestCase):
    """The installer edits the user's real crontab. It must not damage it."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "tools").mkdir()
        shutil.copy(REPO / "tools" / "setup_autopilot.sh", self.dir / "tools")
        shutil.copy(REPO / "tools" / "autopilot.py", self.dir / "tools")
        (self.dir / "config.yaml").write_text(VALID_CONFIG, encoding="utf-8")
        (self.dir / "main.py").write_text(
            "import time\nwhile True:\n    time.sleep(1)\n", encoding="utf-8")

        # A stand-in crontab that behaves like the real one: `-` reads stdin
        # fully and replaces the table atomically.
        self.bin = self.dir / "fakebin"
        self.bin.mkdir()
        fake = self.bin / "crontab"
        fake.write_text(
            '#!/usr/bin/env bash\n'
            'TAB="${CRONTAB_FILE:?}"\ntouch "$TAB"\n'
            'if [[ "${1:-}" == "-l" ]]; then cat "$TAB"; exit 0; fi\n'
            'if [[ "${1:-}" == "-" ]]; then t="$(mktemp)"; cat > "$t"; '
            'mv "$t" "$TAB"; exit 0; fi\nexit 0\n', encoding="utf-8")
        fake.chmod(0o755)
        self.tab = self.dir / "crontab.txt"
        self.tab.write_text("", encoding="utf-8")

    def _run(self, *args):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env["CRONTAB_FILE"] = str(self.tab)
        proc = subprocess.run(
            ["bash", str(self.dir / "tools" / "setup_autopilot.sh"), *args],
            capture_output=True, text=True, env=env, timeout=120)
        self.addCleanup(self._kill_bot)
        return proc

    def _kill_bot(self):
        lock = self.dir / "data" / "autopilot.lock"
        try:
            pid = json.loads(lock.read_text())["pid"]
            os.kill(pid, 15)
        except Exception:
            pass

    def _lines(self):
        return self.tab.read_text(encoding="utf-8").splitlines()

    def test_installing_on_an_empty_crontab_works(self):
        """The first-run case, which `grep -v` exiting 1 used to abort.

        Under `set -euo pipefail`, `grep -v` selecting no lines returns 1 and
        killed the script before it installed anything — silently, exit 1.
        """
        proc = self._run("5")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        entries = [ln for ln in self._lines() if "autopilot" in ln]
        self.assertEqual(len(entries), 2, self._lines())
        self.assertTrue(any(ln.startswith("@reboot") for ln in entries))
        self.assertTrue(any(ln.startswith("*/5 ") for ln in entries))

    def test_unrelated_cron_entries_are_preserved(self):
        self.tab.write_text("0 3 * * * /usr/bin/backup.sh\n", encoding="utf-8")
        self._run("5")
        self.assertIn("0 3 * * * /usr/bin/backup.sh", self._lines())

    def test_reinstalling_replaces_rather_than_duplicates(self):
        self._run("5")
        self._kill_bot()
        (self.dir / "data" / "autopilot.lock").unlink(missing_ok=True)
        self._run("15")
        entries = [ln for ln in self._lines() if "autopilot" in ln]
        self.assertEqual(len(entries), 2)
        self.assertTrue(any(ln.startswith("*/15 ") for ln in entries))

    def test_removing_leaves_only_the_users_own_entries(self):
        self.tab.write_text("0 3 * * * /usr/bin/backup.sh\n", encoding="utf-8")
        self._run("5")
        self._run("--remove")
        self.assertEqual(self._lines(), ["0 3 * * * /usr/bin/backup.sh"])

    def test_it_refuses_to_run_alongside_the_once_a_day_job(self):
        # Both installed means a daily --once cycle starting while the 24/7
        # bot is mid-run: two browsers, one account.
        self.tab.write_text(
            "0 9 * * * /x/run_daily.sh # linkedin-lightning-applier-daily\n",
            encoding="utf-8")
        proc = self._run("5")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("setup_cron.sh --remove", proc.stderr)

    def test_it_refuses_without_a_config(self):
        (self.dir / "config.yaml").unlink()
        proc = self._run("5")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("lla setup", proc.stderr)

    def test_a_nonsense_interval_is_refused(self):
        for bad in ("0", "60", "abc"):
            proc = self._run(bad)
            self.assertEqual(proc.returncode, 1, f"accepted interval {bad!r}")

    def test_both_installers_survive_an_empty_crontab(self):
        """Guards the `|| true` in both scripts against being tidied away."""
        for name in ("setup_cron.sh", "tools/setup_autopilot.sh"):
            text = (REPO / name).read_text(encoding="utf-8")
            install = [ln for ln in text.splitlines()
                       if "grep -v" in ln and "MARKER" in ln]
            self.assertTrue(install, f"{name}: no install line found")
            self.assertTrue(
                any("|| true" in ln for ln in install),
                f"{name}: `grep -v` exits 1 on an empty crontab and, under "
                "set -euo pipefail, aborts before installing anything")


class TestShellAndUnitFiles(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "bash not available")
    def test_the_shell_installers_parse(self):
        for name in ("setup_cron.sh", "run_daily.sh", "tools/setup_autopilot.sh"):
            proc = subprocess.run(["bash", "-n", str(REPO / name)],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, f"{name}: {proc.stderr}")

    def test_the_systemd_unit_has_the_sections_systemd_requires(self):
        text = (REPO / "tools" / "lla-autopilot.service").read_text(encoding="utf-8")
        for section in ("[Unit]", "[Service]", "[Install]"):
            self.assertIn(section, text)
        self.assertIn("ExecStart=", text)

    def test_the_unit_restarts_but_gives_up_on_a_crash_loop(self):
        text = (REPO / "tools" / "lla-autopilot.service").read_text(encoding="utf-8")
        self.assertIn("Restart=always", text)
        self.assertIn("StartLimitBurst=", text)

    def test_the_unit_stops_the_bot_gracefully(self):
        # SIGTERM lets main.py close Chrome and the database; SIGKILL does not.
        text = (REPO / "tools" / "lla-autopilot.service").read_text(encoding="utf-8")
        self.assertIn("KillSignal=SIGTERM", text)

    def test_the_unit_does_not_hardcode_a_secret(self):
        text = (REPO / "tools" / "lla-autopilot.service").read_text(encoding="utf-8")
        self.assertNotIn("API_KEY=", text.replace("EnvironmentFile=", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
