#!/usr/bin/env python3
"""Claude FinOps Command Center: one launcher for macOS, Windows and Linux.

  python3 run.py              build the warehouse if missing, then serve
  python3 run.py --rebuild    re-read transcripts first
  python3 run.py --stop       stop a running dashboard
  python3 run.py --set-key    store a provider API key
  python3 run.py --where      print where your data and keys live
  python3 run.py --help       all commands
  PORT=9000 python3 run.py    different port

Your warehouse and local settings live in ~/.claude-finops (CLAUDE_FINOPS_HOME to
override), never in this folder, so upgrading or repackaging cannot touch them.
"""
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from finops.paths import DATA_DIR as DATA, HOME_DIR, PIDFILE, DB_PATH, migrate, ensure_dirs  # noqa: E402

LEGACY_PIDFILE = os.path.join(ROOT, "data", "server.pid")
IS_WIN = os.name == "nt"


def _python():
    # UTF-8 mode: transcripts are UTF-8, and Windows would otherwise default to cp1252
    return [sys.executable, "-X", "utf8"]


def _alive(pid):
    if IS_WIN:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _free_port(start):
    """The first free port at or above start+1, for the suggestion we print."""
    import socket
    for p in range(start + 1, start + 40):
        with socket.socket() as sk:
            try:
                sk.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start + 1


def _port_owner(port):
    """PID listening on 127.0.0.1:<port>, or None.

    The pidfile is not enough on its own: a dashboard started from a different
    copy (a global npm install alongside a checkout) writes its own, and one
    that was killed hard leaves a stale file behind. Asking the OS who actually
    holds the port is the only answer that is always true.
    """
    try:
        if IS_WIN:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                f = line.split()
                if len(f) >= 5 and f[1].endswith(f":{port}") and f[3] == "LISTENING":
                    return int(f[4])
            return None
        out = subprocess.run(["lsof", "-tnP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.split()[0]) if out.split() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _is_ours(pid):
    """True only if that PID is a claude-finops server.

    Whatever is on the port may be someone else's service. We stop our own
    dashboard without asking; anything else we report and leave alone.
    """
    try:
        if IS_WIN:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f'Get-CimInstance Win32_Process -Filter "ProcessId={pid}" | '
                 "Select-Object -ExpandProperty CommandLine"],
                capture_output=True, text=True, timeout=10).stdout
        else:
            out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "finops.api" in out or "claude-finops" in out


def _kill(pid):
    if IS_WIN:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)


def stop(port=None):
    """Stop a running dashboard. Returns True if we stopped one."""
    if stop_pidfile():
        return True
    # No usable pidfile: fall back to whoever owns the port, but only if it is
    # ours to stop.
    pid = _port_owner(port or os.environ.get("PORT", "8787"))
    if pid and _is_ours(pid):
        try:
            _kill(pid)
            return True
        except OSError:
            return False
    return False


def stop_pidfile():
    for pidfile in (PIDFILE, LEGACY_PIDFILE):   # LEGACY_: a server started before the move
        try:
            with open(pidfile) as fh:
                pid = int(fh.read().strip())
            break
        except (OSError, ValueError):
            continue
    else:
        return False
    if _alive(pid):
        _kill(pid)
        os.remove(pidfile)
        return True
    os.remove(pidfile)
    return False


HELP = """Claude FinOps Command Center

  claude-finops                 start the dashboard (builds the warehouse first run)
  claude-finops --rebuild       re-read your Claude Code transcripts, then start
  claude-finops --stop          stop a running dashboard
  claude-finops --where         print where your data, settings and keys live
  claude-finops --set-key       store a provider API key (prompts, never echoes)
  claude-finops --keys          list which provider keys are configured
  claude-finops --version       print the installed version, and whether a newer one is out
  claude-finops --no-update-check   skip the once-a-day npm version check
  claude-finops --install-hook  suggest a cheaper model in Claude Code, as you send each prompt
  claude-finops --install-statusline   show model, context and advice in your statusline
  claude-finops --help          this message

Environment:
  PORT=9000                     serve on another port (default 8787)
  CLAUDE_FINOPS_HOME=/path      where your data lives (default ~/.claude-finops)
  CLAUDE_PROJECTS=/path         where to read transcripts from
  CLAUDE_FINOPS_PYTHON=/path    which Python the npm wrapper should use
  NO_UPDATE_NOTIFIER=1          never check npm for a newer release
  CLAUDE_FINOPS_NO_UPDATE_CHECK=1   same as --no-update-check
"""


def _version():
    from finops.update import _installed
    return _installed()


def version():
    """Which copy is this, and is it current?

    The second half matters more than the first: people run a global install and
    a repo checkout side by side, and the usual confusion is not "what version
    am I on" but "why does the one I am looking at not have the feature".
    """
    from finops.update import check, disabled, _key, _installed
    # Print what we know locally first: the network check can be slow, disabled,
    # or simply fail, and none of that should delay the one line people actually
    # came here for.
    print(f"  claude-finops {_installed() or 'unknown'}")
    print(f"  installed at  {ROOT}")
    if disabled():
        print("  update        check is off (NO_UPDATE_NOTIFIER)")
        return
    # Asking outright is worth a fresh request: a day-old cached answer is the
    # one thing this command must not give you.
    u = check(force=True)
    if u.get("update_available"):
        print(f"  update        {u['latest']} is out - {u['command']}")
    elif u.get("latest") and _key(u["current"]) > _key(u["latest"]):
        # A checkout mid-release is ahead of what is published. Saying "up to
        # date" there would hide exactly the gap you are looking for.
        print(f"  update        ahead of npm (published latest is {u['latest']})")
    elif u.get("latest"):
        print(f"  update        up to date (npm latest is {u['latest']})")
    else:
        print("  update        could not reach the npm registry")


def where():
    from finops.paths import (HOME_DIR, DB_PATH, LOCAL_SETTINGS_PATH, SECRETS_PATH,
                              LOGFILE, CONFIG_DIR)
    rows = [("state dir", HOME_DIR), ("warehouse", DB_PATH), ("your settings", LOCAL_SETTINGS_PATH),
            ("your API keys", SECRETS_PATH), ("server log", LOGFILE),
            ("shipped defaults", CONFIG_DIR)]
    for label, path in rows:
        mark = " " if os.path.exists(path) else " (not created yet)"
        print(f"  {label:<17} {path}{mark}")


def _providers():
    from finops.cloud import PROVIDERS
    return PROVIDERS


def keys():
    """Show which keys are configured, and where each one came from."""
    import json
    from finops.paths import SECRETS_PATH
    try:
        stored = json.load(open(SECRETS_PATH))
    except (OSError, ValueError):
        stored = {}
    for pid, p in _providers().items():
        env, field = p["env"], p["field"]
        if os.environ.get(env):
            src = f"set (from ${env})"
        elif stored.get(field):
            src = "set (stored)"
        else:
            src = f"not set — --set-key {pid}, or export {env}=..."
        print(f"  {p['name']:<20} {src}")
    print(f"\n  Stored keys live in {SECRETS_PATH}")


def set_key(pid=None):
    """Prompt for a key and store it 0600. The value is never echoed or logged."""
    import getpass
    import json
    from finops.paths import SECRETS_PATH
    provs = _providers()
    if pid not in provs:
        if pid:
            print(f"Unknown provider {pid!r}.")
        print("Which provider?")
        for k, p in provs.items():
            print(f"  {k:<12} {p['name']}")
        pid = input("provider: ").strip()
        if pid not in provs:
            sys.exit("Cancelled.")
    p = provs[pid]
    print(f"{p['name']}: paste the key (input stays hidden), or press Enter to remove it.")
    value = getpass.getpass("key: ").strip()
    try:
        data = json.load(open(SECRETS_PATH))
    except (OSError, ValueError):
        data = {}
    if value:
        data[p["field"]] = value
    else:
        data.pop(p["field"], None)
    fd = os.open(SECRETS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    print(f"{'Stored' if value else 'Removed'} — {SECRETS_PATH}")
    print("Restart the dashboard for it to take effect: claude-finops --stop && claude-finops")


def main():
    args = sys.argv[1:]
    os.chdir(ROOT)
    ensure_dirs()
    migrate()
    if "--no-update-check" in args:
        os.environ["CLAUDE_FINOPS_NO_UPDATE_CHECK"] = "1"
    if "--help" in args or "-h" in args:
        return print(HELP)
    if "--version" in args or "-v" in args or "-V" in args:
        return version()
    # Integration entry points. --hook and --statusline are invoked by Claude Code
    # with a JSON payload on stdin, many times a session; they print one line and
    # never fail loudly.
    if "--hook" in args:
        from finops.integrate import hook
        return sys.exit(hook())
    if "--statusline" in args:
        from finops.integrate import statusline
        return sys.exit(statusline())
    if "--install-hook" in args or "--uninstall-hook" in args:
        from finops.integrate import install_hook
        return install_hook(remove="--uninstall-hook" in args)
    if "--install-statusline" in args or "--uninstall-statusline" in args:
        from finops.integrate import install_statusline
        return install_statusline(remove="--uninstall-statusline" in args)
    if "--where" in args:
        return where()
    if "--keys" in args:
        return keys()
    if "--set-key" in args:
        i = args.index("--set-key")
        return set_key(args[i + 1] if len(args) > i + 1 else None)
    if "--stop" in args:
        print("Stopped." if stop() else "Not running.")
        return
    # A flag we do not know used to fall straight through and start the
    # dashboard, so a typo (or a flag from a newer release than the one you
    # have installed) looked like the command silently doing nothing.
    known = {"--rebuild", "--detach", "--foreground", "--no-update-check"}
    unknown = [a for a in args if a.startswith("-") and a not in known]
    if unknown:
        print(f"Unknown option: {unknown[0]}")
        print(f"This is claude-finops {_version() or 'unknown'}; "
              f"run --help to see what it supports.")
        sys.exit(2)
    port = os.environ.get("PORT", "8787")
    # Decide from who actually holds *this* port, not from the pidfile alone:
    # the pidfile may describe a dashboard serving some other port entirely.
    owner = _port_owner(port)
    if owner is None or _is_ours(owner):
        if owner is not None:
            # flush=True: execv below replaces this process without flushing,
            # so an unflushed line would simply never reach your terminal.
            print(f"Restarting the dashboard already running on {port} …", flush=True)
        stop(port)                                # replace it, whoever started it
        for _ in range(20):                       # give the socket a moment to clear
            if _port_owner(port) is None:
                break
            time.sleep(0.25)
    # Still occupied means it is not ours: say so instead of dying in a traceback.
    if (busy := _port_owner(port)) is not None:
        free = _free_port(int(port))
        print(f"Port {port} is already in use by PID {busy}, and it is not a "
              f"claude-finops dashboard.")
        print(f"Start on another port:  PORT={free} claude-finops")
        print(f"Or find out what is holding it:  "
              + (f"netstat -ano -p TCP | findstr :{port}" if IS_WIN
                 else f"lsof -iTCP:{port} -sTCP:LISTEN"))
        sys.exit(1)
    source = os.environ.get("CLAUDE_PROJECTS", os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    from finops.etl import needs_rebuild
    db_existed = os.path.exists(DB_PATH)
    rebuild_needed = needs_rebuild(DB_PATH)
    if "--rebuild" in args or rebuild_needed:
        if not os.path.isdir(source):
            sys.exit(f"No Claude Code transcripts at {source}. Use Claude Code once, or set CLAUDE_PROJECTS.")
        if db_existed and rebuild_needed and "--rebuild" not in args:
            print("Warehouse schema changed (pricing and request counting were corrected); rebuilding…",
                  flush=True)
        print(f"Building warehouse from {source} …", flush=True)
        subprocess.run(_python() + ["-m", "finops.etl", source], check=True)
    rest = [a for a in args if a != "--rebuild"]
    if IS_WIN:
        print(f"Open http://127.0.0.1:{port}  (Ctrl-C to stop)")
    os.execv(sys.executable, _python() + ["-m", "finops.api", port] + rest) if not IS_WIN else \
        sys.exit(subprocess.call(_python() + ["-m", "finops.api", port] + rest))


if __name__ == "__main__":
    main()
