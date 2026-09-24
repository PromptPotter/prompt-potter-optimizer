import { test, expect, open, ready, noSidewaysScroll } from "../harness";

// A body that scrolls sideways is the observable half of the overflow family no lint sees.

const WIDTHS = [
  { name: "phone-375", width: 375, height: 812 },
  { name: "phone-393", width: 393, height: 852 },
  { name: "phone-412", width: 412, height: 915 },
  { name: "landscape-844", width: 844, height: 390 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "desktop-1440", width: 1440, height: 900 },
];

for (const vp of WIDTHS) {
  test.describe(vp.name, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test("the zero-campaign shell fits its viewport", async ({ page }) => {
      await open(page);
      await noSidewaysScroll(page);
    });

    test("every view fits its viewport", async ({ page, rich }) => {
      for (const tab of ["", "/dashboard", "/compare", "/verify", "/files"]) {
        await open(page, `${rich.addr}${tab}`);
        await noSidewaysScroll(page);
      }
    });

    test("the account modal fits its viewport", async ({ page }) => {
      await open(page, "#/account/storage");
      await ready(page);
      await noSidewaysScroll(page);
    });

    test("the outer-signal panel and the lineage forest fit their viewport", async ({
      page,
      rich,
    }) => {
      // `OuterSignalPanel` mounts on every Dashboard, so its CARD's fit needs no L4 campaign.
      await open(page, `${rich.addr}/dashboard`);
      await expect(page.getByRole("heading", { name: "Outer signal" })).toBeVisible();
      await noSidewaysScroll(page);

      await page.getByRole("button", { name: /the lineage forest/i }).click();
      const cladogram = page.getByRole("img", { name: "Session lineage cladogram" });
      await expect(cladogram).toBeVisible();
      await noSidewaysScroll(page);

      // What `noSidewaysScroll` cannot see: a `viewBox`'d SVG SCALES rather than overflowing.
      const attrWidth = Number(await cladogram.getAttribute("width"));
      expect(attrWidth, "the cladogram <svg> carries no width attribute").toBeGreaterThan(0);
      const box = await cladogram.boundingBox();
      expect(
        box?.width,
        "the lineage cladogram scaled down instead of overflowing its wrapper",
      ).toBeGreaterThanOrEqual(attrWidth - 1);
    });
  });
}

test.describe("below the md breakpoint", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("the sidebar is the list screen, and carries no collapse chevron", async ({ page }) => {
    await open(page);
    await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeHidden();
  });
});
