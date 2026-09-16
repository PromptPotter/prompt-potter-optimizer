import { test, expect, ready } from "../harness";
import { startFakeIssuer, type FakeIssuer } from "../fake_issuer";

// `AccessGate` and `AllowanceSpent` render only for a signed-in, NON-host identity, and the
// shared cold/walk servers can never produce one: `serve.mjs` sets `PROMPTPOTTER_AUTH=off` for
// both, which makes `deps.py::resolve_identity` return the terminal identity unconditionally —
// a session cookie is never even read. That is a structural absence, not a missing fixture
// (`code-debt-cleanup.md`), so this file runs its own throwaway server with auth genuinely
// closed instead, and mints a session directly rather than completing a real OIDC round trip.
//
// One assertion per surface — not a suite over the onboarding gates, just the two that were
// unreachable.

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

  // Pre-accept Terms: a fresh account also fails ConsentGate's version check, and that gate
  // mounts ahead of AllowanceSpent in `app/page.tsx` — without this the assertion below would
  // pass for the wrong reason, finding whichever overlay happened to paint on top. The `request`
  // fixture carries no baseURL of its own here (it is bound to the `cold` project's), so every
  // call below is a full URL with an explicit Cookie header rather than a second context.
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
