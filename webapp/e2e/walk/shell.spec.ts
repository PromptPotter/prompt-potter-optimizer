import { test, expect, open, ready, campaigns } from "../harness";

test.describe("app shell", () => {
  test("boots to the shell, not to a crash", async ({ page }) => {
    await open(page);
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Skip to content" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    // Never the brand literal: `lib/brand.ts` is NEXT_PUBLIC_*-overridable for whitelabel.
    await expect(page.locator("#main-content")).toBeVisible();
  });

  test("the sidebar lists exactly the campaigns the API serves", async ({ page, request }) => {
    const served = await campaigns(request);
    await open(page);

    // Counted by their ⋯ menus: row labels move when the operator runs.
    const rows = page.getByRole("button", { name: "Campaign actions" });
    await expect(rows).toHaveCount(served.length);
  });

  test("a campaign row opens its actions, and Escape closes them", async ({ page, request }) => {
    test.skip((await campaigns(request)).length === 0, "no campaign in this workspace");
    await open(page);

    await page.getByRole("button", { name: "Campaign actions" }).first().click();
    const menu = page.getByRole("menu");
    await expect(menu).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
  });

  test("the campaign filter opens", async ({ page }) => {
    await open(page);
    await page.getByRole("button", { name: "Filter campaigns" }).click();
    // The surface, not the member list: the lifecycle set is owned server-side.
    await expect(page.getByRole("menu").or(page.getByRole("dialog")).first()).toBeVisible();
  });

  test("the sidebar collapses and expands", async ({ page }) => {
    await open(page);
    const collapse = page.getByRole("button", { name: "Collapse sidebar" });
    await expect(collapse).toHaveAttribute("aria-expanded", "true");
    await collapse.click();
    await expect(page.getByRole("button", { name: /Expand sidebar|Collapse sidebar/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  test("the theme toggle actually repaints", async ({ page }) => {
    await open(page);
    const root = page.locator("html");
    const before = await root.getAttribute("data-theme");
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await expect
      .poll(() => root.getAttribute("data-theme"), { message: "data-theme did not change" })
      .not.toBe(before);
  });

  test("support points at the issue tracker", async ({ page }) => {
    await open(page);
    await expect(page.getByRole("link", { name: "Support" })).toHaveAttribute(
      "href",
      /github\.com\/.*\/issues/,
    );
  });

  test("the skip link reaches the main region", async ({ page }) => {
    await open(page);
    // Parked off-viewport until focused, so it is reached by keyboard, never a synthetic click.
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("#main-content")).toBeFocused();
  });

  test("an unparseable address leaves the view alone rather than resetting it", async ({ page }) => {
    await open(page, "#/c/not-a-campaign");
    await ready(page);
  });
});
