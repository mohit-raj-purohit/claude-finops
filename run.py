#!/usr/bin/env python3
"""Claude FinOps Command Center: one launcher for macOS, Windows and Linux.

  python3 run.py              build the warehouse if missing, then serve
  python3 run.py --rebuild    re-read transcripts first
  python3 run.py --stop       stop a running dashboard
  python3 run.py --share      write ../claude-finops.zip (code + defaults, never your data)
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


def main():
    args = sys.argv[1:]
    os.chdir(ROOT)
    ensure_dirs()
    migrate()
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
