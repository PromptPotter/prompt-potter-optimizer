import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

// A signed-in identity for the cold tier, without a real OIDC round trip.
//
// `PROMPTPOTTER_AUTH=off` — what `serve.mjs` sets for both the walk and cold servers — makes
// `deps.py::auth_is_open` return true unconditionally, so `resolve_identity` returns
// `registered_or_default_identity()` and never even LOOKS at a session cookie. That is why
// `AccessGate` and `AllowanceSpent` were unreachable: not a missing fixture, but auth staying
// open for the whole suite. Closing it for one throwaway server is enough — `auth_is_open` also
// opens whenever `ENVIRONMENT == "development"` and no OIDC provider is configured, so writing a
// dummy `oidc.json` (never dialled: no login round trip is completed here) closes that second
// door too. With auth genuinely closed, `OIDCMiddleware` reads whatever session the cookie
// names — and a session is nothing but a JSON file `OIDCSessionStore` will happily read back,
// so minting one directly is the whole mechanism.

// `__dirname`, not `import.meta.url`: Playwright's test loader compiles this file to CommonJS
// (`webapp/package.json` carries no `"type": "module"`, same as `harness.ts` beside it) —
// `serve.mjs` is the one file here that is genuinely ESM, because it is spawned as a standalone
// Node process rather than loaded by that transform.
const WEBAPP = path.resolve(__dirname, "..");
const REPO = path.resolve(WEBAPP, "..");
const PYTHON =
  process.platform === "win32"
    ? path.join(REPO, ".venv", "Scripts", "python.exe")
    : path.join(REPO, ".venv", "bin", "python");

export type FakeIssuer = {
  baseURL: string;
  /** Write a session file and return the cookie value that names it. */
  mintSession(opts: { tenantId: string; email: string | null }): string;
  /** Pre-seed a per-tenant free-tier ceiling of `usd` — 0 puts the account at its cap with
   * nothing spent, which is what `AllowanceSpent` reads without a campaign ever running. */
  seedSpendCeiling(tenantId: string, usd: number): void;
  /** Replace the install's blocklist with exactly `emails`. */
  blockEmails(emails: string[]): void;
  stop(): Promise<void>;
};

/** Boot a throwaway API server with auth CLOSED, on its own workspace and port. */
export async function startFakeIssuer(
  port = process.env.PP_E2E_FAKEAUTH_PORT || "8125",
): Promise<FakeIssuer> {
  const preflight: [string, string, string][] = [
    ["venv interpreter", PYTHON, 'pip install -e ".[all,dev]"'],
    ["static export", path.join(WEBAPP, "out", "index.html"), "npm run build"],
  ];
  for (const [what, where, remedy] of preflight) {
    if (!existsSync(where)) throw new Error(`no ${what} at ${where} — run \`${remedy}\``);
  }

  const home = mkdtempSync(path.join(os.tmpdir(), "promptpotter-e2e-fakeauth-"));
  const identityDir = path.join(home, "identity");
  mkdirSync(path.join(identityDir, "sessions"), { recursive: true });

  // Only its PRESENCE matters (`ProviderConfigBundle.configured`) — nothing here ever dials
  // out to Google, since every test mints a session file directly rather than completing a
  // login round trip.
  writeFileSync(
    path.join(identityDir, "oidc.json"),
    JSON.stringify({
      google: {
        client_id: "e2e-fake",
        client_secret: "e2e-fake",
        redirect_uri: `http://127.0.0.1:${port}/api/v1/auth/callback/google`,
      },
    }),
  );

  const baseURL = `http://127.0.0.1:${port}`;
  // No explicit `stdio` — the default is already "pipe" on all three fds, and leaving it
  // implicit is what keeps `proc.stdout`/`proc.stderr` typed as real streams below.
  const proc = spawn(
    PYTHON,
    ["-m", "uvicorn", "promptpotter.main:app", "--host", "127.0.0.1", "--port", port],
    {
      cwd: REPO,
      env: {
        ...process.env,
        PROMPTPOTTER_HOME: home,
        PYTHONUTF8: "1",
        // Deliberately absent: PROMPTPOTTER_AUTH is what this whole harness exists to NOT set.
      },
    },
  );
  // Not tee'd to a fault log the way `serve.mjs` is (nothing else reads this server's output),
  // but still forwarded so a crash is visible in the runner's own console.
  proc.stdout.pipe(process.stdout);
  proc.stderr.pipe(process.stderr);

  await waitForHealth(`${baseURL}/api/v1/health`);

  return {
    baseURL,

    mintSession({ tenantId, email }) {
      const sessionId = randomBytes(32).toString("base64url");
      const now = Math.floor(Date.now() / 1000);
      writeFileSync(
        path.join(identityDir, "sessions", `${sessionId}.json`),
        JSON.stringify({
          user_id: tenantId,
          tenant_id: tenantId,
          // Any non-empty issuer works: nothing past the middleware dials out to it, and its
          // sole load-bearing property is being non-null (`quota.py::_is_host`'s terminal check).
          issuer: "https://fake-issuer.e2e.test/",
          subject: `sub-${tenantId}`,
          email,
          provider: "google",
          created_at: now,
          expires_at: now + 7 * 24 * 3600,
        }),
      );
      return sessionId;
    },

    seedSpendCeiling(tenantId, usd) {
      const dir = path.join(home, "projects", tenantId);
      mkdirSync(dir, { recursive: true });
      // Every field `User` (StrictModel, extra="forbid") declares — `get_or_create` returns
      // this file as-is once it exists, so it must already be a whole, valid record.
      writeFileSync(
        path.join(dir, "user.json"),
        JSON.stringify({
          user_id: tenantId,
          tenant_id: tenantId,
          email: null,
          terms_accepted: null,
          spend_budget_usd_total: usd,
          token_budget_total: null,
          max_concurrent_cycles: 2,
          max_campaigns_per_day: 1000,
          demo_mode_enabled: true,
          created_at: new Date().toISOString(),
        }),
      );
    },

    blockEmails(emails) {
      writeFileSync(path.join(identityDir, "blocklist.json"), JSON.stringify({ emails }));
    },

    async stop() {
      proc.kill();
      await new Promise((resolve) => proc.once("exit", resolve));
      rmSync(home, { recursive: true, force: true });
    },
  };
}

async function waitForHealth(url: string, timeoutMs = 60_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {
      // Not up yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`fake-issuer server never answered ${url}`);
}
