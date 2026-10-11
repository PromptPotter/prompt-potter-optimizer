import { describe, expect, it } from "vitest";
import { descendantsOf } from "../lineage-descendants";
import { indexLineage, mainLineOf } from "../lineage-candidates";
import type { CourseNode } from "@/lib/api";
import { armNode, courseNode } from "@/lib/test-fixtures";

// A missed descendant fails SILENTLY: a channel keeps rendering a measurement an edit invalidated.
function family(): CourseNode {
  return courseNode({
    id: "cyc_root",
    children: [
      armNode({ id: "C0" }),
      armNode({
        id: "R1.1",
        parent_ids: ["C0"],
        children: [
          courseNode({
            id: "cyc_inner",
            children: [armNode({ id: "F1.1", parent_ids: ["R1.1"] })],
          }),
        ],
      }),
      armNode({ id: "R1.2", parent_ids: ["C0"] }),
      armNode({ id: "R2.1", parent_ids: ["R1.1"] }),
    ],
  });
}

describe("descendantsOf", () => {
  it("takes the whole line under an edited point, across courses", () => {
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
    const cyclic = courseNode({
      id: "c",
      children: [
        armNode({ id: "A", parent_ids: ["B"] }),
        armNode({ id: "B", parent_ids: ["A"] }),
      ],
    });
    expect([...descendantsOf(cyclic, ["A"])].sort()).toEqual(["A", "B"]);
  });
});

describe("mainLineOf", () => {
  const origin = armNode({ id: "C0" });
  // A repair left two rows on one id: the line names the row, so the id decides nothing.
  const retired = armNode({ id: "R1.1", label: "retired", superseded_by: "cyc_fork" }, 1);
  const live = armNode({ id: "R1.1", label: "live" }, 1);
  const head = armNode({ id: "R3.1" }, 3);
  head.main_line = [
    { round: 0, rows: [origin.row] },
    { round: 1, rows: [live.row] },
    { round: 2, rows: [] },
    { round: 3, rows: [head.row] },
    { round: 4, rows: [] },
  ];
  const root = courseNode({
    id: "cyc_root",
    children: [origin, retired, live, armNode({ id: "R1.2" }, 1), head],
  });
  const line = mainLineOf(indexLineage(root), head).map((s) =>
    s.kind === "held" ? `held@${s.round}` : s.node.label,
  );

  it("reads the served crowns from the origin and names every held round", () => {
    expect(line).toEqual(["C0", "live", "held@2", "R3.1", "held@4"]);
  });
});
