import { describe, expect, it } from "vitest";
import { groupByRoot } from "@/lib/derivations";
import type { HierarchyCampaign } from "@/lib/api/types";
import { subtreeCampaign } from "@/lib/test-fixtures";

function rootedAt(address: string, rootCycle: string): HierarchyCampaign {
  const c = subtreeCampaign({ address });
  return { ...c, facts: { ...c.facts, root: `${address}::${rootCycle}` } };
}

describe("groupByRoot", () => {
  it("groups consecutive campaigns sharing a decoded root cycle, keeping served order", () => {
    const groups = groupByRoot([
      rootedAt("camp_a", "cycle_x"),
      rootedAt("camp_b", "cycle_x"),
      rootedAt("camp_c", "cycle_y"),
    ]);
    expect(groups.map((g) => [g.rootCycleId, g.campaigns.map((c) => c.address)])).toEqual([
      ["cycle_x", ["camp_a", "camp_b"]],
      ["cycle_y", ["camp_c"]],
    ]);
  });
});
