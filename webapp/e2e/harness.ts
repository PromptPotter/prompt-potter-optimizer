import { existsSync, readFileSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  test as base,
  expect,
  type Locator,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import {
  STOP_REASON_INFO,
  type CommandPayloads,
  type HierarchyResponse,
  type ServedRound,
} from "@/lib/api/types";
import { isStopReason, tagValue } from "@/lib/derivations";
import { decodeCyclePath, encodeCyclePath } from "@/lib/ids";

const API = "/api/v1";

// Never add anything the app emits: a suppressed app error is the breakage the walk exists to catch.
const BENIGN: RegExp[] = [];

const EXPECTED_4XX: [RegExp, number][] = [
  [/\/active\b/, 404],
  // A selection with no scored rows — ticking a campaign whose origin never ran.
  [/\/evidence\b/, 400],
  // A campaign with no completed round 404s round 0; its audit twin answers `200 null`, so a 4xx on `/audit` is reported.
  [/\/rounds\/\d+(\?|$)/, 404],
  // The throwaway world holds no provider key, so a check-in's resolver turn is refused `no_api_key`.
  [/\/commands\/resolve-origin\b/, 422],
];

type Problem = { kind: string; text: string };

function faultLog(baseURL: string | undefined): string | null {
  if (!baseURL) return null;
  const port = new URL(baseURL).port;
  return port ? path.join(os.tmpdir(), `pp-e2e-server-${port}.log`) : null;
}

// Memoized so concurrent tests share one scan; safe only because the walk tier never writes.
let richest: Promise<Campaign | null> | null = null;

// `provide`, not `use`: `react-hooks/rules-of-hooks` reads `use` as React's hook and fails the lint.
export const test = base.extend<{ problems: Problem[]; rich: Campaign; reads: void }>({
  // Counted in the document, so a reload starts at none: a `fetch` (every read, `lib/api/client.ts`),
  // never the feed's stream nor a lazy pane's chunk (its `aria-busy` says that).
  reads: [
    async ({ page }, provide) => {
      await page.addInitScript(() => {
        const w = window as unknown as { __openReads: number; fetch: typeof fetch };
        const send = w.fetch.bind(window);
        w.__openReads = 0;
        w.fetch = (...args) => {
          w.__openReads++;
          return send(...args).finally(() => w.__openReads--);
        };
      });
      await provide();
    },
    { auto: true },
  ],

  rich: async ({ request }, provide, testInfo) => {
    richest ??= richestCampaign(request);
    const found = await richest;
    testInfo.skip(!found, "no campaign with a cycle in this workspace");
    await provide(found!);
  },

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
        // At any level and past `BENIGN`: a refused style or script is a page the policy broke.
        if (/Content[- ]Security[- ]Policy/i.test(m.text())) {
          problems.push({ kind: "csp", text: m.text() });
          return;
        }
        if (m.type() !== "error") return;
        // Chrome's failed-subresource error carries no status; it is read off the response channel below.
        const url = m.location().url;
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
          // A detached run's later server fault lands on whichever test is next: mis-attributed, never hidden.
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

/** Both headings are read off `ui/ErrorBoundary`. */
export async function ready(page: Page) {
  await expect(page.locator("#main-content")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /hit a render error|does not match the server/i }),
  ).toHaveCount(0);
}

/** The Dashboard preset's views as an address tail (`components/shell/families.tsx::PRESETS`). */
export const DASHBOARD_VIEWS = "/timeline/spend/heatmap/optimizer/candidates/leader";

/** The reload is required: a fragment-only `goto` does not remount, so one view's crash would follow every later address. */
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

export async function campaigns(
  request: APIRequestContext,
  dataset?: string,
): Promise<(Campaign & { dataset: string | null })[]> {
  const qs = dataset ? `&dataset=${encodeURIComponent(dataset)}` : "";
  const res = await request.get(`${API}/hierarchy?depth=0${qs}`);
  expect(res.ok(), `GET ${API}/hierarchy → ${res.status()}`).toBeTruthy();
  const { entries } = (await res.json()) as HierarchyResponse;
  return entries.flatMap((c) => {
    const root = c.entry_kind === "campaign" ? decodeCyclePath(c.facts.root)?.[0] : undefined;
    if (!root) return [];
    return [
      {
        id: c.address,
        cycleId: root.cycleId,
        addr: `#/${encodeCyclePath([root])}`,
        dataset: tagValue(c, "dataset"),
      },
    ];
  });
}

export type Draft = { draftId: string; slug: string };

export async function drafts(request: APIRequestContext): Promise<Draft[]> {
  const res = await request.get(`${API}/hierarchy?depth=0&lifecycle=unstarted`);
  expect(res.ok(), `GET ${API}/hierarchy → ${res.status()}`).toBeTruthy();
  const { entries } = (await res.json()) as HierarchyResponse;
  return entries.map((d) => ({ draftId: d.address, slug: d.label }));
}

/** Never the newest campaign: it is often a cycle with no round yet, whose empty panes pass whatever the chart code does. */
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

const E2E_DATASETS = ["email-tagging", "promptpotter-self/e2e"];

/** Not "the world is empty": the spend specs share one workspace, so the second finds the first's campaign. */
export async function assertThrowawayWorld(request: APIRequestContext) {
  const foreign = (await campaigns(request))
    .map((c) => c.dataset)
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

export async function passConsent(page: Page) {
  const gate = consentGate(page);
  if ((await gate.count()) === 0) return;
  await gate.getByRole("checkbox").check();
  await gate.getByRole("button", { name: /Agree & continue/ }).click();
  await expect(gate).toBeHidden();
}

/** The body IS the kind's payload model (`lib/api/commands.ts`). Never retry a 4xx — a refusal is permanent. */
export async function command<K extends keyof CommandPayloads>(
  request: APIRequestContext,
  kind: K,
  payload: CommandPayloads[K],
): Promise<{ status: number; body: string }> {
  const res = await request.post(`${API}/commands/${kind}`, {
    data: payload,
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

export function roundsOf(dash: Record<string, unknown> | null): ServedRound[] {
  const rows = dash?.rounds;
  return Array.isArray(rows) ? (rows as ServedRound[]) : [];
}

/** A round that CLOSED need not have MEASURED: one whose every cell errored still closes, with `null` fitness throughout. */
export function assertRoundMeasured(label: string, round: ServedRound | undefined) {
  expect(round, `${label}: no round to read`).toBeDefined();
  const graded = (round?.candidates ?? []).filter(
    (c) => c.reading.own?.fitness != null && c.reading.end !== "invalid",
  );
  expect(
    graded.length,
    `${label} round ${round?.round} CLOSED with no graded candidate — every cell on it errored ` +
      `or was rejected, so the round happened without measuring anything`,
  ).toBeGreaterThan(0);
}

/** Checked before any spend: `backend_unreachable` is a bounded `halted`, which `assertBoundedStop` would pass. */
const BACKEND_URL = process.env.PP_E2E_BACKEND_URL || "http://127.0.0.1:8000";

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
  if (!isStopReason(stopped)) {
    throw new Error(
      `${label} stopped on '${stopped}', which STOP_REASON_INFO does not classify — the ` +
        `generated mirror has drifted from domain/phases.py::STOP_REASON_INFO`,
    );
  }
  expect(
    STOP_REASON_INFO[stopped].outcome,
    `${label} terminated on '${stopped}', which the engine classifies as a FAILURE rather than a ` +
      `bounded stop`,
  ).not.toBe("failed");
}

/** `replayed` is `null` where there is no share, never 0: read as zero, a cache that went missing reports a perfect hit. */
type Tape = { used: number; incurred: number; replayed: number | null };

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
    replayed: incurred > 0 ? 1 - used / incurred : null,
  };
}

/** Pass `minReplayed` only where the path guarantees one: a floor on a stochastic path is a flaky failure in the paid tier. */
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

/** A finger taps: a click on the touch project is a mouse, whose hover opens a row's card over the
 *  next control, and no phone hovers. */
export async function press(target: Locator) {
  await (base.info().project.use.hasTouch ? target.tap() : target.click());
}

/** On a phone the sidebar is the list screen, behind the title row's back button. */
export async function showSidebar(page: Page) {
  const back = page.getByRole("button", { name: /^Campaigns/ });
  if (await back.isVisible()) await press(back);
}

/** Back from a phone's list screen: the viewed campaign's row reopens the views it left. */
export async function hideSidebar(page: Page) {
  const row = page.locator('.mobile-list .sidebar-tree-item[aria-current="true"]');
  if (await row.isVisible()) await press(row);
}

/** The page has drawn every answer it asked for: no request open two frames after the last one closed
 *  (an answer's render asks the next read), and no lazy pane still `aria-busy`. A layout check reads
 *  after this, or a row its read has not yet answered is never checked. */
async function drawn(page: Page) {
  const open = () => page.evaluate(() => (window as unknown as { __openReads: number }).__openReads);
  do {
    await expect.poll(open, { message: "a read is still open" }).toBe(0);
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  } while (await open());
  await expect(page.locator('[aria-busy="true"]:visible')).toHaveCount(0);
}

// The panes, not only the page: `.main` clips and `.content` scrolls, so the document never overflows.
export async function noSidewaysScroll(page: Page) {
  await drawn(page);
  const over = await page.evaluate(() =>
    [document.documentElement, ...document.querySelectorAll(".content, .view, .card")]
      .filter((el) => el.getClientRects().length > 0 && el.scrollWidth - el.clientWidth > 1)
      .map((el) => `${el.tagName.toLowerCase()}.${[...el.classList].join(".")} +${el.scrollWidth - el.clientWidth}px`),
  );
  expect(over, "a pane scrolls sideways").toEqual([]);
}

/** A tap lands on the control drawn there: nothing else sits at a visible button's centre, and on a
 *  touch screen no button is under the 44px floor (`responsive.css`). A cover is read again two frames
 *  after the scroll: an open panel follows its trigger a frame later (`ui/overlay.ts::useAnchored`),
 *  so the scroll's own frame shows it where no user sees it. A tap hovers nothing, so a mouse's last
 *  press is taken off the page first: left over a row, it opens the row's card (`ui/HoverCard`,
 *  portalled to `body`), which on a phone width spans the list it would be checked against. */
export async function everyTapLands(page: Page) {
  const touch = base.info().project.use.hasTouch === true;
  if (!touch) {
    await page.mouse.move(-1, -1);
    await expect(page.locator('body > [role="note"]')).toHaveCount(0);
  }
  await drawn(page);
  const wrong = await page.evaluate(async (touch) => {
    const settle = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const cover = (b: HTMLButtonElement) => {
      b.scrollIntoView({ block: "center", inline: "nearest" });
      const r = b.getBoundingClientRect();
      const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return top && !b.contains(top) ? `${top.tagName.toLowerCase()}.${[...top.classList].join(".")}` : null;
    };
    const out: string[] = [];
    for (const b of document.querySelectorAll("button")) {
      if (!b.checkVisibility()) continue;
      const name = (b.getAttribute("aria-label") || b.textContent || "").trim().slice(0, 30);
      let over = cover(b);
      const r = b.getBoundingClientRect();
      if (touch && (r.width < 43.5 || r.height < 43.5)) out.push(`"${name}" is ${Math.round(r.width)}x${Math.round(r.height)}`);
      if (over) {
        await settle();
        cover(b);
        await settle();
        over = cover(b);
      }
      if (over) out.push(`"${name}" is under ${over}`);
    }
    return out;
  }, touch);
  expect(wrong, "a tap lands off the control drawn there").toEqual([]);
}
