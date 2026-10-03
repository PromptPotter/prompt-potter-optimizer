import {
  test,
  expect,
  open,
  ready,
  assertBackendUp,
  assertBoundedStop,
  assertRoundMeasured,
  campaigns,
  command,
  dashboard,
  passConsent,
  reportTape,
  roundsOf,
  tapeOf,
  assertThrowawayWorld,
  type Campaign,
} from "../harness";

// A campaign taken from nothing to a measuring run through the browser, at real spend. Kept cheap
// enough to run often: the draft cap, a poll that stops when satisfied, a pause in `afterAll`.

const DATASET = process.env.PP_E2E_DATASET || "email-tagging";
const BUDGET_USD = Number(process.env.PP_E2E_BUDGET_USD || "0.05");
// Origin plus TWO search rounds: only the second runs against an ELECTED parent.
const WANT_ROUNDS = 3;

test.describe.configure({ mode: "serial" });
test.skip(!process.env.PP_E2E_SPEND, "real spend — set PP_E2E_SPEND=1 to run this tier");

let made: Campaign | null = null;

test.afterAll(async ({ request }) => {
  // Re-resolved: a timeout in the Start test leaves `made` naming the pre-Start cycle.
  made = (await campaigns(request).catch(() => []))[0] ?? made;
  if (!made) return;
  // Unconditional and resumable. A refusal is reported, never swallowed: it may mean the safety
  // net did not catch.
  const { status, body } = await command(request, "pause-cycle", {
    campaign_id: made.id,
    cycle_id: made.cycleId,
  });
  console.log(
    status < 300
      ? `[e2e] paused ${made.id}`
      : `[e2e] pause REFUSED (${status}) — confirm nothing is still running: ${body}`,
  );
});

test.describe("a campaign, end to end", () => {
  test("it is pointed at a throwaway world, not the operator's", async ({ request }) => {
    // A `reuseExistingServer` attach would otherwise mint and spend in whatever workspace answered.
    await assertThrowawayWorld(request);
  });

  test("the backend the dataset needs is reachable", async ({ request }) => {
    await assertBackendUp(request);
  });

  test("picking a dataset mints a campaign and runs the check-in", async ({ page, request }) => {
    test.setTimeout(600_000);
    await open(page);
    await passConsent(page);

    const before = (await campaigns(request)).length;

    // With a campaign present the app follows the active run, so `/` shows no dataset list.
    const picker = page.getByRole("button", { name: new RegExp(`^${DATASET}\\b`) });
    if ((await picker.count()) === 0) {
      await page.getByRole("button", { name: "+ New campaign" }).click();
      await expect(page.getByText(/Start a campaign/)).toBeVisible({ timeout: 60_000 });
    }
    await picker.first().click({ timeout: 60_000 });

    // The check-in agent is an LLM call, so the MINT is the first observable, not the draft.
    await expect
      .poll(async () => (await campaigns(request)).length, {
        message: "picking a dataset minted no campaign",
        timeout: 180_000,
      })
      .toBeGreaterThan(before);

    made = (await campaigns(request))[0]!;
    console.log(`[e2e] minted ${made.id} — watch it at ${new URL(page.url()).origin}/${made.addr}`);
  });

  test("the check-in surface carries the operator's own rows", async ({ page }) => {
    test.setTimeout(600_000);
    await open(page, made!.addr);
    await passConsent(page);

    await expect(page.getByText(/finish the setup below/i)).toBeVisible({ timeout: 300_000 });
    await expect(page.locator("main").getByRole("table")).toBeVisible();
    await expect(page.getByRole("combobox", { name: /Input column/ })).toBeVisible();
  });

  test("Start launches a run, and the browser follows it", async ({ page, request }) => {
    test.setTimeout(900_000);
    await open(page, made!.addr);
    await passConsent(page);

    // Candidate WIDTH is not set here: it rides `datasets/{name}/campaign.yaml`.
    const capped = await command(request, "edit-draft-campaign", {
      draft_id: made!.id,
      patch: { optimization_overrides: { max_rounds: WANT_ROUNDS - 1 } },
    });
    expect(capped.status, `capping the draft to ${WANT_ROUNDS - 1} search round(s): ${capped.body}`).toBeLessThan(300);

    await open(page, made!.addr);

    const start = page.getByRole("button", { name: /Start campaign/ });
    await expect(start).toBeEnabled({ timeout: 120_000 });

    // The cap rides the Start verb, so the run is born under it. After the enabled wait, so a
    // missing field is a failure, not a race; `summary` because `getByText` also hits `<details>`.
    await page.locator("summary", { hasText: "Run bounds" }).click();
    await page.getByRole("spinbutton", { name: "Spend cap in USD" }).fill(String(BUDGET_USD));

    await start.click();

    await expect
      .poll(async () => (await campaigns(request))[0]?.cycleId ?? "", { timeout: 300_000 })
      .toBeTruthy();
    made = (await campaigns(request))[0]!;

    await expect
      .poll(async () => (await dashboard(request, made!))?.run_phase ?? "", {
        message: "no run phase ever became live",
        timeout: 600_000,
      })
      .toBeTruthy();

    await open(page, `${made!.addr}/dashboard`);
    await ready(page);
    await expect(page.getByRole("img", { name: "Pipeline graph" })).toBeVisible();
  });

  test("it optimizes — origin, then two rounds that generate, score and elect", async ({
    page,
    request,
  }) => {
    // Pass: the rounds land, or the run stops on a bounded reason. Round 0 alone searches nothing.
    test.setTimeout(1_500_000);

    // Parked BEFORE polling, so a human watching the headed run sees the rounds land.
    await open(page, `${made!.addr}/dashboard`);
    await passConsent(page);
    await ready(page);

    let rounds: ReturnType<typeof roundsOf> = [];
    let stopped = "";
    await expect
      .poll(
        async () => {
          const d = await dashboard(request, made!);
          // COMPLETED rounds, never `current_round`, which is populated when a round STARTS.
          rounds = roundsOf(d);
          stopped = String(d?.stop_reason ?? "");
          return rounds.length >= WANT_ROUNDS || !!stopped;
        },
        {
          message: `fewer than ${WANT_ROUNDS} rounds completed and the run never stopped — it is neither working nor bounded`,
          timeout: 1_380_000,
        },
      )
      .toBeTruthy();
    console.log(
      `[e2e] completed rounds=${rounds.length} stop_reason=${stopped || "(still running)"}`,
    );

    // The poll above is satisfied by ANY stop, including a crash.
    assertBoundedStop(DATASET, stopped);

    for (const r of rounds) assertRoundMeasured(DATASET, r);

    // `improved` is null until the election stamps it, so it is the "a decision was reached" signal.
    const search = rounds.filter((r) => r.round > 0);
    expect(
      search.length,
      `the run closed ${rounds.length} round(s) but none above the origin — it scored and never searched`,
    ).toBeGreaterThan(0);
    for (const r of search) {
      expect(
        r.candidates.length,
        `round ${r.round} generated ${r.candidates.length} candidate(s) — with nothing to choose between, selection and election are untested`,
      ).toBeGreaterThan(1);
      expect(
        r.improved,
        `round ${r.round} closed with no verdict — candidates were scored but no election was held`,
      ).not.toBeNull();
      console.log(
        `[e2e] round ${r.round}: ${r.candidates.length} candidates, improved=${r.improved}, acc=${r.accuracy}`,
      );
    }

    // Not re-opened: this asserts the page mounted before the poll survived the whole run.
    await ready(page);
    await expect(page.getByRole("img", { name: "Pipeline graph" })).toBeVisible();
  });

  test("it is running under a cap, and the browser renders one", async ({ page, request }) => {
    // Some finite cap no higher than ours, never exactly ours: the launch composes against the
    // account allowance first, which may be tighter.
    const limits = (await dashboard(request, made!))?.run_limits as
      | { spend_budget_usd?: number | null }
      | undefined;
    expect(limits?.spend_budget_usd, "the run has no USD cap at all").not.toBeNull();
    expect(limits?.spend_budget_usd).toBeLessThanOrEqual(BUDGET_USD);

    await open(page, `${made!.addr}/dashboard`);
    await passConsent(page);
    await ready(page);
  });

  test("what it PAID and what it COST are both on the wire", async ({ request }) => {
    // No floor: the check-in agent authors the origin prompt, so replay share is stochastic.
    reportTape(DATASET, tapeOf(await dashboard(request, made!)));
  });

  test("what the run wrote is readable in Files", async ({ page }) => {
    await open(page, `${made!.addr}/files`);
    await passConsent(page);
    await expect(page.locator("#main-content").getByRole("button").first()).toBeVisible();
  });
});
