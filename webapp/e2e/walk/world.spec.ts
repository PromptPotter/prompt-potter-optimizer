import { test, expect, campaigns, richestCampaign } from "../harness";

// THE WALK TIER'S PRECONDITION, asserted rather than assumed.
//
// Every spec that needs a campaign skips itself when discovery finds none. That is right per
// spec and catastrophic in aggregate: against an empty workspace — precisely what CI has — the
// whole tier exits 0 having asserted almost nothing, and reports it as "skipped" rather than as
// no coverage. A suite that cannot tell "passed" from "never ran" is worse than no suite,
// because it is the one that gets trusted.
//
// So the precondition gets its own failing test. One clear red saying the world was wrong,
// instead of 27 greens that mean nothing.

test.describe("the walk world", () => {
  test("has campaigns to walk", async ({ request }) => {
    const all = await campaigns(request);
    expect(
      all.length,
      "the walk tier reads the operator's own workspace and this one holds no campaign with a " +
        "cycle — every spec below it would SKIP and the tier would exit 0 having checked " +
        "nothing. Point PP_E2E_BASE_URL at a populated server, or run --project=cold.",
    ).toBeGreaterThan(0);
  });

  test("has a campaign with rounds, so the dashboard has something to draw", async ({ request }) => {
    // `richestCampaign` is what the dashboard and records tiers anchor on. If the richest
    // campaign has no rounds, those tiers still run — against empty panes, asserting that a
    // chart which was never asked to draw anything did not crash.
    const rich = await richestCampaign(request);
    expect(rich, "no campaign at all").not.toBeNull();
    const res = await request.get(
      `/api/v1/campaigns/${rich!.id}/cycles/${rich!.cycleId}/dashboard`,
    );
    expect(res.ok(), `dashboard for ${rich!.id} → ${res.status()}`).toBeTruthy();
    const rounds = ((await res.json()).rounds ?? []).length;
    console.log(`[e2e] richest campaign: ${rich!.id} (${rounds} rounds)`);
    expect(
      rounds,
      `${rich!.id} is the richest campaign here and it has no completed rounds — the dashboard ` +
        "and records tiers will assert against empty panes and pass whatever the chart code does",
    ).toBeGreaterThan(0);
  });
});
