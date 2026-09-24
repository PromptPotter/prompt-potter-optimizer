import {
  test,
  expect,
  open,
  ready,
  campaigns,
  consentGate,
  datasetNames,
  noSidewaysScroll,
  passConsent,
} from "../harness";

// The world a brand-new account meets: every pane renders from an EMPTY read. ORDER IS
// LOAD-BEARING: accepting the terms is persisted, so the gate can be met once per reset.

test.describe.configure({ mode: "serial" });

test.describe("cold start", () => {
  test("the workspace really is empty", async ({ request }) => {
    // Against a populated home every other assertion here would pass for the wrong reason.
    expect(await campaigns(request)).toEqual([]);
  });

  test("the consent gate blocks the app, and cannot be escaped", async ({ page }) => {
    await open(page);
    const gate = consentGate(page);
    await expect(gate).toBeVisible();

    await expect(gate.getByRole("button", { name: "Close" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(gate).toBeVisible();

    const agree = gate.getByRole("button", { name: /Agree & continue/ });
    await expect(agree).toBeDisabled();
    await gate.getByRole("checkbox").check();
    await expect(agree).toBeEnabled();

    await agree.click();
    await expect(gate).toBeHidden();
  });

  test("consent stays recorded across a reload", async ({ page }) => {
    await open(page);
    await expect(consentGate(page)).toHaveCount(0);
  });

  test("boots to the shell and says there is no campaign", async ({ page }) => {
    await open(page);
    await passConsent(page);
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
    await expect(page.getByText(/No campaigns yet/).first()).toBeVisible();
  });

  test("lists no campaigns and offers to start one", async ({ page }) => {
    await open(page);
    await passConsent(page);
    await expect(page.getByRole("button", { name: "Campaign actions" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "+ New campaign" })).toBeEnabled();
  });

  test("the install's datasets are offered to start from", async ({ page, request }) => {
    const names = await datasetNames(request);
    expect(names.length, "the install ships no dataset to start from").toBeGreaterThan(0);

    await open(page);
    await passConsent(page);
    await expect(page.getByText(/Start a campaign/)).toBeVisible();
    // One served name: the full membership is whatever the install ships.
    await expect(page.getByRole("button", { name: new RegExp(`^${names[0]}\\b`) })).toBeVisible();
  });

  test("the new-campaign CTA is reachable once nothing is gating it", async ({ page }) => {
    await open(page);
    await passConsent(page);
    await page.getByRole("button", { name: "+ New campaign" }).click();
    await ready(page);
  });

  test("every tab renders against an empty read", async ({ page }) => {
    for (const tab of ["", "#/dashboard", "#/compare", "#/verify", "#/files"]) {
      await open(page, tab);
      await passConsent(page);
      await ready(page);
    }
  });

  test("the account modal works with no history behind it", async ({ page }) => {
    for (const pane of ["profile", "usage", "activity", "storage", "preferences", "about"]) {
      await open(page, `#/account/${pane}`);
      await passConsent(page);
      await expect(page.getByRole("dialog", { name: "Account" })).toBeVisible();
    }
  });

  test("the empty shell fits a phone", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await open(page);
    await passConsent(page);
    await noSidewaysScroll(page);
  });

  test("login is reachable from a cold install", async ({ page }) => {
    const res = await page.goto("/login/");
    expect(res?.status()).toBe(200);
    await expect(page.getByRole("link", { name: /Continue with Google/ })).toBeVisible();
  });

  test("a failed sign-in bounces back into the auth modal, and the URL is cleaned", async ({
    page,
  }) => {
    // The only `WelcomeLockoutModal` trigger an auth-off world reaches: the callback's failure 303.
    await page.goto("/?auth_error=access_denied");
    await passConsent(page);

    await expect(page.getByRole("dialog", { name: /Log in or sign up/i })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("auth_error")).toBeNull();
  });
});
