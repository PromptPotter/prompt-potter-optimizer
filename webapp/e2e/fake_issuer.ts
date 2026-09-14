import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

// A signed-in identity for the cold tier, without a real OIDC round trip.
//
// `PROMPTPOTTER_AUTH=off` — what both Playwright servers pass — makes `deps.py::auth_is_open`
// return true unconditionally, so `resolve_identity` returns `registered_or_default_identity()`
// and never even LOOKS at a session cookie. That is why `AccessGate` and `AllowanceSpent` were
// unreachable: not a missing fixture, but auth staying open for the whole suite. So this spawns
// `serve.mjs` — the SAME server script, with the same preflight, the same fault tee and the same
// interpreter — and simply leaves that variable unset. `auth_is_open` also opens whenever
// `ENVIRONMENT == "development"` and no OIDC provider is configured, so a dummy `oidc.json`
// (never dialled: no login round trip is completed here) closes that second door too.
//
// With auth genuinely closed, `OIDCMiddleware` reads whatever session the cookie names — and a
// session is nothing but a JSON file `OIDCSessionStore` will happily read back, so minting one
// directly is the whole mechanism. Everything below writes a fixture; nothing below is a server.

// `__dirname`, not `import.meta.url`: Playwright's test loader compiles this file to CommonJS
// (`webapp/package.json` carries no `"type": "module"`, same as `harness.ts` beside it).
const WEBAPP = path.resolve(__dirname, "..");

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
  const home = mkdtempSync(path.join(os.tmpdir(), "promptpotter-e2e-fakeauth-"));
  const identityDir = path.join(home, "identity");
  mkdirSync(path.join(identityDir, "sessions"), { recursive: true });

  const baseURL = `http://127.0.0.1:${port}`;
  // Only its PRESENCE matters (`ProviderConfigBundle.configured`) — nothing here ever dials out
  // to Google, since every test mints a session file directly.
  writeFileSync(
    path.join(identityDir, "oidc.json"),
    JSON.stringify({
      google: {
        client_id: "e2e-fake",
        client_secret: "e2e-fake",
        redirect_uri: `${baseURL}/api/v1/auth/callback/google`,
      },
    }),
  );

  // Annotated, because the ambient `process.env` is narrowed to the keys the app declares and a
  // spread of it therefore has no `delete`-able members.
  const env: NodeJS.ProcessEnv = { ...process.env, PP_E2E_PORT: port, PROMPTPOTTER_HOME: home };
  // DELETED rather than left to inherit. `PROMPTPOTTER_AUTH` is what this harness exists to NOT
  // set, and the runner's own shell may well carry it; `PP_E2E_RESET` would point `reset_world`
  // at a workspace minted fresh two lines above, which has nothing to wipe.
  delete env.PROMPTPOTTER_AUTH;
  delete env.PP_E2E_RESET;
  const proc = spawn("node", [path.join(WEBAPP, "e2e", "serve.mjs")], {
    cwd: WEBAPP,
    stdio: "inherit",
    env,
  });

  // `serve.mjs` preflights the venv and the static export and exits 2 with the remedy. Catching
  // that here reports it now instead of as a 60s health timeout that names neither.
  let died: number | null = null;
  proc.on("exit", (code) => (died = code ?? 1));
  await waitForHealth(`${baseURL}/api/v1/health`, () => died);

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

async function waitForHealth(
  url: string,
  exitCode: () => number | null,
  timeoutMs = 60_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const code = exitCode();
    if (code !== null) throw new Error(`e2e/serve.mjs exited ${code} — see its output above`);
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
