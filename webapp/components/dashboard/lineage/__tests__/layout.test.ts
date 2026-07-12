import { describe, expect, it } from "vitest";
import type { CampaignLineageCycle } from "@/lib/api";
import {
  COL_W,
  LANE_H,
  LEFT_PAD,
  TOP_PAD,
  buildTree,
  expandedLaneSpan,
  layout,
  placeNodes,
  type CycleDetail,
  type DetailByCycle,
} from "../layout";

// --- builders -------------------------------------------------------------

function cycle(
  id: string,
  opts: Partial<CampaignLineageCycle> = {},
): CampaignLineageCycle {
  return {
    cycle_id: id,
    sibling_kind: id.includes("_fork_") ? "fork" : "root",
    immediate_parent_cycle_id: null,
    fork_from_round: null,
    fork_from_candidate_id: null,
    trigger: "",
    steered_by: null,
    round_column_offset: 0,
    status: "",
    dataset_name: "ds",
    hearts: null,
    lives_cap: null,
    best_accuracy: null,
    rounds: [],
    ...opts,
  };
}

// `counts` is one entry per round (round number is the index + 1); each value
// is the candidate count for that round. The last candidate is the winner, so
// every round here advances the incumbent.
function detail(counts: number[]): CycleDetail {
  return {
    rounds: counts.map((n, ri) => ({
      round: ri + 1,
      advanced: n > 0,
      candidates: Array.from({ length: n }, (_, i) => ({
        candidateId: `c${ri + 1}_${i}`,
        label: `C${ri + 1}.${i + 1}`,
        accuracy: 0.5,
        isWinner: i === n - 1,
      })),
    })),
  };
}

// Per-round builder that can mark a round "held" — it has candidates but crowned
// no winner (advanced=false, no isWinner candidate).
function detailH(rounds: { n: number; held?: boolean }[]): CycleDetail {
  return {
    rounds: rounds.map((r, ri) => ({
      round: ri + 1,
      advanced: !r.held && r.n > 0,
      candidates: Array.from({ length: r.n }, (_, i) => ({
        candidateId: `c${ri + 1}_${i}`,
        label: `C${ri + 1}.${i + 1}`,
        isWinner: !r.held && i === r.n - 1,
      })),
    })),
  };
}

// --- tests ----------------------------------------------------------------

describe("expandedLaneSpan", () => {
  it("is the widest round's candidate count, floored at 1", () => {
    expect(expandedLaneSpan(detail([3, 2, 4]))).toBe(4);
    expect(expandedLaneSpan(detail([1, 1]))).toBe(1);
    expect(expandedLaneSpan({ rounds: [] })).toBe(1);
  });
});

describe("layout", () => {
  it("collapsed: one row per cycle", () => {
    const cycles = [
      cycle("cycle_a"),
      cycle("cycle_a_fork_b", { immediate_parent_cycle_id: "cycle_a", fork_from_round: 1 }),
    ];
    const tree = buildTree("cycle_a", cycles)!;
    const detailByCycle: DetailByCycle = new Map([
      ["cycle_a", detail([2, 2])],
      ["cycle_a_fork_b", detail([1])],
    ]);
    const { totalLaneRows, laneByCycle } = layout(tree, detailByCycle, new Set());
    expect(totalLaneRows).toBe(2);
    expect(laneByCycle.get("cycle_a")!.laneSpan).toBe(1);
    expect(laneByCycle.get("cycle_a")!.laneOffset).toBe(0);
    expect(laneByCycle.get("cycle_a_fork_b")!.laneOffset).toBe(1);
  });

  it("expanded cycle reserves its span and pushes lanes below it down", () => {
    const cycles = [
      cycle("cycle_a"),
      cycle("cycle_a_fork_b", { immediate_parent_cycle_id: "cycle_a", fork_from_round: 1 }),
    ];
    const tree = buildTree("cycle_a", cycles)!;
    const detailByCycle: DetailByCycle = new Map([
      ["cycle_a", detail([3, 2, 4])],
      ["cycle_a_fork_b", detail([1])],
    ]);
    const { totalLaneRows, laneByCycle } = layout(
      tree,
      detailByCycle,
      new Set(["cycle_a"]),
    );
    expect(laneByCycle.get("cycle_a")!.laneSpan).toBe(4);
    expect(laneByCycle.get("cycle_a")!.laneOffset).toBe(0);
    // The collapsed fork now starts at row 4, not row 1.
    expect(laneByCycle.get("cycle_a_fork_b")!.laneOffset).toBe(4);
    expect(totalLaneRows).toBe(5);
  });
});

describe("placeNodes", () => {
  it("collapsed: one summary node per round, chained", () => {
    const cycles = [cycle("cycle_a")];
    const tree = buildTree("cycle_a", cycles)!;
    const detailByCycle: DetailByCycle = new Map([["cycle_a", detail([2, 3])]]);
    const { laneByCycle } = layout(tree, detailByCycle, new Set());
    const { nodes } = placeNodes(laneByCycle);
    const summary = nodes.filter((n) => !n.isExpanded);
    expect(summary).toHaveLength(2);
    expect(summary.map((n) => n.round)).toEqual([1, 2]);
    // Last round's node carries the lane label.
    expect(summary.find((n) => n.round === 2)!.isLastInLane).toBe(true);
  });

  it("expanded: one node per candidate per round + winner→child chain segs", () => {
    const cycles = [cycle("cycle_a")];
    const tree = buildTree("cycle_a", cycles)!;
    const detailByCycle: DetailByCycle = new Map([["cycle_a", detail([3, 2])]]);
    const { laneByCycle } = layout(tree, detailByCycle, new Set(["cycle_a"]));
    const { nodes, segs, spineByCycleRound } = placeNodes(laneByCycle);
    const cands = nodes.filter((n) => n.isExpanded && n.round > 0);
    expect(cands).toHaveLength(5); // 3 + 2
    // Exactly one winner per round.
    expect(cands.filter((n) => n.round === 1 && n.isWinner)).toHaveLength(1);
    // Round 2's two children each chain from round 1's winner.
    const r1winner = spineByCycleRound.get("cycle_a::r1")!;
    const r2 = cands.filter((n) => n.round === 2);
    for (const child of r2) {
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

  it("fork stem anchors to the parent's winner spine under expansion", () => {
    const cycles = [
      cycle("cycle_a"),
      cycle("cycle_a_fork_b", {
        immediate_parent_cycle_id: "cycle_a",
        fork_from_round: 2,
        round_column_offset: 0,
      }),
    ];
    const tree = buildTree("cycle_a", cycles)!;
    const detailByCycle: DetailByCycle = new Map([
      ["cycle_a", detail([2, 3])], // parent expanded, round 2 has a winner
      ["cycle_a_fork_b", detail([1])],
    ]);
    const { laneByCycle } = layout(tree, detailByCycle, new Set(["cycle_a"]));
    const { segs, spineByCycleRound } = placeNodes(laneByCycle);
    const parentR2Winner = spineByCycleRound.get("cycle_a::r2")!;
    const forkStem = segs.find(
      (s) => s.variant === "fork" && s.x1 === parentR2Winner.x && s.y1 === parentR2Winner.y,
    );
    expect(forkStem).toBeTruthy();
  });

  it("expanded: a held round advances nothing; the next round chains from the last winner", () => {
    const cycles = [cycle("cycle_a")];
    const tree = buildTree("cycle_a", cycles)!;
    // R1 wins, R2 is held (no winner), R3 wins.
    const detailByCycle: DetailByCycle = new Map([
      ["cycle_a", detailH([{ n: 2 }, { n: 2, held: true }, { n: 2 }])],
    ]);
    const { laneByCycle } = layout(tree, detailByCycle, new Set(["cycle_a"]));
    const { nodes, segs, spineByCycleRound } = placeNodes(laneByCycle);

    const r1winner = spineByCycleRound.get("cycle_a::r1")!;
    // A held round mints NO new spine node — its spine entry is the retained
    // incumbent (round 1's winner), so a fork cut here still anchors correctly.
    expect(spineByCycleRound.get("cycle_a::r2")).toBe(r1winner);
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
    const cycles = [cycle("cycle_a")];
    const tree = buildTree("cycle_a", cycles)!;
    const detailByCycle: DetailByCycle = new Map([
      ["cycle_a", detailH([{ n: 2 }, { n: 2, held: true }])],
    ]);
    const { laneByCycle } = layout(tree, detailByCycle, new Set());
    const { nodes } = placeNodes(laneByCycle);
    const summary = nodes.filter((n) => !n.isExpanded);
    expect(summary.find((n) => n.round === 1)!.isWinner).toBe(true);
    expect(summary.find((n) => n.round === 2)!.isWinner).toBe(false);
  });

  it("single expanded cycle reproduces the intraloop spine (the 'cool one')", () => {
    const cycles = [cycle("cycle_a")];
    const tree = buildTree("cycle_a", cycles)!;
    const detailByCycle: DetailByCycle = new Map([["cycle_a", detail([2, 2, 1])]]);
    const { laneByCycle, maxCol } = layout(tree, detailByCycle, new Set(["cycle_a"]));
    const { nodes, spineByCycleRound } = placeNodes(laneByCycle);
    // 2 + 2 + 1 candidate nodes (no origin trunk)
    expect(nodes).toHaveLength(5);
    // A winner spine entry per round, columns ascending.
    expect(spineByCycleRound.get("cycle_a::r1")!.x).toBe(LEFT_PAD + 1 * COL_W);
    expect(spineByCycleRound.get("cycle_a::r3")!.x).toBe(LEFT_PAD + 3 * COL_W);
    expect(maxCol).toBe(3);
    // Band-centered round-1 fan: top candidate above center for span 2.
    const r1 = nodes.filter((n) => n.round === 1).sort((a, b) => a.y - b.y);
    expect(r1[1]!.y - r1[0]!.y).toBe(LANE_H);
    expect(TOP_PAD).toBeGreaterThan(0);
  });
});
