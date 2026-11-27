import type { AccountBulletins } from "@/lib/api/types";
import { test, expect, noSidewaysScroll, open, press, showSidebar } from "../harness";

// The walk writes nothing, so the account's read is answered here; its dismiss is the API's test.
const POSTED: AccountBulletins = {
  bulletins: [
    {
      id: "b-wipe",
      posted_at: "2026-11-26T12:00:00Z",
      text: "The next deploy wipes run data at 18:00 UTC: export what you keep.",
      pops_up: true,
      dismissed: false,
    },
  ],
};

test.describe("signed in, the host's bulletins", () => {
  test("one marked to pop up opens on each load until dismissed, and waits in the list", async ({
    page,
  }) => {
    await page.route("**/api/v1/auth/bulletins", (route) => route.fulfill({ json: POSTED }));
    const text = POSTED.bulletins[0]!.text;
    const popup = page.getByRole("dialog", { name: "Bulletin from the host" });
    for (let load = 0; load < 2; load++) {
      await open(page);
      await expect(popup).toContainText(text);
      await expect(popup.getByRole("button", { name: "Dismiss" })).toBeVisible();
      await noSidewaysScroll(page);
      // Closing waits for the next load: only a dismissal ends it.
      await page.keyboard.press("Escape");
      await expect(popup).toBeHidden();
    }
    await showSidebar(page);
    await press(page.getByRole("button", { name: /Bulletins from the host, 1 not dismissed/ }));
    const list = page.getByRole("dialog", { name: "Bulletins from the host" });
    await expect(list).toContainText(text);
    await expect(list.getByRole("button", { name: "Dismiss" })).toBeVisible();
  });
});

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
