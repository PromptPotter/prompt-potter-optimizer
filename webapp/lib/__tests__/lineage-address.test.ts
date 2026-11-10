import { describe, expect, it } from "vitest";
import type { SubtreeCampaign } from "@/lib/api/types";
import { armAt, candidatesAtPath, indexLineage } from "@/lib/derivations";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import {
  courseTags,
  subtreeCampaign,
  hierarchyCandidate,
  subtreeCourse,
  roundsOf,
} from "@/lib/test-fixtures";

const ROOT: CyclePath = [{ campaignId: "demo__aaaaaa", cycleId: "cycle_root" }];
const FORK: CyclePath = [{ campaignId: "demo__aaaaaa", cycleId: "cycle_root_fork_beef" }];
const R = encodeCyclePath(ROOT);
const F = encodeCyclePath(FORK);

const innerRun = (outer: CyclePath, cycleId: string, task: string | null): SubtreeCampaign => {
  const campaignId = `inner__${cycleId}`;
  return subtreeCampaign({
    address: `${encodeCyclePath(outer)}~${campaignId}`,
    children: [
      subtreeCourse(
        { address: encodeCyclePath([...outer, { campaignId, cycleId }]), tags: courseTags("inner") },
        { mint: "inner", task },
      ),
    ],
  });
};

// Both courses mint a C1.1: a fork's cells join on its own course's labels.
function tree(task = "justlogic-d234/seed-0"): SubtreeCampaign {
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
  return subtreeCampaign({
    address: "demo__aaaaaa",
    children: [
      subtreeCourse({ address: R, children: roundsOf(R, root) }),
      subtreeCourse({ address: F, children: roundsOf(F, fork) }, { mint: "fork" }),
    ],
  });
}

const index = () => indexLineage(tree());

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
