import {
  test,
  expect,
  open,
  ready,
  noSidewaysScroll,
  showSidebar,
  DASHBOARD_VIEWS,
} from "../harness";

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

    test("every view fits its viewport", async ({ page, rich, isMobile }) => {
      test.skip(isMobile, "mob 2: the 44px touch floor is layout, so the Candidates header overruns its card");
      for (const views of ["", DASHBOARD_VIEWS, "/compare", "/samples/files", "/samples/spend"]) {
        await open(page, `${rich.addr}${views}`);
        await noSidewaysScroll(page);
      }
    });

    test("the account view fits its viewport", async ({ page }) => {
      await open(page, "#/account=storage");
      await ready(page);
      await noSidewaysScroll(page);
    });

    // A sidebar row is its card's trigger, and on a phone the row spans the screen: no side of it has room.
    test("a sidebar row's hover card opens inside its viewport", async ({ page, rich }) => {
      void rich; // skips a world with no campaign row to hover
      await open(page);
      await showSidebar(page);
      await page.locator(".unit-library-family").first().hover();
      const card = page.getByRole("note");
      await expect(card).toBeVisible();
      const box = (await card.boundingBox())!;
      expect(box.x, "the card starts left of the viewport").toBeGreaterThanOrEqual(0);
      expect(box.y, "the card starts above the viewport").toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, "the card ends right of the viewport").toBeLessThanOrEqual(vp.width);
      expect(box.y + box.height, "the card ends below the viewport").toBeLessThanOrEqual(vp.height);
    });

    test("the lineage forest fits its viewport", async ({ page, rich, isMobile }) => {
      test.skip(
        isMobile && vp.width <= 412,
        "held (operator): the one-row primitive's overflow strategy; the Candidates header overruns its card",
      );
      await open(page, `${rich.addr}/candidates`);
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
