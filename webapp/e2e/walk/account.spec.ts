import { test, expect, open, ready } from "../harness";

// The account modal — the second view axis, and the one surface the address codec names
// that carries no cycle. Six panes, each its own read; "About this unit" additionally
// carries the brand identity the whitelabel build overrides.

const PANES = [
  ["profile", "Profile"],
  ["security", "Security"],
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
      // Exact: the Profile pane also carries an "Update profile" button, and a substring
      // match claims both.
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
    // The account address does NOT carry the cycle: the pin lives in workspace state and is
    // untouched while the modal is up.
    await open(page, "#/account/storage");
    await page.getByRole("dialog", { name: "Account" }).getByRole("button", { name: "Close" }).click();
    await expect(page.getByRole("dialog", { name: "Account" })).toBeHidden();
    await ready(page);
  });

  test("never claims a verification the brand does not declare", async ({ page, request }) => {
    await open(page, "#/account/about");
    const dialog = page.getByRole("dialog", { name: "Account" });
    // The PILL is the claim — `AboutUnit::ProvenancePill`. Asserting the word "verified" is
    // absent instead catches the pane's own prose explaining what verification would mean,
    // which is the opposite of the defect.
    await expect(dialog.getByText("Self-declared").first()).toBeVisible();
    await expect(dialog.getByText("✓ Verified")).toHaveCount(0);

    // And it is the app's version, not a build-time copy of it.
    const health = await (await request.get("/api/v1/health")).json();
    await expect(dialog.getByText(health.version, { exact: false }).first()).toBeVisible();
  });
});
