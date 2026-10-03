// The harness's own server: the real API over the real static export, on PP_E2E_PORT, never the
// operator's :8001. It does not build — `npm run e2e` builds once, since two servers start at once.

import { spawn, spawnSync } from "node:child_process";
import { appendFileSync, existsSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEBAPP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO = path.resolve(WEBAPP, "..");
const PORT = process.env.PP_E2E_PORT || "8123";

// Never a bare `python`: a system interpreter imports promptpotter but not its deps.
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

// PROMPTPOTTER_HOME is read once at import (`config/paths.py`), so it must be set before spawn.
const home = process.env.PROMPTPOTTER_HOME;

// This decides WHETHER to reset; `reset_world.py` decides WHAT and does the deleting — never an
// `fs.rmSync` here.
if (home && process.env.PP_E2E_RESET === "1" && process.env.PP_E2E_KEEP !== "1") {
  const args = [path.join(WEBAPP, "e2e", "reset_world.py"), path.resolve(home)];
  if (process.env.PP_E2E_DROP_CACHES === "1") args.push("--drop-caches");
  // This interpreter is cp1252.
  const reset = spawnSync(PYTHON, args, {
    cwd: REPO,
    stdio: "inherit",
    env: { ...process.env, PYTHONUTF8: "1" },
  });
  if (reset.status !== 0) process.exit(reset.status ?? 2);
}

// Server output is TEED so `harness.ts` fails on faults the server swallows (`ledger.append`
// catches every subscriber failure), which the browser's console guard never sees.
const FAULTS = path.join(os.tmpdir(), `pp-e2e-server-${PORT}.log`);
rmSync(FAULTS, { force: true });

// Anchored to the LEVEL COLUMN, not the word anywhere. WARNING is absent: the engine warns on
// states it handles.
const FAULT_LINE =
  /^\d{4}-\d{2}-\d{2} \S+ +(ERROR|CRITICAL)\b|^(ERROR|CRITICAL):|^Traceback \(most recent call last\):/;
const NEW_RECORD = /^\d{4}-\d{2}-\d{2} |^(INFO|WARNING|DEBUG|ERROR|CRITICAL):/;

// A fault stays open until the next LOG RECORD begins, never a blank line: `logger.exception`
// emits none after the traceback.
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
      // PROMPTPOTTER_AUTH is INHERITED, never set here: `fake_issuer.ts` closes auth by leaving it unset.
      ...process.env,
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
