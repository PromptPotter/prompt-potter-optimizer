import type { APIRequestContext } from "@playwright/test";
import type { HierarchyCourse, SubtreeEntry } from "@/lib/api/types";
import { encodeCyclePath, pathOfAddress, type CyclePath } from "@/lib/ids";
import { test, expect, open, campaigns, DASHBOARD_VIEWS } from "../harness";

const API = "/api/v1";

// A cell and the name the tree row draws for it (`ForestRows`), so the walk clicks what the operator
// reads: an inner campaign's one course folds into its campaign's row, which draws the campaign's label.
type Cell = { course: HierarchyCourse; row: string };
type Found = { outer: CyclePath; named: HierarchyCourse[]; row: string };

const hashOf = (path: CyclePath) => `#/${encodeCyclePath(path)}`;
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function cellsOf(n: SubtreeEntry, out: Cell[] = []): Cell[] {
  for (const c of n.children) {
    const cell = c.level === "course" && c.facts.task && pathOfAddress(c.address).length === 2;
    if (cell && n.level === "campaign") {
      out.push({ course: c, row: n.label });
    }
    cellsOf(c, out);
  }
  return out;
}

// Discover, never name: the first outer campaign serving a cell.
async function outerServing(request: APIRequestContext): Promise<Found | null> {
  for (const c of await campaigns(request)) {
    const res = await request.get(`${API}/hierarchy/${encodeURIComponent(c.id)}`);
    if (!res.ok()) continue;
    const cells = cellsOf((await res.json()) as SubtreeEntry);
    // Two arms measure one task, so a name may draw several rows: any of them is a correct landing.
    const first = cells[0];
    if (first) {
      const named = cells.filter((y) => y.row === first.row).map((y) => y.course);
      return { outer: [{ campaignId: c.id, cycleId: c.cycleId }], named, row: first.row };
    }
  }
  return null;
}

test("a seat cell drills into the inner run that measured it", async ({ page, request, isMobile }) => {
  test.skip(isMobile, "a phone's sidebar is the list screen, never beside the view it drills from (responsive.spec)");
  const found = await outerServing(request);
  // The walk world nests an L4 campaign (`gate.py::_walk_world`), so a world without one is a failure.
  expect(found, "no campaign in this workspace serves a seat cell").not.toBeNull();
  const { outer, named, row: name } = found!;
  await open(page, `${hashOf(outer)}${DASHBOARD_VIEWS}`);

  // The row's name opens on its model's vendor mark, then the label.
  const row = page.getByRole("button", { name: new RegExp(`(^|\\s)${escape(name)}(\\s|$)`) }).first();
  // Only the ACTIVE campaign opens itself (`SidebarContent`); a viewed one is opened down to its cells.
  const viewed = page.locator(".unit-library-family.selected");
  await expect(viewed).toBeVisible({ timeout: 60_000 });
  const own = viewed.getByRole("button", { name: "Expand" });
  if (await own.count()) await own.click();
  // An opened campaign reads its subtree before it draws a row (`ForestRows.tsx::RunRow`): open one
  // level more only once no read is in flight, never on a glance taken while one is.
  const reading = page.getByText(/^Reading its candidates/);
  await expect(async () => {
    if (await row.isVisible()) return;
    await expect(reading).toHaveCount(0);
    if (await row.isVisible()) return;
    await viewed.locator("xpath=following::button[@aria-label='Expand'][1]").click();
    throw new Error(`opened one level more toward seat cell ${name}`);
  }, `the tree lists no row for seat cell ${name}`).toPass({ timeout: 60_000, intervals: [1_000] });
  await row.click();

  const landedOn = () => {
    const hash = new URL(page.url()).hash;
    return named.find((c) => new RegExp(`^${escape(hashOf(pathOfAddress(c.address)))}(/|$)`).test(hash)) ?? null;
  };
  await expect
    .poll(() => landedOn() !== null, { message: `${name} did not drill to a hop it names` })
    .toBe(true);
});
