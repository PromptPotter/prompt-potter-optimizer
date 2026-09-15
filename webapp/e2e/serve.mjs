// The harness's own server: the real API, serving the real static export.
//
// It binds PP_E2E_PORT, never 8001: the operator runs their own uvicorn there and a harness
// that squats on it collides with the loop it is supposed to be checking. Point
// PP_E2E_BASE_URL at :8001 to walk theirs instead, and this never starts.
//
// The BUILD is not here. `output: "export"` means the browser only ever sees what `out/`
// holds, so a walk over a stale export is a false green — but two servers start side by side
// and only one tree can be built at a time, so `npm run e2e` builds once before Playwright
// runs. This refuses to serve an export that was never built at all.

import { spawn, spawnSync } from "node:child_process";
import { appendFileSync, existsSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEBAPP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO = path.resolve(WEBAPP, "..");
const PORT = process.env.PP_E2E_PORT || "8123";

// The repo venv, never a bare `python`: a system interpreter imports promptpotter but not
// its deps, so the app would mount and then die on the first real read.
const PYTHON =
  process.platform === "win32"
    ? path.join(REPO, ".venv", "Scripts", "python.exe")
    : path.join(REPO, ".venv", "bin", "python");

for (const [what, where, remedy] of [
  ["venv interpreter", PYTHON, 'pip install -e ".[all,dev]"'],
  ["static export", path.join(WEBAPP, "out", "index.html"), "npm run build"],
]) {
  if (!existsSync(where)) {
    console.error(`[e2e] no ${what} at ${where} — run \`${remedy}\``);
    process.exit(2);
  }
}

// PROMPTPOTTER_HOME is read once at import (`config/paths.py`), so the workspace is an
// environment decision made before the process starts — which is what lets the cold tier
// run against a throwaway tree instead of the operator's real campaigns.
const home = process.env.PROMPTPOTTER_HOME;

// A zero-campaign assertion is only honest against a tree with no campaigns in it, and the
// cold tier's previous run left some. PP_E2E_KEEP=1 holds a spend run's results for inspection;
// PP_E2E_DROP_CACHES=1 re-records the tape from nothing, which a changed optimizer prompt needs.
//
// THIS DECIDES WHETHER, `reset_world.py` DECIDES WHERE — and it does the deleting, so its
// docstring owns the path guard, the rule about what survives, and why an `fs.rmSync` here would
// be a second author for names that have one.
if (home && process.env.PP_E2E_RESET === "1" && process.env.PP_E2E_KEEP !== "1") {
  const args = [path.join(WEBAPP, "e2e", "reset_world.py"), path.resolve(home)];
  if (process.env.PP_E2E_DROP_CACHES === "1") args.push("--drop-caches");
  // PYTHONUTF8 for the same reason the server below takes it: this interpreter is cp1252 and
  // the reset's own report is the only place the operator sees whether a cache survived.
  const reset = spawnSync(PYTHON, args, {
    cwd: REPO,
    stdio: "inherit",
    env: { ...process.env, PYTHONUTF8: "1" },
  });
  if (reset.status !== 0) process.exit(reset.status ?? 2);
}

// WHAT THE BROWSER CANNOT SEE. The console guard watches the page; an exception the SERVER
// swallows never reaches it. `ledger.append` catches every subscriber failure and carries on, so
// `LiveDashboardProjection` crashed on `float(None)` for an entire L4 run while the dashboard
// silently stopped anchoring and all seven tests stayed green — the log line was the only thing
// that said so, and nothing was reading it.
//
// So the child's output is TEED: still forwarded verbatim (Playwright's webServer capture keeps
// showing it), and anything that reads as a fault is also appended here for `harness.ts` to fail
// on.
const FAULTS = path.join(os.tmpdir(), `pp-e2e-server-${PORT}.log`);
rmSync(FAULTS, { force: true });

// Both patterns are anchored to the LEVEL COLUMN, not to the word anywhere in the line: a URL or
// a message mentioning "ERROR" is not a fault, and a guard that fires on those gets muted within
// a week. WARNING is deliberately absent — the engine warns on states it handles.
const FAULT_LINE =
  /^\d{4}-\d{2}-\d{2} \S+ +(ERROR|CRITICAL)\b|^(ERROR|CRITICAL):|^Traceback \(most recent call last\):/;
const NEW_RECORD = /^\d{4}-\d{2}-\d{2} |^(INFO|WARNING|DEBUG|ERROR|CRITICAL):/;

// A fault is the marker PLUS the traceback under it — the marker alone names a crash without
// locating it. So a fault stays open until the next LOG RECORD begins. Closing on a blank line
// was the first attempt and it is wrong for this format: `logger.exception` emits no blank line
// after the traceback, so against a real captured run it swept in 7 warnings and 63 INFO lines
// and would have reddened every test after the first crash.
let open = false;

function tee(stream, out) {
  let held = "";
  stream.on("data", (chunk) => {
    out.write(chunk);
    const lines = (held + chunk.toString()).split("\n");
    held = lines.pop() ?? "";
    for (const line of lines) {
      if (FAULT_LINE.test(line)) open = true;
      else if (open && NEW_RECORD.test(line)) open = false;
      if (open) appendFileSync(FAULTS, line + "\n");
    }
  });
}

console.log(`[e2e] :${PORT} serving ${home ?? "<checkout workspace>"}`);
const server = spawn(
  PYTHON,
  ["-m", "uvicorn", "promptpotter.main:app", "--host", "127.0.0.1", "--port", PORT],
  {
    cwd: REPO,
    stdio: ["inherit", "pipe", "pipe"],
    env: {
      ...process.env,
      // `deps.py::resolve_identity` short-circuits to the CLI's resolver, so every
      // auth-gated read resolves to the on-disk workspace with no OIDC round-trip.
      PROMPTPOTTER_AUTH: "off",
      // This interpreter is cp1252; the live display writes box-drawing characters.
      PYTHONUTF8: "1",
    },
  },
);
tee(server.stdout, process.stdout);
tee(server.stderr, process.stderr);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    server.kill(signal);
    process.exit(0);
  });
}
server.on("exit", (code) => process.exit(code ?? 0));
