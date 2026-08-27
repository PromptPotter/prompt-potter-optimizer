import { describe, expect, it } from "vitest";
import type { LineageNode } from "@/lib/api";
import {
  ROOMY,
  LANE_H,
  TOP_PAD,
  extentKeys,
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
    status: "",
    election_held: false,
    is_winner: false,
    theta: null,
    theta_se: null,
    cumulative_theta: null,
    evaluators: {},
    mean_fitness_ci_lo: null,
    mean_fitness_ci_hi: null,
    matched_parent_lift: null,
    matched_parent_lift_ci_lo: null,
    matched_parent_lift_ci_hi: null,
    scored_samples: null,
    expected_samples: null,
    cached_samples: null,
    lens_value: null,
    composite_rank: null,
    lens_rank: null,
    sample_set_accuracy: null,
    sample_set_n: null,
    divergence: null,
    divergent: false,
    superseded_by: null,
    course_kind: null,
    run_phase: null,
    dataset_name: "",
    trigger: "",
    fork_direction: null,
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
  // A course is addressed by its PATH, and the lane key is built from it. Default to
  // the tenant's own store; a sandboxed course passes its own `path` via `over`.
  const path = over.path ?? [{ campaign_id: "camp", cycle_id: id }];
  return node({
    kind: "course",
    id,
    label: id,
    course_kind: id.includes("_fork_") ? "fork" : "root",
    dataset_name: "ds",
    ...over,
    path,
    // A candidate wears its COURSE's path, as the server stamps it — `nodeKeyOf` is
    // `(path, id)`, so without it two seeds' identically-labelled arms share one address and
    // anything keyed on it silently folds them together.
    children: children.map((c) => (c.kind === "candidate" ? { ...c, path } : c)),
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

  // A cut has to take the SHAPE down, not just hide nodes: a fork cut after the point would
  // otherwise reserve a lane row and widen the drawing with nothing drawn on it.
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
    // Round 1 entire — the arm it beat comes with it — and nothing past it.
    expect(cut.laneByKey.get(laneKey("cycle_a"))!.candidates.map((c) => c.id)).toEqual([
      "c1_0",
      "c1_1",
    ]);
    // Uncut is the identity: the same call with no extent lays out the whole family.
    expect(layout(tree, new Set()).laneByKey.size).toBe(2);
  });

  // The L4 case, and the reason the extent is a WALK rather than a round-column test. A seed run
  // measured the candidate it hangs off — it is how that point got its number — but it is drawn
  // one column RIGHT of it, so a column test drops every seed of a campaign read at its own
  // origin: a `promptpotter-self` card showing one dot where six lineages ran.
  it("the seed runs that measured the point stay whole; a fork beside it goes", () => {
    const seed = (cycle: string, rounds: number[]): LineageNode =>
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
    // The seed rides in whole — both its rounds — though both sit past the anchor's own column.
    const seedLane = laneByKey.get(nodeKeyOf(measured))!;
    expect(seedLane.candidates.length).toBe(2);
    expect(seedLane.baseCol).toBeGreaterThan(laneByKey.get(laneKey("cycle_a"))!.baseCol);
    // The fork hangs off the same candidate and is a line BESIDE it, not its measurement.
    expect(laneByKey.has(laneKey("cycle_a_fork_b"))).toBe(false);
  });

  // The other half of the same rule, and the one an exemption gets wrong: a point INSIDE a seed
  // is cut inside it, and the seeds measuring its ANCESTOR are not its history — they produced
  // some other number for the same outer point.
  it("a point inside a seed cuts within it, and the sibling seeds are out", () => {
    const seed = (cycle: string): LineageNode =>
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
    // Rounds 1-2 of my own seed; round 3 is after the point and gone.
    expect(laneByKey.get(nodeKeyOf(mine))!.candidates.map((c) => c.id)).toEqual(["c1_0", "c2_0"]);
    // The sibling measured the same outer candidate and is not how THIS point came to be.
    expect(laneByKey.has(nodeKeyOf(sibling))).toBe(false);
    // The outer chain is kept, cut at the candidate the seed hangs off.
    expect(laneByKey.get(laneKey("cycle_a"))!.candidates.map((c) => c.id)).toEqual(["c1_0"]);
  });

  // A point on ANOTHER campaign's tree — the ordinary case once a board holds channels from
  // several. Answering with an extent would cut this drawing to a history it has none of.
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
    // Last round's node carries the lane label.
    expect(summary.find((n) => n.round === 2)!.isLastInLane).toBe(true);
  });

  it("expanded: one node per candidate per round + winner→child chain segs", () => {
    const { laneByKey } = layout(course("cycle_a", cands([3, 2])), new Set([laneKey("cycle_a")]));
    const { nodes, segs, spineByKeyRound } = placeNodes(laneByKey, ROOMY);
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
    const { segs, spineByKeyRound } = placeNodes(laneByKey, ROOMY);
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
    const { nodes, segs, spineByKeyRound } = placeNodes(laneByKey, ROOMY);

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
    // Being the only candidate there is not an election.
    expect(nodes.find((n) => n.round === 2)!.isWinner).toBe(false);
    expect(nodes.find((n) => n.round === 1)!.isWinner).toBe(true);
  });

  it("single expanded course reproduces the intraloop spine (the 'cool one')", () => {
    const { laneByKey, maxCol } = layout(
      course("cycle_a", cands([2, 2, 1])),
      new Set([laneKey("cycle_a")]),
    );
    const { nodes, spineByKeyRound } = placeNodes(laneByKey, ROOMY);
    // 2 + 2 + 1 candidate nodes (no origin trunk)
    expect(nodes).toHaveLength(5);
    // A winner spine entry per round, columns ascending.
    expect(spineByKeyRound.get(`${laneKey("cycle_a")}::r1`)!.x).toBe(ROOMY.leftPad + 1 * ROOMY.colW);
    expect(spineByKeyRound.get(`${laneKey("cycle_a")}::r3`)!.x).toBe(ROOMY.leftPad + 3 * ROOMY.colW);
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
