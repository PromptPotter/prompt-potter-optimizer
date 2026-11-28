import { test, expect, ready } from "../harness";
import { startFakeIssuer, type FakeIssuer } from "../fake_issuer";

// The Account view at phone width, signed in as a non-host account: only it is offered a grantable
// scope (`application/account.py::account_tokens`), which the auth-off walk world never serves.

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

let fake: FakeIssuer;

test.beforeAll(async () => {
  fake = await startFakeIssuer();
});

test.afterAll(async () => {
  await fake.stop();
});

test("at 390px every element of Account lies inside its card, and no band opens under its tabs", async (
  { page, context, request },
  info,
) => {
  const sessionId = fake.mintSession({ tenantId: "e2e-fake-phone", email: "phone@example.com" });
  await context.addCookies([{ name: "promptpotter_session", value: sessionId, url: fake.baseURL }]);
  const cookie = { Cookie: `promptpotter_session=${sessionId}` };
  const me = await (await request.get(`${fake.baseURL}/api/v1/auth/me`, { headers: cookie })).json();
  await request.post(`${fake.baseURL}/api/v1/commands/accept-terms`, {
    headers: { ...cookie, "Idempotency-Key": "accept-terms-phone" },
    data: { version: me.terms_version },
  });

  for (const tab of ["keys", "profile", "usage", "storage", "preferences", "activity", "about"]) {
    await page.goto(`${fake.baseURL}/#/chat/account=${tab}`);
    await ready(page);
    const account = page.getByRole("region", { name: "Account" });
    await expect(account).toBeVisible();
    await expect(page.locator('[aria-busy="true"]:visible')).toHaveCount(0);
    if (tab === "keys") await expect(account.locator('[aria-pressed]').first()).toBeVisible();
    await page.screenshot({ path: info.outputPath(`account-${tab}-390.png`), fullPage: false });

    const outside = await account.evaluate((root) => {
      const out: string[] = [];
      for (const card of root.querySelectorAll<HTMLElement>(".card")) {
        const c = card.getBoundingClientRect();
        for (const el of card.querySelectorAll<HTMLElement>("*")) {
          if (!el.checkVisibility()) continue;
          const r = el.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) continue;
          if (r.left < c.left - 1 || r.right > c.right + 1) {
            out.push(`${el.tagName.toLowerCase()}.${[...el.classList].join(".")} ${Math.round(r.left)}..${Math.round(r.right)} of ${Math.round(c.left)}..${Math.round(c.right)}`);
          }
        }
      }
      return out;
    });
    expect(outside, `${tab}: an element runs off its card`).toEqual([]);

    // The first card sits under the tabs, never a band below them.
    const gap = await account.evaluate((root) => {
      const tabs = root.querySelector('[aria-label="Account tabs"]');
      const card = root.querySelector(".card");
      if (!tabs || !card) return 0;
      return card.getBoundingClientRect().top - tabs.getBoundingClientRect().bottom;
    });
    expect(gap, `${tab}: the band under the tabs`).toBeLessThan(48);
  }
});
