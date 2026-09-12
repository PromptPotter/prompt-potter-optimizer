import { test, expect, noSidewaysScroll } from "../harness";

// `/login/` is the only second route the export ships, and the only page a signed-out
// visitor is guaranteed to meet. Under `StaticFiles(html=True)` with `trailingSlash: true`
// it resolves to its own index — a route that 404s on reload is a first-user dead end, and
// nothing else in the suite would notice.

test.describe("login", () => {
  test("serves as its own document", async ({ page }) => {
    const res = await page.goto("/login/");
    expect(res?.status(), "GET /login/").toBe(200);
    // The h1 is `BRAND.shortName`, which a distributor overrides via NEXT_PUBLIC_BRAND_SHORT_NAME.
    // Asserting the literal "PromptPotter" fails a whitelabel build — a supported configuration —
    // so what is checked is that the page HAS a level-1 heading with something in it.
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
    // We federate identity and never store passwords, so there is no password field to find.
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
  });

  test("carries the buyer-facing aside where the brand declares one", async ({ page }) => {
    await page.goto("/login/");
    // The aside is labelled `About ${BRAND.marketing.title}` and renders only while the brand
    // declares marketing copy — a distributor clears it deliberately. So its ABSENCE is a
    // configuration, not a regression; what must hold is that it is a labelled landmark when
    // it is there at all.
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
