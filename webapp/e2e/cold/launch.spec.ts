import type { Page } from "@playwright/test";
import {
  test,
  expect,
  open,
  campaigns,
  command,
  dashboard,
  drafts,
  passConsent,
  roundsOf,
} from "../harness";

// The operator's run acts, pressed on a phone in the offline world: Start launches the run's own
// process inside the world the server holds (`scripts/offline_world.py::held`), so the round is
// watched live, paused, resumed and forked, all at zero spend. Each step leaves its screenshot.

const DATASET = "email-tagging";

test.describe.configure({ mode: "serial" });
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

async function shot(page: Page, name: string) {
  await page.screenshot({ path: test.info().outputPath(`${name}.png`) });
}

test.afterAll(async ({ request }) => {
  // A run still going is paused, so the next spec's world holds none in flight.
  for (const c of await campaigns(request).catch(() => [])) {
    await command(request, "pause", { campaign_id: c.id, cycle_id: c.cycleId });
  }
});

test("Start runs the round live, and it pauses, resumes and forks", async ({ page, request }) => {
  test.setTimeout(300_000);
  const known = new Set((await drafts(request)).map((d) => d.draftId));
  const minted = (await campaigns(request)).length;
  await open(page);
  await passConsent(page);
  // A world with a campaign follows it; a cold one opens on the entry list.
  const entry = page.getByText(/Start a campaign/);
  const plus = page.getByRole("button", { name: "+ New campaign" });
  await expect(entry.or(plus).first()).toBeVisible({ timeout: 60_000 });
  if (!(await entry.isVisible())) await plus.click();
  await page
    .locator(".ingest-picklist-group", { hasText: "Datasets" })
    .getByRole("button", { name: new RegExp(`^${DATASET}\\b`) })
    .click({ timeout: 60_000 });
  await expect.poll(async () => (await drafts(request)).some((d) => !known.has(d.draftId))).toBeTruthy();

  // Enabled once the check-in's resolver turn, the world's optimizer answering it, lets go of the draft.
  const start = page.getByRole("button", { name: /Start campaign/ });
  await expect(start).toBeEnabled({ timeout: 120_000 });
  await shot(page, "01-checkin");
  await start.click();
  await expect.poll(async () => (await campaigns(request)).length, { timeout: 120_000 }).toBeGreaterThan(minted);

  const pause = page.getByRole("button", { name: "Pause run" });
  const resume = page.getByRole("button", { name: "Resume run" });
  await expect(pause, "the launched round is never live").toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("Running").first()).toBeVisible();
  await shot(page, "02-live");

  await pause.click();
  await expect(resume, "a pause pressed mid-round never landed").toBeVisible({ timeout: 120_000 });
  await shot(page, "03-paused");

  await resume.click();
  await expect(pause, "the resumed run is never live").toBeVisible({ timeout: 120_000 });
  await shot(page, "04-resumed");

  // A fork off a candidate the ended run measured: the campaign's tree gains it, and it runs its round.
  await expect(pause, "the resumed run never ended its rounds").toBeHidden({ timeout: 300_000 });
  const [run] = await campaigns(request);
  const tree = async () => (await request.get(`/api/v1/hierarchy/${run!.id}`)).text();
  await page.getByRole("button", { name: "Dashboard" }).click();
  await page.getByRole("button", { name: /^Candidate C1\.\d/ }).first().click();
  await page.getByRole("button", { name: "Steer & fork" }).click();
  await shot(page, "05-steer");
  await page.getByRole("button", { name: "Confirm steered fork" }).click();
  await expect.poll(tree, { message: "the steered fork cut no cycle", timeout: 120_000 }).toMatch(/_fork_/);
  const fork = { ...run!, cycleId: (await tree()).match(/cycle_[0-9a-f]+_fork_[0-9a-f]+/)![0] };
  await expect
    .poll(async () => roundsOf(await dashboard(request, fork)).length, {
      message: "the fork never closed a round of its own",
      timeout: 120_000,
    })
    .toBeGreaterThan(0);
  await shot(page, "06-forked");
});
