import { describe, expect, it } from "vitest";
import type { LineageNode } from "@/lib/api";
import {
  COL_W,
  LANE_H,
  LEFT_PAD,
  TOP_PAD,
  expandedLaneSpan,
  layout,
  placeNodes,
} from "../forest-layout";
import { candidatesOf, nodeKeyOf } from "@/lib/derivations";

// --- builders -------------------------------------------------------------

// A served node with every field at its serialized default — `over` names only
// what the case is about.
function node(
  over: Partial<LineageNode> & Pick<LineageNode, "kind" | "id" | "label">,
): LineageNode {
  return {
    parent_id: null,
    // A candidate this course minted itself, so both labels agree. A fork-contributed
    // attempt is the case where they diverge, and `over` names it when that is the point.
    course_label: over.label,
    path: [],
    children: [],
    round: null,
    accuracy: null,
    composite_fitness: null,
    state: "",
    is_winner: false,
    theta: null,
    theta_se: null,
    evaluators: {},
    composite_ci_lo: null,
    composite_ci_hi: null,
    scored_samples: null,
    expected_samples: null,
    cumulative_accuracy: null,
    lens_value: null,
    sample_set_accuracy: null,
    sample_set_n: null,
    divergence: null,
    divergent: false,
    course_kind: null,
    run_phase: "",
    dataset_name: "",
    trigger: "",
    steered_by: null,
    task: null,
    best_accuracy: null,
    origin_accuracy: null,
    hearts: null,
    lives_cap: null,
    ...over,
  };
}

// `counts` is one entry per round (round number is the index + 1); each value is
// the candidate count for that round. The last candidate wins, so every round here
// closes AND advances the incumbent.
function cands(counts: number[]): LineageNode[] {
  return candsH(counts.map((n) => ({ n })));
}

// `held` marks a round that crowned nobody — it either closed with no candidate
// beating the incumbent, or never closed at all. Either way there is no winner.
function candsH(rounds: { n: number; held?: boolean }[]): LineageNode[] {
  return rounds.flatMap((r, ri) =>
    Array.from({ length: r.n }, (_, i) =>
      node({
        kind: "candidate",
        id: `c${ri + 1}_${i}`,
        label: `C${ri + 1}.${i + 1}`,
        round: ri + 1,
        accuracy: 0.5,
        is_winner: !r.held && i === r.n - 1,
      }),
    ),
  );
}

function course(
  id: string,
  children: LineageNode[],
  over: Partial<LineageNode> = {},
): LineageNode {
  return node({
    kind: "course",
    id,
    label: id,
    course_kind: id.includes("_fork_") ? "fork" : "root",
    dataset_name: "ds",
    // A course is addressed by its PATH, and the lane key is built from it. Default to
    // the tenant's own store; a sandboxed course passes its own `path` via `over`.
    path: [{ campaign_id: "camp", cycle_id: id }],
    children,
    ...over,
  });
}

// A course's lane key, exactly as `layout` computes it — asked of the same function
// rather than hand-rolled here, so the test cannot drift from the key it asserts on.
function laneKey(id: string, over: Partial<LineageNode> = {}): string {
  return nodeKeyOf(course(id, [], over));
}

// Hang a course off the winner of `round` — the edge the tree serves, and the only
// thing a fork's geometry needs to know.
function hangOffWinner(parent: LineageNode, round: number, child: LineageNode): LineageNode {
  const children = parent.children.map((c) =>
    c.round === round && c.is_winner
      ? { ...c, children: [...c.children, child] }
      : c,
  );
  return { ...parent, children };
}

// --- tests ----------------------------------------------------------------

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

  // Two L4 inner runs, one id. This is real: inner cycle ids are minted per sandbox,
  // so sibling sandboxes repeat them — one id sits in three sandboxes on disk today.
  // Keyed on `course.id` these two collapse onto one lane and a run vanishes from the
  // forest; keyed on the address they are two courses, which is what they are.
  it("two sandboxes' identically-named inner runs get their own lanes", () => {
    const inner = (sandbox: string): LineageNode =>
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
    // Three lanes: the root and BOTH inner runs — not two with one overwritten.
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
    // The collapsed fork now starts at row 4, not row 1.
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
});

describe("placeNodes", () => {
  it("collapsed: one summary node per round, chained", () => {
    const { laneByKey } = layout(course("cycle_a", cands([2, 3])), new Set());
    const { nodes } = placeNodes(laneByKey);
    const summary = nodes.filter((n) => !n.isExpanded);
    expect(summary).toHaveLength(2);
    expect(summary.map((n) => n.round)).toEqual([1, 2]);
    // Last round's node carries the lane label.
    expect(summary.find((n) => n.round === 2)!.isLastInLane).toBe(true);
  });

  it("expanded: one node per candidate per round + winner→child chain segs", () => {
    const { laneByKey } = layout(course("cycle_a", cands([3, 2])), new Set([laneKey("cycle_a")]));
    const { nodes, segs, spineByKeyRound } = placeNodes(laneByKey);
    const placed = nodes.filter((n) => n.isExpanded && n.round > 0);
    expect(placed).toHaveLength(5); // 3 + 2
    // Exactly one winner per round.
    expect(placed.filter((n) => n.round === 1 && n.isWinner)).toHaveLength(1);
    // Round 2's two children each chain from round 1's winner.
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
    const { segs, spineByKeyRound } = placeNodes(laneByKey);
    const parentR2Winner = spineByKeyRound.get(`${laneKey("cycle_a")}::r2`)!;
    const forkStem = segs.find(
      (s) => s.variant === "fork" && s.x1 === parentR2Winner.x && s.y1 === parentR2Winner.y,
    );
    expect(forkStem).toBeTruthy();
  });

  it("expanded: a held round advances nothing; the next round chains from the last winner", () => {
    // R1 wins, R2 is held (closed, no winner), R3 wins.
    const { laneByKey } = layout(
      course("cycle_a", candsH([{ n: 2 }, { n: 2, held: true }, { n: 2 }])),
      new Set([laneKey("cycle_a")]),
    );
    const { nodes, segs, spineByKeyRound } = placeNodes(laneByKey);

    const r1winner = spineByKeyRound.get(`${laneKey("cycle_a")}::r1`)!;
    // A held round mints NO new spine node — its spine entry is the retained
    // incumbent (round 1's winner), so a course cut here still anchors correctly.
    expect(spineByKeyRound.get(`${laneKey("cycle_a")}::r2`)).toBe(r1winner);
    // No round-2 candidate is crowned.
    expect(nodes.filter((n) => n.round === 2 && n.isWinner)).toHaveLength(0);

    // Round 3's candidates chain from round 1's winner (the last REAL winner),
    // never from a held round-2 candidate — the pre-fix misdraw.
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
    const { nodes } = placeNodes(laneByKey);
    const summary = nodes.filter((n) => !n.isExpanded);
    expect(summary.find((n) => n.round === 1)!.isWinner).toBe(true);
    expect(summary.find((n) => n.round === 2)!.isWinner).toBe(false);
  });

  it("a lone candidate in a round that crowned nobody is not promoted", () => {
    const { laneByKey } = layout(
      course("cycle_a", candsH([{ n: 1 }, { n: 1, held: true }])),
      new Set([laneKey("cycle_a")]),
    );
    const { nodes } = placeNodes(laneByKey);
    // Being the only candidate there is not an election.
    expect(nodes.find((n) => n.round === 2)!.isWinner).toBe(false);
    expect(nodes.find((n) => n.round === 1)!.isWinner).toBe(true);
  });

  it("single expanded course reproduces the intraloop spine (the 'cool one')", () => {
    const { laneByKey, maxCol } = layout(
      course("cycle_a", cands([2, 2, 1])),
      new Set([laneKey("cycle_a")]),
    );
    const { nodes, spineByKeyRound } = placeNodes(laneByKey);
    // 2 + 2 + 1 candidate nodes (no origin trunk)
    expect(nodes).toHaveLength(5);
    // A winner spine entry per round, columns ascending.
    expect(spineByKeyRound.get(`${laneKey("cycle_a")}::r1`)!.x).toBe(LEFT_PAD + 1 * COL_W);
    expect(spineByKeyRound.get(`${laneKey("cycle_a")}::r3`)!.x).toBe(LEFT_PAD + 3 * COL_W);
    expect(maxCol).toBe(3);
    // Band-centered round-1 fan: top candidate above center for span 2.
    const r1 = nodes.filter((n) => n.round === 1).sort((a, b) => a.y - b.y);
    expect(r1[1]!.y - r1[0]!.y).toBe(LANE_H);
    expect(TOP_PAD).toBeGreaterThan(0);
  });

  it("candidatesOf takes only candidate children, never a nested course", () => {
    const tree = hangOffWinner(
      course("cycle_a", cands([2])),
      1,
      course("cycle_a_fork_b", cands([1])),
    );
    expect(candidatesOf(tree)).toHaveLength(2);
    expect(candidatesOf(tree).every((c) => c.kind === "candidate")).toBe(true);
  });
});
