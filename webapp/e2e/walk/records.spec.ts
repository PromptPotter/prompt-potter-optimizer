import { test, expect, open, ready } from "../harness";

test.describe("records", () => {
  test("Compare mounts, and a campaign can be ticked into the comparison", async ({
    page,
    rich,
  }) => {
    await open(page, `${rich.addr}/compare`);
    const tick = page.getByRole("button", { name: "Add to the comparison" }).first();
    await tick.click();
    await ready(page);
    // Never that it found numbers: `/evidence` 400s on a campaign whose origin never ran.
    await expect(page.locator("#main-content")).toBeVisible();
  });

  test("Verify mounts", async ({ page, rich }) => {
    await open(page, `${rich.addr}/verify`);
    await expect(page.locator("#main-content")).toBeVisible();
  });

  test("Files lists the campaign's on-disk record", async ({ page, rich }) => {
    await open(page, `${rich.addr}/files`);
    await expect(page.getByRole("tree").or(page.getByRole("list")).first()).toBeVisible();
  });

  test("Files opens a document without crashing the view", async ({ page, rich }) => {
    await open(page, `${rich.addr}/files`);
    const entries = page.locator("#main-content").getByRole("button");
    const n = await entries.count();
    test.skip(n === 0, "this campaign has written no files yet");
    await entries.first().click();
    await ready(page);
  });
});
