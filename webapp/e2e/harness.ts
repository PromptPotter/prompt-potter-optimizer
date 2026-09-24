import { existsSync, readFileSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test as base, expect, type Page, type APIRequestContext } from "@playwright/test";
import { STOP_REASON_OUTCOMES, type RoundSummary } from "@/lib/api/types.generated";

// What every spec shares. A spec asks the API which campaigns exist and never names one; the
// suite has no fixture campaign, and a world that cannot answer a spec makes it SKIP.

const API = "/api/v1";

// Harness-only console noise. Never add anything the app emits: a suppressed app error is the
// breakage the walk exists to catch.
const BENIGN: RegExp[] = [];

// The 4xx answers the app legitimately ASKS FOR; any other 4xx is reported.
const EXPECTED_4XX: [RegExp, number][] = [
  [/\/active\b/, 404],
  // A selection with no scored rows — ticking a campaign whose origin never ran.
  [/\/evidence\b/, 400],
  // `useRoundFile` asks by number, so a campaign with no completed round 404s `round_0000.json`.
  [/\/file\?.*round_\d+\.json/, 404],
];

export type Problem = { kind: string; text: string };

/** Where `serve.mjs` tees this server's faults, keyed by the port the project's `baseURL` carries. */
function faultLog(baseURL: string | undefined): string | null {
  if (!baseURL) return null;
  const port = new URL(baseURL).port;
  return port ? path.join(os.tmpdir(), `pp-e2e-server-${port}.log`) : null;
}

// The PROMISE is memoized so concurrent tests share one scan; safe only because the walk tier
// never writes.
let richest: Promise<Campaign | null> | null = null;

// `provide`, not `use`: `react-hooks/rules-of-hooks` reads `use` as React's hook and fails the lint.
export const test = base.extend<{ problems: Problem[]; rich: Campaign }>({
  // The richest campaign, or SKIP. Only a test that asks for it pays the scan or can skip.
  rich: async ({ request }, provide, testInfo) => {
    richest ??= richestCampaign(request);
    const found = await richest;
    testInfo.skip(!found, "no campaign with a cycle in this workspace");
    await provide(found!);
  },

  // `auto` so no spec can forget it — the guard is the point of the suite, not an opt-in.
  problems: [
    async ({ page, baseURL }, use, testInfo) => {
      const problems: Problem[] = [];
      const note = (kind: string, text: string) => {
        if (!BENIGN.some((re) => re.test(text))) problems.push({ kind, text });
      };

      const expected = (url: string, status: number) =>
        EXPECTED_4XX.some(([re, code]) => code === status && re.test(url));
      const seen4xx = new Map<string, number>();

      page.on("console", (m) => {
        if (m.type() !== "error") return;
        // Chrome's failed-subresource error carries no status, so it is read off the response
        // channel below.
        const url = m.location()?.url ?? "";
        if (/Failed to load resource/.test(m.text()) && expected(url, seen4xx.get(url) ?? 0)) return;
        note("console.error", m.text() + (url ? ` (${url})` : ""));
      });
      page.on("pageerror", (e) => note("pageerror", e.stack || e.message));
      page.on("requestfailed", (r) => {
        // A poll aborted by the navigation that ended the test is not a fault.
        const why = r.failure()?.errorText ?? "";
        if (why.includes("ERR_ABORTED")) return;
        note("requestfailed", `${r.method()} ${r.url()} — ${why}`);
      });
      page.on("response", (r) => {
        const url = r.url();
        const status = r.status();
        if (status < 400 || !url.includes(API)) return;
        seen4xx.set(url, status);
        if (status >= 500 || !expected(url, status)) {
          note("http", `${status} ${r.request().method()} ${url}`);
        }
      });

      // Only what THIS test provoked: the tee is append-only across the whole run.
      const faults = faultLog(baseURL);
      const from = faults && existsSync(faults) ? statSync(faults).size : 0;

      await use(problems);

      if (faults && existsSync(faults)) {
        const fresh = readFileSync(faults).subarray(from).toString("utf8").trim();
        if (fresh) {
          // A detached run's later fault lands on whichever test is next: mis-attributed, never hidden.
          const lines = fresh.split("\n");
          const head = lines.slice(0, 14).join("\n");
          const rest = lines.length > 14 ? `\n… +${lines.length - 14} more line(s)` : "";
          problems.push({ kind: "server", text: head + rest });
        }
      }

      if (problems.length) {
        await testInfo.attach("console-problems", {
          body: problems.map((p) => `[${p.kind}] ${p.text}`).join("\n"),
          contentType: "text/plain",
        });
      }
      expect(problems.map((p) => `${p.kind}: ${p.text.split("\n")[0]}`)).toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/** The shell mounted, not its crash fallback. Both headings are read off `ui/ErrorBoundary`. */
export async function ready(page: Page) {
  await expect(page.locator("#main-content")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /hit a render error|running an old build/i }),
  ).toHaveCount(0);
}

/**
 * The reload is required: a fragment-only `goto` does not remount, so the top-level
 * ErrorBoundary would carry one view's crash into every address visited after it.
 */
export async function open(page: Page, hash = "") {
  await page.goto(`/${hash}`);
  await page.reload();
  await ready(page);
}

export type Campaign = {
  id: string;
  cycleId: string;
  addr: string;
};

export async function campaigns(request: APIRequestContext): Promise<Campaign[]> {
  const res = await request.get(`${API}/campaigns`);
  expect(res.ok(), `GET ${API}/campaigns → ${res.status()}`).toBeTruthy();
  const rows: Record<string, string>[] = (await res.json()).campaigns ?? [];
  // A campaign with no root cycle has no address, so it is skipped, never given a fabricated one.
  return rows.flatMap((c) => {
    const id = c.campaign_id;
    const cycleId = c.root_cycle_id;
    if (!id || !cycleId) return [];
    // The `cycle_` prefix is stripped in the address and nowhere else (`lib/address.ts`).
    return [{ id, cycleId, addr: `#/c/${id}/${cycleId.replace(/^cycle_/, "")}` }];
  });
}

/**
 * Anything reading a run takes this, never the newest campaign — the newest is often an empty
 * check-in cycle, whose empty panes pass whatever the chart code does.
 */
export async function richestCampaign(request: APIRequestContext): Promise<Campaign | null> {
  const all = await campaigns(request);
  let best: Campaign | null = null;
  let bestRounds = -1;
  for (const c of all) {
    const res = await request.get(
      `${API}/campaigns/${c.id}/cycles/${c.cycleId}/dashboard`,
    );
    if (!res.ok()) continue;
    const rounds = ((await res.json()).rounds ?? []).length;
    if (rounds > bestRounds) {
      bestRounds = rounds;
      best = c;
    }
  }
  return best;
}

/** The datasets the spend tier may have created; anything else means a world it must not write. */
export const E2E_DATASETS = ["email-tagging", "promptpotter-self-e2e"];

/**
 * Not "the world is empty": the spend specs share one workspace, so the second finds the
 * first's campaign. Every campaign present must belong to a dataset this suite owns.
 */
export async function assertThrowawayWorld(request: APIRequestContext) {
  const res = await request.get(`${API}/campaigns`);
  expect(res.ok()).toBeTruthy();
  const rows: Record<string, string>[] = (await res.json()).campaigns ?? [];
  const foreign = rows
    .map((c) => c.dataset_name)
    .filter((d) => d && !E2E_DATASETS.includes(d));
  expect(
    [...new Set(foreign)],
    "the spend tier is about to MINT and SPEND, and this workspace holds campaigns it did not " +
      "create — it is not the throwaway world. Refusing rather than writing here.",
  ).toEqual([]);
}

export async function datasetNames(request: APIRequestContext): Promise<string[]> {
  const res = await request.get(`${API}/datasets`);
  expect(res.ok()).toBeTruthy();
  return ((await res.json()).datasets ?? []).map((d: { name: string }) => d.name);
}

export function consentGate(page: Page) {
  return page.getByRole("dialog").filter({
    has: page.getByRole("heading", { name: "One thing before you start" }),
  });
}

/** Idempotent: the accept is persisted to `user.json`. */
export async function passConsent(page: Page) {
  const gate = consentGate(page);
  if ((await gate.count()) === 0) return;
  await gate.getByRole("checkbox").check();
  await gate.getByRole("button", { name: /Agree & continue/ }).click();
  await expect(gate).toBeHidden();
}

/**
 * The body is the ENVELOPE `{kind, payload}` (`lib/api/commands.ts`); a flat one 422s on every
 * kind. Never retry a 4xx — a refusal is permanent.
 */
export async function command(
  request: APIRequestContext,
  kind: string,
  payload: Record<string, unknown>,
): Promise<{ status: number; body: string }> {
  const res = await request.post(`${API}/commands/${kind}`, {
    data: { kind, payload },
    headers: {
      "Idempotency-Key": `e2e-${kind}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    },
  });
  const body = (await res.text()).slice(0, 400);
  if (!res.ok()) console.log(`[e2e] ${kind} → ${res.status()} ${body}`);
  return { status: res.status(), body };
}

export async function dashboard(
  request: APIRequestContext,
  c: Campaign,
): Promise<Record<string, unknown> | null> {
  const r = await request.get(`${API}/campaigns/${c.id}/cycles/${c.cycleId}/dashboard`);
  return r.ok() ? await r.json() : null;
}

export function roundsOf(dash: Record<string, unknown> | null): RoundSummary[] {
  const rows = dash?.rounds;
  return Array.isArray(rows) ? (rows as RoundSummary[]) : [];
}

/**
 * A round that CLOSED need not have MEASURED: a round whose every cell errored still closes, with
 * `null` accuracy on every candidate. Counting rounds cannot tell them apart.
 */
export function assertRoundMeasured(label: string, round: RoundSummary | undefined) {
  expect(round, `${label}: no round to read`).toBeDefined();
  const graded = (round?.candidates ?? []).filter(
    (c) => typeof c.accuracy === "number" && !c.invalid,
  );
  expect(
    graded.length,
    `${label} round ${round?.round} CLOSED with no graded candidate — every cell on it errored ` +
      `or was rejected, so the round happened without measuring anything`,
  ).toBeGreaterThan(0);
}

/**
 * Checked before any spend: the engine classifies `backend_unreachable` as a bounded `halted`,
 * which `assertBoundedStop` would pass, but for a test it means a mis-set harness.
 */
export const BACKEND_URL = process.env.PP_E2E_BACKEND_URL || "http://127.0.0.1:8000";

export async function assertBackendUp(request: APIRequestContext) {
  const res = await request.get(`${BACKEND_URL}/health`).catch(() => null);
  expect(
    res?.ok(),
    `${BACKEND_URL} is not answering — start the backend, or point PP_E2E_BACKEND_URL at one`,
  ).toBeTruthy();
}

/** An empty `stopped` means still running and says nothing either way. */
export function assertBoundedStop(label: string, stopped: string) {
  if (!stopped) return;
  const outcome = STOP_REASON_OUTCOMES[stopped];
  expect(
    outcome,
    `${label} stopped on '${stopped}', which STOP_REASON_OUTCOMES does not classify — the ` +
      `generated mirror has drifted from domain/phases.py::STOP_REASON_INFO`,
  ).toBeDefined();
  expect(
    outcome,
    `${label} terminated on '${stopped}', which the engine classifies as a FAILURE rather than a ` +
      `bounded stop`,
  ).not.toBe("failed");
}

/**
 * `null` where the rollup is ABSENT, never `$0` — read as zero, a cache that silently went
 * missing reports itself as a perfect hit.
 */
export type Tape = { used: number; incurred: number; replayed: number | null };

export function tapeOf(dash: Record<string, unknown> | null): Tape | null {
  const spend = dash?.spend;
  if (!spend || typeof spend !== "object") return null;
  const row = spend as Record<string, unknown>;
  const used = row.total_used_usd;
  const incurred = row.total_incurred_usd;
  if (typeof used !== "number" || typeof incurred !== "number") return null;
  return {
    used,
    incurred,
    // Nothing incurred has no replay share; 0% would read as a cache that answered nothing.
    replayed: incurred > 0 ? 1 - used / incurred : null,
  };
}

/**
 * Pass `minReplayed` only where the path can honestly guarantee one (`README.md` § The second
 * pass is free): a floor on a stochastic path is a flaky failure in the paid tier.
 */
export function reportTape(label: string, tape: Tape | null, minReplayed?: number) {
  if (!tape) {
    console.log(`[e2e] ${label}: no spend rollup served — cannot say what was replayed`);
    expect(process.env.PP_E2E_TAPE, `${label} served no spend rollup to read a tape off`).toBeFalsy();
    return;
  }
  const share = tape.replayed === null ? "n/a" : `${(tape.replayed * 100).toFixed(1)}%`;
  console.log(
    `[e2e] ${label}: paid $${tape.used.toFixed(5)} of $${tape.incurred.toFixed(5)} incurred — ` +
      `${share} replayed off the caches`,
  );
  expect(tape.used, `${label} billed more than it incurred`).toBeLessThanOrEqual(
    tape.incurred + 1e-9,
  );
  if (!process.env.PP_E2E_TAPE || minReplayed === undefined) return;
  expect(
    tape.incurred,
    `${label} incurred nothing — there is no run here to have replayed`,
  ).toBeGreaterThan(0);
  expect(
    tape.replayed,
    `${label} replayed ${share} of its cost, under the ${(minReplayed * 100).toFixed(0)}% this ` +
      `path guarantees — something re-keyed the caches (an optimizer prompt, a dataset, a ` +
      `model). Re-record with PP_E2E_DROP_CACHES=1, or drop PP_E2E_TAPE and pay for this pass.`,
  ).toBeGreaterThanOrEqual(minReplayed);
}

/** The document scrolls vertically; it must never scroll HORIZONTALLY (webapp/CLAUDE.md). */
export async function noSidewaysScroll(page: Page) {
  const over = await page.evaluate(() => {
    const el = document.documentElement;
    return el.scrollWidth - el.clientWidth;
  });
  expect(over, "the page body scrolls sideways").toBeLessThanOrEqual(1);
}
