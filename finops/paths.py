"""Where the app reads code from, and where it writes your data to.

Everything the app *writes* lives under one state directory, outside the
install tree:

  $CLAUDE_FINOPS_HOME  if set, else  ~/.claude-finops

That separation is what lets the app ship as a frozen binary or an npm/pipx
package: those install directories are cache-managed and read-only, and get
replaced wholesale on upgrade. Keeping the warehouse and your local settings
out of them means an upgrade can never take your data with it.

Anything the app only *reads* (config defaults, web assets) stays in the
install tree next to the code.

On first run, an older in-tree layout (ROOT/data, ROOT/config/*.local.json)
is copied across by migrate(). It is a copy: the originals are left exactly
where they are, so a half-finished migration can never lose anything.
"""
import os
import shutil
import sqlite3

# Install tree: read-only at runtime (code, config defaults, web/).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
WEB_DIR = os.path.join(ROOT, "web")

# The legacy in-tree locations we migrate away from.
LEGACY_DATA_DIR = os.path.join(ROOT, "data")

# State tree: everything we write.
HOME_DIR = os.environ.get("CLAUDE_FINOPS_HOME") or os.path.join(
    os.path.expanduser("~"), ".claude-finops")
DATA_DIR = os.path.join(HOME_DIR, "data")

DB_PATH = os.path.join(DATA_DIR, "finops.db")
CACHE_PATH = os.path.join(DATA_DIR, "cloud_cache.json")
PIDFILE = os.path.join(DATA_DIR, "server.pid")
LOGFILE = os.path.join(DATA_DIR, "server.log")

# Read-only shared defaults stay in the install tree; per-machine overrides
# (written by the Settings screen) move to the state tree.
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
PRICING_PATH = os.path.join(CONFIG_DIR, "pricing.json")
LOCAL_SETTINGS_PATH = os.path.join(HOME_DIR, "settings.local.json")
SECRETS_PATH = os.path.join(HOME_DIR, "secrets.local.json")

_MARKER = os.path.join(HOME_DIR, ".migrated")


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)


def _copy_db(src, dst):
    """Copy a SQLite database safely, even if a server is holding it open.

    The backup API resolves the WAL for us; a plain file copy of a live
    database can capture a torn snapshot with its -wal left behind.
    """
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()


def migrate(log=print):
    """Copy an older in-tree layout into the state tree. Never moves, never
    deletes, and never overwrites something already migrated."""
    ensure_dirs()
    if os.path.exists(_MARKER):
        return False

    moved = []

    legacy_db = os.path.join(LEGACY_DATA_DIR, "finops.db")
    if os.path.exists(legacy_db) and not os.path.exists(DB_PATH):
        _copy_db(legacy_db, DB_PATH)
        moved.append("data/finops.db")

    legacy_cache = os.path.join(LEGACY_DATA_DIR, "cloud_cache.json")
    if os.path.exists(legacy_cache) and not os.path.exists(CACHE_PATH):
        shutil.copy2(legacy_cache, CACHE_PATH)
        moved.append("data/cloud_cache.json")

    for name, dst in (("settings.local.json", LOCAL_SETTINGS_PATH),
                      ("secrets.local.json", SECRETS_PATH)):
        legacy = os.path.join(CONFIG_DIR, name)
        if os.path.exists(legacy) and not os.path.exists(dst):
            shutil.copy2(legacy, dst)
            os.chmod(dst, 0o600)
            moved.append(f"config/{name}")

    if moved:
        log(f"Moved your data to {HOME_DIR}: {', '.join(moved)}")
        log("The originals were left untouched; delete them once you are happy.")
    with open(_MARKER, "w") as fh:
        fh.write("state dir in use; delete this file to re-run migration\n")
    return bool(moved)
