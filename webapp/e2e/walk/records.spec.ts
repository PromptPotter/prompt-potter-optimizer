import { test, expect, open, ready } from "../harness";

// The run's written record — the three views that ride one Records segment. They are "real
// but rare", which is exactly the profile of a surface that rots unwatched.

test.describe("records", () => {
  test("Compare mounts, and a campaign can be ticked into the comparison", async ({
    page,
    rich,
  }) => {
    await open(page, `${rich.addr}/compare`);
    // Ticking is the sidebar's act; the pane reads whatever channels result.
    const tick = page.getByRole("button", { name: "Add to the comparison" }).first();
    await tick.click();
    await ready(page);
    // `/evidence` 400s on a selection with no scored rows, and `useFetch`'s `survive` keeps
    // the last good read — so what is asserted is that the pane resolves to SOMETHING, never
    // that it found numbers. A campaign whose origin never ran legitimately has none.
    await expect(page.locator("#main-content")).toBeVisible();
  });

  test("Verify mounts", async ({ page, rich }) => {
    await open(page, `${rich.addr}/verify`);
    await expect(page.locator("#main-content")).toBeVisible();
  });

  test("Files lists the campaign's on-disk record", async ({ page, rich }) => {
    await open(page, `${rich.addr}/files`);
    // The file tree is the project tree — the dashboard the repo actually ships.
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
