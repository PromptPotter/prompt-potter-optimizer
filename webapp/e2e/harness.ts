import { existsSync, readFileSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test as base, expect, type Page, type APIRequestContext } from "@playwright/test";
import { STOP_REASON_OUTCOMES, type RoundSummary } from "@/lib/api/types.generated";

// What every spec shares: the console-clean guard, the address helpers, and campaign
// DISCOVERY.
//
// Discovery is the load-bearing one. A walk that names `swiss-invoices-eval__b1b4f5` asserts
// the operator's disk rather than the app, and dies the first time they archive it — the
// coupling `tests/CLAUDE.md` axis 1 rejects, in a browser. So a spec asks the API what is
// there and checks the app renders THAT. The suite has no fixture campaign and must never
// grow one; where a world cannot answer a spec's question, the spec SKIPS with the reason.

const API = "/api/v1";

// Console noise that is the harness's own, not the app's. Keep this list empty of anything the
// app itself emits — an app error suppressed here is exactly the breakage the walk was built to
// catch, and it will stay suppressed forever.
//
// It is EMPTY, and that is the finding rather than an oversight: the two entries it used to
// carry were both dead. The export links an inline icon so Chrome never requests
// `/favicon.ico`, and React's devtools notice is a `log`, which this handler never reads. A
// suppression nobody has seen fire is indistinguishable from one that fires constantly.
const BENIGN: RegExp[] = [];

/**
 * The 4xx answers the app legitimately ASKS FOR, as (path fragment, status) pairs.
 *
 * Exempting the whole `/api/v1` namespace at every status — which this did — also swallows a
 * 422 from a malformed client request, a 409 idempotency conflict, and a 404 on a mistyped id,
 * each of which renders an empty pane while the suite passes. The app classifies its own
 * failures (`failureKind`), so the cases worth exempting are nameable and few.
 */
const EXPECTED_4XX: [RegExp, number][] = [
  // No active cycle is the normal state of a workspace nobody is running.
  [/\/active\b/, 404],
  // A selection with no scored rows — ticking a campaign whose origin never ran.
  [/\/evidence\b/, 400],
  // A round document that does not exist yet. `useRoundFile` is lazy and asks by number, so a
  // campaign with no completed rounds answers 404 for `round_0000.json` by construction.
  [/\/file\?.*round_\d+\.json/, 404],
];

export type Problem = { kind: string; text: string };

/**
 * Where `serve.mjs` tees this server's faults. Keyed by PORT, which the project's `baseURL`
 * already carries — so the walk tier reads the walk server's log and the cold/spend tiers read
 * the throwaway one's, with nothing to wire per project.
 */
function faultLog(baseURL: string | undefined): string | null {
  if (!baseURL) return null;
  const port = new URL(baseURL).port;
  return port ? path.join(os.tmpdir(), `pp-e2e-server-${port}.log`) : null;
}

/**
 * One scan of the workspace per worker, shared by every spec that reads a run.
 *
 * The PROMISE is memoized, not the value, so tests running together share the single scan
 * rather than racing it. Safe because the walk tier issues no write: nothing it does can
 * change the answer mid-run.
 */
let richest: Promise<Campaign | null> | null = null;

// Playwright's second fixture argument is positional and conventionally spelled `use`; here it
// is `provide`, because `react-hooks/rules-of-hooks` reads that spelling as React's `use` hook
// and fails the lint.
export const test = base.extend<{ problems: Problem[]; rich: Campaign }>({
  /**
   * The richest campaign, or SKIP. Four walk specs each carried this as a `beforeAll` plus a
   * `test.skip`, which was four scans and four places for the reason to drift. A test that
   * does not ask for it never pays the scan and never skips — which is what lets the
   * zero-campaign assertions keep running against an empty world.
   */
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

      // What the app is allowed to ask for, resolved once per response and shared by the two
      // channels that would otherwise disagree about the same request.
      const expected = (url: string, status: number) =>
        EXPECTED_4XX.some(([re, code]) => code === status && re.test(url));
      const seen4xx = new Map<string, number>();

      page.on("console", (m) => {
        if (m.type() !== "error") return;
        // Chrome logs a failed subresource as a console error carrying no URL in its text, so
        // the status has to come from the response channel below. Anything not matched there as
        // expected is reported — including a 4xx, which is usually a client bug rendering an
        // empty pane, and a static-asset miss, which is a dead chunk.
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
        // A 5xx is never a question. A 4xx is one only where the app is known to ask it.
        if (status >= 500 || !expected(url, status)) {
          note("http", `${status} ${r.request().method()} ${url}`);
        }
      });

      // Only what THIS test provoked. The tee is append-only and cumulative, so reading it whole
      // would redden every test after the first fault and bury which one caused it.
      const faults = faultLog(baseURL);
      const from = faults && existsSync(faults) ? statSync(faults).size : 0;

      await use(problems);

      if (faults && existsSync(faults)) {
        const fresh = readFileSync(faults).subarray(from).toString("utf8").trim();
        if (fresh) {
          // A detached run outlives the test that started it, so a fault it logs afterwards
          // lands on whichever test is next. That mis-attributes it; it does not hide it, and
          // the alternative — not looking — is what let a crashed projection ship green.
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

/**
 * The shell has mounted, and what mounted is the app rather than its crash fallback.
 *
 * The headings are READ OFF `ui/ErrorBoundary`, both of them. An earlier version matched
 * `/render error|Something went wrong/i` — half guessed: the boundary's other heading is "This
 * tab is running an old build", which that pattern missed entirely, while "Something went
 * wrong" appears nowhere near it and belongs to a sign-in failure string, so the guess could
 * only ever have fired on the wrong thing.
 */
export async function ready(page: Page) {
  await expect(page.locator("#main-content")).toBeVisible();
  // `app/page.tsx` wraps everything in ErrorBoundary, so a crash REPLACES the shell —
  // `#main-content` disappearing is itself the signal, and this names what happened.
  await expect(
    page.getByRole("heading", { name: /hit a render error|running an old build/i }),
  ).toHaveCount(0);
}

/**
 * Navigate by ADDRESS (`lib/address.ts` owns the syntax; the hash is the whole of it).
 *
 * The reload is not optional. A `goto` that changes only the fragment does not remount, so
 * the top-level ErrorBoundary keeps the state it was in — one crashed view then makes every
 * address visited after it read as crashed, which is how a first pass at this suite
 * mis-attributed a Dashboard crash to Compare, Verify and Files as well.
 */
export async function open(page: Page, hash = "") {
  await page.goto(`/${hash}`);
  await page.reload();
  await ready(page);
}

export type Campaign = {
  id: string;
  cycleId: string;
  /** What the address for this campaign's root cycle looks like. */
  addr: string;
};

/** What the served workspace actually holds. An empty list is a legitimate answer. */
export async function campaigns(request: APIRequestContext): Promise<Campaign[]> {
  const res = await request.get(`${API}/campaigns`);
  expect(res.ok(), `GET ${API}/campaigns → ${res.status()}`).toBeTruthy();
  const rows: Record<string, string>[] = (await res.json()).campaigns ?? [];
  // flatMap rather than filter+map: a `.filter()` does not narrow what follows it under
  // `noUncheckedIndexedAccess`, and neither `!` nor `?? ""` is the answer — a campaign with
  // no root cycle has no address, so it is SKIPPED rather than given a fabricated one.
  return rows.flatMap((c) => {
    const id = c.campaign_id;
    const cycleId = c.root_cycle_id;
    if (!id || !cycleId) return [];
    // The `cycle_` prefix is stripped in the address and nowhere else (`lib/address.ts`).
    return [{ id, cycleId, addr: `#/c/${id}/${cycleId.replace(/^cycle_/, "")}` }];
  });
}

/**
 * The campaign with the MOST completed rounds — the one whose dashboard has something to
 * draw. **Anything reading a run takes this, never the newest campaign.**
 *
 * Not a nicety. The newest is very often a check-in cycle holding nothing at all, so a
 * dashboard spec anchored on it asserts empty panes and passes whatever the chart code does.
 * A crash in the per-round cost fold was caught one run and missed the next for exactly that
 * reason, until a fresh check-in landed on top and the suite went quiet about a bug it had
 * already found.
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

/**
 * The datasets the spend tier is allowed to have created. Anything else in the world means the
 * suite is pointed somewhere it must not write.
 */
export const E2E_DATASETS = ["email-tagging", "promptpotter-self-e2e"];

/**
 * Refuse to write unless this really is the throwaway world.
 *
 * Asserting the world is EMPTY was the first attempt and it cannot hold: two spend specs share
 * one workspace, so whichever runs second legitimately finds the first one's campaign and would
 * fail for being second. What must be true is narrower and order-independent — every campaign
 * present belongs to a dataset this suite owns. An attach to the operator's workspace fails on
 * the first swiss-invoices or sealqa row.
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

/** Every dataset the install offers, by name. */
export async function datasetNames(request: APIRequestContext): Promise<string[]> {
  const res = await request.get(`${API}/datasets`);
  expect(res.ok()).toBeTruthy();
  return ((await res.json()).datasets ?? []).map((d: { name: string }) => d.name);
}

/** The blocking consent gate, when it is up. */
export function consentGate(page: Page) {
  return page.getByRole("dialog").filter({
    has: page.getByRole("heading", { name: "One thing before you start" }),
  });
}

/**
 * Clear the consent gate so a spec can reach the app behind it. Idempotent — the accept
 * writes a provable record to `user.json`, so it is a no-op for the rest of the run, and a
 * no-op from the start in any world whose account has already accepted.
 */
export async function passConsent(page: Page) {
  const gate = consentGate(page);
  if ((await gate.count()) === 0) return;
  await gate.getByRole("checkbox").check();
  await gate.getByRole("button", { name: /Agree & continue/ }).click();
  await expect(gate).toBeHidden();
}

/**
 * One control verb.
 *
 * The body is the ENVELOPE `{kind, payload}`, not the payload flat — `lib/api/commands.ts` is
 * the only spelling of it and a flat body 422s on every kind alike, blaming a missing `kind` and
 * three "extra inputs" rather than anything about the command. It cost a whole spend run to
 * learn, because nothing logged the response body.
 *
 * So this ALWAYS returns the body, and callers must never retry a 4xx: a refusal is permanent,
 * and polling one just spends five minutes arriving at the same answer.
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

/** One cycle's served dashboard, or `null` where the route does not answer. */
export async function dashboard(
  request: APIRequestContext,
  c: Campaign,
): Promise<Record<string, unknown> | null> {
  const r = await request.get(`${API}/campaigns/${c.id}/cycles/${c.cycleId}/dashboard`);
  return r.ok() ? await r.json() : null;
}

/**
 * The completed rounds `dashboard.json` serves. `RoundSummary` is GENERATED off the Pydantic
 * model — hand-declaring the shape here would drift behind it with every gate green, which is
 * `webapp/CLAUDE.md` § A wire shape is GENERATED.
 */
export function roundsOf(dash: Record<string, unknown> | null): RoundSummary[] {
  const rows = dash?.rounds;
  return Array.isArray(rows) ? (rows as RoundSummary[]) : [];
}

/**
 * A round that CLOSED is not a round that MEASURED, and the gap between those two is where this
 * tier's most expensive false green lived.
 *
 * The L4 fixture spent three passes reporting success on a round whose single cell came back
 * `predicted: "ERROR"` — the inner spawn collided on a content-addressed cycle id, the cell was
 * never graded, the round closed carrying nothing but that error, and `rounds.length > 0` was
 * true the whole time. Counting rounds cannot tell the two apart; a graded cell can.
 *
 * So: at least one candidate carrying a real accuracy. An errored round has `null` on every one
 * of them, which is exactly the state that used to pass.
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
 * The backend every install dataset routes to (`backend_type: termnorm`).
 *
 * Asserted by both spend specs, not just the one that noticed first. It is what makes
 * `assertBoundedStop` safe to loosen onto the served table: the engine classifies
 * `backend_unreachable` as a bounded `halted`, correctly — a backend going away IS a reason to
 * stop — but for a test it means the harness was mis-set, so it is caught HERE, before any
 * money is committed, rather than by second-guessing the engine's own classification later.
 */
export const BACKEND_URL = process.env.PP_E2E_BACKEND_URL || "http://127.0.0.1:8000";

export async function assertBackendUp(request: APIRequestContext) {
  const res = await request.get(`${BACKEND_URL}/health`).catch(() => null);
  expect(
    res?.ok(),
    `${BACKEND_URL} is not answering — start the backend, or point PP_E2E_BACKEND_URL at one`,
  ).toBeTruthy();
}

/**
 * A run stopped for a reason the ENGINE calls a failure is not a bounded stop, however calm it
 * looks from outside. An empty *stopped* is "still running" and says nothing either way.
 *
 * It ASKS `STOP_REASON_OUTCOMES` — generated from `domain/phases.py::STOP_REASON_INFO`, total
 * over `StopReason` — rather than matching a hand-listed set of crash names, which is what this
 * replaced and what `promptpotter/CLAUDE.md` § Ask the typed predicate calls a bug outright: the
 * old regex named five reasons, two of which the table classifies `halted` rather than `failed`,
 * and it could never have known about a sixth added after it was written.
 *
 * An UNKNOWN reason fails too. Totality is the whole value of asking, so a reason the mirror
 * does not carry means the generator and the engine have drifted — which is a finding, not a
 * reason to wave the run through.
 */
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
 * What the run PAID against what the search COST, off the two totals `SpendRollup` already
 * separates. What the split means and which pass to run is `README.md` § The second pass is free.
 *
 * `null` where the rollup is ABSENT rather than zero: those are different facts, and reading a
 * missing spend block as `$0` is exactly how a cache that silently went missing would report
 * itself as a perfect hit.
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
    // Undefined rather than 0 when nothing was spent at all: a run that has bought nothing has
    // no replay share, and printing 0% there reads as a cache that answered nothing.
    replayed: incurred > 0 ? 1 - used / incurred : null,
  };
}

/**
 * Report the split, and — where the caller can state a floor — assert it under `PP_E2E_TAPE=1`.
 *
 * **`minReplayed` is optional because not every path can carry one**, which is a measured
 * finding rather than a gap: what authors the origin prompt decides whether anything beneath it
 * replays at all. Which path is which, and the numbers behind it, is `README.md` § The second
 * pass is free — they move whenever something re-keys, so they are quoted in one place only.
 *
 * The rule here is just the consequence: assert a floor only where the caller can honestly name
 * one, because a number drawn from a stochastic path is a flaky failure in the tier that costs
 * money.
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
  // Invariant either way: the bill can never exceed what the search incurred.
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
