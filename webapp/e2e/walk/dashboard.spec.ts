import { test, expect, open, ready } from "../harness";

test.beforeEach(async ({ page, rich }) => {
  await open(page, `${rich.addr}/dashboard`);
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
    // `TimeRay` waits on three reads, which lag well past 15s after the account-pane spec.
    await expect(page.getByRole("region", { name: /Time-ray/ })).toBeVisible({ timeout: 60_000 });
  });

  test("a served object in scope can be copied", async ({ page }) => {
    await expect(page.getByRole("button", { name: /Copy all candidates/ })).toBeVisible();
  });

  test("the round charts draw, and survive a poll", async ({ page }) => {
    // Sit through a poll: the spend map arrives on the wire, not at mount.
    await expect(page.getByText(/Cost by round/)).toBeVisible();
    await page.waitForTimeout(6000);
    await ready(page);
  });

  test("every canvas is named for a screen reader", async ({ page }) => {
    const unnamed = await page.locator("canvas:not([aria-label]):not([aria-hidden='true'])").count();
    expect(unnamed, "a <canvas> is on screen with no accessible name").toBe(0);
  });
});
