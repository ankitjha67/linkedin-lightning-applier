#!/usr/bin/env python3
"""Keep the bot running around the clock, and never run two of it.

    lla autopilot status      # is it up, how long, how many applies today
    lla autopilot start       # start it if it is not already running
    lla autopilot stop        # stop it
    lla autopilot watch       # what cron calls: restart it if it died

The bot's own loop is already continuous — it scans every `scan_interval_minutes`
and `active_hours_start: 0 / active_hours_end: 24` means all day. What a long
run actually needs is not a scheduler but a *supervisor*: something to start it
after a reboot, restart it when Chrome takes the process down with it, and stop
a second copy ever being launched.

That last one matters most. Two instances means two Chrome sessions signed into
one LinkedIn account, applying to the same jobs twice. It is both the fastest
way to look like a bot and the easiest mistake to make — a cron entry that
fires while the previous run is still going does it automatically. So `start`
and `watch` take a lock, and refuse rather than double up.

Restarting is safe because the daily cap lives in SQLite, not in memory:
`state.daily_applied_count()` reads `daily_stats` for today, so a bot that
restarts forty times still stops at `max_applies_per_day`. A supervisor that
reset the count on restart would quietly turn a crash loop into a ban.

Crash loops are handled rather than amplified. If the bot keeps dying within
`MIN_HEALTHY_RUN` of starting, `watch` backs off instead of hammering it, and
says why.
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "data" / "autopilot.lock"
LOG_DIR = ROOT / "logs"

TIME_FMT = "%Y-%m-%dT%H:%M:%S"

# A run that dies sooner than this never really started — a bad config, a
# missing driver, LinkedIn refusing the login. Restarting on a loop will not
# fix any of those, so back off and make the reason visible instead.
MIN_HEALTHY_RUN = 120          # seconds
MAX_FAST_FAILURES = 3          # consecutive short runs before backing off
BACKOFF_MINUTES = 30           # how long to wait after that
STOP_GRACE = 20                # seconds to let the bot shut down cleanly


# ---------------------------------------------------------------------------
# Lock file — the record of what we started
# ---------------------------------------------------------------------------

def read_lock() -> dict:
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_lock(data: dict):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOCK_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(LOCK_PATH)          # atomic: never a half-written lock


def clear_lock():
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Is it actually alive?
# ---------------------------------------------------------------------------

def process_command(pid: int) -> str:
    """The command line of `pid`, or '' if it cannot be read.

    PIDs are recycled — after a reboot, the number in our lock file is very
    likely someone else's process. Checking that the command still looks like
    our bot is what stops the supervisor reporting a stranger as "running" and
    refusing to start.
    """
    if pid <= 0:
        return ""
    if sys.platform.startswith("win"):
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=15)
            line = (out.stdout or "").strip()
            return line if str(pid) in line else ""
        except Exception:
            return ""
    proc = Path(f"/proc/{pid}/cmdline")
    if proc.exists():
        try:
            return proc.read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace").strip()
        except Exception:
            return ""
    try:                                    # macOS and other BSDs
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=15)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def process_state(pid: int) -> str:
    """The kernel's one-letter state for `pid`, or '' if unknown.

    'Z' is the one that matters. A process that has exited but not yet been
    reaped still answers `os.kill(pid, 0)` and still has a /proc entry, so
    without this check a dead bot reads as running for as long as nobody
    reaps it — and the supervisor politely declines to restart it, forever.
    """
    if sys.platform.startswith("win"):
        return ""
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            text = stat.read_text(encoding="utf-8", errors="replace")
            # The comm field is parenthesised and may itself contain spaces or
            # brackets, so the state is the first field after the LAST ')'.
            return text[text.rindex(")") + 1:].split()[0]
        except Exception:
            return ""
    try:                                    # macOS and other BSDs
        out = subprocess.run(["ps", "-p", str(pid), "-o", "state="],
                             capture_output=True, text=True, timeout=15)
        return (out.stdout or "").strip()[:1]
    except Exception:
        return ""


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        return bool(process_command(pid))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                         # alive, just not ours to signal
    except Exception:
        return False
    # Exited but not yet reaped — gone, as far as the bot is concerned.
    return not process_state(pid).startswith("Z")


def is_running() -> tuple:
    """(running, info). Clears a lock whose process is gone or was recycled."""
    lock = read_lock()
    pid = int(lock.get("pid") or 0)
    if not pid:
        return False, lock
    if not pid_alive(pid):
        return False, lock
    command = process_command(pid)
    if not command:
        # A live process always has a command line. An empty one means the
        # entry is a zombie or a kernel thread — either way, not our bot.
        return False, lock
    # On Windows tasklist gives the image name only, so a python process is as
    # specific as we get; elsewhere require the bot's own entry point.
    looks_like_ours = (
        "python" in command.lower()
        if sys.platform.startswith("win")
        else ("main.py" in command or "autopilot" in command)
    )
    if not looks_like_ours:
        # The PID was reused by something unrelated. Our bot is not running.
        return False, lock
    return True, lock


# ---------------------------------------------------------------------------
# Starting and stopping
# ---------------------------------------------------------------------------

def _python() -> str:
    """Prefer the project venv, the way run_daily.sh does."""
    for candidate in (ROOT / "venv" / "bin" / "python",
                      ROOT / ".venv" / "bin" / "python",
                      ROOT / "venv" / "Scripts" / "python.exe",
                      ROOT / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python3"


def _load_dotenv():
    """cron gives a process almost no environment; .env is where the keys are."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except Exception:
        pass


def start(config: str = "config.yaml", reason: str = "manual") -> tuple:
    """Launch the bot detached. (ok, message)."""
    running, lock = is_running()
    if running:
        return False, (f"already running (pid {lock.get('pid')}, started "
                       f"{lock.get('started_at', '?')}) — not starting a second copy")

    cfg_path = ROOT / config
    if not cfg_path.exists():
        return False, (f"{config} not found. Run `lla setup` first — the bot "
                       "cannot log in without it.")

    _load_dotenv()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"autopilot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    handle = open(log_path, "ab", buffering=0)

    cmd = [_python(), str(ROOT / "main.py"), "-c", str(cfg_path)]
    kwargs = {"cwd": str(ROOT), "stdout": handle, "stderr": subprocess.STDOUT,
              "stdin": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — survives the console
        # that cron/Task Scheduler opened to launch it.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True      # survives the cron shell exiting

    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except Exception as exc:
        handle.close()
        return False, f"could not start the bot: {exc}"
    finally:
        # The child has its own descriptor for the log now. Keeping ours open
        # leaks one per start, and holds the file even after the bot rotates
        # or the log is deleted.
        handle.close()

    starts = list(lock.get("starts") or [])[-19:]
    starts.append(datetime.now().strftime(TIME_FMT))
    write_lock({
        "pid": proc.pid,
        "started_at": datetime.now().strftime(TIME_FMT),
        "config": config,
        "log": str(log_path),
        "reason": reason,
        "starts": starts,
    })
    return True, f"started (pid {proc.pid}), logging to {log_path}"


def stop(timeout: int = STOP_GRACE) -> tuple:
    """Ask the bot to stop, then insist. (ok, message)."""
    running, lock = is_running()
    if not running:
        clear_lock()
        return False, "not running"
    pid = int(lock.get("pid"))

    # main.py installs a SIGTERM handler that closes the browser and the
    # database cleanly, so give it that chance before killing it.
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(pid), "/T"],
                           capture_output=True, timeout=20)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        return False, f"could not signal pid {pid}: {exc}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            clear_lock()
            return True, f"stopped (pid {pid})"
        time.sleep(1)

    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20)
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception as exc:
        return False, f"pid {pid} would not stop: {exc}"
    clear_lock()
    return True, f"stopped (pid {pid}, forced after {timeout}s)"


# ---------------------------------------------------------------------------
# Crash-loop backoff
# ---------------------------------------------------------------------------

def recent_fast_failures(lock: dict, now=None) -> int:
    """How many times in a row it died almost immediately after starting.

    Consecutive starts less than MIN_HEALTHY_RUN apart mean each run died
    almost at once. Restarting again will not help — something is wrong that a
    restart cannot fix — so this is what `watch` backs off on.
    """
    now = now or datetime.now()
    stamps = []
    for raw in (lock.get("starts") or []):
        try:
            stamps.append(datetime.strptime(raw, TIME_FMT))
        except (ValueError, TypeError):
            continue
    if len(stamps) < 2:
        return 0
    stamps.sort()
    # Walk backwards while each run was shorter than MIN_HEALTHY_RUN.
    boundaries = stamps + [now]
    failures = 0
    for i in range(len(boundaries) - 1, 0, -1):
        if (boundaries[i] - boundaries[i - 1]).total_seconds() < MIN_HEALTHY_RUN:
            failures += 1
        else:
            break
    return failures


def backing_off(lock: dict, now=None) -> tuple:
    """(should_wait, message)."""
    now = now or datetime.now()
    failures = recent_fast_failures(lock, now)
    if failures < MAX_FAST_FAILURES:
        return False, ""
    last = (lock.get("starts") or [])[-1:]
    try:
        last_start = datetime.strptime(last[0], TIME_FMT) if last else None
    except (ValueError, TypeError):
        last_start = None
    if last_start and now - last_start < timedelta(minutes=BACKOFF_MINUTES):
        resume = last_start + timedelta(minutes=BACKOFF_MINUTES)
        return True, (
            f"{failures} runs in a row died within {MIN_HEALTHY_RUN}s of "
            f"starting — not restarting until {resume.strftime('%H:%M')}. "
            f"Check the newest logs/autopilot_*.log: a restart will not fix a "
            "bad config, a Chrome/driver mismatch, or a login LinkedIn is "
            "refusing.")
    return False, ""


def watch(config: str = "config.yaml") -> tuple:
    """What cron calls. (action, message)."""
    running, lock = is_running()
    if running:
        return "running", (f"already running (pid {lock.get('pid')}, since "
                           f"{lock.get('started_at', '?')})")
    wait, why = backing_off(lock)
    if wait:
        return "backoff", why
    ok, msg = start(config, reason="watchdog")
    return ("started" if ok else "failed"), msg


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def _uptime(started_at: str) -> str:
    try:
        delta = datetime.now() - datetime.strptime(started_at, TIME_FMT)
    except (ValueError, TypeError):
        return "unknown"
    seconds = int(delta.total_seconds())
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def applies_today(config: str = "config.yaml") -> tuple:
    """(applied, cap). (None, None) if the database cannot be read."""
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / config).read_text(encoding="utf-8")) or {}
    except Exception:
        return None, None
    sched = cfg.get("scheduling", {}) or {}
    cap = sched.get("max_applies_per_day", 40)
    try:
        sys.path.insert(0, str(ROOT))
        from state import State
        state = State(db_path=(cfg.get("state", {}) or {}).get(
            "db_path", "data/state.db"))
        applied = state.daily_applied_count()
        try:
            state.close()
        except Exception:
            pass
        return applied, cap
    except Exception:
        return None, cap


def status(config: str = "config.yaml") -> dict:
    running, lock = is_running()
    applied, cap = applies_today(config)
    return {
        "running": running,
        "pid": lock.get("pid") if running else None,
        "started_at": lock.get("started_at") if running else None,
        "uptime": _uptime(lock.get("started_at", "")) if running else "",
        "log": lock.get("log", ""),
        "reason": lock.get("reason", ""),
        "applied_today": applied,
        "daily_cap": cap,
        "fast_failures": recent_fast_failures(lock),
    }


def format_status(s: dict) -> str:
    lines = [""]
    if s["running"]:
        lines.append(f"  Running    pid {s['pid']}, up {s['uptime']}")
        lines.append(f"  Started    {s['started_at']}  ({s['reason'] or 'manual'})")
        if s["log"]:
            lines.append(f"  Log        {s['log']}")
    else:
        lines.append("  Not running")
        if s["fast_failures"] >= MAX_FAST_FAILURES:
            lines.append(f"  {s['fast_failures']} fast failures in a row — "
                         "autopilot is backing off. Read the newest log.")

    if s["applied_today"] is not None:
        cap = s["daily_cap"]
        lines.append(f"  Today      {s['applied_today']} of {cap} applications")
        if cap and s["applied_today"] >= cap:
            lines.append("             daily cap reached — the bot will idle "
                         "until midnight rather than keep applying")
    if not s["running"]:
        lines.append("\n  Start it with:  lla autopilot start")
        lines.append("  Run 24/7 with:  bash tools/setup_autopilot.sh")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv):
    action = (argv[0] if argv else "status").lower()
    config = "config.yaml"
    if "-c" in argv:
        i = argv.index("-c")
        if i + 1 < len(argv):
            config = argv[i + 1]

    if action == "status":
        print(format_status(status(config)))
        return 0
    if action == "start":
        ok, msg = start(config)
        print(f"  {msg}")
        return 0 if ok else 1
    if action == "stop":
        ok, msg = stop()
        print(f"  {msg}")
        return 0 if ok else 1
    if action == "restart":
        stop()
        ok, msg = start(config, reason="restart")
        print(f"  {msg}")
        return 0 if ok else 1
    if action == "watch":
        outcome, msg = watch(config)
        # cron mails anything printed, so stay quiet on the normal path.
        if outcome != "running":
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"{stamp} autopilot {outcome}: {msg}")
        return 0 if outcome in ("running", "started") else 1

    print(__doc__.strip().splitlines()[0])
    print("\n  actions: status, start, stop, restart, watch")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
