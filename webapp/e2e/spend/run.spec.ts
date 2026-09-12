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

// The half that costs money: a campaign taken from nothing to a live, measuring run, entirely
// through the browser. Every other tier reads what a run LEFT; this one makes one.
//
//     PP_E2E_SPEND=1 npx playwright test --project=spend
//
// It is built to be CHEAP ENOUGH TO RUN OFTEN, which is a design constraint rather than a
// courtesy — a tier that costs $5 a pass gets run once, ceremonially, and then never guards
// anything. Three mechanisms carry it, each argued where it sits: the draft cap below, a poll
// that stops when SATISFIED rather than when the campaign ends, and an unconditional pause in
// `afterAll`. The knobs are ../README.md's.
//
// It needs the dataset's backend up: every install dataset declares `backend_type: termnorm`
// on 127.0.0.1:8000.
//
// The L4 half is `l4.spec.ts` and cannot be run from here — its header says why.

const DATASET = process.env.PP_E2E_DATASET || "email-tagging";
const BUDGET_USD = Number(process.env.PP_E2E_BUDGET_USD || "0.05");
// Round 0 (the origin) plus the two SEARCH rounds. Two rather than one because the second is
// the first that runs against an ELECTED parent rather than the origin — the carry-forward is a
// transition of its own, and a single search round never exercises it.
const WANT_ROUNDS = 3;

test.describe.configure({ mode: "serial" });
test.skip(!process.env.PP_E2E_SPEND, "real spend — set PP_E2E_SPEND=1 to run this tier");

/** The cycle this run made, so `afterAll` can stop it whatever happened above. */
let made: Campaign | null = null;

test.afterAll(async ({ request }) => {
  // Re-resolve rather than trusting the module variable. `made` is reassigned inside the Start
  // test's poll, so a timeout there leaves it naming the pre-Start cycle — and the safety net
  // would then pause something other than the thing that is running.
  made = (await campaigns(request).catch(() => []))[0] ?? made;
  if (!made) return;
  // Unconditional. `pause-cycle` is resumable, so stopping a run we are done watching costs
  // nothing and leaving one running costs money for as long as nobody notices.
  //
  // A refusal is REPORTED rather than swallowed, and the two reasons are not the same thing:
  // a cycle that already stopped has nothing to pause and that is fine, while anything else
  // means the safety net did not catch — which is exactly what a silent `→ 422` hid last run.
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
    // The cold tier asserts its world is empty; this one had no equivalent, so a
    // `reuseExistingServer` attach to the wrong server would have had it mint campaigns and
    // spend money in whatever workspace answered — and pass. Everything below writes.
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

    // Reach the picker the way a user with existing campaigns does. Landing on `/` and
    // expecting the dataset list only works in an EMPTY world: with a campaign present the app
    // follows the active run instead, and this spec hung for fifteen minutes clicking for a
    // button that was never on screen once the L4 spec started minting into the same workspace.
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
    // Their data, on screen, before anything is spent measuring it.
    await expect(page.locator("main").getByRole("table")).toBeVisible();
    await expect(page.getByRole("combobox", { name: /Input column/ })).toBeVisible();
  });

  test("Start launches a run, and the browser follows it", async ({ page, request }) => {
    test.setTimeout(900_000);
    await open(page, made!.addr);
    await passConsent(page);

    // The only bound a draft can carry — `OptimizationOverrides` declares `max_rounds` and
    // nothing about spend — and the reason this tier stays affordable. The candidate WIDTH is
    // not ours to set here: it rides `datasets/{name}/campaign.yaml` (`n_variants: 3` for
    // email-tagging), which is the dataset's own declaration.
    const capped = await command(request, "edit-draft-campaign", {
      draft_id: made!.id,
      patch: { optimization_overrides: { max_rounds: WANT_ROUNDS - 1 } },
    });
    expect(capped.status, `capping the draft to ${WANT_ROUNDS - 1} search round(s): ${capped.body}`).toBeLessThan(300);

    await open(page, made!.addr);
    const start = page.getByRole("button", { name: /Start campaign/ });
    await expect(start).toBeEnabled({ timeout: 120_000 });
    await start.click();

    // Wait for the cycle, then clamp the money — but only DOWNWARD.
    //
    // The account's own allowance already binds (a fresh account ran at $0.025), and the
    // ceiling composes against the account first precisely so that raising one here cannot
    // become the way around the host-wallet gate. Asking for more than the account permits is
    // refused, so a test that "clamps" to a number above the allowance is not tightening
    // anything — it is requesting a raise, and being told no.
    await expect
      .poll(async () => (await campaigns(request))[0]?.cycleId ?? "", { timeout: 300_000 })
      .toBeTruthy();
    made = (await campaigns(request))[0]!;

    const served = (await dashboard(request, made))?.run_limits as
      | { spend_budget_usd?: number | null }
      | undefined;
    const current = served?.spend_budget_usd ?? null;
    if (current === null || current > BUDGET_USD) {
      const clamp = await command(request, "change-spend-budget", {
        campaign_id: made.id,
        cycle_id: made.cycleId,
        max_usd: BUDGET_USD,
      });
      expect(clamp.status, `clamping to $${BUDGET_USD}: ${clamp.body}`).toBeLessThan(300);
      console.log(`[e2e] clamped $${current ?? "none"} → $${BUDGET_USD}`);
    } else {
      console.log(`[e2e] the account already binds tighter ($${current}) — left alone`);
    }

    // Live means the SERVED phase says so — the route derives `run_phase` rather than trusting
    // what the producer last wrote.
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
    // ROUND 0 IS NOT ENOUGH, and stopping there is what this tier used to do: the origin has one
    // candidate, no generation, no selection, no election and no parent carried anywhere, so
    // pausing there verified the control plane and none of the search. `WANT_ROUNDS` says what
    // each round above it buys.
    //
    // Two outcomes remain a pass — the rounds land, or the run stops on the ceiling we set. The
    // budget gate working is the other thing worth knowing. What fails is NEITHER, inside the
    // window.
    test.setTimeout(1_500_000);

    // PARK THE BROWSER ON THE RUN BEFORE POLLING, not after. Playwright hands each test a fresh
    // context, so a test that watches the API and opens the page only at the end shows a blank
    // window for the whole of the longest thing this suite does — ten minutes of nothing, on the
    // tier whose entire point is that a human can watch it happen. The dashboard polls
    // `dashboard.json` itself every 2s, so parking here costs one navigation and the operator
    // sees candidates, bars and the verdict land as they land.
    await open(page, `${made!.addr}/dashboard`);
    await passConsent(page);
    await ready(page);

    let rounds: ReturnType<typeof roundsOf> = [];
    let stopped = "";
    await expect
      .poll(
        async () => {
          const d = await dashboard(request, made!);
          // COMPLETED rounds, never `current_round`. `current_round` is populated when a round
          // STARTS, so reading it here made a test called "a measurement actually lands" pass
          // before anything had been measured — the exact false green this tier exists to
          // prevent, written into the tier itself.
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

    // The hole this closes: the poll above is satisfied by ANY stop, so a run that crashed
    // outright left this test green — `l4.spec.ts` had learned that lesson and this one had not.
    // One asymmetry between two specs doing the same thing is one of them still wrong.
    assertBoundedStop(DATASET, stopped);

    // Per round rather than over the set, so a failure names which one.
    for (const r of rounds) assertRoundMeasured(DATASET, r);

    // Everything above round 0 is a SEARCH round, and each owes two things the origin cannot:
    // a field to choose from, and a verdict about it. `improved` is null until the election
    // stamps it, so it is the honest "a decision was reached" signal — `webapp/CLAUDE.md` makes
    // the same point about `is_winner`, which says nothing on its own.
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

    // Still on the dashboard from before the poll — assert it survived the whole run rather
    // than re-opening it, which would only prove a fresh mount works.
    await ready(page);
    await expect(page.getByRole("img", { name: "Pipeline graph" })).toBeVisible();
  });

  test("it is running under a ceiling, and the browser renders one", async ({ page, request }) => {
    // SOME finite ceiling is in force and no higher than we asked for — never "OUR number",
    // which would fail on exactly the configuration that is safest (see the clamp above).
    const limits = (await dashboard(request, made!))?.run_limits as
      | { spend_budget_usd?: number | null }
      | undefined;
    expect(limits?.spend_budget_usd, "the run has no USD ceiling at all").not.toBeNull();
    expect(limits?.spend_budget_usd).toBeLessThanOrEqual(BUDGET_USD);

    await open(page, `${made!.addr}/dashboard`);
    await passConsent(page);
    await ready(page);
  });

  test("what it PAID and what it COST are both on the wire", async ({ request }) => {
    // Reported and deliberately NOT asserted: this campaign is minted through the check-in, so
    // an agent writes its origin prompt and a floor here would be a number drawn from a coin
    // flip. Why that follows, and what it measured, is ../README.md's.
    reportTape(DATASET, tapeOf(await dashboard(request, made!)));
  });

  test("what the run wrote is readable in Files", async ({ page }) => {
    await open(page, `${made!.addr}/files`);
    await passConsent(page);
    // The project file tree IS the dashboard; a run whose record the operator cannot open is a
    // run they cannot audit.
    await expect(page.locator("#main-content").getByRole("button").first()).toBeVisible();
  });
});
