#!/usr/bin/env python3
"""Claude FinOps Command Center: one launcher for macOS, Windows and Linux.

  python3 run.py              build the warehouse if missing, then serve
  python3 run.py --rebuild    re-read transcripts first
  python3 run.py --stop       stop a running dashboard
  python3 run.py --share      write ../claude-finops.zip (code + defaults, never your data)
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
import zipfile

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


def stop():
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
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        os.remove(pidfile)
        return True
    os.remove(pidfile)
    return False


def share(out=None):
    out = out or os.path.join(os.path.dirname(ROOT), "claude-finops.zip")
    skip_dirs = {"data", "__pycache__", ".git"}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if f.endswith((".pyc", ".zip", ".tgz")) or f in ("settings.local.json", "secrets.local.json"):
                    continue
                full = os.path.join(base, f)
                arc = os.path.join("claude-finops", os.path.relpath(full, ROOT)).replace(os.sep, "/")
                info = zipfile.ZipInfo.from_file(full, arc)
                info.external_attr = (0o755 if f.endswith((".sh", ".py")) else 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as fh:
                    z.writestr(info, fh.read())
    print(f"Wrote {out} (code + default config only; your data/ and local settings are excluded)")


HELP = """Claude FinOps Command Center

  claude-finops                 start the dashboard (builds the warehouse first run)
  claude-finops --rebuild       re-read your Claude Code transcripts, then start
  claude-finops --stop          stop a running dashboard
  claude-finops --where         print where your data, settings and keys live
  claude-finops --set-key       store a provider API key (prompts, never echoes)
  claude-finops --keys          list which provider keys are configured
  claude-finops --share         write ../claude-finops.zip (code only, never your data)
  claude-finops --help          this message

Environment:
  PORT=9000                     serve on another port (default 8787)
  CLAUDE_FINOPS_HOME=/path      where your data lives (default ~/.claude-finops)
  CLAUDE_PROJECTS=/path         where to read transcripts from
  CLAUDE_FINOPS_PYTHON=/path    which Python the npm wrapper should use
  NO_UPDATE_NOTIFIER=1          never check npm for a newer release
"""


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
    if "--help" in args or "-h" in args:
        return print(HELP)
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
    if "--share" in args:
        return share()
    stop()   # replace a previous detached server
    port = os.environ.get("PORT", "8787")
    source = os.environ.get("CLAUDE_PROJECTS", os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    if "--rebuild" in args or not os.path.exists(DB_PATH):
        if not os.path.isdir(source):
            sys.exit(f"No Claude Code transcripts at {source}. Use Claude Code once, or set CLAUDE_PROJECTS.")
        print(f"Building warehouse from {source} …")
        subprocess.run(_python() + ["-m", "finops.etl", source], check=True)
    rest = [a for a in args if a != "--rebuild"]
    if IS_WIN:
        print(f"Open http://127.0.0.1:{port}  (Ctrl-C to stop)")
    os.execv(sys.executable, _python() + ["-m", "finops.api", port] + rest) if not IS_WIN else \
        sys.exit(subprocess.call(_python() + ["-m", "finops.api", port] + rest))


if __name__ == "__main__":
    main()
