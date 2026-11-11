import { test, expect, open, ready, DASHBOARD_VIEWS } from "../harness";

test.beforeEach(async ({ page, rich }) => {
  await open(page, `${rich.addr}${DASHBOARD_VIEWS}`);
});

test.describe("dashboard", () => {
  test("draws the pipeline graph", async ({ page }) => {
    const graph = page.getByRole("img", { name: "Pipeline graph" });
    await expect(graph).toBeVisible();
    await expect(graph.getByRole("button").first()).toBeVisible();
  });

  test("offers the candidates card and its bar channels", async ({ page }) => {
    await expect(page.getByRole("group", { name: "Bars" })).toBeVisible();
    for (const name of ["accuracy", "ability θ", "composite"]) {
      await expect(page.getByRole("button", { name }).first()).toBeVisible();
    }
  });

  test("the chronology is its own region and is readable", async ({ page }) => {
    // A column off screen reads nothing (`shell/Views.tsx`): swipe to it, as an operator would.
    await page.locator("#view-timeline").scrollIntoViewIfNeeded();
    // `TimeRay` waits on three reads, which lag well past 15s after the account spec.
    await expect(page.getByRole("region", { name: /Time-ray/ })).toBeVisible({ timeout: 60_000 });
  });

  test("a served object in scope can be copied", async ({ page }) => {
    await expect(page.getByRole("button", { name: /Copy this run's candidates/ })).toBeVisible();
  });

  test("the round charts draw, and survive a poll", async ({ page }) => {
    await page.locator("#view-spend").scrollIntoViewIfNeeded();
    // Sit through a poll: the spend map arrives on the wire, not at mount.
    await expect(page.getByText(/Spend by round/)).toBeVisible();
    await page.waitForTimeout(6000);
    await ready(page);
  });

  test("every canvas is named for a screen reader", async ({ page }) => {
    const unnamed = await page.locator("canvas:not([aria-label]):not([aria-hidden='true'])").count();
    expect(unnamed, "a <canvas> is on screen with no accessible name").toBe(0);
  });
});
