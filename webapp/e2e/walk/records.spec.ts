import { test, expect, open, press, ready, showSidebar, hideSidebar } from "../harness";

test.describe("records", () => {
  test("Compare mounts, and a campaign can be ticked into the comparison", async ({
    page,
    rich,
  }) => {
    await open(page, `${rich.addr}/compare`);
    await showSidebar(page);
    const tick = page.getByRole("button", { name: "Add to the comparison" }).first();
    await press(tick);
    await hideSidebar(page);
    await ready(page);
    await expect(page.locator("#view-compare")).toBeVisible();
    // Never that it found numbers: `/evidence` 400s on a campaign whose origin never ran.
    await expect(page.locator("#main-content")).toBeVisible();
  });

  test("Files lists the campaign's on-disk record", async ({ page, rich }) => {
    await open(page, `${rich.addr}/files`);
    await expect(page.locator("#view-files .tree-pane").getByRole("button").first()).toBeVisible();
  });

  test("Files opens a document without crashing the view", async ({ page, rich }) => {
    await open(page, `${rich.addr}/files`);
    const tree = page.locator("#view-files .tree-pane");
    await expect(tree).toBeVisible();
    const entries = tree.getByRole("button");
    const n = await entries.count();
    test.skip(n === 0, "this campaign has written no files yet");
    await entries.first().click();
    await ready(page);
  });
});
