import { test, expect, ready } from "../harness";
import { startFakeIssuer, type FakeIssuer } from "../fake_issuer";

// `AccessGate` and a used-up coupon need a signed-in NON-host identity, which the auth-off servers cannot produce.

let fake: FakeIssuer;

test.beforeAll(async () => {
  fake = await startFakeIssuer();
});

test.afterAll(async () => {
  await fake.stop();
});

test("a blocked account sees AccessGate, not the app", async ({ page, context }) => {
  const tenantId = "e2e-fake-blocked";
  fake.blockEmails(["blocked@example.com"]);
  const sessionId = fake.mintSession({ tenantId, email: "blocked@example.com" });
  await context.addCookies([{ name: "promptpotter_session", value: sessionId, url: fake.baseURL }]);

  await page.goto(fake.baseURL);
  await ready(page);
  await expect(page.getByRole("dialog", { name: "This account is switched off" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeEnabled();
});

test("an account whose coupon is used up reads it inline, never under a modal", async ({ page, context, request }) => {
  const tenantId = "e2e-fake-allowance";
  fake.seedCouponFace(tenantId, 0);
  const sessionId = fake.mintSession({ tenantId, email: "allowance@example.com" });
  await context.addCookies([{ name: "promptpotter_session", value: sessionId, url: fake.baseURL }]);

  // Pre-accept Terms, or ConsentGate paints over the app; `request` is bound to the cold project's baseURL, hence full URLs.
  const cookie = { Cookie: `promptpotter_session=${sessionId}` };
  const me = await (
    await request.get(`${fake.baseURL}/api/v1/auth/me`, { headers: cookie })
  ).json();
  await request.post(`${fake.baseURL}/api/v1/commands/accept-terms`, {
    headers: { ...cookie, "Idempotency-Key": "accept-terms" },
    data: { version: me.terms_version },
  });

  await page.goto(fake.baseURL);
  await ready(page);
  // The refusal is the account's reading beside its spend; a launch's own control shows its note.
  await expect(page.locator(".account-spend.account-spend-exhausted")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
