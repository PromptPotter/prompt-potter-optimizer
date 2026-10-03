import { test, expect, ready } from "../harness";
import { startFakeIssuer, type FakeIssuer } from "../fake_issuer";

// `AccessGate` and `AllowanceSpent` need a signed-in NON-host identity, which the auth-off
// cold/walk servers can never produce — hence `fake_issuer.ts`.

let fake: FakeIssuer;

test.beforeAll(async () => {
  fake = await startFakeIssuer();
});

test.afterAll(async () => {
  await fake?.stop();
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

test("an account at its free-tier ceiling sees AllowanceSpent", async ({ page, context, request }) => {
  const tenantId = "e2e-fake-allowance";
  fake.seedSpendCeiling(tenantId, 0);
  const sessionId = fake.mintSession({ tenantId, email: "allowance@example.com" });
  await context.addCookies([{ name: "promptpotter_session", value: sessionId, url: fake.baseURL }]);

  // Pre-accept Terms, or ConsentGate paints over AllowanceSpent. `request` is bound to the cold
  // project's baseURL, hence full URLs and an explicit Cookie header.
  const cookie = { Cookie: `promptpotter_session=${sessionId}` };
  const me = await (
    await request.get(`${fake.baseURL}/api/v1/auth/me`, { headers: cookie })
  ).json();
  await request.post(`${fake.baseURL}/api/v1/auth/accept-terms`, {
    headers: cookie,
    data: { version: me.terms_version },
  });

  await page.goto(fake.baseURL);
  await ready(page);
  // The apostrophe is U+2019 (`&rsquo;` in the source) — a straight `'` will not match.
  await expect(
    page.getByRole("dialog", { name: "That’s the last of your free runs" }),
  ).toBeVisible();
});
