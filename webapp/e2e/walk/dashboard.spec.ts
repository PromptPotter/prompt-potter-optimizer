import { test, expect, open, ready } from "../harness";

// The Dashboard's regions — the §0 primitives the operator observes. Vitest covers the
// derivations behind them as pure data; what it cannot reach is whether the panes mount at
// all against a real served cycle, which is the failure this file exists for.

test.beforeEach(async ({ page, rich }) => {
  await open(page, `${rich.addr}/dashboard`);
});

test.describe("dashboard", () => {
  test("draws the pipeline graph", async ({ page }) => {
    // One graph, served `view` + tier/rank — never a hand-placed geometry beside it.
    const graph = page.getByRole("img", { name: "Pipeline graph" });
    await expect(graph).toBeVisible();
    // A node is a painted control and earns keyboard operability.
    await expect(graph.getByRole("button").first()).toBeVisible();
  });

  test("offers the candidates card and its bar channels", async ({ page }) => {
    await expect(page.getByRole("group", { name: "Bars" })).toBeVisible();
    // The three channels are declared once in `candidates/series.ts`.
    for (const name of ["accuracy", "ability θ", "composite"]) {
      await expect(page.getByRole("button", { name }).first()).toBeVisible();
    }
  });

  test("the chronology is its own region and is readable", async ({ page }) => {
    // The one assertion here that waits on THREE reads landing together — `/cycles`, the
    // dashboard poll and `/ray` — because `TimeRay` renders null until the ray reports
    // `loaded`. After a spec that has walked the account panes, all three sit unanswered well
    // past the 15s default while the server drains what those panes started; they arrive, just
    // late. That wait is a server-capacity fact filed in `code-debt-cleanup.md`, and it is not
    // what this test is for — so the bound is generous and the assertion stays about the region.
    await expect(page.getByRole("region", { name: /Time-ray/ })).toBeVisible({ timeout: 60_000 });
  });

  test("the secondary panes and the config map mount", async ({ page }) => {
    await expect(page.getByRole("region", { name: /Config map/ })).toBeVisible();
    await expect(page.getByRole("region", { name: /2ndary-relevant-info/ })).toBeVisible();
  });

  test("a served object in scope can be copied", async ({ page }) => {
    // "A panel that can be READ can be COPIED" — the payload is a derivation, so the
    // assertion is that the affordance is there, never what it serializes.
    await expect(page.getByRole("button", { name: /Copy all candidates as JSON/ })).toBeVisible();
  });

  test("the collapsed panes open, and what they draw survives a poll", async ({ page }) => {
    // The cost/trend/frequency charts live behind a fold, so a walk that only asserts the
    // first paint never renders them — which is how a crash in `roundCosts` could be caught
    // one run in two and look like flake. Open the fold, then sit through a poll: the
    // spend map arrives on the wire, not at mount.
    const fold = page.getByRole("button", { name: /2ndary-relevant-info/ }).first();
    if ((await fold.getAttribute("aria-expanded")) === "false") await fold.click();
    await expect(page.getByText(/Cost by round/)).toBeVisible();
    // Two poll ticks at 2s each, plus slack — long enough for `spend_by_round` to land.
    await page.waitForTimeout(6000);
    await ready(page);
  });

  test("every canvas is named for a screen reader", async ({ page }) => {
    // "A painted surface is a control too … a <canvas> earns an aria-label naming what it
    // plots" — the rule that let every Chart.js canvas ship unnamed once already.
    const unnamed = await page.locator("canvas:not([aria-label]):not([aria-hidden='true'])").count();
    expect(unnamed, "a <canvas> is on screen with no accessible name").toBe(0);
  });
});
