import { describe, expect, it } from "vitest";
import type { ArmNode, CourseNode } from "@/lib/api";
import {
  ROOMY,
  LANE_H,
  TOP_PAD,
  extentKeys,
  expandedLaneSpan,
  layout,
  placeNodes,
} from "../forest-layout";
import { nodeKeyOf } from "@/lib/derivations";
import { armNode, armReading, courseNode, ownLevel } from "@/lib/test-fixtures";

function cands(counts: number[]): ArmNode[] {
  return candsH(counts.map((n) => ({ n })));
}

function candsH(rounds: { n: number; held?: boolean }[]): ArmNode[] {
  return rounds.flatMap((r, ri) =>
    Array.from({ length: r.n }, (_, i) => {
      const id = `c${ri + 1}_${i}`;
      const label = `C${ri + 1}.${i + 1}`;
      return armNode({
        id,
        label,
        reading: armReading({
          arm: { round: ri + 1, label, candidate_id: id },
          own: ownLevel(0.5),
          election: { held: true, selected: !r.held && i === r.n - 1 },
        }),
      });
    }),
  );
}

function course(id: string, children: ArmNode[], over: Partial<CourseNode> = {}): CourseNode {
  const path = over.path ?? [{ campaign_id: "camp", cycle_id: id }];
  return courseNode({
    id,
    course_kind: id.includes("_fork_") ? "fork" : "root",
    dataset_name: "ds",
    ...over,
    path,
    // Without its course's path, two seeds' identically-labelled arms share one `nodeKeyOf`.
    children: children.map((c) => ({ ...c, path })),
  });
}

function laneKey(id: string, over: Partial<CourseNode> = {}): string {
  return nodeKeyOf(course(id, [], over));
}

function hangOffWinner(parent: CourseNode, round: number, child: CourseNode): CourseNode {
  const children = parent.children.map((c) =>
    c.reading.arm.round === round && c.reading.election.selected
      ? { ...c, children: [...c.children, child] }
      : c,
  );
  return { ...parent, children };
}


describe("expandedLaneSpan", () => {
  it("is the widest round's candidate count, floored at 1", () => {
    expect(expandedLaneSpan(cands([3, 2, 4]))).toBe(4);
    expect(expandedLaneSpan(cands([1, 1]))).toBe(1);
    expect(expandedLaneSpan([])).toBe(1);
  });
});

describe("layout", () => {
  it("collapsed: one row per course", () => {
    const tree = hangOffWinner(
      course("cycle_a", cands([2, 2])),
      1,
      course("cycle_a_fork_b", cands([1])),
    );
    const { totalLaneRows, laneByKey } = layout(tree, new Set());
    expect(totalLaneRows).toBe(2);
    expect(laneByKey.get(laneKey("cycle_a"))!.laneSpan).toBe(1);
    expect(laneByKey.get(laneKey("cycle_a"))!.laneOffset).toBe(0);
    expect(laneByKey.get(laneKey("cycle_a_fork_b"))!.laneOffset).toBe(1);
  });

  it("two sandboxes' identically-named inner runs get their own lanes", () => {
    const inner = (sandbox: string): CourseNode =>
      course("cycle_inner", cands([1]), {
        course_kind: "inner",
        path: [
          { campaign_id: "camp", cycle_id: sandbox },
          { campaign_id: "inner_camp", cycle_id: "cycle_inner" },
        ],
      });
    const tree = hangOffWinner(
      hangOffWinner(course("cycle_a", cands([1, 1])), 1, inner("cycle_a")),
      2,
      inner("cycle_a_fork_b"),
    );
    const { totalLaneRows, laneByKey } = layout(tree, new Set());
    expect(totalLaneRows).toBe(3);
    expect(laneByKey.size).toBe(3);
    const a = laneKey("cycle_inner", {
      path: [
        { campaign_id: "camp", cycle_id: "cycle_a" },
        { campaign_id: "inner_camp", cycle_id: "cycle_inner" },
      ],
    });
    const b = laneKey("cycle_inner", {
      path: [
        { campaign_id: "camp", cycle_id: "cycle_a_fork_b" },
        { campaign_id: "inner_camp", cycle_id: "cycle_inner" },
      ],
    });
    expect(a).not.toBe(b);
    expect(laneByKey.get(a)!.laneOffset).not.toBe(laneByKey.get(b)!.laneOffset);
  });

  it("expanded course reserves its span and pushes lanes below it down", () => {
    const tree = hangOffWinner(
      course("cycle_a", cands([3, 2, 4])),
      1,
      course("cycle_a_fork_b", cands([1])),
    );
    const { totalLaneRows, laneByKey } = layout(tree, new Set([laneKey("cycle_a")]));
    expect(laneByKey.get(laneKey("cycle_a"))!.laneSpan).toBe(4);
    expect(laneByKey.get(laneKey("cycle_a"))!.laneOffset).toBe(0);
    expect(laneByKey.get(laneKey("cycle_a_fork_b"))!.laneOffset).toBe(4);
    expect(totalLaneRows).toBe(5);
  });

  it("a child course's columns start one right of the candidate it hangs off", () => {
    const tree = hangOffWinner(
      course("cycle_a", cands([2, 2])),
      2,
      course("cycle_a_fork_b", cands([1])),
    );
    const { laneByKey } = layout(tree, new Set());
    expect(laneByKey.get(laneKey("cycle_a"))!.baseCol).toBe(0);
    // Cut at the parent's round 2 (column 2) ⇒ the fork's round 0 is column 3.
    expect(laneByKey.get(laneKey("cycle_a_fork_b"))!.baseCol).toBe(3);
  });

  it("cut at a candidate: later rounds and the courses cut after it take no row", () => {
    const tree = hangOffWinner(
      course("cycle_a", cands([2, 2])),
      2,
      course("cycle_a_fork_b", cands([1])),
    );
    const here = layout(tree, new Set()).laneByKey.get(laneKey("cycle_a"))!.coursePathKey;
    const keep = extentKeys(tree, { coursePathKey: here, candidateId: "c1_1" })!;
    const cut = layout(tree, new Set([laneKey("cycle_a")]), keep);
    expect(cut.laneByKey.size).toBe(1);
    expect(cut.maxCol).toBe(1);
    expect(cut.laneByKey.get(laneKey("cycle_a"))!.candidates.map((c) => c.id)).toEqual([
      "c1_0",
      "c1_1",
    ]);
    expect(layout(tree, new Set()).laneByKey.size).toBe(2);
  });

  it("the seed runs that measured the point stay whole; a fork beside it goes", () => {
    const seed = (cycle: string, rounds: number[]): CourseNode =>
      course(cycle, cands(rounds), {
        course_kind: "inner",
        path: [
          { campaign_id: "camp", cycle_id: "cycle_a" },
          { campaign_id: "inner_camp", cycle_id: cycle },
        ],
      });
    const measured = seed("cycle_seed", [1, 1]);
    const tree = hangOffWinner(
      hangOffWinner(course("cycle_a", cands([1, 1])), 1, measured),
      1,
      course("cycle_a_fork_b", cands([1])),
    );
    const here = layout(tree, new Set()).laneByKey.get(laneKey("cycle_a"))!.coursePathKey;
    const keep = extentKeys(tree, { coursePathKey: here, candidateId: "c1_0" })!;
    const { laneByKey } = layout(tree, new Set(), keep);
    const seedLane = laneByKey.get(nodeKeyOf(measured))!;
    expect(seedLane.candidates.length).toBe(2);
    expect(seedLane.baseCol).toBeGreaterThan(laneByKey.get(laneKey("cycle_a"))!.baseCol);
    expect(laneByKey.has(laneKey("cycle_a_fork_b"))).toBe(false);
  });

  it("a point inside a seed cuts within it, and the sibling seeds are out", () => {
    const seed = (cycle: string): CourseNode =>
      course(cycle, cands([1, 1, 1]), {
        course_kind: "inner",
        path: [
          { campaign_id: "camp", cycle_id: "cycle_a" },
          { campaign_id: "inner_camp", cycle_id: cycle },
        ],
      });
    const mine = seed("cycle_seed0");
    const sibling = seed("cycle_seed1");
    let tree = hangOffWinner(course("cycle_a", cands([1])), 1, mine);
    tree = hangOffWinner(tree, 1, sibling);
    const keep = extentKeys(tree, {
      coursePathKey: layout(tree, new Set()).laneByKey.get(nodeKeyOf(mine))!.coursePathKey,
      candidateId: "c2_0",
    })!;
    const { laneByKey } = layout(tree, new Set(), keep);
    expect(laneByKey.get(nodeKeyOf(mine))!.candidates.map((c) => c.id)).toEqual(["c1_0", "c2_0"]);
    expect(laneByKey.has(nodeKeyOf(sibling))).toBe(false);
    expect(laneByKey.get(laneKey("cycle_a"))!.candidates.map((c) => c.id)).toEqual(["c1_0"]);
  });

  it("an anchor this tree does not hold has no extent", () => {
    const tree = course("cycle_a", cands([2]));
    const here = layout(tree, new Set()).laneByKey.get(laneKey("cycle_a"))!.coursePathKey;
    expect(extentKeys(tree, { coursePathKey: here, candidateId: "nope" })).toBeNull();
    expect(extentKeys(tree, { coursePathKey: "other::cycle_z", candidateId: "c1_0" })).toBeNull();
  });
});

describe("placeNodes", () => {
  it("collapsed: one summary node per round, chained", () => {
    const { laneByKey } = layout(course("cycle_a", cands([2, 3])), new Set());
    const { nodes } = placeNodes(laneByKey, ROOMY);
    const summary = nodes.filter((n) => !n.isExpanded);
    expect(summary).toHaveLength(2);
    expect(summary.map((n) => n.round)).toEqual([1, 2]);
    expect(summary.find((n) => n.round === 2)!.isLastInLane).toBe(true);
  });

  it("expanded: one node per candidate per round + winner→child chain segs", () => {
    const { laneByKey } = layout(course("cycle_a", cands([3, 2])), new Set([laneKey("cycle_a")]));
    const { nodes, segs, spineByKeyRound } = placeNodes(laneByKey, ROOMY);
    const placed = nodes.filter((n) => n.isExpanded && n.round > 0);
    expect(placed).toHaveLength(5);
    expect(placed.filter((n) => n.round === 1 && n.isWinner)).toHaveLength(1);
    const r1winner = spineByKeyRound.get(`${laneKey("cycle_a")}::r1`)!;
    for (const child of placed.filter((n) => n.round === 2)) {
      const seg = segs.find(
        (s) =>
          s.variant === "chain" &&
          s.x1 === r1winner.x &&
          s.y1 === r1winner.y &&
          s.y2 === child.y,
      );
      expect(seg).toBeTruthy();
    }
  });

  it("a child course's stem anchors to the exact candidate it hangs off", () => {
    const tree = hangOffWinner(
      course("cycle_a", cands([2, 3])),
      2,
      course("cycle_a_fork_b", cands([1])),
    );
    const { laneByKey } = layout(tree, new Set([laneKey("cycle_a")]));
    const { segs, spineByKeyRound } = placeNodes(laneByKey, ROOMY);
    const parentR2Winner = spineByKeyRound.get(`${laneKey("cycle_a")}::r2`)!;
    const forkStem = segs.find(
      (s) => s.variant === "fork" && s.x1 === parentR2Winner.x && s.y1 === parentR2Winner.y,
    );
    expect(forkStem).toBeTruthy();
  });

  it("expanded: a held round advances nothing; the next round chains from the last winner", () => {
    const { laneByKey } = layout(
      course("cycle_a", candsH([{ n: 2 }, { n: 2, held: true }, { n: 2 }])),
      new Set([laneKey("cycle_a")]),
    );
    const { nodes, segs, spineByKeyRound } = placeNodes(laneByKey, ROOMY);

    const r1winner = spineByKeyRound.get(`${laneKey("cycle_a")}::r1`)!;
    // A held round's spine entry is the retained parent, so a course cut there still anchors.
    expect(spineByKeyRound.get(`${laneKey("cycle_a")}::r2`)).toBe(r1winner);
    expect(nodes.filter((n) => n.round === 2 && n.isWinner)).toHaveLength(0);

    const r2xs = new Set(nodes.filter((n) => n.round === 2).map((n) => n.x));
    for (const child of nodes.filter((n) => n.round === 3)) {
      const fromWinner = segs.find(
        (s) =>
          s.variant === "chain" &&
          s.x1 === r1winner.x &&
          s.y1 === r1winner.y &&
          s.y2 === child.y,
      );
      expect(fromWinner).toBeTruthy();
      const fromHeld = segs.find(
        (s) => s.variant === "chain" && r2xs.has(s.x1) && s.y2 === child.y,
      );
      expect(fromHeld).toBeFalsy();
    }
  });

  it("collapsed: a held round's summary node is marked not-won", () => {
    const { laneByKey } = layout(
      course("cycle_a", candsH([{ n: 2 }, { n: 2, held: true }])),
      new Set(),
    );
    const { nodes } = placeNodes(laneByKey, ROOMY);
    const summary = nodes.filter((n) => !n.isExpanded);
    expect(summary.find((n) => n.round === 1)!.isWinner).toBe(true);
    expect(summary.find((n) => n.round === 2)!.isWinner).toBe(false);
  });

  it("a lone candidate in a round that crowned nobody is not promoted", () => {
    const { laneByKey } = layout(
      course("cycle_a", candsH([{ n: 1 }, { n: 1, held: true }])),
      new Set([laneKey("cycle_a")]),
    );
    const { nodes } = placeNodes(laneByKey, ROOMY);
    expect(nodes.find((n) => n.round === 2)!.isWinner).toBe(false);
    expect(nodes.find((n) => n.round === 1)!.isWinner).toBe(true);
  });

  it("single expanded course reproduces the intraloop spine (the 'cool one')", () => {
    const { laneByKey, maxCol } = layout(
      course("cycle_a", cands([2, 2, 1])),
      new Set([laneKey("cycle_a")]),
    );
    const { nodes, spineByKeyRound } = placeNodes(laneByKey, ROOMY);
    expect(nodes).toHaveLength(5);
    expect(spineByKeyRound.get(`${laneKey("cycle_a")}::r1`)!.x).toBe(ROOMY.leftPad + 1 * ROOMY.colW);
    expect(spineByKeyRound.get(`${laneKey("cycle_a")}::r3`)!.x).toBe(ROOMY.leftPad + 3 * ROOMY.colW);
    expect(maxCol).toBe(3);
    const r1 = nodes.filter((n) => n.round === 1).sort((a, b) => a.y - b.y);
    expect(r1[1]!.y - r1[0]!.y).toBe(LANE_H);
    expect(TOP_PAD).toBeGreaterThan(0);
  });
});
