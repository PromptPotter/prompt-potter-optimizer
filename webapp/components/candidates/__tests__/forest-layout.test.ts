import { describe, expect, it } from "vitest";
import type {
  CourseKind,
  CourseSpawn,
  SubtreeCampaign,
  HierarchyCandidate,
  SubtreeCourse,
} from "@/lib/api";
import {
  ROOMY,
  LANE_H,
  TOP_PAD,
  extentKeys,
  expandedLaneSpan,
  layout,
  placeNodes,
} from "../forest-layout";
import { candidatesOf } from "@/lib/derivations";
import { decodeCyclePath, encodeCyclePath } from "@/lib/ids";
import {
  armReading,
  courseTags,
  subtreeCampaign,
  hierarchyCandidate,
  subtreeCourse,
  ownLevel,
  roundsOf,
} from "@/lib/test-fixtures";

type Rounds = (number | { n: number; held?: boolean })[];

const A = encodeCyclePath([{ campaignId: "camp", cycleId: "cycle_a" }]);
const B = encodeCyclePath([{ campaignId: "camp", cycleId: "cycle_a_fork_b" }]);

// Round `first + i` holds `rounds[i]` candidates; the last of each is crowned unless it held, and
// each descends from the newest crown before it, as a mutation of the line serves.
function cands(course: string, rounds: Rounds, first = 1): HierarchyCandidate[] {
  let crown: string | null = null;
  return rounds.flatMap((spec, ri) => {
    const r = typeof spec === "number" ? { n: spec } : spec;
    const round = first + ri;
    const from = crown;
    const made = Array.from({ length: r.n }, (_, i) => {
      const id = `c${round}_${i}`;
      const label = `C${round}.${i + 1}`;
      return hierarchyCandidate(course, {
        id,
        descends_from: from,
        reading: armReading({
          arm: { round, label, key: id },
          own: ownLevel(0.5),
          election: { held: true, selected: !r.held && i === r.n - 1 },
        }),
      });
    });
    if (!r.held) crown = made.at(-1)?.address ?? crown;
    return made;
  });
}

function course(
  address: string,
  rounds: Rounds,
  mint: CourseKind = "root",
  spawn: CourseSpawn | null = null,
  first = 1,
): SubtreeCourse {
  return subtreeCourse(
    { address, tags: courseTags(mint, "ds"), children: roundsOf(address, cands(address, rounds, first)) },
    { mint, spawn },
  );
}

const campaign = (address: string, courses: SubtreeCourse[]): SubtreeCampaign =>
  subtreeCampaign({ address, children: courses });

const winnerAt = (c: SubtreeCourse, round: number): HierarchyCandidate =>
  candidatesOf(c).find(
    (n) => n.facts.reading.arm.round === round && n.facts.reading.election.selected,
  )!;

// A fork of `parent` cut at its round-`round` winner, numbering on from there.
function forkAt(parent: SubtreeCourse, round: number, rounds: Rounds): SubtreeCourse {
  const spawn: CourseSpawn = {
    parent: parent.address,
    cut_round: round + 1,
    trigger: "operator_steered",
    direction: "offshoot",
    issued_by: "",
  };
  const fork = course(B, rounds, "fork", spawn, round + 1);
  return { ...fork, facts: { ...fork.facts, from_candidate: winnerAt(parent, round).address } };
}

// An inner campaign of one course, measuring for a candidate of `outer`.
function inner(outer: string, campaignId: string, cycleId: string, rounds: Rounds) {
  const address = encodeCyclePath([...decodeCyclePath(outer)!, { campaignId, cycleId }]);
  const run = course(address, rounds, "inner");
  return { run, campaign: campaign(`${outer}~${campaignId}`, [run]) };
}

function hangOffWinner(
  parent: SubtreeCourse,
  round: number,
  child: SubtreeCampaign,
): SubtreeCourse {
  const children = parent.children.map((rnd) =>
    rnd.level === "round" && rnd.facts.round === round
      ? {
          ...rnd,
          children: rnd.children.map((c) =>
            c.facts.reading.election.selected
              ? { ...c, children: [...c.children, child] }
              : c,
          ),
        }
      : rnd,
  );
  return { ...parent, children };
}

const ids = (cs: readonly HierarchyCandidate[]) => cs.map((c) => c.facts.individual);

describe("expandedLaneSpan", () => {
  it("is the widest round's candidate count, floored at 1", () => {
    expect(expandedLaneSpan(cands(A, [3, 2, 4]))).toBe(4);
    expect(expandedLaneSpan(cands(A, [1, 1]))).toBe(1);
    expect(expandedLaneSpan([])).toBe(1);
  });
});

describe("layout", () => {
  it("collapsed: one row per course", () => {
    const a = course(A, [2, 2]);
    const { totalLaneRows, laneByKey } = layout(campaign("camp", [a, forkAt(a, 1, [1])]), new Set());
    expect(totalLaneRows).toBe(2);
    expect(laneByKey.get(A)!.laneSpan).toBe(1);
    expect(laneByKey.get(A)!.laneOffset).toBe(0);
    expect(laneByKey.get(B)!.laneOffset).toBe(1);
  });

  it("two sandboxes' identically-named inner runs get their own lanes", () => {
    const inA = inner(A, "inner_camp", "cycle_inner", [1]);
    const inB = inner(B, "inner_camp", "cycle_inner", [1]);
    const a = course(A, [1, 1]);
    const tree = campaign("camp", [
      hangOffWinner(a, 1, inA.campaign),
      hangOffWinner(forkAt(a, 1, [1]), 2, inB.campaign),
    ]);
    const { totalLaneRows, laneByKey } = layout(tree, new Set());
    expect(totalLaneRows).toBe(4);
    expect(laneByKey.size).toBe(4);
    expect(inA.run.address).not.toBe(inB.run.address);
    expect(laneByKey.get(inA.run.address)!.laneOffset).not.toBe(
      laneByKey.get(inB.run.address)!.laneOffset,
    );
  });

  it("expanded course reserves its span and pushes lanes below it down", () => {
    const a = course(A, [3, 2, 4]);
    const tree = campaign("camp", [a, forkAt(a, 1, [1])]);
    const { totalLaneRows, laneByKey } = layout(tree, new Set([A]));
    expect(laneByKey.get(A)!.laneSpan).toBe(4);
    expect(laneByKey.get(A)!.laneOffset).toBe(0);
    expect(laneByKey.get(B)!.laneOffset).toBe(4);
    expect(totalLaneRows).toBe(5);
  });

  it("an inner run starts one column right of its spawner; a fork shares its parent's axis", () => {
    const seed = inner(A, "inner_camp", "cycle_seed", [1]);
    const a = hangOffWinner(course(A, [2, 2]), 2, seed.campaign);
    const { laneByKey } = layout(campaign("camp", [a, forkAt(a, 2, [1])]), new Set());
    expect(laneByKey.get(A)!.baseCol).toBe(0);
    // Hung off round 2 (column 2) ⇒ the run's round 0 is column 3.
    expect(laneByKey.get(seed.run.address)!.baseCol).toBe(3);
    expect(laneByKey.get(B)!.baseCol).toBe(0);
  });

  it("cut at a candidate: later rounds and the courses cut after it take no row", () => {
    const a = course(A, [2, 2]);
    const tree = campaign("camp", [a, forkAt(a, 2, [1])]);
    const keep = extentKeys(tree, { coursePathKey: A, candidateId: "c1_1" })!;
    const cut = layout(tree, new Set([A]), keep);
    expect(cut.laneByKey.size).toBe(1);
    expect(cut.maxCol).toBe(1);
    expect(ids(cut.laneByKey.get(A)!.candidates)).toEqual(["c1_0", "c1_1"]);
    expect(layout(tree, new Set()).laneByKey.size).toBe(2);
  });

  it("a fork lifting no round keeps the chain to its from-candidate, never its parent's", () => {
    const C = encodeCyclePath([{ campaignId: "camp", cycleId: "cycle_a_fork_c" }]);
    const a = course(A, [2, 2, 2]);
    const spawn: CourseSpawn = {
      parent: B,
      cut_round: 0,
      trigger: "operator_steered",
      direction: "offshoot",
      issued_by: "",
    };
    const steered = course(C, [1], "fork", spawn);
    const tree = campaign("camp", [
      a,
      forkAt(a, 2, [1]),
      { ...steered, facts: { ...steered.facts, from_candidate: winnerAt(a, 1).address } },
    ]);
    const keep = extentKeys(tree, { coursePathKey: C, candidateId: "c1_0" })!;
    const { laneByKey } = layout(tree, new Set([A]), keep);
    expect(ids(laneByKey.get(A)!.candidates)).toEqual(["c1_0", "c1_1"]);
    expect(laneByKey.has(B)).toBe(false);
  });

  it("the seed runs that measured the point stay whole; a fork beside it goes", () => {
    const measured = inner(A, "inner_camp", "cycle_seed", [1, 1]);
    const a = hangOffWinner(course(A, [1, 1]), 1, measured.campaign);
    const tree = campaign("camp", [a, forkAt(a, 1, [1])]);
    const keep = extentKeys(tree, { coursePathKey: A, candidateId: "c1_0" })!;
    const { laneByKey } = layout(tree, new Set(), keep);
    const seedLane = laneByKey.get(measured.run.address)!;
    expect(seedLane.candidates.length).toBe(2);
    expect(seedLane.baseCol).toBeGreaterThan(laneByKey.get(A)!.baseCol);
    expect(laneByKey.has(B)).toBe(false);
  });

  it("a point inside a seed cuts within it, and the sibling seeds are out", () => {
    const mine = inner(A, "seed0", "cycle_seed", [1, 1, 1]);
    const sibling = inner(A, "seed1", "cycle_seed", [1, 1, 1]);
    const a = hangOffWinner(hangOffWinner(course(A, [1]), 1, mine.campaign), 1, sibling.campaign);
    const tree = campaign("camp", [a]);
    const keep = extentKeys(tree, { coursePathKey: mine.run.address, candidateId: "c2_0" })!;
    const { laneByKey } = layout(tree, new Set(), keep);
    expect(ids(laneByKey.get(mine.run.address)!.candidates)).toEqual(["c1_0", "c2_0"]);
    expect(laneByKey.has(sibling.run.address)).toBe(false);
    expect(ids(laneByKey.get(A)!.candidates)).toEqual(["c1_0"]);
  });

  it("a point inside an inner run no candidate claims keeps its host's lane, and no round of it", () => {
    const loose = inner(A, "seed0", "cycle_seed", [1, 1, 1]);
    const host = course(A, [1, 1]);
    const tree = campaign("camp", [{ ...host, children: [...host.children, loose.campaign] }]);
    const keep = extentKeys(tree, { coursePathKey: loose.run.address, candidateId: "c2_0" })!;
    const { laneByKey } = layout(tree, new Set(), keep);
    expect(ids(laneByKey.get(loose.run.address)!.candidates)).toEqual(["c1_0", "c2_0"]);
    expect(ids(laneByKey.get(A)!.candidates)).toEqual([]);
  });

  it("an anchor this tree does not hold has no extent", () => {
    const tree = campaign("camp", [course(A, [2])]);
    expect(extentKeys(tree, { coursePathKey: A, candidateId: "nope" })).toBeNull();
    expect(extentKeys(tree, { coursePathKey: "other::cycle_z", candidateId: "c1_0" })).toBeNull();
  });
});

describe("placeNodes", () => {
  const one = (rounds: Rounds) => campaign("camp", [course(A, rounds)]);

  it("collapsed: one summary node per round, chained", () => {
    const { laneByKey } = layout(one([2, 3]), new Set());
    const { nodes } = placeNodes(laneByKey, ROOMY);
    const summary = nodes.filter((n) => !n.isExpanded);
    expect(summary).toHaveLength(2);
    expect(summary.map((n) => n.round)).toEqual([1, 2]);
    expect(summary.find((n) => n.round === 2)!.isLastInLane).toBe(true);
  });

  it("expanded: one node per candidate per round + winner→child chain segs", () => {
    const { laneByKey } = layout(one([3, 2]), new Set([A]));
    const { nodes, segs, spineByKeyRound } = placeNodes(laneByKey, ROOMY);
    const placed = nodes.filter((n) => n.isExpanded && n.round > 0);
    expect(placed).toHaveLength(5);
    expect(placed.filter((n) => n.round === 1 && n.isWinner)).toHaveLength(1);
    const r1winner = spineByKeyRound.get(`${A}::r1`)!;
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

  it("expanded: a child hangs from its served main; donor and gene links reach their sources", () => {
    const base = one([1, 2, 1]);
    const ran = (round: number) =>
      candidatesOf(base.children[0]!).filter((c) => c.facts.reading.arm.round === round);
    const [early] = ran(1);
    const [loser, winner] = ran(2);
    const lane = base.children[0]!;
    const tree = campaign("camp", [
      {
        ...lane,
        children: lane.children.map((rnd) =>
          rnd.level === "round" && rnd.facts.round === 3
            ? {
                ...rnd,
                children: rnd.children.map((c) => ({
                  ...c,
                  facts: {
                    ...c.facts,
                    descends_from: loser!.address,
                    donor: early!.address,
                    donated_loci: ["n.persona"],
                    gene_sources: [
                      { locus: "n.instruction", source: winner!.address, similarity: 0.9 },
                      { locus: "n.instruction", source: loser!.address, similarity: 0.4 },
                    ],
                  },
                })),
              }
            : rnd,
        ),
      },
    ]);
    const { nodes, segs } = placeNodes(layout(tree, new Set([A])).laneByKey, ROOMY);
    const at = (address: string) => nodes.find((n) => n.candKey === address)!;
    const child = nodes.find((n) => n.round === 3)!;
    const into = segs.filter((s) => s.y2 === child.y && s.x2 === child.x - ROOMY.candStub);
    const from = (variant: string) =>
      into.filter((s) => s.variant === variant).map((s) => [s.x1, s.y1]);

    expect(from("chain")).toEqual([[at(loser!.address).x, at(loser!.address).y]]);
    expect(from("donor")).toEqual([[at(early!.address).x, at(early!.address).y]]);
    expect(from("gene")).toEqual([[at(winner!.address).x, at(winner!.address).y]]);
  });

  it("a fork's stem anchors to the exact candidate it was cut from", () => {
    const a = course(A, [2, 3]);
    const { laneByKey } = layout(campaign("camp", [a, forkAt(a, 2, [1])]), new Set([A]));
    const { segs, spineByKeyRound } = placeNodes(laneByKey, ROOMY);
    const parentR2Winner = spineByKeyRound.get(`${A}::r2`)!;
    const forkStem = segs.find(
      (s) => s.variant === "fork" && s.x1 === parentR2Winner.x && s.y1 === parentR2Winner.y,
    );
    expect(forkStem).toBeTruthy();
  });

  it("expanded: a held round advances nothing; the next round chains from the last winner", () => {
    const { laneByKey } = layout(one([{ n: 2 }, { n: 2, held: true }, { n: 2 }]), new Set([A]));
    const { nodes, segs, spineByKeyRound } = placeNodes(laneByKey, ROOMY);

    const r1winner = spineByKeyRound.get(`${A}::r1`)!;
    // A held round's spine entry is the retained parent, so a course cut there still anchors.
    expect(spineByKeyRound.get(`${A}::r2`)).toBe(r1winner);
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
    const { laneByKey } = layout(one([{ n: 2 }, { n: 2, held: true }]), new Set());
    const { nodes } = placeNodes(laneByKey, ROOMY);
    const summary = nodes.filter((n) => !n.isExpanded);
    expect(summary.find((n) => n.round === 1)!.isWinner).toBe(true);
    expect(summary.find((n) => n.round === 2)!.isWinner).toBe(false);
  });

  it("a lone candidate in a round that crowned nobody is not promoted", () => {
    const { laneByKey } = layout(one([{ n: 1 }, { n: 1, held: true }]), new Set([A]));
    const { nodes } = placeNodes(laneByKey, ROOMY);
    expect(nodes.find((n) => n.round === 2)!.isWinner).toBe(false);
    expect(nodes.find((n) => n.round === 1)!.isWinner).toBe(true);
  });

  it("single expanded course reproduces the intraloop spine (the 'cool one')", () => {
    const { laneByKey, maxCol } = layout(one([2, 2, 1]), new Set([A]));
    const { nodes, spineByKeyRound } = placeNodes(laneByKey, ROOMY);
    expect(nodes).toHaveLength(5);
    expect(spineByKeyRound.get(`${A}::r1`)!.x).toBe(ROOMY.leftPad + 1 * ROOMY.colW);
    expect(spineByKeyRound.get(`${A}::r3`)!.x).toBe(ROOMY.leftPad + 3 * ROOMY.colW);
    expect(maxCol).toBe(3);
    const r1 = nodes.filter((n) => n.round === 1).sort((a, b) => a.y - b.y);
    expect(r1[1]!.y - r1[0]!.y).toBe(LANE_H);
    expect(TOP_PAD).toBeGreaterThan(0);
  });
});
