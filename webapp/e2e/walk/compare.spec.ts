import type { APIRequestContext, Page } from "@playwright/test";
import type { HierarchyCandidate, HierarchyEntry, HierarchyResponse } from "@/lib/api/types";
import { test, expect, open } from "../harness";
import { DESKTOP } from "../world";

// Every candidate of every campaign's served tree, inner runs included: discovered, never named.
async function candidates(request: APIRequestContext): Promise<HierarchyCandidate[]> {
  const listing = (await (await request.get("/api/v1/hierarchy")).json()) as HierarchyResponse;
  const out: HierarchyCandidate[] = [];
  const walk = (e: HierarchyEntry) => {
    if (e.entry_kind === "candidate") out.push(e);
    for (const c of "children" in e ? (e.children as HierarchyEntry[]) : []) walk(c);
  };
  for (const campaign of listing.entries) {
    const res = await request.get(`/api/v1/hierarchy/${encodeURIComponent(campaign.address)}`);
    if (res.ok()) walk((await res.json()) as HierarchyEntry);
  }
  return out;
}

// Ticks the candidate into Compare from its own Candidates card, then opens Compare beside it.
async function compare(page: Page, c: HierarchyCandidate) {
  await open(page, `#/${c.address}/candidates`);
  await page.getByRole("button", { name: "More candidate options" }).first().click();
  await page.getByText("Compare this searchpoint").first().click();
  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("button", { name: "Compare", exact: true })
    .click();
  return page.locator("#view-compare .cmp-channel").first();
}

// A phone shows one column: Compare opens over the card.
test.describe("compare", DESKTOP, () => {

  test("a candidate re-measured in a later round compares at the round it was picked in", async ({
    page,
    request,
  }) => {
    const all = await candidates(request);
    const cycleOf = (c: HierarchyCandidate) => c.address.slice(0, c.address.lastIndexOf("/r"));
    const early = all.find((c) =>
      all.some(
        (o) =>
          cycleOf(o) === cycleOf(c) &&
          o.facts.reading.arm.key === c.facts.reading.arm.key &&
          o.facts.reading.arm.round > c.facts.reading.arm.round,
      ),
    );
    test.skip(!early, "no arm in this world is re-measured in a later round");
    const card = await compare(page, early!);
    await expect(card.locator(".cmp-channel-name")).toHaveText(early!.label);
  });

  test("an inner run's candidate compares with every read resolved through its chain", async ({
    page,
    request,
  }) => {
    const inner = (await candidates(request)).find((c) => c.address.includes("~"));
    test.skip(!inner, "no L4 inner run in this world");
    const card = await compare(page, inner!);
    await expect(card.locator(".cmp-channel-name")).toHaveText(inner!.label);
    // The auto `problems` fixture fails a 404 on `/hierarchy/<inner>` or `/campaigns/<inner>/pipeline`.
    await expect(page.getByText("How we got here")).toBeVisible();
  });
});
