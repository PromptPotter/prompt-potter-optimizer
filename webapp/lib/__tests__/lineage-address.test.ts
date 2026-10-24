import { describe, expect, it } from "vitest";
import type { CourseLineage } from "@/lib/api";
import { armAt, candidatesAtPath, indexLineage, innerPanelIndex, panelCellKey } from "@/lib/derivations";
import type { CyclePath } from "@/lib/ids";
import {
  armNode,
  armReading,
  courseLineage,
  courseTags,
  hierarchyCourse,
} from "@/lib/test-fixtures";

const ROOT: CyclePath = [{ campaignId: "demo__aaaaaa", cycleId: "cycle_root" }];
const FORK: CyclePath = [{ campaignId: "demo__aaaaaa", cycleId: "cycle_root_fork_beef" }];

const hops = (path: CyclePath) =>
  path.map((h) => ({ campaign_id: h.campaignId, cycle_id: h.cycleId }));

const innerRun = (id: string, task: string | null) =>
  courseLineage({
    id,
    course: hierarchyCourse({ address: id, tags: courseTags("inner") }, { task }),
    path: hops([...ROOT, { campaignId: "inner__1", cycleId: id }]),
  });

// The fork's own C1.1 (`reading.arm.label`) is renumbered C1.2 (`label`) on the root's timeline.
function tree(task: string | null = "justlogic-d234/seed-0"): CourseLineage {
  return courseLineage({
    id: "cycle_root",
    path: hops(ROOT),
    children: [
      armNode({ id: "root-c0", label: "C0", path: hops(ROOT), children: [innerRun("inner_a", task)] }),
      armNode(
        { id: "root-c1", label: "C1.1", path: hops(ROOT), children: [innerRun("inner_b", task)] },
        1,
      ),
      armNode({
        id: "fork-c1",
        label: "C1.2",
        reading: armReading({ arm: { round: 1, label: "C1.1", key: "fork-c1" } }),
        path: hops(FORK),
        children: [innerRun("inner_c", task)],
      }),
    ],
  });
}

const index = (task?: string | null) => indexLineage(tree(task));

describe("candidatesAtPath", () => {
  it("addresses a fork's attempts, which no course lookup can reach", () => {
    expect(candidatesAtPath(index(), FORK).map((c) => c.id)).toEqual(["fork-c1"]);
  });

  it("gives a course its OWN candidates, without the ones a fork contributed to it", () => {
    expect(candidatesAtPath(index(), ROOT).map((c) => c.id)).toEqual(["root-c0", "root-c1"]);
  });

  it("does not match a different campaign that reuses a cycle id", () => {
    const other: CyclePath = [{ campaignId: "other__bbbbbb", cycleId: "cycle_root" }];
    expect(candidatesAtPath(index(), other)).toEqual([]);
  });
});

describe("armAt", () => {
  it("meets a document's arm at that document's own address", () => {
    expect(armAt(index(), ROOT, "root-c1")?.id).toBe("root-c1");
    expect(armAt(index(), FORK, "fork-c1")?.id).toBe("fork-c1");
  });

  it("is null for an arm the ledger has not minted, and without an address", () => {
    expect(armAt(index(), ROOT, "fork-c1")).toBeNull();
    expect(armAt(index(), null, "root-c1")).toBeNull();
  });
});

describe("innerPanelIndex", () => {
  it("keys a fork's cells on the label the FORK minted, not the renumbered one", () => {
    const panel = innerPanelIndex(index(), FORK);
    expect(panel.get(panelCellKey("C1.1", "justlogic-d234/seed-0"))?.id).toBe("inner_c");
    expect(panel.get(panelCellKey("C1.2", "justlogic-d234/seed-0"))).toBeUndefined();
  });

  it("resolves an ordinary course's cells, where both labels agree", () => {
    const panel = innerPanelIndex(index(), ROOT);
    expect(panel.get(panelCellKey("C0", "justlogic-d234/seed-0"))?.id).toBe("inner_a");
    expect(panel.get(panelCellKey("C1.1", "justlogic-d234/seed-0"))?.id).toBe("inner_b");
    expect([...panel.values()].map((r) => r.id)).not.toContain("inner_c");
  });

  it("drops a run with no task rather than guessing its cell", () => {
    expect(innerPanelIndex(index(null), ROOT).size).toBe(0);
  });

  it("is empty rather than throwing when the tree or the address is missing", () => {
    expect(innerPanelIndex(indexLineage(null), ROOT).size).toBe(0);
    expect(innerPanelIndex(index(), null).size).toBe(0);
  });
});
