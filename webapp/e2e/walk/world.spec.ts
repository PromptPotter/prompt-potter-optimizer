import { test, expect, campaigns, richestCampaign } from "../harness";

// The walk tier's precondition as a failing test: every other spec SKIPS on an empty world, so
// without this the tier exits 0 having checked nothing.

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
