import { describe, expect, it } from "vitest";
import { descendantsOf } from "../lineage-descendants";
import type { LineageNode } from "@/lib/api";

// The closure this computes decides which served numbers a surface may still show as answers, and
// it fails SILENTLY in the dangerous direction: miss a descendant and a channel goes on rendering a
// measurement an edit invalidated, with nothing to say it did. The two shapes worth pinning are the
// ones a real campaign always has — a losing arm that nothing was built on, and a FORK, whose
// candidates hang off the point it branched from and are reachable by no other edge.

function node(
  over: Partial<LineageNode> & Pick<LineageNode, "kind" | "id">,
): LineageNode {
  return { children: [], parent_id: null, ...over } as unknown as LineageNode;
}

// One campaign: root course c0 with C0 → (R1.1 winner, R1.2 loser) → R2.1 under the winner, plus a
// fork course branching off R1.1 and minting F1.1.
function family(): LineageNode {
  return node({
    kind: "course",
    id: "cyc_root",
    children: [
      node({ kind: "candidate", id: "C0" }),
      node({ kind: "candidate", id: "R1.1", parent_id: "C0" }),
      node({ kind: "candidate", id: "R1.2", parent_id: "C0" }),
      node({ kind: "candidate", id: "R2.1", parent_id: "R1.1" }),
      node({
        kind: "course",
        id: "cyc_fork",
        children: [node({ kind: "candidate", id: "F1.1", parent_id: "R1.1" })],
      }),
    ],
  });
}

describe("descendantsOf", () => {
  it("takes the whole line under an edited point, across a fork", () => {
    // R1.1 is where the fork left, so the fork's own candidate descends from it too — reachable by
    // `parent_id` and by nothing else, since a fork is not a node on the line.
    expect([...descendantsOf(family(), ["R1.1"])].sort()).toEqual(["F1.1", "R1.1", "R2.1"]);
  });

  it("takes only itself under a losing arm", () => {
    // Nothing was built on R1.2 — a round's losers are not parents. Over-reaching here would blank
    // a channel for an edit that cost it nothing.
    expect([...descendantsOf(family(), ["R1.2"])]).toEqual(["R1.2"]);
  });

  it("takes the family under the origin", () => {
    expect(descendantsOf(family(), ["C0"]).size).toBe(5);
  });

  it("is empty with nothing edited, and survives no tree", () => {
    expect(descendantsOf(family(), []).size).toBe(0);
    expect(descendantsOf(null, ["R1.1"]).size).toBe(1);
  });

  it("terminates on a tree that cycles", () => {
    // A parent edge pointing back up must not hang the tab; `out` doubles as the visited set.
    const cyclic = node({
      kind: "course",
      id: "c",
      children: [
        node({ kind: "candidate", id: "A", parent_id: "B" }),
        node({ kind: "candidate", id: "B", parent_id: "A" }),
      ],
    });
    expect([...descendantsOf(cyclic, ["A"])].sort()).toEqual(["A", "B"]);
  });
});
