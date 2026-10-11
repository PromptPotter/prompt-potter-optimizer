import os from "node:os";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

// Two servers: cold and spend share a throwaway PROMPTPOTTER_HOME, which binds at import; walk reads the operator's workspace.

const WALK_PORT = process.env.PP_E2E_PORT || "8123";
const COLD_PORT = process.env.PP_E2E_COLD_PORT || "8124";
const COLD_HOME = process.env.PP_E2E_COLD_HOME || path.join(os.tmpdir(), "promptpotter-e2e");

// Set to walk a server already up; the walk server then never starts.
const WALK_URL = process.env.PP_E2E_BASE_URL || `http://127.0.0.1:${WALK_PORT}`;
const COLD_URL = `http://127.0.0.1:${COLD_PORT}`;

// `webServer` is per RUN, not per project: a cold-only run (the gate's) must never start the server reading the operator's workspace.
const ASKED = process.argv.flatMap((arg, i, argv) => {
  if (arg.startsWith("--project=")) return [arg.slice("--project=".length)];
  if (arg !== "--project") return [];
  const rest = argv.slice(i + 1);
  const end = rest.findIndex((next) => next.startsWith("-"));
  return end < 0 ? rest : rest.slice(0, end);
});

// Playwright's own matching: case-insensitive, `*` a wildcard, no `--project` meaning every one.
function runs(project: string) {
  const matches = (asked: string) =>
    new RegExp(`^${asked.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*")}$`, "i").test(
      project,
    );
  return ASKED.length === 0 || ASKED.some(matches);
}

function server(port: string, env: Record<string, string>, reusable: boolean) {
  return {
    command: "node e2e/serve.mjs",
    url: `http://127.0.0.1:${port}/api/v1/health`,
    // The COLD server is never reused: `serve.mjs` resets the workspace at startup, and adopting a running one silently skips it.
    reuseExistingServer: reusable && !process.env.CI,
    timeout: 120_000,
    stdout: "pipe" as const,
    stderr: "pipe" as const,
    // Here, not in `serve.mjs`: `e2e/fake_issuer.ts` spawns the same script with auth unset.
    env: { PP_E2E_PORT: port, PROMPTPOTTER_AUTH: "off", ...env },
  };
}

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    ignoreHTTPSErrors: true,
  },
  projects: [
    {
      name: "walk",
      testMatch: /walk[/\\].*\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: WALK_URL },
    },
    {
      name: "cold",
      testMatch: /cold[/\\].*\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: COLD_URL },
    },
    {
      name: "spend",
      testMatch: /spend[/\\].*\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: COLD_URL },
    },
  ],
  webServer: [
    ...(runs("walk") && !process.env.PP_E2E_BASE_URL ? [server(WALK_PORT, {}, true)] : []),
    ...(runs("cold") || runs("spend")
      ? [server(COLD_PORT, { PROMPTPOTTER_HOME: COLD_HOME, PP_E2E_RESET: "1" }, false)]
      : []),
  ],
});
