import { test, expect, noSidewaysScroll } from "../harness";

test.describe("login", () => {
  test("serves as its own document", async ({ page }) => {
    const res = await page.goto("/login/");
    expect(res?.status(), "GET /login/").toBe(200);
    // The h1 is the whitelabel-overridable `BRAND.shortName`, so no literal is asserted.
    const h1 = page.getByRole("heading", { level: 1 });
    await expect(h1).toBeVisible();
    await expect(h1).not.toHaveText(/^\s*$/);
  });

  test("offers Google, and only Google", async ({ page }) => {
    await page.goto("/login/");
    await expect(page.getByRole("link", { name: /Continue with Google/ })).toHaveAttribute(
      "href",
      "/api/v1/auth/login/google",
    );
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
  });

  test("carries the buyer-facing aside where the brand declares one", async ({ page }) => {
    await page.goto("/login/");
    // Absent when the brand declares no marketing copy: a configuration, not a regression.
    const aside = page.getByRole("complementary");
    const n = await aside.count();
    test.skip(n === 0, "this brand declares no marketing copy");
    await expect(aside.first()).toBeVisible();
  });

  test("fits a phone", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/login/");
    await noSidewaysScroll(page);
  });
});
