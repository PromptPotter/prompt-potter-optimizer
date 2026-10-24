import { describe, expect, it } from "vitest";
import type { HierarchyCampaign, HierarchyCourse } from "@/lib/api";
import {
  armAt,
  candidatesAtPath,
  EMPTY_LINEAGE,
  indexLineage,
  innerPanelIndex,
  panelCellKey,
} from "@/lib/derivations";
import { encodeCyclePath, pathLeaf, pathOfAddress, type CyclePath } from "@/lib/ids";
import {
  courseTags,
  hierarchyCampaign,
  hierarchyCandidate,
  hierarchyCourse,
  roundsOf,
} from "@/lib/test-fixtures";

const ROOT: CyclePath = [{ campaignId: "demo__aaaaaa", cycleId: "cycle_root" }];
const FORK: CyclePath = [{ campaignId: "demo__aaaaaa", cycleId: "cycle_root_fork_beef" }];
const R = encodeCyclePath(ROOT);
const F = encodeCyclePath(FORK);

const innerRun = (outer: CyclePath, cycleId: string, task: string | null): HierarchyCampaign => {
  const campaignId = `inner__${cycleId}`;
  return hierarchyCampaign({
    address: `${encodeCyclePath(outer)}~${campaignId}`,
    children: [
      hierarchyCourse(
        { address: encodeCyclePath([...outer, { campaignId, cycleId }]), tags: courseTags("inner") },
        { mint: "inner", task },
      ),
    ],
  });
};

// Both courses mint a C1.1: a fork's cells join on its own course's labels.
function tree(task: string | null = "justlogic-d234/seed-0"): HierarchyCampaign {
  const root = [
    hierarchyCandidate(R, { id: "root-c0", label: "C0" }, { children: [innerRun(ROOT, "inner_a", task)] }),
    hierarchyCandidate(
      R,
      { id: "root-c1", round: 1, label: "C1.1" },
      { children: [innerRun(ROOT, "inner_b", task)] },
    ),
  ];
  const fork = [
    hierarchyCandidate(
      F,
      { id: "fork-c1", round: 1, label: "C1.1" },
      { children: [innerRun(FORK, "inner_c", task)] },
    ),
  ];
  return hierarchyCampaign({
    address: "demo__aaaaaa",
    children: [
      hierarchyCourse({ address: R, children: roundsOf(R, root) }),
      hierarchyCourse({ address: F, children: roundsOf(F, fork) }, { mint: "fork" }),
    ],
  });
}

const index = (task?: string | null) => indexLineage(tree(task));
const cycleOf = (run: HierarchyCourse | undefined) =>
  run && pathLeaf(pathOfAddress(run.address)).cycleId;

describe("candidatesAtPath", () => {
  it("addresses a fork's own candidates", () => {
    expect(candidatesAtPath(index(), FORK).map((c) => c.facts.individual)).toEqual(["fork-c1"]);
  });

  it("gives a course its OWN candidates, never a fork's", () => {
    expect(candidatesAtPath(index(), ROOT).map((c) => c.facts.individual)).toEqual([
      "root-c0",
      "root-c1",
    ]);
  });

  it("does not match a different campaign that reuses a cycle id", () => {
    const other: CyclePath = [{ campaignId: "other__bbbbbb", cycleId: "cycle_root" }];
    expect(candidatesAtPath(index(), other)).toEqual([]);
  });
});

describe("armAt", () => {
  it("meets a document's arm at that document's own address", () => {
    expect(armAt(index(), ROOT, "root-c1")?.facts.individual).toBe("root-c1");
    expect(armAt(index(), FORK, "fork-c1")?.facts.individual).toBe("fork-c1");
  });

  it("is null for an arm the ledger has not minted, and without an address", () => {
    expect(armAt(index(), ROOT, "fork-c1")).toBeNull();
    expect(armAt(index(), null, "root-c1")).toBeNull();
  });
});

describe("innerPanelIndex", () => {
  it("keys each course's cells on the labels that course minted", () => {
    const cell = "justlogic-d234/seed-0";
    expect(cycleOf(innerPanelIndex(index(), FORK).get(panelCellKey("C1.1", cell)))).toBe("inner_c");
    const panel = innerPanelIndex(index(), ROOT);
    expect(cycleOf(panel.get(panelCellKey("C0", cell)))).toBe("inner_a");
    expect(cycleOf(panel.get(panelCellKey("C1.1", cell)))).toBe("inner_b");
    expect([...panel.values()].map(cycleOf)).not.toContain("inner_c");
  });

  it("drops a run with no task rather than guessing its cell", () => {
    expect(innerPanelIndex(index(null), ROOT).size).toBe(0);
  });

  it("is empty rather than throwing when the tree or the address is missing", () => {
    expect(innerPanelIndex(EMPTY_LINEAGE, ROOT).size).toBe(0);
    expect(innerPanelIndex(index(), null).size).toBe(0);
  });
});
