import { describe, expect, it } from "vitest";
import { EMPTY_LINEAGE, indexLineage, mainLineOf, withdrawnUnder } from "../lineage-candidates";
import type { CycleMint, HierarchyCandidate, SubtreeCycle } from "@/lib/api/types";
import {
  subtreeCampaign,
  hierarchyCandidate,
  subtreeCycle,
  roundsOf,
  supersededTag,
} from "@/lib/test-fixtures";

const C = "camp__aaaaaa::cyc_root";
const FORK = "camp__aaaaaa::cyc_root_fork";
const INNER = `${C}~inner__1::cyc_inner`;

const cycle = (address: string, cands: HierarchyCandidate[], mint: CycleMint = "root") =>
  subtreeCycle({ address, children: roundsOf(address, cands) }, { mint });
const campaign = (address: string, cycles: SubtreeCycle[]) =>
  subtreeCampaign({ address, children: cycles });

const at = (cycle: string, round: number, key: string) => `${cycle}/r${round}/${key}`;
const C0 = at(C, 0, "C0");
const R11 = at(C, 1, "R1.1");
const F11 = at(INNER, 0, "F1.1");

// A missed descendant fails SILENTLY: a channel keeps rendering a measurement an edit invalidated.
// The one individual repeats across cycles (`F1.1` re-proposes `R1.1`'s id), so only an address keys.
function family() {
  const inner = campaign(`${C}~inner__1`, [
    cycle(INNER, [hierarchyCandidate(INNER, { id: "R1.1", key: "F1.1", descends_from: R11 })], "inner"),
  ]);
  return indexLineage(
    campaign("camp__aaaaaa", [
      cycle(C, [
        hierarchyCandidate(C, { id: "C0" }),
        hierarchyCandidate(C, { id: "R1.1", round: 1, descends_from: C0 }, { children: [inner] }),
        hierarchyCandidate(C, { id: "R1.2", round: 1, descends_from: C0 }),
        hierarchyCandidate(C, { id: "R2.1", round: 2, descends_from: R11 }),
        hierarchyCandidate(C, { id: "X2.2", round: 2, descends_from: C0, donor: F11 }),
      ]),
    ]),
  );
}

const under = (edited: string[], index = family()) =>
  [...withdrawnUnder(index, (c) => edited.includes(c.address))].sort();

describe("withdrawnUnder", () => {
  it("takes the whole line under an edited point, across cycles and through a donor", () => {
    expect(under([R11])).toEqual([at(C, 2, "R2.1"), at(C, 2, "X2.2"), R11, F11].sort());
  });

  it("takes only itself under a losing arm", () => {
    // A round's losers are not parents; over-reaching would blank a channel the edit cost nothing.
    expect(under([at(C, 1, "R1.2")])).toEqual([at(C, 1, "R1.2")]);
  });

  it("takes the family under the origin", () => {
    expect(under([C0])).toHaveLength(6);
  });

  it("is empty with nothing edited, and with no tree", () => {
    expect(under([])).toEqual([]);
    expect(under([R11], EMPTY_LINEAGE)).toEqual([]);
  });

  it("terminates on served edges that cycle", () => {
    const a = at(C, 0, "A");
    const b = at(C, 0, "B");
    const cyclic = indexLineage(
      campaign("c", [
        cycle(C, [
          hierarchyCandidate(C, { id: "A", descends_from: b }),
          hierarchyCandidate(C, { id: "B", descends_from: a }),
        ]),
      ]),
    );
    expect(under([a], cyclic)).toEqual([a, b]);
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
      cycle(C, [origin, retired, hierarchyCandidate(C, { id: "R1.2", round: 1 }), head]),
      cycle(FORK, [live], "fork"),
    ]),
  );
  const line = mainLineOf(index, head).map((s) =>
    s.kind === "held" ? `held@${s.round}` : s.node.label,
  );

  it("reads the served crowns from the origin and names every held round", () => {
    expect(line).toEqual(["C0", "live", "held@2", "R3.1", "held@4"]);
  });
});
