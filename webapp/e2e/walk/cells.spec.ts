import type { APIRequestContext } from "@playwright/test";
import type { HierarchyCourse, HierarchyEntry } from "@/lib/api";
import { panelCellLabel } from "@/lib/derivations";
import { pathOfAddress, type CyclePath } from "@/lib/ids";
import { test, expect, open, campaigns } from "../harness";

const API = "/api/v1";

type Found = { outer: CyclePath; named: HierarchyCourse[] };

const hashOf = (path: CyclePath) =>
  `#/c/${path.map((h) => `${h.campaignId}/${h.cycleId.replace(/^cycle_/, "")}`).join("/")}`;
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// The name the tree row draws for a cell (`ForestRows`), so the walk clicks what the operator reads.
const rowName = (c: HierarchyCourse) => panelCellLabel(c.facts.task ?? "");

function cellsOf(n: HierarchyEntry, out: HierarchyCourse[] = []): HierarchyCourse[] {
  if (n.level === "course" && n.facts.task && pathOfAddress(n.address).length === 2) out.push(n);
  for (const c of n.children) cellsOf(c, out);
  return out;
}

// Discover, never name: the first outer campaign serving a cell.
async function outerServing(request: APIRequestContext): Promise<Found | null> {
  for (const c of await campaigns(request)) {
    const res = await request.get(`${API}/hierarchy/${encodeURIComponent(c.id)}`);
    if (!res.ok()) continue;
    const cells = cellsOf((await res.json()) as HierarchyEntry);
    // Two arms measure one task, so a name may draw several rows: any of them is a correct landing.
    const first = cells[0];
    if (first) {
      const named = cells.filter((y) => rowName(y) === rowName(first));
      return { outer: [{ campaignId: c.id, cycleId: c.cycleId }], named };
    }
  }
  return null;
}

test("a seat cell drills into the inner run that measured it", async ({ page, request }) => {
  const found = await outerServing(request);
  test.skip(!found, "no campaign in this workspace serves a seat cell");
  const { outer, named } = found!;
  await open(page, `${hashOf(outer)}/dashboard`);

  const name = rowName(named[0]!);
  const row = page.getByRole("button", { name: new RegExp(`^${escape(name)}(\\s|$)`) }).first();
  // Only the ACTIVE campaign opens itself (`SidebarContent`); a viewed one is opened down to its cells.
  const viewed = page.locator(".unit-library-family.selected");
  await expect(viewed).toBeVisible({ timeout: 60_000 });
  const own = viewed.getByRole("button", { name: "Expand" });
  if (await own.count()) await own.click();
  for (let depth = 0; depth < 2 && !(await row.isVisible()); depth++) {
    await viewed.locator("xpath=following::button[@aria-label='Expand'][1]").click();
  }
  await expect(row, `the tree lists no row for seat cell ${name}`).toBeVisible({
    timeout: 60_000,
  });
  await row.click();

  const landedOn = () => {
    const hash = new URL(page.url()).hash;
    return named.find((c) => new RegExp(`^${escape(hashOf(pathOfAddress(c.address)))}(/|$)`).test(hash)) ?? null;
  };
  await expect
    .poll(() => landedOn() !== null, { message: `${name} did not drill to a hop it names` })
    .toBe(true);
});
