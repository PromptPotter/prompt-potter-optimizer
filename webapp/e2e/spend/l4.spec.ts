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

// THE RECURSION, end to end: an outer campaign whose every cell is a whole inner campaign.
//
//     PP_E2E_SPEND=1 npx playwright test --project=spend e2e/spend/l4.spec.ts --headed
//
// It runs `promptpotter-self-e2e`, the degenerate one-cell panel in `datasets/`, NOT the real
// `promptpotter-self`, which is an instrument rather than a fixture and costs dollars an arm.
// Read nothing off this run; `datasets/promptpotter-self-e2e/dataset.md` says why at length.
//
// It is minted through the CONTROL PLANE rather than the browser or the CLI, and each of those
// is ruled out for its own reason. The browser cannot: an outer dataset owns an
// `inner_tasks.yaml`, so its cells are inner campaigns rather than rows and
// `resolve_dataset_items` — the one materialization seam the webapp ingest shares with run-time
// init — answers 0 for it. The CLI cannot, unattended: a backgrounded run has no TTY and blocks
// at the origin gate. `mint-campaign` can, and it takes `LaunchLimits`, so the ceiling rides the
// mint itself and there is never an unclamped window.

const BUDGET_USD = Number(process.env.PP_E2E_BUDGET_USD || "0.05");
const DATASET = "promptpotter-self-e2e";
// Round 0 (the origin) plus ONE outer search round. Depth is the expensive axis here — every
// outer cell is a whole inner campaign — so this stops one round earlier than the ordinary tier.
const WANT_ROUNDS = 2;

test.describe.configure({ mode: "serial" });
test.skip(!process.env.PP_E2E_SPEND, "real spend — set PP_E2E_SPEND=1 to run this tier");

let outer: Campaign | null = null;

/** Find our outer campaign again by DATASET, never by list position — the other spend spec
 *  mints into the same world and either may be newest. */
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
  // An L4 run holds an INNER run inside it. Pausing the outer is what stops the spend — the
  // inner is spawned by it and cannot outlive the process — but say so, because "paused" here
  // means something a level deeper than it does anywhere else in this suite.
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
    // The outer cells ARE inner campaigns on `justlogic-d234`, which routes over HTTP like every
    // other install dataset — so this tier needs the backend too, and asserted it nowhere.
    await assertBackendUp(request);
  });

  test("the fixture panel is the degenerate one, not the instrument", async ({ request }) => {
    // A guard against the one mistake that would be expensive rather than merely wrong: running
    // the real panel here, which would quietly cost dollars while this tier claims cents.
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

    // The origin gate is a real decision point and an unattended run stops dead at it, so it is
    // ANSWERED here rather than waited out — which is also the one L4 moment an operator watching
    // the browser gets to see a decision surface as a button.
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

    // "Measured, or stopped" is only an honest pair while STOPPED means a terminal reason the
    // engine chose. `crashed` is neither, and accepting it made this test pass on a run that
    // died in `AxisIndex._fold_entry` before the round loop was ever entered — a false green
    // written into the one tier that costs money to run.
    assertBoundedStop("the recursion", stopped);
    expect(
      completed > 0 || /budget/.test(stopped),
      `nothing was measured and the run stopped on '${stopped}' rather than on its ceiling`,
    ).toBeTruthy();

    // This tier is the one `assertRoundMeasured` was written for — its docstring carries the
    // three green passes that paid for it.
    const rounds = roundsOf(await dashboard(request, outer!));
    for (const r of rounds) assertRoundMeasured(DATASET, r);

    // The outer SEARCH round — generate, score, elect — at the one depth that costs a whole
    // inner campaign per cell. One is enough here; `campaign.yaml` says why depth is the
    // expensive axis and why the inner campaigns stay at `max_inner_rounds: 1`.
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

    // INCURRED, not used: on a replayed pass the bill is ~0 by construction, so reading the
    // drift guard off the bill would report a re-keyed panel as a cheap one. What must stay under
    // the ceiling is what the search COSTS, whoever paid for it.
    const tape = tapeOf(d);
    expect(tape, "the L4 run served no spend rollup").not.toBeNull();
    expect(
      tape!.incurred,
      "the fixture incurred its whole ceiling — the panel geometry has drifted",
    ).toBeLessThan(BUDGET_USD);
    // This path CAN carry a floor: the outer origin is the dataset's own file, so nothing
    // upstream of the cells is authored at run time. The 0.5, and why a tighter bar was refuted,
    // are ../README.md's.
    reportTape(DATASET, tape, 0.5);
  });

  test("what the recursion wrote is readable in Files", async ({ page }) => {
    await open(page, `${outer!.addr}/files`);
    await passConsent(page);
    await expect(page.locator("#main-content").getByRole("button").first()).toBeVisible();
  });
});
