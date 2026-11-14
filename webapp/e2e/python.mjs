// The clone's pinned interpreter: the walk's helpers run on it, and the config asks the gate through it.

import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const WEBAPP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const REPO = path.resolve(WEBAPP, "..");

// Never a bare `python`: a system interpreter imports promptpotter but not its deps. The venv is the
// clone's one (`scripts/processes.py::clone_root`), which a worktree or a pushed tip reads in place.
const CLONE = path.dirname(
  execFileSync("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], {
    cwd: REPO,
    encoding: "utf8",
  }).trim(),
);
export const PYTHON =
  process.platform === "win32"
    ? path.join(CLONE, ".venv", "Scripts", "python.exe")
    : path.join(CLONE, ".venv", "bin", "python");
// That venv's editable install names its owner; the walk serves this tree.
export const PYTHON_ENV = {
  ...process.env,
  PYTHONPATH: [REPO, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
  // This interpreter is cp1252; the live display writes box-drawing characters.
  PYTHONUTF8: "1",
};
