import { test, expect, open, ready, DASHBOARD_VIEWS } from "../harness";

type Page = import("@playwright/test").Page;

const PRESETS = [
  { views: "", label: "Chat" },
  { views: DASHBOARD_VIEWS, label: "Dashboard" },
  { views: "/samples/files", label: "Records" },
] as const;

function preset(page: Page, label: string) {
  return page
    .getByRole("group", { name: "Campaign view" })
    .getByRole("button", { name: label, exact: true });
}

async function pressed(page: Page, label: string) {
  await expect(preset(page, label)).toHaveAttribute("aria-pressed", "true");
}

test.describe("the view axis", () => {
  for (const { views, label } of PRESETS) {
    test(`deep-links straight to the ${label} preset`, async ({ page, rich }) => {
      await open(page, `${rich.addr}${views}`);
      await pressed(page, label);
    });
  }

  test("a view beside a preset presses none of them", async ({ page, rich }) => {
    await open(page, `${rich.addr}/chat/compare`);
    const strip = page.getByRole("group", { name: "Campaign view" });
    await expect(strip).toBeVisible();
    await expect(strip.locator('[aria-pressed="true"]')).toHaveCount(0);
  });

  test("a preset replaces the open views and rewrites the address", async ({ page, rich }) => {
    await open(page, rich.addr);

    await preset(page, "Dashboard").click();
    await pressed(page, "Dashboard");
    await expect.poll(() => new URL(page.url()).hash).toContain(DASHBOARD_VIEWS);

    await preset(page, "Records").click();
    await expect.poll(() => new URL(page.url()).hash).toContain("/samples/files");

    await preset(page, "Chat").click();
    await expect.poll(() => new URL(page.url()).hash).not.toContain("/files");
  });

  test("command words open views, and a view opens once", async ({ page, rich, isMobile }) => {
    test.skip(isMobile, "a phone shows one column: the opened view replaces the chat it was typed in");
    await open(page, rich.addr);
    const field = page.getByRole("textbox", { name: "Chat input" });
    await field.fill("samples and spend");
    await field.press("Enter");
    await expect.poll(() => new URL(page.url()).hash).toContain("/samples/spend");

    await field.fill("samples");
    await field.press("Enter");
    await expect.poll(() => new URL(page.url()).hash).toContain("/spend/samples");
  });

  test("Compare opens from the campaign list beside what is open", async ({ page, rich, isMobile }) => {
    test.skip(isMobile, "a phone shows one column: nothing stands beside the opened Compare");
    await open(page, `${rich.addr}${DASHBOARD_VIEWS}`);
    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("button", { name: "Compare", exact: true })
      .click();
    await expect.poll(() => new URL(page.url()).hash).toContain("/compare");

    await page.getByRole("button", { name: "Close Compare" }).click();
    await pressed(page, "Dashboard");
  });

  test("a reload restores the open views", async ({ page, rich }) => {
    await open(page, `${rich.addr}${DASHBOARD_VIEWS}`);
    await page.reload();
    await ready(page);
    await pressed(page, "Dashboard");
  });

  test("the pinned cycle is named on screen and can be released", async ({ page, rich }) => {
    await open(page, rich.addr);
    // A phone folds the id behind the title row's ⋯.
    const fold = page.getByRole("button", { name: "This cycle's id" });
    if (await fold.isVisible()) await fold.click();
    await expect(page.getByText(`ID: ${rich.cycleId}`).filter({ visible: true })).toBeVisible();
    await page.getByRole("button", { name: /Follow your run/ }).click();
    await expect.poll(() => new URL(page.url()).hash).not.toContain(rich.id);
  });
});
