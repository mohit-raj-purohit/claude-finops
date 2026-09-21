#!/usr/bin/env node
"use strict";
/*
 * npm entry point. Node does no work here beyond finding a Python 3.9+ and
 * handing control to run.py, which stays the single source of truth.
 *
 * Interpreter search order: $CLAUDE_FINOPS_PYTHON, then the platform default.
 * The app writes only to ~/.claude-finops, so it runs fine from a read-only
 * npx cache.
 */
const { spawn, spawnSync } = require("child_process");
const path = require("path");

const RUN_PY = path.join(__dirname, "..", "run.py");
const MIN = [3, 9];

function candidates() {
  const explicit = process.env.CLAUDE_FINOPS_PYTHON;
  if (explicit) return [{ cmd: explicit, pre: [] }];
  return process.platform === "win32"
    ? [{ cmd: "py", pre: ["-3"] }, { cmd: "python", pre: [] }, { cmd: "python3", pre: [] }]
    : [{ cmd: "python3", pre: [] }, { cmd: "python", pre: [] }];
}

// Ask the interpreter its own version rather than parsing `--version` output,
// which differs across builds.
function versionOf(c) {
  const probe = spawnSync(c.cmd, [...c.pre, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                          { encoding: "utf8" });
  if (probe.error || probe.status !== 0) return null;
  const parts = String(probe.stdout).trim().split(".").map(Number);
  return parts.length === 2 && parts.every(Number.isFinite) ? parts : null;
}

function resolve() {
  let best = null;
  for (const c of candidates()) {
    const v = versionOf(c);
    if (!v) continue;
    if (v[0] > MIN[0] || (v[0] === MIN[0] && v[1] >= MIN[1])) return { ...c, v };
    best = best || { ...c, v };
  }
  return { tooOld: best };
}

const py = resolve();

if (py.tooOld) {
  console.error(`Claude FinOps needs Python ${MIN.join(".")}+, but found ${py.tooOld.v.join(".")}.`);
  console.error("Install a newer Python: https://www.python.org/downloads/");
  process.exit(1);
}
if (!py.cmd) {
  console.error("Claude FinOps needs Python 3.9+, which was not found on your PATH.");
  console.error(process.platform === "darwin"
    ? "Install it with:  brew install python3   (or https://www.python.org/downloads/)"
    : "Install it from:  https://www.python.org/downloads/");
  console.error("Already have one elsewhere? Set CLAUDE_FINOPS_PYTHON=/path/to/python3");
  process.exit(1);
}

const child = spawn(py.cmd, [...py.pre, "-X", "utf8", RUN_PY, ...process.argv.slice(2)],
                    { stdio: "inherit" });

// Forward the signals a user actually sends, so Ctrl-C stops the dashboard
// rather than orphaning it.
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => { try { child.kill(sig); } catch { /* already gone */ } });
}
child.on("error", (err) => {
  console.error(`Could not start ${py.cmd}: ${err.message}`);
  process.exit(1);
});
child.on("exit", (code, signal) => process.exit(signal ? 1 : code === null ? 1 : code));
