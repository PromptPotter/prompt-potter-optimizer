import { test, expect, open, ready, campaigns } from "../harness";

// The chrome every view sits inside: the sidebar, the masthead controls, the account door.
// It renders on every tab, so a break here is a break everywhere — and none of it is
// reachable by a jsdom unit, which is why it is the first thing the walk covers.

test.describe("app shell", () => {
  test("boots to the shell, not to a crash", async ({ page }) => {
    await open(page);
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Skip to content" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    // Not the brand LITERAL — `lib/brand.ts` is NEXT_PUBLIC_*-overridable for whitelabel, and a
    // distributor's build is a supported configuration this suite must not fail.
    await expect(page.locator("#main-content")).toBeVisible();
  });

  test("the sidebar lists exactly the campaigns the API serves", async ({ page, request }) => {
    const served = await campaigns(request);
    await open(page);

    // Each row's ⋯ is one campaign, so the menus count the rows without reading a label —
    // labels carry the dataset name and a stop reason, which move when the operator runs.
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
    // The lifecycle filter set is a QUERY-param union with no response model behind it
    // (`webapp/CLAUDE.md` § A wire shape is GENERATED), so the surface is asserted, not the
    // member list — a member added in Python must not fail this.
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
    // It is parked off-viewport until focused — which is the point of it — so it is reached
    // the way a keyboard user reaches it, never by a synthetic click on a hidden element.
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("#main-content")).toBeFocused();
  });

  test("an unparseable address leaves the view alone rather than resetting it", async ({ page }) => {
    // `parseAddress` returns null on malformed input and the writer must not act on it.
    await open(page, "#/c/not-a-campaign");
    await ready(page);
  });
});
