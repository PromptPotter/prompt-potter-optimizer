import { test, expect, open, ready } from "../harness";

const PANES = [
  ["profile", "Profile"],
  ["usage", "Usage & limits"],
  ["activity", "Activity"],
  ["storage", "Storage"],
  ["preferences", "Preferences"],
  ["about", "About this unit"],
] as const;

test.describe("account", () => {
  test("opens from the sidebar", async ({ page }) => {
    await open(page);
    await page.getByRole("button", { name: "Open account" }).click();
    await expect(page.getByRole("dialog", { name: "Account" })).toBeVisible();
  });

  for (const [pane, label] of PANES) {
    test(`deep-links to the ${pane} pane`, async ({ page }) => {
      await open(page, `#/account/${pane}`);
      const dialog = page.getByRole("dialog", { name: "Account" });
      await expect(dialog).toBeVisible();
      // Exact: the Profile pane also carries an "Update profile" button.
      await expect(dialog.getByRole("button", { name: label, exact: true })).toBeVisible();
    });
  }

  test("walks every pane in one session", async ({ page }) => {
    await open(page, "#/account/profile");
    const dialog = page.getByRole("dialog", { name: "Account" });
    for (const [pane, label] of PANES) {
      await dialog.getByRole("button", { name: label, exact: true }).click();
      await expect.poll(() => new URL(page.url()).hash).toContain(`/account/${pane}`);
      await expect(dialog).toBeVisible();
    }
  });

  test("closing returns to where the operator was, not to a second memory of it", async ({ page }) => {
    // The account address carries no cycle; the pin lives in workspace state.
    await open(page, "#/account/storage");
    await page.getByRole("dialog", { name: "Account" }).getByRole("button", { name: "Close" }).click();
    await expect(page.getByRole("dialog", { name: "Account" })).toBeHidden();
    await ready(page);
  });

  test("never claims a verification the brand does not declare", async ({ page, request }) => {
    await open(page, "#/account/about");
    const dialog = page.getByRole("dialog", { name: "Account" });
    // The PILL is the claim (`AboutUnit::ProvenancePill`); the pane's prose says "verified" too.
    await expect(dialog.getByText("Self-declared").first()).toBeVisible();
    await expect(dialog.getByText("✓ Verified")).toHaveCount(0);

    const health = await (await request.get("/api/v1/health")).json();
    await expect(dialog.getByText(health.version, { exact: false }).first()).toBeVisible();
  });
});
