import { test, expect, open, ready } from "../harness";

const SECTIONS = [
  ["profile", "Profile"],
  ["usage", "Usage & limits"],
  ["keys", "API keys"],
  ["activity", "Activity"],
  ["storage", "Storage"],
  ["preferences", "Preferences"],
  ["about", "About this unit"],
] as const;

const account = (page: import("@playwright/test").Page) =>
  page.getByRole("region", { name: "Account" });

// The nav's own: a section body may link another section by its label.
const navButton = (page: import("@playwright/test").Page, label: string) =>
  account(page).getByRole("navigation", { name: "Account sections" }).getByRole("button", { name: label, exact: true });

test.describe("account", () => {
  test("opens from the sidebar", async ({ page }) => {
    await open(page);
    await page.getByRole("button", { name: "Open account" }).click();
    await expect(account(page)).toBeVisible();
  });

  for (const [section, label] of SECTIONS) {
    test(`deep-links to the ${section} section`, async ({ page }) => {
      await open(page, `#/account=${section}`);
      await expect(navButton(page, label)).toBeVisible();
    });
  }

  test("walks every section in one session", async ({ page }) => {
    await open(page, "#/account=profile");
    for (const [section, label] of SECTIONS) {
      await navButton(page, label).click();
      await expect.poll(() => new URL(page.url()).hash).toContain(`/account=${section}`);
    }
  });

  test("closing the view returns to what was open beside it", async ({ page }) => {
    await open(page, "#/chat/account=storage");
    await page.getByRole("button", { name: "Close Account" }).click();
    await expect(account(page)).toBeHidden();
    await ready(page);
  });

  test("never claims a verification the brand does not declare", async ({ page, request }) => {
    await open(page, "#/account=about");
    // The PILL is the claim (`AboutUnit::ProvenancePill`); the section's prose says "verified" too.
    await expect(account(page).getByText("Self-declared").first()).toBeVisible();
    await expect(account(page).getByText("✓ Verified")).toHaveCount(0);

    const health = await (await request.get("/api/v1/health")).json();
    await expect(account(page).getByText(health.version, { exact: false }).first()).toBeVisible();
  });
});
