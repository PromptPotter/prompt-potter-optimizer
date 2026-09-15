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
