import { test, expect, open, ready, noSidewaysScroll } from "../harness";

// The widths the mobile pass never swept. `code-debt-cleanup.md` records that 375 and 1440
// were verified on four views and that 393, 412, 768 and landscape were not — so those are
// exactly the ones a standing walk is worth having.
//
// What it asserts is the one failure no screenshot review catches by eye and no stylesheet
// lint can see: `overflow:hidden` on a wrapper DELETES content with nothing on screen to say
// so, and a viewBox'd SVG at width:100% SCALES instead of overflowing. A body that scrolls
// sideways is the observable half of that family.

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
      // `OuterSignalPanel` ("Outer signal") mounts unconditionally on every Dashboard — round 0
      // or an empty read renders its own placeholder text — so unlike its per-round lift chart
      // (which needs a real `promptpotter-self` campaign to ever draw a bar, and so stays
      // untested by this walk), the CARD's own fit needs no special campaign shape. Read
      // `code-debt-cleanup.md` before touching this claim again.
      await open(page, `${rich.addr}/dashboard`);
      await expect(page.getByRole("heading", { name: "Outer signal" })).toBeVisible();
      await noSidewaysScroll(page);

      // The forest is the OTHER half of that same gap: `CandidatesCard`'s own dendrogram moved
      // into `ForestCard` behind this toggle, so opening it is what a plain tab visit never did.
      await page.getByRole("button", { name: /the lineage forest/i }).click();
      const cladogram = page.getByRole("img", { name: "Session lineage cladogram" });
      await expect(cladogram).toBeVisible();
      await noSidewaysScroll(page);

      // The failure class `noSidewaysScroll` cannot see: a `viewBox`'d SVG at `width:100%`
      // never overflows, it SCALES — silently compressing until labels collide, with the page
      // never growing wider (webapp/CLAUDE.md § Stylesheet organization). `Forest.tsx` gives the
      // `<svg>` an explicit intrinsic `width` for exactly this reason; assert it actually reaches
      // the DOM at that width instead of being squeezed into the viewport.
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
    // "A sidebar collapsed on a desktop must not become an empty list screen" — below
    // --bp-md the same component IS the list screen, so it drops the collapse affordance.
    await open(page);
    await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeHidden();
  });
});
