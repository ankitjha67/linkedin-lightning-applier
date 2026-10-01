"""Set up and verify the Chrome profile the bot signs in through.

    lla profile --check      is this profile actually signed into LinkedIn?
    lla profile --create     make the dedicated profile directory
    lla profile --login      open Chrome on it so you sign in once, by hand

`browser.user_data_dir` is the better way to authenticate an unattended bot:
you sign in once, by hand, and no LinkedIn password ever goes in config.yaml.
The documentation has always said to point it at a directory. Nothing created
that directory, and nothing checked whether the profile behind it had a session
— so pointing it at an empty path passed every check, and the bot then reached
LinkedIn's login page, asked for a manual login into a window nobody can see,
waited three minutes, and did it again. Looking healthy to the watchdog
throughout.

This closes that. A profile is verified by reading cookie *names* out of
Chrome's cookie database, opened read-only and immutable so it is safe even
while Chrome holds the file. Cookie values are encrypted and are never touched;
the presence and expiry of LinkedIn's `li_at` is all that is needed, and all
that is read.

Use a dedicated profile, not your everyday one. Chrome locks a profile
directory to one instance, so pointing the bot at the browser you actually use
means whichever starts second fails — and it would be the bot, silently, at
3am.
"""

import logging
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("lla.profile")

# Chrome's own convention. Already in .gitignore — it holds a live session.
DEFAULT_PROFILE_DIRNAME = "chrome-lla-profile"

# LinkedIn's authentication cookie. Its presence means a signed-in session; its
# value is encrypted and is never read.
AUTH_COOKIE = "li_at"
LINKEDIN_HOSTS = ("linkedin.com", ".linkedin.com", "www.linkedin.com")

# Chrome stores cookie expiry as microseconds since 1601-01-01 UTC.
_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def default_profile_dir(root: Path = None) -> Path:
    return (root or Path.cwd()) / DEFAULT_PROFILE_DIRNAME


def _chrome_time(value) -> datetime:
    try:
        return _CHROME_EPOCH + timedelta(microseconds=int(value))
    except (TypeError, ValueError, OverflowError):
        return None


def cookie_db(profile: Path) -> Path:
    """Where Chrome keeps cookies for the default profile of this directory."""
    for candidate in (profile / "Default" / "Network" / "Cookies",
                      profile / "Default" / "Cookies",
                      profile / "Network" / "Cookies",
                      profile / "Cookies"):
        if candidate.exists():
            return candidate
    return profile / "Default" / "Cookies"


def linkedin_session(profile: Path) -> dict:
    """What LinkedIn session this profile holds. Reads names only, never values.

    {present, expires_at, days_left, error}
    """
    out = {"present": False, "expires_at": None, "days_left": None, "error": ""}
    path = cookie_db(Path(profile))
    if not path.exists():
        out["error"] = "no cookie database — the profile has never been used"
        return out
    try:
        # Read-only and immutable, so this is safe while Chrome holds the file.
        con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    except Exception as exc:
        out["error"] = f"could not open the cookie database ({exc})"
        return out
    try:
        rows = con.execute(
            "SELECT host_key, expires_utc FROM cookies WHERE name = ?",
            (AUTH_COOKIE,)).fetchall()
    except Exception as exc:
        out["error"] = f"could not read cookies ({exc})"
        return out
    finally:
        try:
            con.close()
        except Exception:
            pass

    for host, expires in rows:
        if not any(h in (host or "") for h in LINKEDIN_HOSTS):
            continue
        out["present"] = True
        when = _chrome_time(expires)
        if when:
            out["expires_at"] = when
            out["days_left"] = round(
                (when - datetime.now(timezone.utc)).total_seconds() / 86400, 1)
        break
    if not out["present"] and not out["error"]:
        out["error"] = "no LinkedIn session in this profile — sign in once"
    return out


def profile_state(path) -> dict:
    """Everything worth knowing before trusting a profile to sign the bot in."""
    profile = Path(path).expanduser()
    state = {
        "path": str(profile),
        "exists": profile.exists(),
        "initialised": (profile / "Default").exists(),
        "locked": (profile / "SingletonLock").exists()
                  or (profile / "lockfile").exists(),
        "session": {"present": False, "error": "profile does not exist"},
    }
    if state["exists"]:
        state["session"] = linkedin_session(profile)
    return state


def usable(state: dict) -> bool:
    """Can this profile sign the bot in without a person present?"""
    session = state.get("session") or {}
    if not session.get("present"):
        return False
    days = session.get("days_left")
    return days is None or days > 0


def create(path) -> tuple:
    """Make the profile directory. (ok, message)."""
    profile = Path(path).expanduser()
    if profile.exists():
        return True, f"{profile} already exists"
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"could not create {profile}: {exc}"
    return True, f"created {profile}"


def login_command(path, binary: str = "") -> list:
    """The command that opens Chrome on this profile so a person can sign in.

    Deliberately not headless, and deliberately just LinkedIn's login page:
    the entire point is that a human does this part once.
    """
    if not binary:
        try:
            from env_doctor import detect_chrome_binary
            binary = detect_chrome_binary()
        except Exception:
            binary = ""
    binary = binary or ("chrome" if sys.platform.startswith("win") else "google-chrome")
    return [binary, f"--user-data-dir={Path(path).expanduser()}",
            "--no-first-run", "--no-default-browser-check",
            "https://www.linkedin.com/login"]


def wait_for_login(path, timeout_sec: int = 300, poll_sec: int = 3) -> dict:
    """Poll the profile until a LinkedIn session appears. Returns the session."""
    import time
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        session = linkedin_session(Path(path).expanduser())
        if session.get("present"):
            return session
        time.sleep(poll_sec)
    return linkedin_session(Path(path).expanduser())


def launch_for_login(path, binary: str = "") -> tuple:
    """Open Chrome on the profile for an interactive sign-in. (proc, command)."""
    command = login_command(path, binary)
    if not shutil.which(command[0]) and not Path(command[0]).exists():
        return None, command
    try:
        proc = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except Exception as exc:
        log.debug("could not launch the browser: %s", exc)
        return None, command
    return proc, command


def format_state(state: dict) -> str:
    session = state.get("session") or {}
    lines = ["", f"  Profile   {state['path']}"]
    if not state["exists"]:
        lines.append("  Status    does not exist")
        lines.append("\n  Create it, then sign in once:")
        lines.append("    lla profile --create")
        lines.append("    lla profile --login")
        return "\n".join(lines)

    lines.append(f"  Status    {'initialised' if state['initialised'] else 'empty'}"
                 + ("  (locked — Chrome is using it right now)"
                    if state["locked"] else ""))
    if session.get("present"):
        days = session.get("days_left")
        if days is None:
            lines.append("  LinkedIn  signed in")
        elif days <= 0:
            lines.append(f"  LinkedIn  session EXPIRED {abs(days):.0f} day(s) ago")
            lines.append("\n  Sign in again:  lla profile --login")
        else:
            lines.append(f"  LinkedIn  signed in — {days:.0f} day(s) left")
            if days < 7:
                lines.append("            expiring soon; sign in again when it does")
    else:
        lines.append(f"  LinkedIn  not signed in ({session.get('error', '')})")
        lines.append("\n  Sign in once, by hand:  lla profile --login")
        lines.append("  Until then the bot cannot authenticate on its own.")
    return "\n".join(lines)
