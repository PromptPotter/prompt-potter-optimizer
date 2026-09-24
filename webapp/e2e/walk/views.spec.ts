import { test, expect, open, ready } from "../harness";

const TABS = ["chat", "dashboard", "compare", "verify", "files"] as const;
const LABEL: Record<(typeof TABS)[number], string> = {
  chat: "Chat",
  dashboard: "Dashboard",
  compare: "Compare",
  verify: "Verify",
  files: "Files",
};
const RECORDS = ["compare", "verify", "files"] as const;

async function tabPressed(page: import("@playwright/test").Page, label: string) {
  await expect(page.getByRole("button", { name: label, exact: true }).first()).toHaveAttribute(
    "aria-pressed",
    "true",
  );
}

test.describe("the view axis", () => {
  for (const tab of TABS) {
    test(`deep-links straight to ${tab}`, async ({ page, rich }) => {
      await open(page, `${rich.addr}${tab === "chat" ? "" : `/${tab}`}`);
      await tabPressed(page, RECORDS.includes(tab as never) ? "Records" : LABEL[tab]);
    });
  }

  test("clicking the strip moves the view and rewrites the address", async ({ page, rich }) => {
    await open(page, rich.addr);

    await page.getByRole("button", { name: "Dashboard", exact: true }).first().click();
    await tabPressed(page, "Dashboard");
    await expect.poll(() => new URL(page.url()).hash).toContain("/dashboard");

    await page.getByRole("button", { name: "Records", exact: true }).first().click();
    await expect.poll(() => new URL(page.url()).hash).toContain("/compare");

    for (const member of ["Verify", "Files"]) {
      await page.getByRole("button", { name: member, exact: true }).first().click();
      await expect.poll(() => new URL(page.url()).hash).toContain(`/${member.toLowerCase()}`);
    }

    // Re-clicking Records must not bounce back to its entry member (`ViewTabs::pickGroup`).
    await page.getByRole("button", { name: "Records", exact: true }).first().click();
    await expect.poll(() => new URL(page.url()).hash).toContain("/files");
  });

  test("a reload restores the view, not Chat", async ({ page, rich }) => {
    await open(page, `${rich.addr}/dashboard`);
    await page.reload();
    await ready(page);
    await tabPressed(page, "Dashboard");
  });

  test("the pinned cycle is named on screen and can be released", async ({ page, rich }) => {
    await open(page, rich.addr);
    await expect(page.getByText(`ID: ${rich.cycleId}`)).toBeVisible();
    await page.getByRole("button", { name: /Follow active/ }).click();
    await expect.poll(() => new URL(page.url()).hash).not.toContain(rich.id);
  });

  test("the campaign switcher is offered", async ({ page, rich }) => {
    await open(page, rich.addr);
    await expect(page.getByRole("combobox", { name: "Switch campaign or session" })).toBeVisible();
  });
});
