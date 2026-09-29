"""Made-up usage for screenshots and demos. Never reads anything from this machine.

  python3 tools/demo_data.py [home]      # default: a new temp dir
  CLAUDE_FINOPS_HOME=<home> CLAUDE_PROJECTS=<home>/projects ./run.sh --foreground

Writes Claude Code-shaped transcripts for a few invented projects under
<home>/projects, builds <home>/data/finops.db from them, and writes example
budgets to <home>/settings.local.json.
"""
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PROJECTS = {"shop-app": 1.6, "blog": 0.5, "data-pipeline": 1.0, "mobile-client": 0.8}
MODEL = "claude-sonnet-4-5"


def _usage(rng, ctx):
    out = rng.randint(200, 2500)
    return {"input_tokens": rng.randint(5, 60), "output_tokens": out,
            "cache_read_input_tokens": ctx, "cache_creation_input_tokens": rng.randint(500, 6000)}


def _session(rng, project, start, turns):
    cwd = f"/work/{project}"
    rows, ctx, ts = [], 20000, start
    for t in range(turns):
        ts += timedelta(seconds=rng.randint(20, 240))
        rows.append({"type": "user", "uuid": f"u{t}-{ts.timestamp()}", "timestamp": ts.isoformat(),
                     "cwd": cwd, "message": {"role": "user", "content": f"step {t + 1} of the {project} task"}})
        ts += timedelta(seconds=rng.randint(3, 40))
        ctx += rng.randint(1500, 9000)
        rows.append({"type": "assistant", "uuid": f"a{t}-{ts.timestamp()}", "timestamp": ts.isoformat(),
                     "requestId": f"req-{project}-{start.timestamp()}-{t}", "cwd": cwd,
                     "message": {"id": f"msg-{project}-{start.timestamp()}-{t}", "model": MODEL,
                                 "role": "assistant", "usage": _usage(rng, ctx),
                                 "content": [{"type": "text", "text": "done"}]}})
    return rows


def build(home, days=30, seed=7):
    from finops.etl import Loader
    rng = random.Random(seed)
    src = os.path.join(home, "projects")
    today = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    n = 0
    for project, weight in PROJECTS.items():
        pdir = os.path.join(src, "-work-" + project)
        os.makedirs(pdir, exist_ok=True)
        for d in range(days):
            if rng.random() > 0.35 * weight + 0.2:
                continue
            turns = int(rng.lognormvariate(2.3, 0.9)) + 1        # mostly short, a few very long
            start = today - timedelta(days=days - 1 - d, hours=rng.randint(0, 8))
            with open(os.path.join(pdir, f"demo-{project}-{d}.jsonl"), "w") as fh:
                for r in _session(rng, project, start, min(turns, 160)):
                    fh.write(json.dumps(r) + "\n")
            n += 1
    os.makedirs(os.path.join(home, "data"), exist_ok=True)
    # other_agents=False and desktop_roots=[]: never pick up this machine's real
    # Cursor/Codex/Gemini data or Claude desktop (Cowork) sessions
    Loader(db_path=os.path.join(home, "data", "finops.db"), source=src,
           other_agents=False, desktop_roots=[]).build(verbose=False)
    with open(os.path.join(home, "settings.local.json"), "w") as fh:
        json.dump({"budgets": {"monthly_usd": 400, "daily_usd": 25, "monthly_tokens": 900000000},
                   "guard": {"session_tokens": 8000000, "warn_pct": [75, 80],
                             "after_approval": "step", "step_pct": 25,
                             "projects": {"/work/shop-app": {"session_tokens": 15000000}}}}, fh, indent=2)
    open(os.path.join(home, ".migrated"), "w").close()
    return {"home": home, "projects": list(PROJECTS), "sessions": n}


if __name__ == "__main__":
    home = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="finops-demo-")
    out = build(home)
    print(f"{out['sessions']} demo sessions in {home}")
    print(f"CLAUDE_FINOPS_HOME={home} CLAUDE_PROJECTS={home}/projects ./run.sh --foreground")
