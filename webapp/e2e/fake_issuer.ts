import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

// A signed-in identity without an OIDC round trip: `serve.mjs` with PROMPTPOTTER_AUTH unset plus a
// dummy `oidc.json` closes both doors of `deps.py::auth_is_open`; a session is a file minted directly.

// `__dirname`, not `import.meta.url`: Playwright's loader compiles this file to CommonJS.
const WEBAPP = path.resolve(__dirname, "..");

export type FakeIssuer = {
  baseURL: string;
  mintSession(opts: { tenantId: string; email: string | null }): string;
  /** `0` puts the account at its cap with nothing spent, which is what `AllowanceSpent` reads. */
  seedSpendCeiling(tenantId: string, usd: number): void;
  blockEmails(emails: string[]): void;
  stop(): Promise<void>;
};

export async function startFakeIssuer(
  port = process.env.PP_E2E_FAKEAUTH_PORT || "8125",
): Promise<FakeIssuer> {
  const home = mkdtempSync(path.join(os.tmpdir(), "promptpotter-e2e-fakeauth-"));
  const identityDir = path.join(home, "identity");
  mkdirSync(path.join(identityDir, "sessions"), { recursive: true });

  const baseURL = `http://127.0.0.1:${port}`;
  // Only its PRESENCE matters (`ProviderConfigBundle.configured`); nothing dials out.
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

  // Annotated: the ambient `process.env` is narrowed, so its spread has no `delete`-able members.
  const env: NodeJS.ProcessEnv = { ...process.env, PP_E2E_PORT: port, PROMPTPOTTER_HOME: home };
  // Deleted, not inherited: the runner's own shell may carry either.
  delete env.PROMPTPOTTER_AUTH;
  delete env.PP_E2E_RESET;
  const proc = spawn("node", [path.join(WEBAPP, "e2e", "serve.mjs")], {
    cwd: WEBAPP,
    stdio: "inherit",
    env,
  });

  // `serve.mjs` exits 2 with the remedy on a failed preflight; surface it before the health timeout.
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
          // Must be non-null (`quota.py::_is_host`); nothing dials out to it.
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
      // Every field `User` (extra="forbid") declares: `get_or_create` returns this file as-is.
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
