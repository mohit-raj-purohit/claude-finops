"""What to do right now: the few one-click actions that apply to this machine today.

Everything here already exists deeper in the dashboard (Running sessions, Skills &
MCP, Why so many tokens?). This picks the high-signal items only, so the overview
can act instead of just reporting:

  session     a running session carrying heavy context  -> Compact / Hand over
  skill       a shell command you keep re-running       -> Create skill
  memory      an instruction you keep re-typing          -> Add to CLAUDE.md
  statusline  no statusline configured                   -> Install

Prompt-based skill suggestions and one-off memory lines are left to their own pages:
they are noisy enough that a one-click card would mostly be wrong.
"""

MAX_SESSIONS = 3
MAX_SKILLS = 2
MAX_MEMORY = 2


def _k(n):
    return f"{n / 1000:.0f}K" if n < 1_000_000 else f"{n / 1_000_000:.1f}M"


def build(live, skills, memory, statusline):
    """live: procs.list_sessions rows; skills: actions.suggestions()["skills"];
    memory: Diagnoser.memory_suggestions(); statusline: integrate.statusline_state()."""
    items = []
    heavy = sorted((x for x in live if x.get("severity") in ("medium", "high") and x.get("context")),
                   key=lambda x: -x["context"])[:MAX_SESSIONS]
    for x in heavy:
        items.append({
            "kind": "session", "severity": x["severity"], "pid": x.get("pid"),
            "session_id": x.get("session_id"), "hosts_dashboard": bool(x.get("hosts_dashboard")),
            "title": f"{x.get('name') or 'A session'} is carrying {_k(x['context'])} tokens of context",
            "detail": f"{x.get('project') or ''} · every message re-reads all of it. "
                      + ("Hand it over to a fresh session." if x["severity"] == "high"
                         else "Compact it at the next break."),
        })
    cmds = sorted((s for s in skills if s.get("kind") == "command" and not s.get("installed")),
                  key=lambda s: -s.get("runs", 0))[:MAX_SKILLS]
    for s in cmds:
        items.append({
            "kind": "skill", "skill": s,
            "title": f"You ran `{s['trigger']}` {s['runs']} times in {s['sessions']} sessions",
            "detail": "Make it a skill so Claude runs it the way this project expects, "
                      "without re-explaining each time.",
        })
    themes = sorted((m for m in memory if m.get("kind") == "theme" and not m.get("already_saved")),
                    key=lambda m: -m.get("sessions", 0))[:MAX_MEMORY]
    for m in themes:
        items.append({
            "kind": "memory",
            "title": f"You keep re-typing instructions about: {m['text']}",
            "detail": f"Seen in {m['sessions']} sessions. Put it in {m['target']} once instead.",
        })
    if statusline is None:
        items.append({
            "kind": "statusline",
            "title": "See model, context % and 5-hour limit under every Claude Code prompt",
            "detail": "Adds a statusline to ~/.claude/settings.json (a backup is kept). "
                      "Undo any time with claude-finops --uninstall-statusline.",
        })
    return {"items": items}


def act_now(a):
    """Gather the live inputs and build the strip. Each source fails soft."""
    from .procs import list_sessions
    from .actions import suggestions
    from .diagnose import Diagnoser
    from .integrate import statusline_state

    def safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    return build(safe(lambda: list_sessions(a.pricing), []),
                 safe(lambda: suggestions(a)["skills"], []),
                 safe(lambda: Diagnoser(a).memory_suggestions({}), []),
                 safe(statusline_state, "other"))
