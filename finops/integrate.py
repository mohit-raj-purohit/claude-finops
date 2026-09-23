"""A statusline inside Claude Code itself, showing model and context usage.

  claude-finops --statusline    reads a statusline payload on stdin, prints model + context %
  claude-finops --install-statusline   wire it into settings
  claude-finops --hook          retired no-op, kept so existing installs do not error
  claude-finops --install-hook  prints that the hook is retired and does nothing else

The prompt hook that used to "suggest a cheaper model" is retired: that
suggestion had no basis — it would have meant repricing work that never ran.
The statusline is on the path of every prompt, so it is built to be boring:
fail silent, never block, never take long.
"""
import json
import os
import shutil
import sys

SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def _stdin_json():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return {}


def _dig(d, *paths, default=None):
    """First present value among dotted paths.

    The payload shapes are not identical across Claude Code versions, and a
    statusline that breaks on upgrade is worse than one that misses a field, so
    every read tries the spellings we know of.
    """
    for path in paths:
        cur = d
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                cur = None
                break
            cur = cur[part]
        if cur not in (None, ""):
            return cur
    return default


# -------------------------------------------------------------------- hook ----

def hook():
    """UserPromptSubmit: kept as a no-op so existing installed hooks do not error."""
    try:
        _stdin_json()
    except Exception:
        pass
    return 0


# -------------------------------------------------------------- statusline ----

def statusline():
    """One line, refreshed constantly: model and context usage."""
    try:
        d = _stdin_json()
        pct = _dig(d, "context.percentUsed", "context.percent_used")
        name = _dig(d, "model.display_name", "session.model", "model") or "claude"
        bits = [str(name)]
        if isinstance(pct, (int, float)):
            bits.append(f"{pct:.0f}% ctx")
        print(" · ".join(bits))
    except Exception:
        print("")     # an empty statusline beats a stack trace under the prompt
    return 0


# --------------------------------------------------------------- installing ----

def _command():
    """How to invoke us from settings: the installed CLI if it is on PATH."""
    exe = shutil.which("claude-finops")
    if exe:
        return exe
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"{sys.executable} {os.path.join(root, 'run.py')}"


def _load_settings():
    try:
        with open(SETTINGS) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_settings(data):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    if os.path.exists(SETTINGS):
        # Their settings file is not ours to lose.
        shutil.copy2(SETTINGS, SETTINGS + ".finops-backup")
    with open(SETTINGS, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def install_hook(remove=False):
    """The prompt hook is retired: it never had a basis for its "cheaper model"
    suggestion (that would mean repricing work that had not run yet). It is kept
    as a no-op (see hook() above) so existing installs do not error, but nothing
    new is installed here.
    """
    print("The finops prompt hook has been retired — it made suggestions with no "
          "basis in your data. Nothing was installed or changed.")
    print("The statusline (claude-finops --install-statusline) still shows model and context %.")


def install_statusline(remove=False):
    cmd = f"{_command()} --statusline"
    s = _load_settings()
    if remove:
        if "finops" in str(s.get("statusLine", "")):
            s.pop("statusLine", None)
    else:
        prev = s.get("statusLine")
        if prev and "finops" not in str(prev):
            print(f"You already have a statusLine configured:\n  {prev}")
            print("Leaving it alone. Remove it first if you want ours.")
            return
        s["statusLine"] = cmd
    _save_settings(s)
    print(("Removed" if remove else "Installed") + f" the statusline in {SETTINGS}")
    if not remove:
        print("  Shows the model and context usage percentage.")
        print("  Undo: claude-finops --uninstall-statusline")
