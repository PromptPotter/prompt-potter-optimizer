import os from "node:os";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

// walk reads the operator's workspace and never writes; cold and spend share a throwaway
// PROMPTPOTTER_HOME, which binds at import — hence two servers, neither on the operator's 8001.

const WALK_PORT = process.env.PP_E2E_PORT || "8123";
const COLD_PORT = process.env.PP_E2E_COLD_PORT || "8124";
const COLD_HOME = process.env.PP_E2E_COLD_HOME || path.join(os.tmpdir(), "promptpotter-e2e");

// Set to walk a server already up; the walk server then never starts, the cold one still does.
const WALK_URL = process.env.PP_E2E_BASE_URL || `http://127.0.0.1:${WALK_PORT}`;
const COLD_URL = `http://127.0.0.1:${COLD_PORT}`;

function server(port: string, env: Record<string, string>, reusable: boolean) {
  return {
    command: "node e2e/serve.mjs",
    url: `http://127.0.0.1:${port}/api/v1/health`,
    // The COLD server is never reused: `serve.mjs` resets the throwaway workspace at startup, and
    // adopting a running one silently skips the reset.
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
    ...(process.env.PP_E2E_BASE_URL ? [] : [server(WALK_PORT, {}, true)]),
    server(COLD_PORT, { PROMPTPOTTER_HOME: COLD_HOME, PP_E2E_RESET: "1" }, false),
  ],
});
