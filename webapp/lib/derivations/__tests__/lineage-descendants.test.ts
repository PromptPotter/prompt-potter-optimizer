import { describe, expect, it } from "vitest";
import { descendantsOf } from "../lineage-descendants";
import { EMPTY_LINEAGE, indexLineage, mainLineOf } from "../lineage-candidates";
import type { CourseKind, HierarchyCandidate, SubtreeCourse } from "@/lib/api";
import {
  subtreeCampaign,
  hierarchyCandidate,
  subtreeCourse,
  roundsOf,
  supersededTag,
} from "@/lib/test-fixtures";

const C = "camp__aaaaaa::cyc_root";
const FORK = "camp__aaaaaa::cyc_root_fork";
const INNER = `${C}~inner__1::cyc_inner`;

const course = (address: string, cands: HierarchyCandidate[], mint: CourseKind = "root") =>
  subtreeCourse({ address, children: roundsOf(address, cands) }, { mint });
const campaign = (address: string, courses: SubtreeCourse[]) =>
  subtreeCampaign({ address, children: courses });

// A missed descendant fails SILENTLY: a channel keeps rendering a measurement an edit invalidated.
function family() {
  const inner = campaign(`${C}~inner__1`, [
    course(INNER, [hierarchyCandidate(INNER, { id: "F1.1", parent_ids: ["R1.1"] })], "inner"),
  ]);
  return indexLineage(
    campaign("camp__aaaaaa", [
      course(C, [
        hierarchyCandidate(C, { id: "C0" }),
        hierarchyCandidate(C, { id: "R1.1", round: 1, parent_ids: ["C0"] }, { children: [inner] }),
        hierarchyCandidate(C, { id: "R1.2", round: 1, parent_ids: ["C0"] }),
        hierarchyCandidate(C, { id: "R2.1", round: 2, parent_ids: ["R1.1"] }),
      ]),
    ]),
  );
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
    expect(descendantsOf(EMPTY_LINEAGE, ["R1.1"]).size).toBe(1);
  });

  it("terminates on a tree that cycles", () => {
    const cyclic = indexLineage(
      campaign("c", [
        course(C, [
          hierarchyCandidate(C, { id: "A", parent_ids: ["B"] }),
          hierarchyCandidate(C, { id: "B", parent_ids: ["A"] }),
        ]),
      ]),
    );
    expect([...descendantsOf(cyclic, ["A"])].sort()).toEqual(["A", "B"]);
  });
});

describe("mainLineOf", () => {
  const origin = hierarchyCandidate(C, { id: "C0" });
  // A supersede left one individual on two candidates: the line names the address, so the id decides nothing.
  const retired = hierarchyCandidate(
    C,
    { id: "R1.1", round: 1, label: "retired" },
    { tags: [supersededTag(FORK)] },
  );
  const live = hierarchyCandidate(FORK, { id: "R1.1", round: 1, label: "live" });
  const head = hierarchyCandidate(C, { id: "R3.1", round: 3 });
  head.facts.main_line = [
    { round: 0, candidates: [origin.address] },
    { round: 1, candidates: [live.address] },
    { round: 2, candidates: [] },
    { round: 3, candidates: [head.address] },
    { round: 4, candidates: [] },
  ];
  const index = indexLineage(
    campaign("camp__aaaaaa", [
      course(C, [origin, retired, hierarchyCandidate(C, { id: "R1.2", round: 1 }), head]),
      course(FORK, [live], "fork"),
    ]),
  );
  const line = mainLineOf(index, head).map((s) =>
    s.kind === "held" ? `held@${s.round}` : s.node.label,
  );

  it("reads the served crowns from the origin and names every held round", () => {
    expect(line).toEqual(["C0", "live", "held@2", "R3.1", "held@4"]);
  });
});
