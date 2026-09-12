import os from "node:os";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

// The browser walk. Vitest covers reader-side derivations as pure data → data; this covers
// the half no unit can reach — that the built export, mounted by the real API, renders and
// responds. Three projects, because the app has two populations and they need different
// worlds:
//
//   walk  — the operator's own workspace, read-only. Real campaigns, real rounds, real
//           charts: the only world where Dashboard, Compare, Verify and Files have anything
//           to draw. It never issues a write.
//   cold  — a throwaway PROMPTPOTTER_HOME. The zero-campaign path a brand-new account meets,
//           plus every write whose cost is a file: draft, label, archive, delete.
//   spend — the same throwaway world, carried through a real run. Real LLM calls, real
//           money, minutes not seconds, so it is opt-in: PP_E2E_SPEND=1.
//
// Two servers rather than one, because PROMPTPOTTER_HOME is bound at import and a process
// cannot hold both. Neither binds 8001 — that port is the operator's.

const WALK_PORT = process.env.PP_E2E_PORT || "8123";
const COLD_PORT = process.env.PP_E2E_COLD_PORT || "8124";
const COLD_HOME = process.env.PP_E2E_COLD_HOME || path.join(os.tmpdir(), "promptpotter-e2e");

// Set to walk a server that is already up — the operator's :8001, or a deployed box. The
// walk server then never starts; the cold one still does, having nowhere else to live.
const WALK_URL = process.env.PP_E2E_BASE_URL || `http://127.0.0.1:${WALK_PORT}`;
const COLD_URL = `http://127.0.0.1:${COLD_PORT}`;

function server(port: string, env: Record<string, string>, reusable: boolean) {
  return {
    command: "node e2e/serve.mjs",
    url: `http://127.0.0.1:${port}/api/v1/health`,
    // The COLD server is never reused, and that is a correctness rule rather than hygiene.
    // `serve.mjs` resets the throwaway workspace at startup, so adopting one that is already
    // up silently skips the reset and runs the zero-campaign assertions against whatever the
    // last run left. Worse, a leaked-but-dying server gets adopted and then exits underneath
    // the run — which read as 27 product failures once, with nothing anywhere saying a server
    // had been reused. The walk server holds no state and may be shared with the operator's own.
    reuseExistingServer: reusable && !process.env.CI,
    timeout: 120_000,
    stdout: "pipe" as const,
    stderr: "pipe" as const,
    env: { PP_E2E_PORT: port, ...env },
  };
}

export default defineConfig({
  testDir: "./e2e",
  // A spend run holds one campaign's worth of on-disk state and a walk shares one uvicorn,
  // so parallelism buys little and costs determinism.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  // A cold poll settles in ~2s; a round does not, and says so per test.
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    // `trailingSlash: true` under StaticFiles(html=True) — /login/ resolves to its index.
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
