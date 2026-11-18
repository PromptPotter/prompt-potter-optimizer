import { describe, expect, it } from "vitest";
import type { SubtreeCampaign } from "@/lib/api/types";
import { armAt, candidatesAtPath, indexLineage } from "@/lib/derivations";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import {
  cycleTags,
  subtreeCampaign,
  hierarchyCandidate,
  subtreeCycle,
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
      subtreeCycle(
        { address: encodeCyclePath([...outer, { campaignId, cycleId }]), tags: cycleTags("inner") },
        { mint: "inner", task },
      ),
    ],
  });
};

// Both cycles mint a C1.1: a fork's cells join on its own cycle's labels.
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
      subtreeCycle({ address: R, children: roundsOf(R, root) }),
      subtreeCycle({ address: F, children: roundsOf(F, fork) }, { mint: "fork" }),
    ],
  });
}

const index = () => indexLineage(tree());

describe("candidatesAtPath", () => {
  it("addresses a fork's own candidates", () => {
    expect(candidatesAtPath(index(), FORK).map((c) => c.facts.individual)).toEqual(["fork-c1"]);
  });

  it("gives a cycle its OWN candidates, never a fork's", () => {
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
    expect(armAt(index(), ROOT, { round: 1, arm: "root-c1" })?.facts.individual).toBe("root-c1");
    expect(armAt(index(), FORK, { round: 1, arm: "fork-c1" })?.facts.individual).toBe("fork-c1");
  });

  it("is null for an arm the ledger has not minted, in another round, and without an address", () => {
    expect(armAt(index(), ROOT, { round: 1, arm: "fork-c1" })).toBeNull();
    expect(armAt(index(), ROOT, { round: 2, arm: "root-c1" })).toBeNull();
    expect(armAt(index(), null, { round: 1, arm: "root-c1" })).toBeNull();
  });
});
