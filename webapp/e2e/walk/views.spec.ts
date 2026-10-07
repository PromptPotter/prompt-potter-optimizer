import { test, expect, open, ready } from "../harness";

// Each campaign view, the strip that owns it, and the segment that lights there (a Records
// member lights its own strip besides). The workspace view has no strip; its test is below.
const VIEWS = [
  { tab: "chat", strip: "Campaign view", label: "Chat" },
  { tab: "dashboard", strip: "Campaign view", label: "Dashboard" },
  { tab: "measurements", strip: "Records", label: "Measurements" },
  { tab: "files", strip: "Records", label: "Files" },
] as const;

type Page = import("@playwright/test").Page;

function segment(page: Page, strip: string, label: string) {
  return page.getByRole("group", { name: strip }).getByRole("button", { name: label, exact: true });
}

async function tabPressed(page: Page, strip: string, label: string) {
  await expect(segment(page, strip, label)).toHaveAttribute("aria-pressed", "true");
}

test.describe("the view axis", () => {
  for (const { tab, strip, label } of VIEWS) {
    test(`deep-links straight to ${tab}`, async ({ page, rich }) => {
      await open(page, `${rich.addr}${tab === "chat" ? "" : `/${tab}`}`);
      await tabPressed(page, strip, label);
      if (strip === "Records") await tabPressed(page, "Campaign view", "Records");
    });
  }

  test("deep-links straight to compare, where the campaign strip is not on screen", async ({
    page,
    rich,
  }) => {
    await open(page, `${rich.addr}/compare`);
    await expect(page.getByRole("group", { name: "Campaign view" })).toHaveCount(0);
  });

  test("clicking the strip moves the view and rewrites the address", async ({ page, rich }) => {
    await open(page, rich.addr);

    await segment(page, "Campaign view", "Dashboard").click();
    await tabPressed(page, "Campaign view", "Dashboard");
    await expect.poll(() => new URL(page.url()).hash).toContain("/dashboard");

    await segment(page, "Campaign view", "Records").click();
    await expect.poll(() => new URL(page.url()).hash).toContain("/measurements");

    await segment(page, "Records", "Files").click();
    await expect.poll(() => new URL(page.url()).hash).toContain("/files");

    // Re-clicking Records must not bounce back to its entry member (`ViewTabs::pickGroup`).
    await segment(page, "Campaign view", "Records").click();
    await expect.poll(() => new URL(page.url()).hash).toContain("/files");
  });

  test("a workspace view is entered from the campaign list and returns to the view it left", async ({
    page,
    rich,
  }) => {
    await open(page, `${rich.addr}/dashboard`);

    await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Compare", exact: true }).click();
    await expect.poll(() => new URL(page.url()).hash).toContain("/compare");

    await page.getByRole("button", { name: /Back to campaign/ }).click();
    await tabPressed(page, "Campaign view", "Dashboard");
  });

  test("a reload restores the view, not Chat", async ({ page, rich }) => {
    await open(page, `${rich.addr}/dashboard`);
    await page.reload();
    await ready(page);
    await tabPressed(page, "Campaign view", "Dashboard");
  });

  test("the pinned cycle is named on screen and can be released", async ({ page, rich }) => {
    await open(page, rich.addr);
    await expect(page.getByText(`ID: ${rich.cycleId}`)).toBeVisible();
    await page.getByRole("button", { name: /Follow the latest launch/ }).click();
    await expect.poll(() => new URL(page.url()).hash).not.toContain(rich.id);
  });

  test("the campaign switcher is offered", async ({ page, rich }) => {
    await open(page, rich.addr);
    await expect(page.getByRole("button", { name: "Switch campaign" })).toBeVisible();
  });
});
