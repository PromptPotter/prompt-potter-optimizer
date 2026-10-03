import {
  test,
  expect,
  open,
  ready,
  assertBackendUp,
  assertBoundedStop,
  assertRoundMeasured,
  command,
  dashboard,
  passConsent,
  reportTape,
  roundsOf,
  tapeOf,
  assertThrowawayWorld,
  type Campaign,
} from "../harness";

// The recursion end to end, on the degenerate `promptpotter-self-e2e` panel. Minted through the
// control plane: browser ingest materializes 0 items for an outer dataset, and the CLI blocks on a TTY.

const BUDGET_USD = Number(process.env.PP_E2E_BUDGET_USD || "0.05");
const DATASET = "promptpotter-self-e2e";
// One outer search round: every outer cell is a whole inner campaign.
const WANT_ROUNDS = 2;

test.describe.configure({ mode: "serial" });
test.skip(!process.env.PP_E2E_SPEND, "real spend — set PP_E2E_SPEND=1 to run this tier");

let outer: Campaign | null = null;

// By DATASET, never list position: the other spend spec mints into the same world.
async function findOuter(request: import("@playwright/test").APIRequestContext) {
  const res = await request.get("/api/v1/campaigns");
  if (!res.ok()) return null;
  const rows: Record<string, string>[] = (await res.json()).campaigns ?? [];
  const row = rows.find((c) => c.dataset_name === DATASET && c.root_cycle_id);
  return row
    ? {
        id: row.campaign_id!,
        cycleId: row.root_cycle_id!,
        addr: `#/c/${row.campaign_id}/${row.root_cycle_id!.replace(/^cycle_/, "")}`,
      }
    : null;
}

test.afterAll(async ({ request }) => {
  outer = (await findOuter(request)) ?? outer;
  if (!outer) return;
  // Pausing the outer stops the inner too: it cannot outlive the process that spawned it.
  const { status, body } = await command(request, "pause-cycle", {
    campaign_id: outer.id,
    cycle_id: outer.cycleId,
  });
  console.log(
    status < 300
      ? `[e2e] paused outer ${outer.id} (its inner run stops with it)`
      : `[e2e] pause REFUSED (${status}) — confirm nothing is still running: ${body}`,
  );
});

test.describe("the recursion, end to end", () => {
  test("it is pointed at a throwaway world, not the operator's", async ({ request }) => {
    await assertThrowawayWorld(request);
  });

  test("the backend the inner benchmark needs is reachable", async ({ request }) => {
    // The outer cells are inner campaigns on `justlogic-d234`, which routes to the backend too.
    await assertBackendUp(request);
  });

  test("the fixture panel is the degenerate one, not the instrument", async ({ request }) => {
    const res = await request.get("/api/v1/datasets");
    expect(res.ok()).toBeTruthy();
    const names = ((await res.json()).datasets ?? []).map((d: { name: string }) => d.name);
    expect(names, `${DATASET} is not installed`).toContain(DATASET);
    expect(DATASET, "this tier must never run the calibrated panel").not.toBe("promptpotter-self");
  });

  test("minting carries the ceiling, so there is no unclamped window", async ({ request }) => {
    const res = await command(request, "mint-campaign", {
      dataset_name: DATASET,
      spend_budget_usd: BUDGET_USD,
    });
    expect(res.status, `mint-campaign: ${res.body}`).toBeLessThan(300);

    await expect
      .poll(async () => (await findOuter(request)) !== null, {
        message: "mint-campaign acked but no campaign appeared",
        timeout: 120_000,
      })
      .toBeTruthy();
    outer = await findOuter(request);
    console.log(`[e2e] outer ${outer!.id} minted at $${BUDGET_USD}`);
  });

  test("the browser can address the outer campaign before it runs", async ({ page }) => {
    await open(page, outer!.addr);
    await passConsent(page);
    await ready(page);
    await expect(page.getByText(`ID: ${outer!.cycleId}`)).toBeVisible();
  });

  test("start-run spawns the recursion, and it either measures or stops on its ceiling", async ({
    page,
    request,
  }) => {
    test.setTimeout(900_000);
    const started = await command(request, "start-run", {
      campaign_id: outer!.id,
      cycle_id: outer!.cycleId,
      kind: "new",
      spend_budget_usd: BUDGET_USD,
    });
    expect(started.status, `start-run: ${started.body}`).toBeLessThan(300);

    // An unattended run stops dead at the origin gate, so it is answered here.
    let gated = false;
    let completed = 0;
    let stopped = "";
    await expect
      .poll(
        async () => {
          const d = await dashboard(request, outer!);
          const blob = JSON.stringify(d ?? {});
          completed = ((d?.rounds as unknown[]) ?? []).length;
          stopped = String(d?.stop_reason ?? "");
          if (!gated && /origin_gate/.test(blob)) {
            gated = true;
            const ans = await command(request, "origin-gate-decision", {
              campaign_id: outer!.id,
              cycle_id: outer!.cycleId,
              decision: "proceed",
            });
            console.log(`[e2e] origin gate answered proceed → ${ans.status}`);
          }
          return completed >= WANT_ROUNDS || !!stopped;
        },
        {
          message:
            `the recursion completed fewer than ${WANT_ROUNDS} rounds and never stopped — it is neither working nor bounded`,
          timeout: 780_000,
        },
      )
      .toBeTruthy();
    console.log(
      `[e2e] outer rounds=${completed} stop_reason=${stopped || "(still running)"} gate=${gated}`,
    );

    assertBoundedStop("the recursion", stopped);
    expect(
      completed > 0 || /budget/.test(stopped),
      `nothing was measured and the run stopped on '${stopped}' rather than on its ceiling`,
    ).toBeTruthy();

    const rounds = roundsOf(await dashboard(request, outer!));
    for (const r of rounds) assertRoundMeasured(DATASET, r);

    for (const r of rounds.filter((x) => x.round > 0)) {
      expect(
        r.candidates.length,
        `outer round ${r.round} generated ${r.candidates.length} candidate(s) — with nothing to choose between, selection and election are untested`,
      ).toBeGreaterThan(1);
      expect(
        r.improved,
        `outer round ${r.round} closed with no verdict — cells were scored but no election was held`,
      ).not.toBeNull();
      console.log(
        `[e2e] outer round ${r.round}: ${r.candidates.length} candidates, improved=${r.improved}`,
      );
    }

    await open(page, `${outer!.addr}/dashboard`);
    await passConsent(page);
    await ready(page);
    await expect(page.getByRole("img", { name: "Pipeline graph" })).toBeVisible();
  });

  test("the run stayed inside the ceiling it was minted with", async ({ request }) => {
    const d = await dashboard(request, outer!);
    const limits = d?.run_limits as { spend_budget_usd?: number | null } | undefined;
    expect(limits?.spend_budget_usd, "the L4 run has no USD ceiling at all").not.toBeNull();
    expect(limits?.spend_budget_usd).toBeLessThanOrEqual(BUDGET_USD);

    // INCURRED, not used: a replayed pass bills ~0, which would hide a re-keyed panel.
    const tape = tapeOf(d);
    expect(tape, "the L4 run served no spend rollup").not.toBeNull();
    expect(
      tape!.incurred,
      "the fixture incurred its whole ceiling — the panel geometry has drifted",
    ).toBeLessThan(BUDGET_USD);
    // A floor holds here: the outer origin is the dataset's own file, authored nothing at run time.
    reportTape(DATASET, tape, 0.5);
  });

  test("what the recursion wrote is readable in Files", async ({ page }) => {
    await open(page, `${outer!.addr}/files`);
    await passConsent(page);
    await expect(page.locator("#main-content").getByRole("button").first()).toBeVisible();
  });
});
