import { describe, expect, it } from "vitest";
import { descendantsOf } from "../lineage-descendants";
import type { LineageNode } from "@/lib/api";

// A missed descendant fails SILENTLY: a channel keeps rendering a measurement an edit invalidated.
// Pinned: a losing arm nothing was built on, and a FORK reachable by `parent_id` alone.

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
    // The fork left at R1.1, so its candidate descends from it — reachable by `parent_id` alone.
    expect([...descendantsOf(family(), ["R1.1"])].sort()).toEqual(["F1.1", "R1.1", "R2.1"]);
  });

  it("takes only itself under a losing arm", () => {
    // A round's losers are not parents; over-reaching would blank a channel the edit cost nothing.
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
