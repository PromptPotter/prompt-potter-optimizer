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

// The world a brand-new account meets: a workspace with nothing in it. Every pane here
// renders from an EMPTY read, which is the branch a populated dev machine never exercises
// and no jsdom unit covers — `code-debt-cleanup.md`'s "a whole class of first-user breakage
// ships green", stated as tests.
//
// ORDER IS LOAD-BEARING in this file. Accepting the terms writes a provable record to the
// throwaway `user.json`, so the gate can only be met once per reset — it is therefore
// asserted before anything clears it, and everything after it clears it defensively.

test.describe.configure({ mode: "serial" });

test.describe("cold start", () => {
  test("the workspace really is empty", async ({ request }) => {
    // Guards every other assertion in this file: run against a populated home by accident
    // and they would all pass for the wrong reason.
    expect(await campaigns(request)).toEqual([]);
  });

  test("the consent gate blocks the app, and cannot be escaped", async ({ page }) => {
    await open(page);
    const gate = consentGate(page);
    await expect(gate).toBeVisible();

    // "no ×, no overlay-click dismiss, no ESC (the a11y hook's onClose is a no-op)". A gate
    // with a way around it is not a gate, and the provable-consent record is the point.
    await expect(gate.getByRole("button", { name: "Close" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(gate).toBeVisible();

    // Agreement is the only way out, and it is not armed until the box is ticked.
    const agree = gate.getByRole("button", { name: /Agree & continue/ });
    await expect(agree).toBeDisabled();
    await gate.getByRole("checkbox").check();
    await expect(agree).toBeEnabled();

    await agree.click();
    await expect(gate).toBeHidden();
  });

  test("consent stays recorded across a reload", async ({ page }) => {
    // The record is server-side (version + server-stamped timestamp), not a browser flag,
    // so a reload must not ask again.
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
    // One served name is enough. Asserting the whole membership would couple this to
    // whatever the install happens to ship, which moves.
    await expect(page.getByRole("button", { name: new RegExp(`^${names[0]}\\b`) })).toBeVisible();
  });

  test("the new-campaign CTA is reachable once nothing is gating it", async ({ page }) => {
    await open(page);
    await passConsent(page);
    await page.getByRole("button", { name: "+ New campaign" }).click();
    await ready(page);
  });

  test("every tab renders against an empty read", async ({ page }) => {
    // No campaign is pinned, so each tab takes its "nothing to show" branch — the one that
    // ships green because a populated machine never renders it.
    for (const tab of ["", "#/dashboard", "#/compare", "#/verify", "#/files"]) {
      await open(page, tab);
      await passConsent(page);
      await ready(page);
    }
  });

  test("the account modal works with no history behind it", async ({ page }) => {
    for (const pane of ["profile", "security", "activity", "storage", "preferences", "about"]) {
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
    // `WelcomeLockoutModal`'s only reachable trigger here, and the one that matters: the two
    // chips that open it render for `unauthed`, which an auth-off world never is, while
    // `/auth/callback/{provider}` 303s to this on failure whatever the session says. `page.goto`
    // rather than `open` because the harness's rule is about a hash-only navigation not
    // remounting, and this one must be a full load carrying a QUERY.
    await page.goto("/?auth_error=access_denied");
    await passConsent(page);

    await expect(page.getByRole("dialog", { name: /Log in or sign up/i })).toBeVisible();
    // Stripped after reading, or a refresh replays an error the operator already dealt with.
    expect(new URL(page.url()).searchParams.get("auth_error")).toBeNull();
  });
});
